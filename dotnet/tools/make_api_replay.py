"""Generate tests/Fullscope.Cloud.Tests/vectors/api_seed.db.gz and api_replay.json: about 200 requests
to the Python web API (hvac-cloud/hvaccloud/api.py) covering every endpoint, sign-in, invites,
password resets, roles and error answers, on a database filled by four hours of the Python ingest
(the same traffic as make_ingest_replay.py).

The clock is frozen and moved forward step by step, so times in the answers are reproducible.
replay.json lists every request with the answer's status and body, cookies set or cleared, emails
sent and MQTT messages published. The C# test sends the same requests to its API on a copy of the
seed (with the clock set to each step's time) and must give the same answers. Invite and reset
links carry random tokens: both sides number them in order of appearance (<tok1>, <tok2>, ...) and
later requests use them by that name.

Run from the dotnet folder (needs fastapi, httpx and freezegun):

    ..\\hvac-cloud\\.venv\\Scripts\\python.exe tools\\make_api_replay.py
"""
import datetime as dt
import gzip
import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_ingest_replay as replay  # noqa: E402  (sets TZ=UTC and the import path)

from fastapi.testclient import TestClient  # noqa: E402
from freezegun import freeze_time  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402

from hvaccloud import alerts, auth, settings  # noqa: E402
from hvaccloud.api import create_app  # noqa: E402
from hvaccloud.db import Account, Alert, ApiKey, System, User, hash_key, session_factory  # noqa: E402
from hvaccloud.ingest import Ingest  # noqa: E402
from hvaccloud.refrigerants import Tables  # noqa: E402

OUT = replay.OUT
PW = "correct horse battery"
KEY1 = "hvk_account-one-test-key-0000000000000000000"
KEY2 = "hvk_account-two-test-key-0000000000000000000"
APP_URL = "http://localhost:8000/app/"
ORDER_EMAIL = "sales@fullscope.example"          # gets a copy of each new order and Office inbox ticket
XRW = {"X-Requested-With": "fullscope"}
TOKEN = re.compile(r"#/(invite|reset|claim)/([A-Za-z0-9_-]+)")


def make_seed(path):
    replay.seed(path)
    sessions = session_factory(create_engine("sqlite:///" + path))
    i = Ingest(sessions, Tables(lambda *a: None), mailer=replay.Mailer([]))
    i.spawn = lambda fn, *a: fn(*a)
    i.publish = lambda topic, payload: None
    for t, topic, payload in replay.script(random.Random(20261015)):
        if topic == "sweep":
            i.sweep(now=t)
        else:
            i.handle(topic, (payload if isinstance(payload, str) else json.dumps(payload)).encode(), now=t)
    with sessions() as s, s.begin():
        a2 = Account(name="Other Co", email="office@other.example")
        s.add(a2)
        s.flush()
        s.add(System(account_id=a2.id, name="Other", site_id="other"))
        for key, acct in ((KEY1, 1), (KEY2, a2.id)):
            s.add(ApiKey(account_id=acct, prefix=key[:12], key_hash=hash_key(key)))
        s.add(User(email="tech@desertair.example", name="Tech", role="contractor", account_id=1, admin=True, staff=True,
                   password_hash=auth.hash_password(PW)))
        s.add(User(email="boss@other.example", role="contractor", account_id=a2.id, admin=True,
                   password_hash=auth.hash_password(PW)))
        owner = s.scalar(select(User).where(User.email == "owner@home.example"))
        owner.password_hash = auth.hash_password(PW)
    with sessions() as s, s.begin():                     # the API only reads each node's newest raw message
        s.execute(text("DELETE FROM telemetry WHERE time < :cut"),
                  {"cut": dt.datetime.fromtimestamp(replay.T0 + replay.HOURS * 3600 - 300, dt.timezone.utc).replace(tzinfo=None)})
    with sessions() as s:
        s.execute(text("VACUUM"))
    sessions.kw["bind"].dispose()


class Recorder:
    def __init__(self):
        self.emails, self.published = [], []

    enabled = True

    def send(self, to, subject, body):
        self.emails.append({"to": to, "subject": subject, "body": body})
        return True

    def publish(self, topic, payload, retain=False):
        self.published.append({"topic": topic, "payload": payload, "retain": retain})
        return True


