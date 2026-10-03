"""REST API for accounts' systems: live state, history, CSV export, settings and node commands.

Requests are signed in either with a session cookie (POST /api/auth/login, see auth.py) or with an
account API key in the X-API-Key header (create one with manage.py). Contractors (and API keys) see
all their account's systems; homeowners see the systems they are members of, read-only apart from
marking the air filter changed.

Run:  uvicorn hvaccloud.api:app --port 8000
"""
import csv
import datetime as dt
import io
import json
import mimetypes
import os
import random
import threading
from typing import Literal

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import auth, calc, service, settings
from .db import (Account, Alert, AlertMute, AlertPrefs, ApiKey, Command, Device, ServiceInfo, Snapshot, System,
                 SystemMember, Telemetry, User, as_utc, hash_key, init_db, make_engine, session_factory, utcnow)
from .refrigerants import FLUIDS

mimetypes.add_type("font/woff", ".woff")   # Windows does not know it; StaticFiles uses mimetypes
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
HISTORY_KEYS = ["p_low", "p_high", "sat_low", "sat_high", "t_suc", "t_liq", "sh", "sc", "oat", "t_sup", "t_ret", "dt"]
CSV_COLS = ["mode", "p_low", "p_high", "sat_low", "sat_high", "t_suc", "t_liq", "sh", "sc",
            "oat", "orh", "t_ret", "t_sup", "dt", "ctoa", "approach", "Y", "W", "G", "OB"]


class MqttPublisher:
    """Publishes commands to hvac/<site>/<node>/cmd; connects on first use."""

    def __init__(self, connect_timeout=5.0):
        self._client = None
        self._lock = threading.Lock()
        self._connected = threading.Event()
        self._timeout = connect_timeout

    def __call__(self, topic, payload):
        import paho.mqtt.client as mqtt
        with self._lock:
            if self._client is None:
                c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                client_id=f"hvac-cloud-api-{random.randint(1000, 9999)}")
                if settings.MQTT_USER:
                    c.username_pw_set(settings.MQTT_USER, settings.MQTT_PASS)
                c.on_connect = lambda cl, u, f, rc, p=None: self._connected.set() if rc == 0 else None
                c.on_disconnect = lambda *a: self._connected.clear()
                c.reconnect_delay_set(1, 30)
                c.connect_async(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=30)
                c.loop_start()
                self._client = c
        if not self._connected.wait(self._timeout):
            return False
        info = self._client.publish(topic, json.dumps(payload), qos=1)
        return info.rc == 0


class SystemSettings(BaseModel):
    refrigerant: str | None = None
    heat_pump: bool | None = None
    ob_energized: Literal["cool", "heat"] | None = None
    atm_psia: float | None = Field(default=None, ge=10, le=15.5)


class ContractorIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=320)


class ItemIn(BaseModel):
    """Only the fields sent are changed; send interval_run_hours: null to stop counting run hours."""
    interval_days: int | None = Field(default=None, ge=1, le=3650)
    interval_run_hours: float | None = Field(default=None, gt=0, le=20000)
    last_done: dt.date | None = None


class AckIn(BaseModel):
    ack: bool = True


class AlertSettingsIn(BaseModel):
    email_owner: bool | None = None
    email_contractor: bool | None = None


class MuteIn(BaseModel):
    hours: float = Field(ge=0, le=24 * 90)     # 0 = unmute


class DoneIn(BaseModel):
    date: dt.date | None = None        # the viewer's local date; the server's when left out


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


