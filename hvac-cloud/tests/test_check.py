import datetime as dt

from hvaccloud import check
from hvaccloud.db import Alert, Command, Device, Snapshot, System, User

T0 = 1_790_000_000.0


def at(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc)


def levels(found, word):
    return [lvl for lvl, text in found if word in text]


def test_a_healthy_and_a_quiet_system(sessions, seeded):
    with sessions() as s, s.begin():
        home = s.query(System).filter_by(site_id="home").one()
        out = Device(system_id=home.id, node="outdoor", fw="0.3.1", rssi=-60, last_seen=at(T0 - 5))
        ind = Device(system_id=home.id, node="indoor", last_seen=at(T0 - 3600))
        s.add_all([out, ind])
        s.flush()
        s.add_all([Snapshot(system_id=home.id, time=at(T0 - 10 * i), mode="cooling", data={}) for i in range(3)])
        s.add(Command(device_id=out.id, payload={"cmd": "status"}, created_at=at(T0 - 600), sent=True,
                      reply={"ok": True}, replied_at=at(T0 - 599)))
        s.add(Alert(system_id=home.id, code="dt_low", level="warn", text="x", started_at=at(T0 - 900),
                    last_seen=at(T0), raised_at=at(T0 - 600), emailed_at=at(T0 - 590)))
        s.add(User(email="a@b.com", role="contractor", account_id=home.account_id))
    with sessions() as s:
        found = check.run(s, now=T0, email_on=True)
    text = "\n".join(f"{lvl} {t}" for lvl, t in found)
    assert levels(found, "outdoor node last heard") == ["OK"] and "firmware 0.3.1, WiFi -60 dBm" in text
    assert levels(found, "indoor node last heard") == ["FAIL"] and "60 min ago" in text
    assert levels(found, "Home (site home): newest reading") == ["OK"]
    assert levels(found, "compressor running in 3 readings") == ["OK"]
    assert levels(found, "1 open alert") == ["WARN"]
    assert levels(found, "last alert dt_low raised 10 min ago, emailed") == ["OK"]
    assert levels(found, "last command to the outdoor node (10 min ago) was answered") == ["OK"]
    assert levels(found, "two-step sign-in on for 0 of 1") == ["WARN"]
    # the other account's system has never reported
    assert levels(found, "Other (site other): no node has reported yet") == ["FAIL"]
    assert levels(found, "Other (site other): no cooling or heating call") == ["WARN"]
