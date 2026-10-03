import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from hvaccloud import alerts
from hvaccloud.api import create_app
from hvaccloud.db import Alert, System
from hvaccloud.ingest import Ingest
from tests.test_calc import COOL_IN, COOL_OUT

T0 = 1_790_000_000.0
FLAG = {"level": "warn", "code": "dt_low", "text": "Air delta-T 9.0°F is low"}


class FakeMailer:
    enabled = True

    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))
        return True


def ingest_with(sessions, tables, mailer=None):
    i = Ingest(sessions, tables, mailer=mailer)
    i.spawn = lambda fn, *a: fn(*a)          # send inline so the test can see it
    return i


def send(i, node, data, now):
    return i.handle(f"hvac/home/{node}/telemetry", json.dumps(data).encode(), now=now)


def rows(sessions):
    with sessions() as s:
        return list(s.scalars(select(Alert).order_by(Alert.id)))


def run_sync(sessions, flags, now, hold=300):
    with sessions() as s, s.begin():
        system = s.scalar(select(System).where(System.site_id == "home"))
        return [(k, a.code) for k, a in alerts.sync(s, system, flags, now, hold)]


def test_a_flag_must_hold_before_it_raises(sessions, seeded):
    assert run_sync(sessions, [FLAG], T0) == []
    assert run_sync(sessions, [FLAG], T0 + 299) == []
    assert run_sync(sessions, [FLAG], T0 + 300) == [("raised", "dt_low")]
    assert run_sync(sessions, [FLAG], T0 + 400) == []                     # raised once only


def test_a_blip_never_raises_and_is_forgotten(sessions, seeded):
    run_sync(sessions, [FLAG], T0)
    run_sync(sessions, [], T0 + 10)
    assert run_sync(sessions, [], T0 + 400) == []
    assert rows(sessions) == []


def test_a_flapping_flag_stays_one_alert_and_clears_after_the_hold(sessions, seeded):
    for k in range(40):                                  # on, off, on, off... every 10 s
        run_sync(sessions, [FLAG] if k % 2 == 0 else [], T0 + k * 10)
    assert [a.code for a in rows(sessions)] == ["dt_low"] and rows(sessions)[0].raised_at is not None
    last = T0 + 38 * 10
    assert run_sync(sessions, [], last + 299) == []
    assert run_sync(sessions, [], last + 300) == [("cleared", "dt_low")]
    assert run_sync(sessions, [], last + 900) == []


def test_offline_node_raises_emails_and_clears(sessions, seeded, tables):
    mailer = FakeMailer()
    i = ingest_with(sessions, tables, mailer)
    send(i, "indoor", COOL_IN, T0)
    for k in range(72):                                  # outdoor keeps reporting for 12 min, indoor is quiet
        send(i, "outdoor", COOL_OUT, T0 + 1 + k * 10)
    offline = [a for a in rows(sessions) if a.code == "node_offline"]
    assert len(offline) == 1 and offline[0].node == "indoor" and offline[0].raised_at is not None
    assert offline[0].emailed_at is not None
    assert [m[0] for m in mailer.sent] == ["t@example.com"] and "A monitor isn't reporting" in mailer.sent[0][1]
    assert "For your contractor: Indoor node is offline" in mailer.sent[0][2]
    for k in range(40):                                  # indoor back for ~7 min
        t = T0 + 721 + k * 10
        send(i, "indoor", COOL_IN, t)
        send(i, "outdoor", COOL_OUT, t + 1)
    offline = [a for a in rows(sessions) if a.code == "node_offline"]
    assert offline[0].cleared_at is not None
    assert "back to normal" in mailer.sent[-1][1]


def test_email_cooldown_per_code(sessions, seeded, tables):
    mailer = FakeMailer()
    i = ingest_with(sessions, tables, mailer)
    for start in (T0, T0 + 3600):                        # raised, cleared, raised again an hour later
        with sessions() as s, s.begin():
            system = s.scalar(select(System).where(System.site_id == "home"))
            jobs = alerts.emails_for(s, system, alerts.sync(s, system, [FLAG], start, 0), start, 6 * 3600)
        i._send(jobs)
        run_sync(sessions, [], start + 60, hold=0)
    assert len([m for m in mailer.sent if "back to normal" not in m[1]]) == 1


def test_sweep_raises_no_data_and_readings_clear_it(sessions, seeded, tables):
    mailer = FakeMailer()
    i = ingest_with(sessions, tables, mailer)
    i.sweep(T0)                                          # nothing ever reported: no alert
    assert rows(sessions) == []
    send(i, "indoor", COOL_IN, T0)
    send(i, "outdoor", COOL_OUT, T0 + 1)
    for k in range(1, 20):                               # both nodes silent, sweep once a minute
        i.sweep(T0 + 1 + k * 60)
    nd = [a for a in rows(sessions) if a.code == "no_data"]
    assert len(nd) == 1 and nd[0].raised_at is not None and nd[0].cleared_at is None
    back = T0 + 1300
    send(i, "indoor", COOL_IN, back)
    send(i, "outdoor", COOL_OUT, back + 1)
    nd = [a for a in rows(sessions) if a.code == "no_data"]
    assert nd[0].cleared_at is not None                  # cleared on the first reading, not after the hold
    assert any("lost contact" in m[1] for m in mailer.sent)


def test_no_email_without_a_mailer(sessions, seeded, tables):
    i = Ingest(sessions, tables)                         # default: email off
    send(i, "indoor", COOL_IN, T0)
    for k in range(72):
        send(i, "outdoor", COOL_OUT, T0 + 1 + k * 10)
    assert all(a.emailed_at is None for a in rows(sessions))


def test_alerts_api(sessions, seeded, tables):
    run_sync(sessions, [FLAG], T0)
    run_sync(sessions, [FLAG, {"level": "warn", "code": "sc_low", "text": "Subcooling 1.0°F is low"}], T0 + 300)
    c = TestClient(create_app(sessions, publisher=lambda *a: True))
    sys_id = c.get("/api/systems", headers={"X-API-Key": seeded["key1"]}).json()[0]["id"]
    got = c.get(f"/api/systems/{sys_id}/alerts", headers={"X-API-Key": seeded["key1"]}).json()
    assert [a["code"] for a in got] == ["dt_low"]        # sc_low is still pending
    assert got[0]["open"] is True and got[0]["raised_at"]
    assert c.get(f"/api/systems/{sys_id}/alerts", headers={"X-API-Key": seeded["key2"]}).status_code == 404
