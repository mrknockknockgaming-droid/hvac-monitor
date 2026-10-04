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
import re
import secrets
import threading
from typing import Literal

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from . import alerts, auth, calc, equipment, service, settings, thermostat
from .db import (Account, Alert, AlertMute, AlertPrefs, ApiKey, Command, Device, Invite, PasswordReset, ServiceInfo, ServiceVisit, Snapshot,
                 System, SystemMember, Telemetry, ThermostatConfig, User, as_utc, hash_key, init_db, make_engine,
                 session_factory, utcnow)
from .refrigerants import FLUIDS

mimetypes.add_type("font/woff", ".woff")   # Windows does not know it; StaticFiles uses mimetypes
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
HISTORY_KEYS = ["p_low", "p_high", "sat_low", "sat_high", "t_suc", "t_liq", "sh", "sc", "oat", "t_sup", "t_ret", "dt"]
INVITE_DAYS = 7
RESET_HOURS = 1                    # a forgotten-password link works this long, once
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LEVEL_RANK = {"fault": 4, "offline": 3, "caution": 2, "advisory": 1, "ok": 0}
CSV_COLS = ["mode", "p_low", "p_high", "sat_low", "sat_high", "t_suc", "t_liq", "sh", "sc",
            "oat", "orh", "t_ret", "t_sup", "dt", "ctoa", "approach", "Y", "W", "G", "OB"]


class MqttPublisher:
    """Publishes commands to hvac/<site>/<node>/cmd (and the thermostat's retained config); connects on first use."""

    def __init__(self, connect_timeout=5.0):
        self._client = None
        self._lock = threading.Lock()
        self._connected = threading.Event()
        self._timeout = connect_timeout

    def __call__(self, topic, payload, retain=False):
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
        info = self._client.publish(topic, json.dumps(payload), qos=1, retain=retain)
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


def past_date(d):
    """A done date: not in the future (a day of slack for time zones) and not absurdly old."""
    if d is not None and not (dt.date(2000, 1, 1) <= d <= dt.date.today() + dt.timedelta(days=1)):
        raise ValueError("the date must be between 2000 and today")
    return d


class ItemIn(BaseModel):
    """Only the fields sent are changed; send interval_run_hours: null to stop counting run hours."""
    interval_days: int | None = Field(default=None, ge=1, le=3650)
    interval_run_hours: float | None = Field(default=None, gt=0, le=20000)
    last_done: dt.date | None = None
    _check = field_validator("last_done")(past_date)


class AckIn(BaseModel):
    ack: bool = True


class AlertSettingsIn(BaseModel):
    email_owner: bool | None = None
    email_contractor: bool | None = None


class MuteIn(BaseModel):
    hours: float = Field(ge=0, le=24 * 90)     # 0 = unmute


class DoneIn(BaseModel):
    date: dt.date | None = None        # the viewer's local date; the server's when left out
    _check = field_validator("date")(past_date)


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


