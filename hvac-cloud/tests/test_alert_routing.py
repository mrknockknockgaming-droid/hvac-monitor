import datetime as dt
import sqlite3

from fastapi.testclient import TestClient
from sqlalchemy import select

from hvaccloud import alerts
from hvaccloud.api import create_app
from hvaccloud.db import Alert, AlertMute, AlertPrefs, ServiceInfo, System, SystemMember, User, init_db, make_engine

T0 = 1_790_000_000.0
FLAG = {"level": "warn", "code": "dt_low", "text": "Air delta-T 9.0°F is low"}


def system(s):
    return s.scalar(select(System).where(System.site_id == "home"))


def raise_and_clear(sessions, start=T0, ack=False):
    """Raise dt_low (hold 0), mark it emailed, optionally ack it, then clear it. Returns (raise jobs, clear jobs)."""
    with sessions() as s, s.begin():
        sy = system(s)
        ev = alerts.sync(s, sy, [FLAG], start, 0)
        up = alerts.emails_for(s, sy, ev, start, 6 * 3600)
        if up:
            ev[0][1].emailed_at = alerts.ts(start)
        if ack:
            ev[0][1].acked_at = alerts.ts(start + 10)
    with sessions() as s, s.begin():
        sy = system(s)
        down = alerts.emails_for(s, sy, alerts.sync(s, sy, [], start + 60, 0), start + 60, 6 * 3600)
    return up, down


def setup_contractor(sessions, owner=True, contractor=True, email="shop@example.com"):
    with sessions() as s, s.begin():
        sid = system(s).id
        s.add(ServiceInfo(system_id=sid, name="Saguaro", email=email))
        s.add(AlertPrefs(system_id=sid, email_owner=owner, email_contractor=contractor))


def add_homeowner(sessions, email="pat@example.com"):
    with sessions() as s, s.begin():
        u = User(email=email, role="homeowner")
        s.add(u)
        s.flush()
        s.add(SystemMember(system_id=system(s).id, user_id=u.id))


def test_no_homeowners_yet_means_the_contractor_account_only(sessions, seeded):
    up, down = raise_and_clear(sessions)
    assert [j[1] for j in up] == ["t@example.com"]                       # the contractor account's email
    assert up[0][2] == "[Fullscope] Home (site home): Air delta-T 9.0°F is low"
    assert "cleared" in down[0][2]


def test_homeowners_get_plain_words_and_the_contractor_the_technical_version(sessions, seeded):
    add_homeowner(sessions)
    setup_contractor(sessions)
    up, down = raise_and_clear(sessions)
    by = {j[1]: j for j in up}
    assert set(by) == {"pat@example.com", "shop@example.com"}            # not the account email any more
    assert by["pat@example.com"][2] == "[Fullscope] Home: Air from your vents isn't as cool as it should be"
    assert "Check your air filter" in by["pat@example.com"][3] and "#/home/" in by["pat@example.com"][3]
    assert by["shop@example.com"][2] == "[Fullscope] Home (site home): Air delta-T 9.0°F is low"
    assert "#/monitor/" in by["shop@example.com"][3]
    assert any("back to normal" in j[2] for j in down)


def test_switching_recipients_off(sessions, seeded):
    add_homeowner(sessions)
    setup_contractor(sessions, owner=False)
    up, _ = raise_and_clear(sessions)
    assert [j[1] for j in up] == ["shop@example.com"]
    with sessions() as s, s.begin():
        s.get(AlertPrefs, system(s).id).email_contractor = False
    up, _ = raise_and_clear(sessions, start=T0 + 7 * 3600)
    assert up == []                                                       # nobody to send to


def test_mute_stops_emails_until_it_ends(sessions, seeded):
    with sessions() as s, s.begin():
        s.add(AlertMute(system_id=system(s).id, code="dt_low", until=alerts.ts(T0 + 3600)))
    up, down = raise_and_clear(sessions)
    assert up == [] and down == []
    up, _ = raise_and_clear(sessions, start=T0 + 2 * 3600)
    assert len(up) == 1


def test_acknowledged_alert_sends_no_cleared_email(sessions, seeded):
    _, down = raise_and_clear(sessions, ack=True)
    assert down == []


def test_alert_api(sessions, seeded):
    with sessions() as s, s.begin():
        alerts.sync(s, system(s), [FLAG], T0, 0)
    c = TestClient(create_app(sessions, publisher=lambda *a: True))
    h, h2 = {"X-API-Key": seeded["key1"]}, {"X-API-Key": seeded["key2"]}
    sid = c.get("/api/systems", headers=h).json()[0]["id"]
    aid = c.get(f"/api/systems/{sid}/alerts", headers=h).json()[0]["id"]
    assert c.post(f"/api/systems/{sid}/alerts/{aid}/ack", headers=h).json()["acked_at"]
    assert c.post(f"/api/systems/{sid}/alerts/{aid}/ack", headers=h, json={"ack": False}).json()["acked_at"] is None
    assert c.post(f"/api/systems/{sid}/alerts/{aid}/ack", headers=h2).status_code == 404
    v = c.get(f"/api/systems/{sid}/alert-settings", headers=h).json()
    assert v["email_owner"] is True and v["email_contractor"] is True
    assert v["homeowner_emails"] == [] and v["contractor_email"] == "t@example.com"
    v = c.put(f"/api/systems/{sid}/alert-settings", headers=h, json={"email_owner": False}).json()
    assert v["email_owner"] is False and v["email_contractor"] is True
    v = c.put(f"/api/systems/{sid}/alert-mutes/dt_low", headers=h, json={"hours": 24}).json()
    assert [m["code"] for m in v["mutes"]] == ["dt_low"]
    v = c.put(f"/api/systems/{sid}/alert-mutes/dt_low", headers=h, json={"hours": 0}).json()
    assert v["mutes"] == []
    assert c.put(f"/api/systems/{sid}/alert-mutes/bad code!", headers=h, json={"hours": 1}).status_code in (404, 422)


def test_existing_database_gets_new_columns(tmp_path):
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as c:                       # an alerts table from before acked_at existed
        c.execute("CREATE TABLE alerts (id INTEGER PRIMARY KEY, system_id INTEGER, code VARCHAR(32), node VARCHAR(16), "
                  "level VARCHAR(8), text VARCHAR(300), started_at DATETIME, last_seen DATETIME, raised_at DATETIME, "
                  "cleared_at DATETIME, emailed_at DATETIME)")
    engine = make_engine("sqlite:///" + str(db).replace("\\", "/"))
    init_db(engine)
    engine.dispose()
    with sqlite3.connect(db) as c:
        assert "acked_at" in [r[1] for r in c.execute("PRAGMA table_info(alerts)")]