def main():
    seed = os.path.join(OUT, "api_seed.db")
    make_seed(seed)
    work = os.path.join(OUT, "api_work.db")
    with open(seed, "rb") as a, open(work, "wb") as b:
        b.write(a.read())
    engine = create_engine("sqlite:///" + work)
    sessions = session_factory(engine)
    settings.APP_URL = APP_URL
    settings.ORDER_EMAIL = ORDER_EMAIL
    alerts.settings.APP_URL = APP_URL
    rec = Recorder()
    app = create_app(sessions, publisher=rec.publish, stale=30, mailer=rec, send_async=False)
    clients = {}
    tokens = {}
    steps = []
    t_end = replay.T0 + replay.HOURS * 3600
    clock = {"t": t_end + 5.25}

    with sessions() as s:
        alert_ids = {sid: [a.id for a in s.scalars(select(Alert).where(Alert.system_id == sid, Alert.raised_at.is_not(None))
                                                   .order_by(Alert.id))] for sid in (1, 2, 3)}

    def norm(text_):
        def sub(m):
            tok = m.group(2)
            if tok not in tokens:
                tokens[tok] = f"tok{len(tokens) + 1}"
            return f"#/{m.group(1)}/<{tokens[tok]}>"
        return TOKEN.sub(sub, text_)

    def tok(n=-1):
        """The placeholder of a token seen so far (-1: the newest)."""
        return "{" + list(tokens.values())[n] + "}"

    def fill(s):
        names = {v: k for k, v in tokens.items()}
        return re.sub(r"\{(tok\d+)\}", lambda m: names[m.group(1)], s)

    def call(actor, method, path, body=None, raw=None, headers=None, advance=3.5, compare="exact"):
        clock["t"] += advance
        with freeze_time(dt.datetime.fromtimestamp(clock["t"], dt.timezone.utc)):
            c = clients.setdefault(actor, TestClient(app, base_url="http://testserver"))
            h = dict(headers if headers is not None else (XRW if actor not in ("anon",) else {}))
            if actor == "key1":
                h["X-API-Key"] = KEY1
            if actor == "key2":
                h["X-API-Key"] = KEY2
            e0, p0 = len(rec.emails), len(rec.published)
            kw = {"headers": h}
            if raw is not None:
                kw["content"] = fill(raw).encode()
            elif body is not None:
                kw["content"] = fill(json.dumps(body)).encode()
                h["Content-Type"] = "application/json"
            r = c.request(method, fill(path), **kw)
        ctype = r.headers.get("content-type", "")
        if ctype.startswith("application/json"):
            answer = norm(json.dumps(r.json(), sort_keys=True))
        else:
            answer = norm(r.text)
        cookie = None
        sc = r.headers.get("set-cookie")
        if sc and sc.startswith(auth.COOKIE + "="):
            cookie = "cleared" if "Max-Age=0" in sc or 'fs_session=""' in sc else "set"
        steps.append({"t": clock["t"], "actor": actor, "method": method, "path": path, "headers": headers,
                      "body": None if body is None else json.dumps(body), "raw": raw,
                      "status": r.status_code, "type": ctype.split(";")[0], "answer": answer, "compare": compare,
                      "cookie": cookie,
                      "emails": [json.loads(norm(json.dumps(e))) for e in rec.emails[e0:]],
                      "published": [{"topic": p["topic"], "payload": json.dumps(p["payload"], sort_keys=True), "retain": p["retain"]}
                                    for p in rec.published[p0:]]})
        return r

    login = lambda actor, email, pw=PW: call(actor, "POST", "/api/auth/login", {"email": email, "password": pw})

    # ---- not signed in ----
    call("anon", "GET", "/health")
    call("anon", "GET", "/api/refrigerants")
    call("anon", "GET", "/api/systems")
    call("anon", "GET", "/api/systems", headers={"X-API-Key": "hvk_nope"})
    call("anon", "GET", "/api/auth/me")
    call("anon", "GET", "/api/nothing-here")
    call("anon", "DELETE", "/health")
    login("anon", "tech@desertair.example", "wrong password!")
    login("anon", "nobody@example.com")
    call("anon", "POST", "/api/auth/login", {"email": "tech@desertair.example"})
    call("anon", "POST", "/api/auth/login", raw='{"email": ')
    call("anon", "POST", "/api/auth/login", {"email": "x" * 400, "password": "p"})
    call("anon", "POST", "/api/auth/login", [1, 2])

    # ---- API keys ----
    call("key1", "GET", "/api/systems")
    call("key1", "GET", "/api/auth/me")
    call("key1", "POST", "/api/auth/password", {"current": "a", "new": "b"})
    call("key2", "GET", "/api/systems")
    call("key2", "GET", "/api/systems/1")
    call("key2", "GET", "/api/systems/4")
    call("key1", "GET", "/api/systems/4/latest")

    # ---- contractor ----
    login("tech", "Tech@DesertAir.example ")
    call("tech", "GET", "/api/auth/me")
    call("tech", "GET", "/api/systems")
    call("tech", "GET", "/api/fleet")
    for sid in (1, 2, 3):
        call("tech", "GET", f"/api/systems/{sid}")
        call("tech", "GET", f"/api/systems/{sid}/latest")
        call("tech", "GET", f"/api/systems/{sid}/summary")
        call("tech", "GET", f"/api/systems/{sid}/summary?hours=2.5")
        call("tech", "GET", f"/api/systems/{sid}/history?minutes=15")
        call("tech", "GET", f"/api/systems/{sid}/history")
        call("tech", "GET", f"/api/systems/{sid}/history?minutes=360")
        call("tech", "GET", f"/api/systems/{sid}/history?minutes=1440", compare="status")    # C# reads the minute rollup
        call("tech", "GET", f"/api/systems/{sid}/alerts")
        call("tech", "GET", f"/api/systems/{sid}/alerts?days=0.05")
        call("tech", "GET", f"/api/systems/{sid}/thermostat")
        call("tech", "GET", f"/api/systems/{sid}/thermostat/display")
        call("tech", "GET", f"/api/systems/{sid}/equipment")
        call("tech", "GET", f"/api/systems/{sid}/alert-settings")
        call("tech", "GET", f"/api/systems/{sid}/service")
        call("tech", "GET", f"/api/systems/{sid}/people")
        call("tech", "GET", f"/api/systems/{sid}/commands?limit=2")
    call("tech", "GET", "/api/systems/1/summary?hours=24")
    call("tech", "GET", "/api/systems/1/summary?hours=0")
    call("tech", "GET", "/api/systems/1/summary?hours=abc")
    call("tech", "GET", "/api/systems/1/summary?hours=745")
    call("tech", "GET", "/api/systems/1/history?minutes=7")
    call("tech", "GET", "/api/systems/1/export.csv?minutes=20")
    call("tech", "GET", "/api/systems/2/export.csv?minutes=3")
    call("tech", "GET", "/api/systems/99")
    call("tech", "GET", "/api/systems/abc")
    call("tech", "GET", "/api/systems/4")

    # alerts
    a1 = alert_ids[1][-1]
    call("tech", "POST", f"/api/systems/1/alerts/{a1}/ack")
    call("tech", "POST", f"/api/systems/1/alerts/{a1}/ack", {"ack": False})
    call("tech", "POST", f"/api/systems/1/alerts/{a1}/ack", {})
    call("tech", "POST", f"/api/systems/2/alerts/{a1}/ack")
    call("tech", "POST", "/api/systems/1/alerts/99999/ack")
    call("tech", "POST", "/api/systems/1/alerts/x/ack")
    call("tech", "POST", f"/api/systems/1/alerts/{a1}/ack", headers={})
    call("tech", "PUT", "/api/systems/1/alert-settings", {"email_owner": False})
    call("tech", "PUT", "/api/systems/2/alert-settings", {"email_contractor": True})
    call("tech", "PUT", "/api/systems/1/alert-mutes/sh_low", {"hours": 2})
    call("tech", "PUT", "/api/systems/1/alert-mutes/sh_low", {"hours": 1.5})
    call("tech", "PUT", "/api/systems/1/alert-mutes/cap_fan", {"hours": 0})
    call("tech", "PUT", "/api/systems/1/alert-mutes/a-b", {"hours": 1})
    call("tech", "PUT", "/api/systems/1/alert-mutes/sh_low", {"hours": -1})
    call("tech", "PUT", "/api/systems/1/alert-mutes/sh_low", {})
    call("tech", "GET", "/api/systems/1/alerts")

    # equipment and system settings
    eq = {"system_type": "split_hp", "metering": "txv", "tonnage": 3, "sc_target": 11, "sc_tolerance": None,
          "rated_btuh": 36000, "rated_cfm": 1200, "max_esp": 0.5, "elevation_ft": 1240, "comp_rla": 14.1, "comp_lra": 73,
          "fan_fla": 1.4, "cap_herm_uf": 45, "cap_fan_uf": 5, "volt_min": 208, "volt_max": 253,
          "refrigerant": "R-410A", "heat_pump": True, "ob_energized": "cool", "atm_psia": None}
    call("tech", "PUT", "/api/systems/1/equipment", eq)
    call("tech", "PUT", "/api/systems/2/equipment", {**eq, "system_type": "split_ac", "elevation_ft": None, "atm_psia": 14.2,
                                                     "metering": "piston", "sc_target": None})
    call("tech", "PUT", "/api/systems/1/equipment", {**eq, "refrigerant": "R-999"})
    call("tech", "PUT", "/api/systems/1/equipment", {**eq, "volt_min": 260})
    call("tech", "PUT", "/api/systems/1/equipment", {**eq, "tonnage": 50})
    call("tech", "PUT", "/api/systems/1/equipment", {k: v for k, v in eq.items() if k != "heat_pump"})
    call("tech", "GET", "/api/systems/1/equipment")
    call("tech", "PATCH", "/api/systems/3", {"atm_psia": 13.9, "heat_pump": True})
    call("tech", "PATCH", "/api/systems/3", {"refrigerant": "R-999"})
    call("tech", "PATCH", "/api/systems/3", {"ob_energized": "x"})
    call("tech", "PATCH", "/api/systems/3", {"refrigerant": "R-22", "atm_psia": None})

    # thermostat
    call("tech", "PUT", "/api/systems/1/thermostat", {"mode": "heat", "heat_sp": 70})
    call("tech", "PUT", "/api/systems/1/thermostat", {"heat_sp": 120})
    call("tech", "PUT", "/api/systems/1/thermostat", {"mode": "sideways"})
    call("tech", "PUT", "/api/systems/1/thermostat", {"schedule": [{"days": [5, 6], "at": "08:00", "heat": 66, "cool": 78}]})
    call("tech", "POST", "/api/systems/1/thermostat/hold", {"heat": 71})
    call("tech", "POST", "/api/systems/1/thermostat/hold", {"cool": 74, "permanent": True})
    call("tech", "POST", "/api/systems/1/thermostat/hold", {"heat": 80, "cool": 70})
    call("tech", "DELETE", "/api/systems/1/thermostat/hold")
    call("tech", "PUT", "/api/systems/1/thermostat/tech", {"min_on_s": 300, "heat_pump": False})
    call("tech", "PUT", "/api/systems/1/thermostat/tech", {"bogus": 1, "also": 2})
    call("tech", "PUT", "/api/systems/1/thermostat/tech", {"min_on_s": 5})
    call("tech", "PUT", "/api/systems/2/thermostat", {"mode": "cool", "cool_sp": 76.5})
    call("tech", "GET", "/api/systems/2/thermostat")

    # commands
    call("tech", "POST", "/api/systems/1/commands", {"node": "outdoor", "cmd": {"cmd": "status"}})
    call("tech", "POST", "/api/systems/1/commands", {"node": "indoor", "cmd": {"cmd": "cal_ice", "ch": "t_ret"}})
    call("tech", "POST", "/api/systems/1/commands", {"node": "outdoor", "cmd": {"ch": "p_liq"}})
    call("tech", "POST", "/api/systems/1/commands", {"node": "thermostat", "cmd": {"cmd": "status"}})
    call("tech", "POST", "/api/systems/4/commands", {"node": "outdoor", "cmd": {"cmd": "status"}})
    call("tech", "GET", "/api/systems/1/commands")

    # service
    call("tech", "PUT", "/api/systems/1/service/contractor", {"name": " Desert Air ", "phone": "", "email": "svc@desertair.example"})
    call("tech", "PUT", "/api/systems/2/service/contractor", {"name": "Plain HVAC"})
    call("tech", "PATCH", "/api/systems/1/service/items/filter", {"interval_days": 60, "last_done": "2026-09-01"})
    call("tech", "PATCH", "/api/systems/1/service/items/tuneup", {"interval_run_hours": 250.5})
    call("tech", "PATCH", "/api/systems/1/service/items/filter", {"interval_days": None})
    call("tech", "PATCH", "/api/systems/1/service/items/filter", {"last_done": "2031-01-01"})
    call("tech", "PATCH", "/api/systems/1/service/items/bogus", {"interval_days": 5})
    call("tech", "POST", "/api/systems/1/service/items/tuneup/done", {"date": "2026-10-01"})
    call("tech", "POST", "/api/systems/3/service/items/filter/done")
    call("tech", "POST", "/api/systems/2/service/items/filter/done", {"date": None})
    call("tech", "POST", "/api/systems/1/visits", {"date": "2026-10-05", "kind": "tuneup", "work": " Checked charge ",
                                                    "technician": " Sam ", "filter_changed": True, "attach_readings": True})
    call("tech", "POST", "/api/systems/2/visits", {"date": "2026-10-04", "kind": "repair", "work": "Capacitor"})
    call("tech", "POST", "/api/systems/2/visits", {"date": "2026-10-04", "kind": "repair", "work": ""})
    call("tech", "POST", "/api/systems/2/visits", {"date": "2026-10-04", "kind": "nap", "work": "x"})
    call("tech", "POST", "/api/systems/2/visits", {"date": "1999-12-31", "kind": "repair", "work": "x"})
    call("tech", "GET", "/api/systems/1/visits")
    call("tech", "GET", "/api/systems/2/visits?limit=1")
    call("tech", "GET", "/api/systems/1/threshold-report?days=1")
    call("tech", "GET", "/api/systems/2/threshold-report")
    call("tech", "GET", "/api/systems/3/threshold-report?days=0.1")
    call("tech", "GET", "/api/systems/1/threshold-report?days=0")
    call("tech", "GET", "/api/systems/1/threshold-report?days=40")
    gauges = {"p_low": 118, "p_high": 361.5, "t_suc": 52.4, "t_liq": 99.0, "t_ret": 76.2, "t_sup": 57.0}
    call("tech", "POST", "/api/systems/1/gauge-checks", gauges)                                  # newest snapshot is minutes old
    call("tech", "POST", "/api/systems/1/gauge-checks", {**gauges, "minutes_ago": 9.5, "technician": " Sam ", "note": "SMAN 460"})
    call("tech", "POST", "/api/systems/1/gauge-checks", {"t_ret": 75, "t_sup": 58.5, "minutes_ago": 10.25})
    call("tech", "POST", "/api/systems/2/gauge-checks", {"p_low": 130.25, "t_suc": 60, "minutes_ago": 10})
    call("tech", "POST", "/api/systems/1/gauge-checks", {"note": "no numbers", "minutes_ago": 9.5})
    call("tech", "POST", "/api/systems/1/gauge-checks", {"p_low": 900, "minutes_ago": 9.5})
    call("tech", "POST", "/api/systems/1/gauge-checks", {"p_low": 120, "minutes_ago": 61})
    call("tech", "POST", "/api/systems/1/gauge-checks", {"p_low": 120, "minutes_ago": None})
    call("tech", "POST", "/api/systems/1/gauge-checks", {"p_low": 120, "minutes_ago": 300})
    call("tech", "GET", "/api/systems/1/gauge-checks")
    call("tech", "GET", "/api/systems/1/gauge-checks?limit=1")
    call("tech", "GET", "/api/systems/2/gauge-checks")
    call("tech", "DELETE", "/api/systems/1/gauge-checks/3")                                  # system 2's check
    call("tech", "DELETE", "/api/systems/2/gauge-checks/3")
    call("tech", "GET", "/api/systems/2/gauge-checks")
    call("tech", "GET", "/api/fleet")

    # ---- homeowner invites, reset links, accepting ----
    call("tech", "POST", "/api/systems/1/invites", {"email": " New@Home.example "})
    new_tok = tok()
    call("tech", "POST", "/api/systems/1/invites", {"email": "new@home.example"})       # replaces the first link
    call("tech", "POST", "/api/systems/2/invites", {"email": "owner@home.example"})
    call("tech", "POST", "/api/systems/2/invites", {"email": "tech@desertair.example"})
    call("tech", "POST", "/api/systems/2/invites", {"email": "not-an-email"})
    call("tech", "GET", "/api/systems/1/people")
    call("anon", "POST", "/api/invites/lookup", {"token": new_tok})
    call("anon", "POST", "/api/invites/lookup", {"token": tok()})
    call("newbie", "POST", "/api/invites/accept", {"token": tok(), "name": " Newbie ", "password": "short"})
    call("newbie", "POST", "/api/invites/accept", {"token": tok(), "name": " Newbie ", "password": PW + "!"})
    call("newbie", "GET", "/api/auth/me")
    call("newbie", "GET", "/api/systems")
    call("anon", "POST", "/api/invites/accept", {"token": tok(), "password": PW})
    call("tech", "POST", "/api/systems/1/members/1/reset-link")
    call("tech", "POST", "/api/systems/2/members/2/reset-link")
    call("anon", "POST", "/api/auth/reset", {"token": tok(), "password": "too short"})
    call("owner", "POST", "/api/auth/reset", {"token": tok(), "password": "a brand new password"})
    call("owner", "POST", "/api/auth/reset", {"token": tok(), "password": "a brand new password"})
    call("owner", "GET", "/api/auth/me")
    call("anon", "POST", "/api/auth/forgot", {"email": "Spouse@Home.example"})
    call("spouse", "POST", "/api/auth/reset", {"token": tok(), "password": "spouse password 1"})
    call("anon", "POST", "/api/auth/forgot", {"email": "nobody@nowhere.example"})
    for _ in range(3):
        call("anon", "POST", "/api/auth/forgot", {"email": "spouse@home.example"})
    call("tech", "DELETE", "/api/systems/2/members/1")
    call("tech", "DELETE", "/api/systems/2/members/1")

    # ---- homeowner ----
    call("owner", "GET", "/api/systems")
    call("owner", "GET", "/api/systems/2")
    call("owner", "GET", "/api/fleet")
    call("owner", "GET", "/api/systems/1/latest")
    call("owner", "GET", "/api/systems/1/equipment")
    call("owner", "GET", "/api/systems/1/service")
    call("owner", "GET", "/api/systems/1/visits")
    call("owner", "GET", "/api/systems/1/gauge-checks")
    call("owner", "GET", "/api/systems/1/threshold-report")
    call("owner", "POST", "/api/systems/1/gauge-checks", {"p_low": 120})
    call("owner", "POST", "/api/systems/1/service/items/filter/done", {"date": "2026-10-05"})
    call("owner", "POST", "/api/systems/1/service/items/tuneup/done")
    call("owner", "POST", "/api/systems/1/service/items/filter/done", {"date": "2030-01-01"})
    call("owner", "POST", "/api/systems/1/service/items/filter/done", headers={})
    call("owner", "PUT", "/api/systems/1/thermostat", {"cool_sp": 74})
    call("owner", "PUT", "/api/systems/1/thermostat/tech", {"min_on_s": 300})
    call("owner", "POST", "/api/systems/1/alerts/1/ack")
    call("owner", "GET", "/api/account/people")
    call("owner", "POST", "/api/account/invites", {"email": "x@y.example"})
    call("owner", "POST", "/api/auth/password", {"current": "wrong", "new": "whatever123"})
    call("owner", "POST", "/api/auth/password", {"current": "a brand new password", "new": " padded password "})
    call("owner", "POST", "/api/auth/password", {"current": "a brand new password", "new": "the third password"})
    call("owner", "POST", "/api/auth/logout")
    call("owner", "GET", "/api/auth/me")
    login("owner", "owner@home.example", "the third password")

    # ---- teammates ----
    call("tech", "POST", "/api/account/invites", {"email": "Mate@DesertAir.example"})
    call("mate", "POST", "/api/invites/accept", {"token": tok(), "password": "mate password 1"})
    call("mate", "GET", "/api/fleet")
    # ---- access levels: the mate joined as a technician ----
    with sessions() as s:
        mate = s.scalar(select(User.id).where(User.email == "mate@desertair.example"))
        me = s.scalar(select(User.id).where(User.email == "tech@desertair.example"))
    call("mate", "GET", "/api/account/people")
    call("mate", "POST", "/api/account/invites", {"email": "x@desertair.example"})
    call("mate", "PATCH", f"/api/account/users/{mate}", {"admin": True})
    call("mate", "GET", "/api/systems/1/equipment")
    call("tech", "PATCH", f"/api/account/users/{me}", {"admin": False})
    call("tech", "PATCH", f"/api/account/users/{mate}", {"admin": "yes please"})
    call("tech", "PATCH", f"/api/account/users/{mate}", {})
    call("tech", "PATCH", "/api/account/users/99999", {"admin": True})
    call("tech", "PATCH", f"/api/account/users/{mate}", {"admin": True})
    call("mate", "GET", "/api/auth/me")
    call("mate", "GET", "/api/account/people")
    call("mate", "PATCH", f"/api/account/users/{mate}", {"admin": False})
    call("mate", "GET", "/api/fleet")
    call("key1", "PATCH", f"/api/account/users/{mate}", {"admin": False})
    call("tech", "POST", "/api/account/invites", {"email": "later@desertair.example"})
    call("tech", "POST", "/api/account/invites", {"email": "owner@home.example"})
    call("tech", "GET", "/api/account/people")
    call("key2", "DELETE", "/api/invites/1")
    call("tech", "DELETE", "/api/invites/99999")
    call("tech", "DELETE", "/api/account/users/6")
    call("tech", "DELETE", "/api/account/users/3")
    call("tech", "DELETE", "/api/account/users/7")
    call("mate", "GET", "/api/auth/me")
    call("tech", "GET", "/api/account/people")
    with sessions() as s:
        pending = s.scalar(select(text("max(id)")).select_from(text("invites")))
    call("tech", "DELETE", f"/api/invites/{pending}")
    call("tech", "GET", "/api/account/people")
    call("boss", "POST", "/api/auth/login", {"email": "boss@other.example", "password": PW})
    call("boss", "DELETE", "/api/account/users/3")
    call("boss", "PATCH", "/api/account/users/3", {"admin": False})
    call("tech", "POST", "/api/account/invites", {"email": "office@desertair.example", "admin": True})
    call("tech", "GET", "/api/account/people")
    call("office", "POST", "/api/invites/accept", {"token": tok(), "password": "office password 1"})
    call("office", "GET", "/api/fleet")

    # ---- Fullscope Office: inbox, support requests, customers ----
    order = {"company": "Sun Valley Air", "name": "Kim", "email": "kim@sunvalley.example", "phone": "623-555-0199", "systems": 12,
             "notes": "Glendale maintenance customers"}
    contact = {"name": "Pat", "email": " Pat@Cool.example ", "company": "Cool Co", "topic": "sales", "message": "Do you ship to Texas?"}
    call("anon", "POST", "/api/orders", order)
    call("anon", "POST", "/api/contact", contact)
    call("anon", "POST", "/api/contact", {"name": "Lee", "email": "lee@example.com", "message": "Homeowner version?"})
    call("anon", "POST", "/api/contact", {**contact, "topic": "spam"})
    call("anon", "POST", "/api/contact", {**contact, "message": "   "})
    call("anon", "POST", "/api/contact", {**contact, "website": "http://spam.example"})
    call("anon", "GET", "/api/office/inbox")
    call("key1", "GET", "/api/office/inbox")
    call("mate", "GET", "/api/office/summary")
    call("boss", "GET", "/api/office/customers")
    call("mate", "POST", "/api/support", {"subject": "Indoor node offline", "message": "Since this morning", "system_id": 1})
    call("mate", "POST", "/api/support", {"subject": "Wrong system", "message": "x", "system_id": 4})
    call("mate", "POST", "/api/support", {"subject": " ", "message": "x"})
    call("owner", "POST", "/api/support", {"subject": "Help", "message": "x"})
    call("owner", "GET", "/api/support")
    call("tech", "GET", "/api/office/summary")
    call("tech", "GET", "/api/office/inbox")
    call("tech", "GET", "/api/office/inbox?status=all&kind=order")
    call("tech", "GET", "/api/office/inbox?status=bogus")
    with sessions() as s:
        ids = {k: s.scalar(select(text("max(id)")).select_from(text("tickets")).where(text(f"kind = '{k}'")))
               for k in ("order", "contact", "support")}
    call("tech", "GET", f"/api/office/inbox/{ids['order']}")
    call("tech", "POST", f"/api/office/inbox/{ids['order']}/customer")
    call("tech", "POST", f"/api/office/inbox/{ids['order']}/customer")
    call("tech", "POST", f"/api/office/inbox/{ids['contact']}/messages", {"kind": "note", "body": "Call back Monday"})
    call("tech", "POST", f"/api/office/inbox/{ids['contact']}/messages", {"kind": "reply", "body": "Yes, we ship anywhere. "})
    call("tech", "POST", f"/api/office/inbox/{ids['contact']}/messages", {"kind": "shout", "body": "x"})
    call("tech", "POST", f"/api/office/inbox/{ids['contact']}/messages", {"kind": "reply", "body": "  "})
    call("tech", "PATCH", f"/api/office/inbox/{ids['contact']}", {"status": "closed"})
    call("tech", "PATCH", f"/api/office/inbox/{ids['contact']}", {"customer_id": 999})
    call("tech", "GET", "/api/office/inbox?status=closed")
    call("tech", "GET", "/api/office/inbox/99999")
    call("tech", "POST", f"/api/office/inbox/{ids['support']}/messages", {"kind": "reply", "body": "Power-cycle it please"})
    call("mate", "GET", "/api/support")
    call("tech", "GET", "/api/office/customers")
    with sessions() as s:
        cid = s.scalar(select(text("max(id)")).select_from(text("customers")))
    call("tech", "PATCH", f"/api/office/customers/{cid}", {"address": "1 Main St, Glendale AZ", "notes": "Met at ACCA", "status": "paused"})
    call("tech", "PATCH", f"/api/office/customers/{cid}", {"company": " "})
    call("tech", "PATCH", f"/api/office/customers/{cid}", {"email": "nope"})
    call("tech", "POST", "/api/office/customers", {"company": "Acme Air", "phone": "602-555-0111"})
    call("tech", "POST", "/api/office/customers", {"phone": "1"})
    call("tech", "POST", f"/api/office/customers/{cid}/account", {"email": "boss@other.example"})
    call("tech", "POST", f"/api/office/customers/{cid}/account", {"email": "Kim@SunValley.example"})
    call("tech", "POST", f"/api/office/customers/{cid}/account", {"email": "k2@sunvalley.example"})
    call("tech", "GET", f"/api/office/customers/{cid}")
    call("tech", "POST", f"/api/office/inbox/{ids['support']}/customer")
    with sessions() as s:
        cid2 = s.scalar(select(text("max(id)")).select_from(text("customers")))
    call("tech", "GET", f"/api/office/customers/{cid2}")
    call("tech", "PATCH", f"/api/office/inbox/{ids['contact']}", {"customer_id": 0, "status": "open"})
    call("tech", "GET", "/api/office/customers")
    call("tech", "GET", "/api/office/customers/99999")
    call("tech", "GET", "/api/office/summary")

    # ---- Fullscope Office: kits, boards, warranty; the contractor's QR claim ----
    kit = {"code": "fs-r7k3q", "hw_rev": "B", "notes": "Replay kit",
           "boards": [{"kind": "outdoor", "serial": "aa-bb-cc-dd-ee-01", "fw": "0.3.2"}, {"kind": "indoor", "serial": "AABBCCDDEE02"}]}
    call("office", "GET", "/api/office/kits")
    call("boss", "POST", "/api/office/kits", kit)
    call("tech", "GET", "/api/office/kits")
    call("tech", "POST", "/api/office/kits", kit)
    kit1_tok = tok()
    call("tech", "POST", "/api/office/kits", kit)
    call("tech", "POST", "/api/office/kits", {**kit, "code": "FS-ZZZZ"})
    call("tech", "POST", "/api/office/kits", {"code": "kit-1", "boards": [{"kind": "indoor", "serial": "AABBCCDDEE03"}]})
    call("tech", "POST", "/api/office/kits", {"code": "FS-OTHER", "boards": [{"kind": "indoor", "serial": "AABBCCDDEE03"}]})
    call("tech", "POST", "/api/office/kits", {"code": "FS-TWO", "boards": [{"kind": "indoor", "serial": "AABBCCDDEE03"},
                                                                          {"kind": "indoor", "serial": "AABBCCDDEE04"}]})
    call("tech", "POST", "/api/office/kits", {"code": "FS-BAD", "boards": [{"kind": "indoor", "serial": "nope"}]})
    call("tech", "POST", "/api/office/kits", {"code": "FS-NONE", "boards": []})
    call("tech", "POST", "/api/office/kits", {"code": "FS-KIND", "boards": [{"kind": "attic", "serial": "AABBCCDDEE03"}]})
    call("tech", "POST", "/api/office/kits", {"code": "FS-S2X9", "hw_rev": "B", "boards": [{"kind": "outdoor", "serial": "AA:BB:CC:DD:EE:05"},
                                                                                        {"kind": "indoor", "serial": "AA:BB:CC:DD:EE:06"}]})
    kit2_tok = tok()
    with sessions() as s:
        k1, k2 = (s.scalar(select(text("id")).select_from(text("kits")).where(text(f"code = '{c}'"))) for c in ("FS-R7K3Q", "FS-S2X9"))
    call("tech", "GET", "/api/office/kits")
    call("tech", "GET", f"/api/office/kits/{k1}")
    call("tech", "GET", "/api/office/kits/99999")
    call("tech", "PATCH", f"/api/office/kits/{k2}", {"customer_id": cid, "shipped": True})
    call("tech", "PATCH", f"/api/office/kits/{k2}", {"customer_id": 99999})
    call("tech", "PATCH", f"/api/office/kits/{k1}", {"shipped": True, "notes": " Boxed "})
    call("tech", "PATCH", f"/api/office/kits/{k1}", {"shipped": False})
    call("tech", "GET", "/api/office/boards?q=ee:0")
    call("tech", "GET", "/api/office/boards?q=EE05")
    call("owner", "POST", "/api/claim/lookup", {"code": kit1_tok})
    call("office", "POST", "/api/claim/lookup", {"code": "FS-R7K3Q-WRONG"})
    call("office", "POST", "/api/claim/lookup", {"code": kit2_tok})               # shipped to Sun Valley
    call("office", "POST", "/api/claim", {"code": kit2_tok, "system_name": "Not ours"})
    call("office", "POST", "/api/claim/lookup", {"code": kit1_tok})
    call("office", "POST", "/api/claim", {"code": kit1_tok, "system_name": " "})
    call("office", "POST", "/api/claim", {"code": kit1_tok, "system_name": "Lee home", "homeowner_email": "boss@other.example"})
    call("office", "POST", "/api/claim", {"code": kit1_tok, "system_name": "Lee home", "address": "9 Saguaro Dr, Mesa AZ",
                                        "homeowner_email": " Lee@Home.example "})
    call("office", "POST", "/api/claim/lookup", {"code": kit1_tok})
    call("boss", "POST", "/api/claim/lookup", {"code": kit1_tok})
    call("office", "POST", "/api/claim", {"code": kit1_tok, "system_name": "Again"})
    call("tech", "GET", "/api/systems")
    call("tech", "GET", f"/api/office/kits/{k1}")
    call("tech", "POST", f"/api/office/kits/{k1}/new-code")
    with sessions() as s:
        b1, b2 = s.scalars(select(text("id")).select_from(text("boards")).where(text(f"kit_id = {k1}")).order_by(text("id"))).all()
    call("tech", "POST", f"/api/office/boards/{b1}/claims", {"problem": "Suction sensor reads open"})
    call("tech", "POST", f"/api/office/boards/{b2}/claims", {"problem": "  "})
    call("tech", "POST", "/api/office/boards/99999/claims", {"problem": "x"})
    with sessions() as s:
        wc = s.scalar(select(text("max(id)")).select_from(text("warranty_claims")))
    call("tech", "PATCH", f"/api/office/claims/{wc}", {"status": "replaced"})
    call("tech", "PATCH", f"/api/office/claims/{wc}", {"status": "replaced", "replacement_serial": "AA:BB:CC:DD:EE:05"})
    call("tech", "PATCH", f"/api/office/claims/{wc}", {"status": "lost"})
    call("tech", "PATCH", f"/api/office/claims/{wc}", {"status": "replaced", "replacement_serial": "aabbccddee99",
                                                      "resolution": "Sent a new outdoor board"})
    call("tech", "POST", f"/api/office/boards/{b2}/claims", {"problem": "Loose terminal"})
    call("tech", "PATCH", f"/api/office/claims/{wc + 1}", {"status": "repaired", "resolution": "Tightened"})
    call("tech", "PATCH", "/api/office/claims/99999", {"status": "denied"})
    call("tech", "GET", f"/api/office/kits/{k1}")
    call("tech", "POST", f"/api/office/kits/{k1}/release")
    call("tech", "POST", f"/api/office/kits/{k1}/release")
    call("tech", "POST", f"/api/office/kits/{k2}/new-code")
    call("tech", "GET", "/api/office/kits")
    call("tech", "GET", "/api/systems")

    # ---- password guessing ----
    for _ in range(8):
        login("anon", "spouse@home.example", "wrong guess here")
    login("spouse", "spouse@home.example", "spouse password 1")

    # ---- sessions expire ----
    call("tech", "GET", "/api/auth/me", advance=6 * 60)               # last_seen moves along
    call("owner", "GET", "/api/auth/me", advance=31 * 86400)
    call("tech", "GET", "/api/systems", advance=1)
    call("key1", "GET", "/api/systems/1/latest")
    call("key1", "GET", "/api/systems/1/summary")
    call("key1", "GET", "/api/fleet")

    engine.dispose()
    with open(seed, "rb") as a, open(seed + ".gz", "wb") as raw_out:     # 8 MB -> about 1 MB in git
        with gzip.GzipFile(fileobj=raw_out, mode="wb", compresslevel=9, mtime=0) as b:
            b.write(a.read())
    os.remove(seed)
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(work + ext):
            os.remove(work + ext)
    with open(os.path.join(OUT, "api_replay.json"), "w") as f:
        json.dump({"app_url": APP_URL, "order_email": ORDER_EMAIL, "keys": {"key1": KEY1, "key2": KEY2}, "steps": steps}, f, indent=0)
    by_status = {}
    for s_ in steps:
        by_status[s_["status"]] = by_status.get(s_["status"], 0) + 1
    print(f"{len(steps)} requests {dict(sorted(by_status.items()))}, {sum(len(s_['emails']) for s_ in steps)} emails, "
          f"{sum(len(s_['published']) for s_ in steps)} publishes, {len(tokens)} tokens")


if __name__ == "__main__":
    main()