class PasswordIn(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


class EquipmentIn(BaseModel):
    """The whole Equipment page; empty optional fields are sent as null."""
    system_type: Literal["split_hp", "split_ac", "packaged_hp", "packaged_ac"] | None = None
    metering: Literal["txv", "eev", "piston"] | None = None
    tonnage: float | None = Field(default=None, ge=0.5, le=30)
    sc_target: float | None = Field(default=None, ge=0, le=30)
    sc_tolerance: float | None = Field(default=None, ge=0.5, le=10)
    rated_btuh: float | None = Field(default=None, ge=1000, le=500000)
    rated_cfm: float | None = Field(default=None, ge=100, le=20000)
    max_esp: float | None = Field(default=None, gt=0, le=2)
    elevation_ft: float | None = Field(default=None, ge=-1500, le=12000)
    refrigerant: str
    heat_pump: bool
    ob_energized: Literal["cool", "heat"]
    atm_psia: float | None = Field(default=None, ge=10, le=15.5)    # used when no elevation is given


class InviteIn(BaseModel):
    email: str = Field(max_length=320)


class TokenIn(BaseModel):
    token: str = Field(max_length=100)


class ForgotIn(BaseModel):
    email: str = Field(max_length=320)


class ResetIn(TokenIn):
    password: str = Field(max_length=200)


class AcceptIn(TokenIn):
    name: str | None = Field(default=None, max_length=200)
    password: str = Field(max_length=200)


class VisitIn(BaseModel):
    date: dt.date
    kind: Literal["tuneup", "repair", "install", "inspection", "other"]
    technician: str | None = Field(default=None, max_length=200)
    work: str = Field(min_length=1, max_length=2000)
    filter_changed: bool = False
    attach_readings: bool = False
    _check = field_validator("date")(past_date)


class ThermostatIn(BaseModel):
    """Homeowner settings; fields left out are unchanged."""
    mode: Literal[thermostat.MODES] | None = None
    fan: Literal[thermostat.FANS] | None = None
    heat_sp: float | None = None
    cool_sp: float | None = None
    schedule: list[dict] | None = None


class HoldIn(BaseModel):
    heat: float | None = None
    cool: float | None = None
    permanent: bool = False


class CommandIn(BaseModel):
    node: Literal["outdoor", "indoor"]
    cmd: dict


def create_app(sessions=None, publisher=None, stale=settings.STALE_SECONDS, mailer=None, send_async=True):
    if sessions is None:
        engine = make_engine()
        init_db(engine)
        sessions = session_factory(engine)
    publish = publisher or MqttPublisher()
    throttle = auth.Throttle()
    reset_throttle = auth.Throttle(limit=3, window=60 * 60)      # reset emails per address per hour
    mailer = mailer or alerts.Mailer()
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
        stored = user.password_hash if user is not None and user.password_hash else auth.DUMMY_HASH
        if not auth.check_password(body.password, stored) or user is None:     # same work either way
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
    def change_password(body: PasswordIn, who=Depends(principal), s=Depends(db), fs_session: str | None = Cookie(None)):
        user = s.get(User, who.user_id) if who.user_id else None
        if user is None:
            raise HTTPException(400, "API keys have no password")
        if not auth.check_password(body.current, user.password_hash):
            raise HTTPException(401, "The current password is not right.")
        problem = auth.password_problem(body.new)
        if problem:
            raise HTTPException(422, problem)
        user.password_hash = auth.hash_password(body.new)
        auth.end_other_sessions(s, user.id, keep=fs_session)       # anyone else signed in as this user is out
        s.commit()
        return {"ok": True}

    # ---------- forgotten password ----------
    def make_reset(s, user):
        """Revoke the user's older links and return a new one."""
        for old in s.scalars(select(PasswordReset).where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None))):
            s.delete(old)
        token = secrets.token_urlsafe(24)
        s.add(PasswordReset(token_hash=hash_key(token), user_id=user.id,
                            expires_at=utcnow() + dt.timedelta(hours=RESET_HOURS)))
        s.commit()
        return f"{settings.APP_URL}#/reset/{token}"

    @app.post("/api/auth/forgot")
    def forgot_password(body: ForgotIn, s=Depends(db)):
        """Email a reset link. The answer never says whether the email has a sign-in."""
        email = body.email.strip().lower()
        if not mailer.enabled:
            return {"ok": True, "email_enabled": False}
        user = s.scalar(select(User).where(User.email == email))
        if user is not None and not reset_throttle.blocked(email):
            reset_throttle.fail(email)
            url = make_reset(s, user)
            send_invite(email, "Reset your Fullscope password",
                        f"Someone asked to reset the password for {email} on Fullscope.\n\n"
                        f"Choose a new password here (the link works once, for {RESET_HOURS} hour):\n{url}\n\n"
                        f"If it wasn't you, ignore this email; your password stays the same.")
        return {"ok": True, "email_enabled": True}

    @app.post("/api/systems/{system_id}/members/{user_id}/reset-link")
    def member_reset_link(user_id: int, system=Depends(tech_system), s=Depends(db)):
        """A reset link for one of this system's homeowners, to pass on yourself (works without email)."""
        if s.get(SystemMember, (system.id, user_id)) is None:
            raise HTTPException(404, "not a member")
        user = s.get(User, user_id)
        return {"url": make_reset(s, user), "email": user.email, "hours": RESET_HOURS}

    @app.post("/api/auth/reset")
    def reset_password(body: ResetIn, response: Response, s=Depends(db)):
        row = s.get(PasswordReset, hash_key(body.token))
        if row is None or row.used_at is not None or as_utc(row.expires_at) <= utcnow():
            raise HTTPException(410, "This reset link has expired or was already used. Ask for a new one.")
        problem = auth.password_problem(body.password)
        if problem:
            raise HTTPException(422, problem)
        user = s.get(User, row.user_id)
        user.password_hash = auth.hash_password(body.password)
        user.last_login = utcnow()
        row.used_at = utcnow()
        auth.end_other_sessions(s, user.id, keep=None)              # whoever had the old password is out
        token = auth.new_session(s, user.id)
        s.commit()
        throttle.clear(user.email)
        set_cookie(response, token)
        return me_view(s, auth.Principal(role=user.role, account_id=user.account_id, user_id=user.id))

    # ---------- equipment ----------
    @app.get("/api/systems/{system_id}/equipment")
    def get_equipment(system=Depends(tech_system), s=Depends(db)):
        """Equipment details, the system's settings and the targets the diagnostics use."""
        return equipment.view(s.get(equipment.Equipment, system.id), system)

    @app.put("/api/systems/{system_id}/equipment")
    def set_equipment(body: EquipmentIn, system=Depends(tech_system), s=Depends(db)):
        if body.refrigerant not in FLUIDS:
            raise HTTPException(422, f"refrigerant must be one of {list(FLUIDS)}")
        eq = equipment.get_or_new(s, system.id)
        for k in ("system_type", "metering", "tonnage", "sc_target", "rated_btuh", "rated_cfm", "max_esp", "elevation_ft"):
            setattr(eq, k, getattr(body, k))
        eq.sc_tolerance = body.sc_tolerance if body.sc_tolerance is not None else equipment.DEFAULT_SC_TOL
        system.refrigerant, system.heat_pump, system.ob_energized = body.refrigerant, body.heat_pump, body.ob_energized
        if body.elevation_ft is not None:
            system.atm_psia = equipment.atm_from_elevation(body.elevation_ft)
        elif body.atm_psia is not None:
            system.atm_psia = body.atm_psia
        if body.system_type in ("split_ac", "packaged_ac"):
            system.heat_pump = False
        s.commit()
        return equipment.view(eq, system)

    # ---------- display thermostat ----------
    def tstat_row(s, system):
        row = s.get(ThermostatConfig, system.id)
        if row is None:
            row = ThermostatConfig(system_id=system.id, settings={}, tech={}, version=0, sent=False)
            s.add(row)
        return row

    def tstat_view(s, system, row):
        d = s.scalar(select(Device).where(Device.system_id == system.id, Device.node == thermostat.NODE))
        raw = s.scalar(select(Telemetry.data).where(Telemetry.device_id == d.id)
                       .order_by(Telemetry.time.desc()).limit(1)) if d else None
        st = thermostat.merged(row.settings if row else {})
        tech = thermostat.config_payload(0, {}, row.tech if row else {}, system)["tech"]
        heat, cool, source, nxt = thermostat.schedule_now(st, thermostat.local_now(tech))
        return {"present": d is not None, "device": device_view(d, utcnow()) if d else None, "report": raw,
                "settings": st, "tech": tech, "limits": thermostat.TECH_LIMITS,
                "version": row.version if row else 0, "sent": bool(row and row.sent),
                "applied": bool(raw and row and raw.get("cfg_ver") == row.version),
                "updated_at": _iso(row.updated_at) if row else None,
                "now": {"heat": heat, "cool": cool, "source": source, "next_change": nxt}}

    def tstat_save(s, system, row, settings_=None, tech=None):
        """Store a change, bump the version and send the whole config (retained, so a thermostat
        that was offline gets it when it reconnects)."""
        if settings_ is not None:
            errs = thermostat.validate(settings_)
            if errs:
                raise HTTPException(422, "; ".join(errs))
            row.settings = settings_
        if tech is not None:
            errs = thermostat.validate_tech(tech)
            if errs:
                raise HTTPException(422, "; ".join(errs))
            row.tech = tech
        row.version = (row.version or 0) + 1
        row.updated_at = utcnow()
        row.sent = bool(publish(f"hvac/{system.site_id}/{thermostat.NODE}/config",
                                thermostat.config_payload(row.version, row.settings, row.tech, system), True))
        s.commit()
        return tstat_view(s, system, row)

    @app.get("/api/systems/{system_id}/thermostat")
    def get_thermostat(system=Depends(own_system), s=Depends(db)):
        return tstat_view(s, system, s.get(ThermostatConfig, system.id))

    @app.put("/api/systems/{system_id}/thermostat")
    def set_thermostat(body: ThermostatIn, system=Depends(own_system), s=Depends(db)):
        """Mode, fan, setpoints and schedule: the homeowner may change these."""
        row = tstat_row(s, system)
        return tstat_save(s, system, row, settings_={**thermostat.merged(row.settings), **body.model_dump(exclude_none=True)})

    @app.post("/api/systems/{system_id}/thermostat/hold")
    def hold_thermostat(body: HoldIn, system=Depends(own_system), s=Depends(db)):
        """New setpoints until the next scheduled change (or until resumed, if permanent)."""
        row = tstat_row(s, system)
        st = thermostat.merged(row.settings)
        hold = thermostat.hold_until_next(st, thermostat.local_now(row.tech), body.heat, body.cool)
        if body.permanent:
            hold["until"] = None
        if body.heat is not None and body.cool is None:      # pushing one setpoint moves the other along
            hold["cool"] = max(hold["cool"], body.heat + thermostat.AUTO_DEADBAND)
        if body.cool is not None and body.heat is None:
            hold["heat"] = min(hold["heat"], body.cool - thermostat.AUTO_DEADBAND)
        errs = thermostat.validate_hold(hold)
        if errs:
            raise HTTPException(422, "; ".join(errs))
        return tstat_save(s, system, row, settings_={**st, "hold": hold})

    @app.delete("/api/systems/{system_id}/thermostat/hold")
    def resume_schedule(system=Depends(own_system), s=Depends(db)):
        row = tstat_row(s, system)
        return tstat_save(s, system, row, settings_={**thermostat.merged(row.settings), "hold": None})

    @app.put("/api/systems/{system_id}/thermostat/tech")
    def set_thermostat_tech(body: dict, system=Depends(tech_system), s=Depends(db)):
        """Safety settings (compressor protection, aux heat lockouts, time zone): technician only."""
        row = tstat_row(s, system)
        unknown = set(body) - set(thermostat.DEFAULT_TECH)
        if unknown:
            raise HTTPException(422, f"unknown settings: {', '.join(sorted(unknown))}")
        keep = {k: v for k, v in body.items() if k not in ("heat_pump", "ob_energized")}   # Equipment page owns these
        return tstat_save(s, system, row, tech={**(row.tech or {}), **keep})

    # ---------- invites ----------
    def clean_email(raw):
        email = raw.strip().lower()
        if not EMAIL_RE.match(email):
            raise HTTPException(422, "That doesn't look like an email address.")
        return email

    def send_invite(email, subject, body):
        if not mailer.enabled:
            return False
        if send_async:
            threading.Thread(target=mailer.send, args=(email, subject, body), daemon=True).start()
            return True
        return mailer.send(email, subject, body)

    def make_invite(s, who, email, role, account_id=None, system_id=None):
        """New invite (older pending ones for the same person and place are revoked). Returns the link."""
        q = select(Invite).where(Invite.email == email, Invite.accepted_at.is_(None), Invite.role == role)
        q = q.where(Invite.system_id == system_id) if system_id else q.where(Invite.account_id == account_id)
        for old in s.scalars(q):
            s.delete(old)
        token = secrets.token_urlsafe(24)
        inv = Invite(token_hash=hash_key(token), email=email, role=role, account_id=account_id, system_id=system_id,
                     invited_by=who.user_id, expires_at=utcnow() + dt.timedelta(days=INVITE_DAYS))
        s.add(inv)
        s.commit()
        return inv, f"{settings.APP_URL}#/invite/{token}"

    def invite_view(inv):
        return {"id": inv.id, "email": inv.email, "role": inv.role, "expires_at": _iso(inv.expires_at),
                "expired": as_utc(inv.expires_at) <= utcnow()}

    @app.post("/api/systems/{system_id}/invites")
    def invite_homeowner(body: InviteIn, system=Depends(tech_system), who=Depends(principal), s=Depends(db)):
        """Give a homeowner access: an existing homeowner sign-in is added at once, anyone else gets a link."""
        email = clean_email(body.email)
        user = s.scalar(select(User).where(User.email == email))
        if user is not None:
            if user.role != "homeowner":
                raise HTTPException(409, "That email already has a contractor sign-in.")
            if s.get(SystemMember, (system.id, user.id)) is None:
                s.add(SystemMember(system_id=system.id, user_id=user.id))
                s.commit()
            return {"status": "added", "email": email}
        inv, url = make_invite(s, who, email, "homeowner", system_id=system.id)
        acct = s.get(Account, system.account_id)
        emailed = send_invite(email, f"Your Fullscope access for {system.name}",
                              f"{acct.name if acct else 'Your contractor'} has given you access to {system.name} on Fullscope, "
                              f"which watches how your heating and cooling are running.\n\n"
                              f"Choose a password here (the link works for {INVITE_DAYS} days):\n{url}")
        return {"status": "invited", "email": email, "url": url, "emailed": emailed, "expires_at": _iso(inv.expires_at)}

    @app.get("/api/systems/{system_id}/people")
    def system_people(system=Depends(tech_system), s=Depends(db)):
        """Homeowners with access to this system, and pending invites."""
        members = s.scalars(select(User).join(SystemMember, SystemMember.user_id == User.id)
                            .where(SystemMember.system_id == system.id).order_by(User.email))
        invites = s.scalars(select(Invite).where(Invite.system_id == system.id, Invite.accepted_at.is_(None))
                            .order_by(Invite.created_at.desc()))
        return {"members": [{"user_id": u.id, "email": u.email, "name": u.name, "last_login": _iso(u.last_login)} for u in members],
                "invites": [invite_view(i) for i in invites]}

    @app.delete("/api/systems/{system_id}/members/{user_id}")
    def remove_member(user_id: int, system=Depends(tech_system), s=Depends(db)):
        m = s.get(SystemMember, (system.id, user_id))
        if m is None:
            raise HTTPException(404, "not a member")
        s.delete(m)
        s.commit()
        return {"ok": True}

    @app.post("/api/account/invites")
    def invite_teammate(body: InviteIn, who=Depends(principal), s=Depends(db)):
        """Invite a contractor colleague to this account."""
        if not who.is_tech:
            raise HTTPException(403, "for your contractor only")
        email = clean_email(body.email)
        if s.scalar(select(User).where(User.email == email)):
            raise HTTPException(409, "That email already has a sign-in.")
        inv, url = make_invite(s, who, email, "contractor", account_id=who.account_id)
        acct = s.get(Account, who.account_id)
        emailed = send_invite(email, f"Join {acct.name if acct else 'your team'} on Fullscope",
                              f"You've been invited to {acct.name if acct else 'a contractor account'} on Fullscope.\n\n"
                              f"Choose a password here (the link works for {INVITE_DAYS} days):\n{url}")
        return {"status": "invited", "email": email, "url": url, "emailed": emailed, "expires_at": _iso(inv.expires_at)}

    @app.get("/api/account/people")
    def account_people(who=Depends(principal), s=Depends(db)):
        if not who.is_tech:
            raise HTTPException(403, "for your contractor only")
        users = s.scalars(select(User).where(User.account_id == who.account_id).order_by(User.email))
        invites = s.scalars(select(Invite).where(Invite.account_id == who.account_id, Invite.role == "contractor",
                                                 Invite.accepted_at.is_(None)).order_by(Invite.created_at.desc()))
        return {"users": [{"user_id": u.id, "email": u.email, "name": u.name, "last_login": _iso(u.last_login)} for u in users],
                "invites": [invite_view(i) for i in invites]}

    @app.delete("/api/account/users/{user_id}")
    def remove_teammate(user_id: int, who=Depends(principal), s=Depends(db)):
        """Take a colleague off the account: their sign-in is deleted and they are signed out."""
        user = s.get(User, user_id)
        if not who.is_tech or user is None or user.role != "contractor" or user.account_id != who.account_id:
            raise HTTPException(404, "no such teammate")
        if user.id == who.user_id:
            raise HTTPException(409, "You can't remove yourself.")
        auth.end_other_sessions(s, user.id, keep=None)
        for inv in s.scalars(select(Invite).where(Invite.invited_by == user.id)):
            inv.invited_by = None
        for r in s.scalars(select(PasswordReset).where(PasswordReset.user_id == user.id)):
            s.delete(r)
        for v in s.scalars(select(ServiceVisit).where(ServiceVisit.created_by == user.id)):
            v.created_by = None
        s.delete(user)
        s.commit()
        return {"ok": True}

    @app.delete("/api/invites/{invite_id}")
    def revoke_invite(invite_id: int, who=Depends(principal), s=Depends(db)):
        inv = s.get(Invite, invite_id)
        owner = None
        if inv is not None:
            owner = inv.account_id if inv.system_id is None else s.get(System, inv.system_id).account_id
        if inv is None or not who.is_tech or owner != who.account_id:
            raise HTTPException(404, "no such invite")
        s.delete(inv)
        s.commit()
        return {"ok": True}

    def open_invite(s, token):
        inv = s.scalar(select(Invite).where(Invite.token_hash == hash_key(token)))
        if inv is None or inv.accepted_at is not None:
            raise HTTPException(404, "This invite link isn't valid any more. Ask for a new one.")
        if as_utc(inv.expires_at) <= utcnow():
            raise HTTPException(410, "This invite link has expired. Ask for a new one.")
        return inv

    @app.post("/api/invites/lookup")
    def invite_info(body: TokenIn, s=Depends(db)):
        """What an invite link is for (no sign-in needed: the link itself is the secret, so it is
        sent in the body, where access logs don't record it)."""
        inv = open_invite(s, body.token)
        system = s.get(System, inv.system_id) if inv.system_id else None
        acct = s.get(Account, system.account_id if system else inv.account_id)
        return {"email": inv.email, "role": inv.role, "system": system.name if system else None,
                "account": acct.name if acct else None, "expires_at": _iso(inv.expires_at),
                "has_user": s.scalar(select(User.id).where(User.email == inv.email)) is not None}

    @app.post("/api/invites/accept")
    def accept_invite(body: AcceptIn, response: Response, s=Depends(db)):
        inv = open_invite(s, body.token)
        if s.scalar(select(User).where(User.email == inv.email)):
            raise HTTPException(409, "This email already has a sign-in. Sign in instead.")
        problem = auth.password_problem(body.password)
        if problem:
            raise HTTPException(422, problem)
        account_id = inv.account_id if inv.role == "contractor" else None
        user = User(email=inv.email, name=(body.name or "").strip() or None, role=inv.role, account_id=account_id,
                    password_hash=auth.hash_password(body.password), last_login=utcnow())
        s.add(user)
        s.flush()
        if inv.system_id:
            s.add(SystemMember(system_id=inv.system_id, user_id=user.id))
        inv.accepted_at = utcnow()
        token_cookie = auth.new_session(s, user.id)
        s.commit()
        set_cookie(response, token_cookie)
        return me_view(s, auth.Principal(role=user.role, account_id=user.account_id, user_id=user.id))

    # ---------- contractor fleet ----------
    @app.get("/api/fleet")
    def fleet(who=Depends(principal), s=Depends(db)):
        """Every system the contractor services, most in need of attention first."""
        if not who.is_tech:
            raise HTTPException(403, "for your contractor only")
        now, today = utcnow(), dt.date.today()
        out = []
        for system in s.scalars(visible_systems(s, who).order_by(System.name)):
            row = s.execute(select(Snapshot.time, Snapshot.data).where(Snapshot.system_id == system.id)
                            .order_by(Snapshot.time.desc()).limit(1)).first()
            data = (row.data or {}) if row else {}
            age = (now - as_utc(row.time)).total_seconds() if row else None
            fresh = age is not None and age <= stale
            devices = list(s.scalars(select(Device).where(Device.system_id == system.id)))
            online = sum(1 for d in devices if d.connected and d.last_seen and (now - as_utc(d.last_seen)).total_seconds() <= stale)
            open_alerts = list(s.scalars(select(Alert).where(Alert.system_id == system.id, Alert.raised_at.is_not(None),
                                                             Alert.cleared_at.is_(None)).order_by(Alert.started_at)))
            flags = data.get("flags") or [] if fresh else []
            level = "ok"
            for code in [f.get("code") for f in flags] + [a.code for a in open_alerts if a.acked_at is None]:
                lv = alerts.LEVEL.get(code, "caution")
                if LEVEL_RANK[lv] > LEVEL_RANK[level]:
                    level = lv
            if not fresh and LEVEL_RANK[level] < LEVEL_RANK["offline"]:
                level = "offline"
            maint = service.service_view(s, system.id, today=today)["items"]
            members = s.scalar(select(func.count()).select_from(SystemMember).where(SystemMember.system_id == system.id))
            out.append({"id": system.id, "name": system.name, "site_id": system.site_id, "level": level,
                        "mode": data.get("mode") if fresh else None, "last_seen": _iso(row.time) if row else None,
                        "nodes": {"seen": len(devices), "online": online},
                        "issues": [f.get("text") for f in flags],
                        "open_alerts": [{"id": a.id, "code": a.code, "text": a.text, "since": _iso(a.started_at),
                                         "acked": a.acked_at is not None} for a in open_alerts],
                        "maintenance": {m["kind"]: {"status": m["status"], "next_due": m["next_due"]} for m in maint},
                        "homeowners": members})
        due = lambda r: any(m["status"] == "due" for m in r["maintenance"].values())
        out.sort(key=lambda r: (-LEVEL_RANK[r["level"]], not due(r), r["name"].lower()))
        return out

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
        for n in calc.NODES + (thermostat.NODE,):
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
    def history(minutes: float = Query(60, gt=0, le=60 * 24 * 31), system=Depends(own_system), s=Depends(db)):
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
        owners_on, contractor_on = alerts.wants(s, system.id)
        now = utcnow()
        mutes = [{"code": m.code, "until": _iso(m.until)} for m in
                 s.scalars(select(AlertMute).where(AlertMute.system_id == system.id).order_by(AlertMute.code))
                 if as_utc(m.until) > now]
        return {"email_owner": owners_on, "email_contractor": contractor_on,
                "homeowner_emails": alerts.homeowner_emails(s, system.id),
                "contractor_email": alerts.contractor_email(s, system),
                "email_enabled": bool(settings.SMTP_HOST), "mutes": mutes}

    @app.get("/api/systems/{system_id}/alert-settings")
    def get_alert_settings(system=Depends(tech_system), s=Depends(db)):
        """Who gets alert emails, and which alert codes are muted."""
        return alert_settings_view(s, system)

    @app.put("/api/systems/{system_id}/alert-settings")
    def set_alert_settings(body: AlertSettingsIn, system=Depends(tech_system), s=Depends(db)):
        prefs = s.get(AlertPrefs, system.id) or AlertPrefs(system_id=system.id, email_owner=True, email_contractor=True)
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

    # ---------- service history ----------
    @app.get("/api/systems/{system_id}/visits")
    def list_visits(limit: int = Query(100, gt=0, le=500), system=Depends(own_system), s=Depends(db)):
        """Logged service visits, newest first (homeowners see them too)."""
        rows = s.scalars(select(ServiceVisit).where(ServiceVisit.system_id == system.id)
                         .order_by(ServiceVisit.date.desc(), ServiceVisit.id.desc()).limit(limit))
        return [service.visit_view(v) for v in rows]

    @app.post("/api/systems/{system_id}/visits")
    def add_visit(body: VisitIn, system=Depends(tech_system), who=Depends(principal), s=Depends(db)):
        """Log a visit. A tune-up resets the tune-up reminder, a changed filter the filter reminder;
        attach_readings stores the newest snapshot's main values if it is under 15 minutes old."""
        readings = None
        if body.attach_readings:
            row = s.execute(select(Snapshot.time, Snapshot.data).where(Snapshot.system_id == system.id)
                            .order_by(Snapshot.time.desc()).limit(1)).first()
            if row is None or (utcnow() - as_utc(row.time)).total_seconds() > service.READINGS_MAX_AGE_S:
                raise HTTPException(409, "No readings from the last 15 minutes to attach.")
            readings = {k: (row.data or {}).get(k) for k in service.READING_KEYS}
            readings["time"] = _iso(row.time)
        v = ServiceVisit(system_id=system.id, date=body.date, kind=body.kind, technician=(body.technician or "").strip() or None,
                         work=body.work.strip(), filter_changed=body.filter_changed, readings=readings, created_by=who.user_id)
        s.add(v)
        if body.kind == "tuneup":
            service.mark_done(s, system.id, "tuneup", body.date)
        if body.filter_changed:
            service.mark_done(s, system.id, "filter", body.date)
        s.commit()
        return service.visit_view(v)

    @app.delete("/api/systems/{system_id}/visits/{visit_id}")
    def delete_visit(visit_id: int, system=Depends(tech_system), s=Depends(db)):
        """Remove a visit logged by mistake (reminder dates it moved are left as they are)."""
        v = s.get(ServiceVisit, visit_id)
        if v is None or v.system_id != system.id:
            raise HTTPException(404, "no such visit")
        s.delete(v)
        s.commit()
        return {"ok": True}

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