class PasswordIn(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


class CommandIn(BaseModel):
    node: Literal["outdoor", "indoor"]
    cmd: dict


def create_app(sessions=None, publisher=None, stale=settings.STALE_SECONDS):
    if sessions is None:
        engine = make_engine()
        init_db(engine)
        sessions = session_factory(engine)
    publish = publisher or MqttPublisher()
    throttle = auth.Throttle()
    app = FastAPI(title="HVAC Monitor cloud API", version="0.1.0")

    def db():
        with sessions() as s:
            yield s

    def principal(request: Request, s=Depends(db), x_api_key: str | None = Header(None),
                  fs_session: str | None = Cookie(None)):
        """Who is asking: an API key (acts as a contractor of its account) or a signed-in user."""
        if x_api_key:
            key = s.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(x_api_key)))
            if key is None:
                raise HTTPException(401, "invalid API key")
            key.last_used = utcnow()
            s.commit()
            return auth.Principal(role="contractor", account_id=key.account_id, via="key")
        user_id = auth.session_user_id(s, fs_session)
        user = s.get(User, user_id) if user_id else None
        if user is None:
            raise HTTPException(401, "sign in required")
        if request.method not in ("GET", "HEAD") and not request.headers.get(auth.CSRF_HEADER):
            raise HTTPException(403, f"missing {auth.CSRF_HEADER} header")
        return auth.Principal(role=user.role, account_id=user.account_id, user_id=user.id)

    def visible_systems(s, who):
        if who.is_tech:
            return select(System).where(System.account_id == who.account_id)
        return select(System).join(SystemMember, SystemMember.system_id == System.id).where(SystemMember.user_id == who.user_id)

    def own_system(system_id: int, who=Depends(principal), s=Depends(db)):
        system = s.scalar(visible_systems(s, who).where(System.id == system_id))
        if system is None:
            raise HTTPException(404, "no such system")
        return system

    def tech_system(system=Depends(own_system), who=Depends(principal)):
        """Technician-only endpoints: settings, commands, calibration, alert handling."""
        if not who.is_tech:
            raise HTTPException(403, "for your contractor only")
        return system

    def me_view(s, who):
        user = s.get(User, who.user_id) if who.user_id else None
        acct = s.get(Account, who.account_id) if who.account_id else None
        return {"role": who.role, "via": who.via,
                "user": {"id": user.id, "email": user.email, "name": user.name} if user else None,
                "account": {"id": acct.id, "name": acct.name} if acct else None}

    def set_cookie(response, token):
        response.set_cookie(auth.COOKIE, token, max_age=int(settings.SESSION_DAYS * 86400), httponly=True,
                            samesite="lax", secure=settings.COOKIE_SECURE, path="/")

    def device_view(d, now):
        seen = as_utc(d.last_seen)
        age = (now - seen).total_seconds() if seen else None
        return {"id": d.id, "node": d.node, "online": bool(age is not None and age <= stale and d.connected),
                "age": round(age, 1) if age is not None else None, "fw": d.fw, "ip": d.ip, "rssi": d.rssi,
                "status": d.last_status}

    def system_view(system):
        return {"id": system.id, "name": system.name, "site_id": system.site_id, **system.calc_config()}

    def snapshots(s, system, minutes):
        since = utcnow() - dt.timedelta(minutes=minutes)
        return s.execute(select(Snapshot.time, Snapshot.data)
                         .where(Snapshot.system_id == system.id, Snapshot.time >= since)
                         .order_by(Snapshot.time)).all()

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/app/")

    # ---------- sign-in ----------
    @app.post("/api/auth/login")
    def login(body: LoginIn, response: Response, s=Depends(db)):
        email = body.email.strip().lower()
        if throttle.blocked(email):
            raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
        user = s.scalar(select(User).where(User.email == email))
        if user is None or not auth.check_password(body.password, user.password_hash):
            throttle.fail(email)
            raise HTTPException(401, "Email or password is not right.")
        throttle.clear(email)
        user.last_login = utcnow()
        token = auth.new_session(s, user.id)
        s.commit()
        set_cookie(response, token)
        return me_view(s, auth.Principal(role=user.role, account_id=user.account_id, user_id=user.id))

    @app.post("/api/auth/logout")
    def logout(response: Response, s=Depends(db), fs_session: str | None = Cookie(None)):
        auth.end_session(s, fs_session)
        response.delete_cookie(auth.COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(who=Depends(principal), s=Depends(db)):
        return me_view(s, who)

    @app.post("/api/auth/password")
    def change_password(body: PasswordIn, who=Depends(principal), s=Depends(db)):
        user = s.get(User, who.user_id) if who.user_id else None
        if user is None:
            raise HTTPException(400, "API keys have no password")
        if not auth.check_password(body.current, user.password_hash):
            raise HTTPException(401, "The current password is not right.")
        problem = auth.password_problem(body.new)
        if problem:
            raise HTTPException(422, problem)
        user.password_hash = auth.hash_password(body.new)
        s.commit()
        return {"ok": True}

    @app.get("/api/refrigerants")
    def refrigerants():
        return list(FLUIDS)

    @app.get("/api/systems")
    def list_systems(who=Depends(principal), s=Depends(db)):
        rows = s.scalars(visible_systems(s, who).order_by(System.id))
        return [system_view(x) for x in rows]

    @app.get("/api/systems/{system_id}")
    def get_system(system=Depends(own_system), s=Depends(db)):
        now = utcnow()
        devices = s.scalars(select(Device).where(Device.system_id == system.id).order_by(Device.node))
        return {**system_view(system), "devices": [device_view(d, now) for d in devices]}

    @app.patch("/api/systems/{system_id}")
    def update_system(body: SystemSettings, system=Depends(tech_system), s=Depends(db)):
        changes = body.model_dump(exclude_none=True)
        if "refrigerant" in changes and changes["refrigerant"] not in FLUIDS:
            raise HTTPException(422, f"refrigerant must be one of {list(FLUIDS)}")
        for k, v in changes.items():
            setattr(system, k, v)
        s.commit()
        return system_view(system)

    @app.get("/api/systems/{system_id}/latest")
    def latest(system=Depends(own_system), s=Depends(db)):
        """Newest derived snapshot, plus each node's status and its newest raw telemetry."""
        now = utcnow()
        devices = {d.node: d for d in s.scalars(select(Device).where(Device.system_id == system.id))}
        row = s.execute(select(Snapshot.time, Snapshot.data).where(Snapshot.system_id == system.id)
                        .order_by(Snapshot.time.desc()).limit(1)).first()
        nodes = {}
        for n in calc.NODES:
            d = devices.get(n)
            if d is None:
                nodes[n] = None
                continue
            raw = s.scalar(select(Telemetry.data).where(Telemetry.device_id == d.id)
                           .order_by(Telemetry.time.desc()).limit(1))
            nodes[n] = {**device_view(d, now), "data": raw}
        return {"time": as_utc(row.time).isoformat() if row else None,
                "run_started_at": as_utc(system.run_started_at).isoformat() if system.run_started_at else None,
                "derived": row.data if row else None, "nodes": nodes}

    @app.get("/api/systems/{system_id}/summary")
    def summary(hours: float = Query(24, gt=0, le=24 * 31), system=Depends(own_system), s=Depends(db)):
        """Compressor runtime and cycles, outside high and inside average over the last `hours`."""
        on_s, cycles, prev_t, prev_on, oat_hi, ret = 0.0, 0, None, False, None, []
        for t, d in snapshots(s, system, hours * 60):
            t = as_utc(t).timestamp()
            on = d.get("mode") in ("cooling", "heating")
            if on and not prev_on:
                cycles += 1
            if prev_on and prev_t is not None:
                on_s += min(t - prev_t, 60)      # a gap longer than a minute is an outage, not runtime
            prev_t, prev_on = t, on
            oat = calc.num(d.get("oat"))
            if oat is not None:
                oat_hi = oat if oat_hi is None else max(oat_hi, oat)
            v = calc.num(d.get("t_ret"))
            if v is not None:
                ret.append(v)
        return {"hours": hours, "runtime_hours": round(on_s / 3600, 2), "cycles": cycles,
                "avg_on_minutes": round(on_s / 60 / cycles, 1) if cycles else None,
                "outside_high": calc.r1(oat_hi), "inside_avg": calc.r1(sum(ret) / len(ret)) if ret else None,
                "cycle_started_at": as_utc(system.run_started_at).isoformat() if system.run_started_at else None}

    @app.get("/api/systems/{system_id}/history")
    def history(minutes: float = Query(60, gt=0, le=60 * 24 * 90), system=Depends(own_system), s=Depends(db)):
        """Averaged into at most ~600 points per series, like the PC dashboard."""
        bucket = max(1, int(minutes * 60 / 600))
        out, acc, cur = [], {}, None
        for t, d in snapshots(s, system, minutes):
            b = int(as_utc(t).timestamp() // bucket)
            if cur is not None and b != cur:
                out.append(_avg(cur * bucket, acc))
                acc = {}
            cur = b
            for k in HISTORY_KEYS:
                v = calc.num(d.get(k))
                if v is not None:
                    acc.setdefault(k, []).append(v)
            acc.setdefault("_on", []).append(1 if d.get("mode") in ("cooling", "heating") else 0)
        if acc:
            out.append(_avg(cur * bucket, acc))
        return out

    @app.get("/api/systems/{system_id}/export.csv", response_class=PlainTextResponse)
    def export_csv(minutes: float = Query(1440, gt=0, le=60 * 24 * 365), system=Depends(own_system),
                   s=Depends(db)):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["time_utc"] + CSV_COLS)
        for t, d in snapshots(s, system, minutes):
            w.writerow([as_utc(t).strftime("%Y-%m-%d %H:%M:%S")] + [d.get(c) for c in CSV_COLS])
        return PlainTextResponse(buf.getvalue(), media_type="text/csv", headers={
            "Content-Disposition": f"attachment; filename={system.site_id}-export.csv"})

    @app.get("/api/systems/{system_id}/alerts")
    def list_alerts(days: float = Query(7, gt=0, le=365), system=Depends(own_system), s=Depends(db)):
        """Raised alerts that were open at any time in the last `days`, newest first (pending ones are left out)."""
        since = utcnow() - dt.timedelta(days=days)
        rows = s.scalars(select(Alert).where(Alert.system_id == system.id, Alert.raised_at.is_not(None))
                         .where((Alert.cleared_at.is_(None)) | (Alert.cleared_at >= since))
                         .order_by(Alert.started_at.desc()).limit(200))
        return [_alert_view(a) for a in rows]

    def check_kind(kind):
        if kind not in service.KINDS:
            raise HTTPException(404, f"kind must be one of {list(service.KINDS)}")

    @app.post("/api/systems/{system_id}/alerts/{alert_id}/ack")
    def ack_alert(alert_id: int, body: AckIn | None = None, system=Depends(tech_system), s=Depends(db)):
        """Mark an alert as being handled (or undo with {"ack": false})."""
        a = s.get(Alert, alert_id)
        if a is None or a.system_id != system.id:
            raise HTTPException(404, "no such alert")
        a.acked_at = utcnow() if (body is None or body.ack) else None
        s.commit()
        return _alert_view(a)

    def alert_settings_view(s, system):
        prefs = s.get(AlertPrefs, system.id)
        acct, info = s.get(Account, system.account_id), s.get(ServiceInfo, system.id)
        now = utcnow()
        mutes = [{"code": m.code, "until": _iso(m.until)} for m in
                 s.scalars(select(AlertMute).where(AlertMute.system_id == system.id).order_by(AlertMute.code))
                 if as_utc(m.until) > now]
        return {"email_owner": True if prefs is None else prefs.email_owner,
                "email_contractor": False if prefs is None else prefs.email_contractor,
                "owner_email": acct.email if acct else None, "contractor_email": info.email if info else None,
                "email_enabled": bool(settings.SMTP_HOST), "mutes": mutes}

    @app.get("/api/systems/{system_id}/alert-settings")
    def get_alert_settings(system=Depends(tech_system), s=Depends(db)):
        """Who gets alert emails, and which alert codes are muted."""
        return alert_settings_view(s, system)

    @app.put("/api/systems/{system_id}/alert-settings")
    def set_alert_settings(body: AlertSettingsIn, system=Depends(tech_system), s=Depends(db)):
        prefs = s.get(AlertPrefs, system.id) or AlertPrefs(system_id=system.id, email_owner=True, email_contractor=False)
        if body.email_owner is not None:
            prefs.email_owner = body.email_owner
        if body.email_contractor is not None:
            prefs.email_contractor = body.email_contractor
        s.add(prefs)
        s.commit()
        return alert_settings_view(s, system)

    @app.put("/api/systems/{system_id}/alert-mutes/{code}")
    def mute_alerts(code: str, body: MuteIn, system=Depends(tech_system), s=Depends(db)):
        """No emails for this alert code for `hours` (0 unmutes). Alerts are still recorded and shown."""
        if not code.replace("_", "").isalnum() or len(code) > 32:
            raise HTTPException(422, "bad alert code")
        m = s.get(AlertMute, (system.id, code))
        if body.hours == 0:
            if m is not None:
                s.delete(m)
        else:
            m = m or AlertMute(system_id=system.id, code=code, until=utcnow())
            m.until = utcnow() + dt.timedelta(hours=body.hours)
            s.add(m)
        s.commit()
        return alert_settings_view(s, system)

    @app.get("/api/systems/{system_id}/service")
    def get_service(system=Depends(own_system), s=Depends(db)):
        """Service contractor and maintenance reminders (air filter, tune-up)."""
        return service.service_view(s, system.id)

    @app.put("/api/systems/{system_id}/service/contractor")
    def set_contractor(body: ContractorIn, system=Depends(tech_system), s=Depends(db)):
        info = s.get(ServiceInfo, system.id) or ServiceInfo(system_id=system.id)
        info.name, info.phone, info.email = [(v or "").strip() or None for v in (body.name, body.phone, body.email)]
        s.add(info)
        s.commit()
        return service.service_view(s, system.id)

    @app.patch("/api/systems/{system_id}/service/items/{kind}")
    def update_item(kind: str, body: ItemIn, system=Depends(tech_system), s=Depends(db)):
        check_kind(kind)
        row = service.get_item(s, system.id, kind)
        for k in body.model_fields_set:
            if k == "interval_days" and body.interval_days is None:
                raise HTTPException(422, "interval_days can't be empty")
            setattr(row, k, getattr(body, k))
        s.commit()
        return service.service_view(s, system.id)

    @app.post("/api/systems/{system_id}/service/items/{kind}/done")
    def item_done(kind: str, body: DoneIn | None = None, system=Depends(own_system), who=Depends(principal),
                  s=Depends(db)):
        check_kind(kind)
        if kind != "filter" and not who.is_tech:
            raise HTTPException(403, "for your contractor only")
        row = service.get_item(s, system.id, kind)
        row.last_done = (body.date if body and body.date else None) or dt.date.today()
        s.commit()
        return service.service_view(s, system.id)

    @app.post("/api/systems/{system_id}/commands")
    def send_command(body: CommandIn, system=Depends(tech_system), s=Depends(db)):
        if not isinstance(body.cmd.get("cmd"), str):
            raise HTTPException(422, 'cmd needs a "cmd" verb, e.g. {"cmd":"cal_zero","ch":"p_liq"}')
        device = s.scalar(select(Device).where(Device.system_id == system.id, Device.node == body.node))
        if device is None:
            raise HTTPException(404, f"{body.node} node has not reported yet")
        cmd = Command(device_id=device.id, payload=body.cmd)
        s.add(cmd)
        s.flush()
        cmd.sent = publish(f"hvac/{system.site_id}/{body.node}/cmd", body.cmd)
        s.commit()
        return _command_view(cmd, body.node)

    @app.get("/api/systems/{system_id}/commands")
    def list_commands(limit: int = Query(20, gt=0, le=200), system=Depends(tech_system), s=Depends(db)):
        rows = s.execute(select(Command, Device.node).join(Device).where(Device.system_id == system.id)
                         .order_by(Command.id.desc()).limit(limit)).all()
        return [_command_view(c, node) for c, node in rows]

    if os.path.isdir(WEB_DIR):
        app.mount("/app", StaticFiles(directory=WEB_DIR, html=True), name="web")

        @app.middleware("http")
        async def revalidate_web_files(request, call_next):
            """Browsers must check for a newer web app on every load instead of reusing old copies."""
            response = await call_next(request)
            if request.url.path.startswith("/app"):
                response.headers["Cache-Control"] = "no-cache"
            return response
    return app


def _avg(t, acc):
    row = {"ts": t}
    for k, vals in acc.items():
        row[k] = round(sum(vals) / len(vals), 1)
    return row


def _iso(t):
    return as_utc(t).isoformat() if t else None


def _alert_view(a):
    return {"id": a.id, "code": a.code, "node": a.node, "level": a.level, "text": a.text,
            "open": a.cleared_at is None, "started_at": _iso(a.started_at), "raised_at": _iso(a.raised_at),
            "cleared_at": _iso(a.cleared_at), "emailed_at": _iso(a.emailed_at), "acked_at": _iso(a.acked_at)}


def _command_view(c, node):
    return {"id": c.id, "node": node, "cmd": c.payload, "sent": c.sent,
            "created_at": as_utc(c.created_at).isoformat() if c.created_at else None,
            "reply": c.reply, "replied_at": as_utc(c.replied_at).isoformat() if c.replied_at else None}


_app = None


def __getattr__(name):
    """`uvicorn hvaccloud.api:app` builds the app on first access, not at import (keeps tests DB-free)."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
