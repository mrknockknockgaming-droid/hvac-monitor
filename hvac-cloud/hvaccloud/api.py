"""REST API for accounts' systems: live state, history, CSV export, settings and node commands.

Every request carries an account API key (create one with manage.py) in the X-API-Key header.

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

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import calc, settings
from .db import (ApiKey, Command, Device, Snapshot, System, Telemetry, as_utc, hash_key, init_db,
                 make_engine, session_factory, utcnow)
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


class CommandIn(BaseModel):
    node: Literal["outdoor", "indoor"]
    cmd: dict


def create_app(sessions=None, publisher=None, stale=settings.STALE_SECONDS):
    if sessions is None:
        engine = make_engine()
        init_db(engine)
        sessions = session_factory(engine)
    publish = publisher or MqttPublisher()
    app = FastAPI(title="HVAC Monitor cloud API", version="0.1.0")

    def db():
        with sessions() as s:
            yield s

    def account_id(x_api_key: str = Header(...), s=Depends(db)):
        key = s.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(x_api_key)))
        if key is None:
            raise HTTPException(401, "invalid API key")
        key.last_used = utcnow()
        s.commit()
        return key.account_id

    def own_system(system_id: int, acct=Depends(account_id), s=Depends(db)):
        system = s.get(System, system_id)
        if system is None or system.account_id != acct:
            raise HTTPException(404, "no such system")
        return system

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

    @app.get("/api/refrigerants")
    def refrigerants():
        return list(FLUIDS)

    @app.get("/api/systems")
    def list_systems(acct=Depends(account_id), s=Depends(db)):
        rows = s.scalars(select(System).where(System.account_id == acct).order_by(System.id))
        return [system_view(x) for x in rows]

    @app.get("/api/systems/{system_id}")
    def get_system(system=Depends(own_system), s=Depends(db)):
        now = utcnow()
        devices = s.scalars(select(Device).where(Device.system_id == system.id).order_by(Device.node))
        return {**system_view(system), "devices": [device_view(d, now) for d in devices]}

    @app.patch("/api/systems/{system_id}")
    def update_system(body: SystemSettings, system=Depends(own_system), s=Depends(db)):
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

    @app.post("/api/systems/{system_id}/commands")
    def send_command(body: CommandIn, system=Depends(own_system), s=Depends(db)):
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
    def list_commands(limit: int = Query(20, gt=0, le=200), system=Depends(own_system), s=Depends(db)):
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
