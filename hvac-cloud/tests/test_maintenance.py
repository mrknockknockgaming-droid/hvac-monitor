import datetime as dt
import os
import sqlite3

from sqlalchemy import select

from hvaccloud import maintenance
from hvaccloud.db import Account, Device, Snapshot, System, Telemetry, init_db, make_engine, session_factory

NOW = 1_790_000_000.0
DAY = 86400


def ts(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc)


def add_minute(s, system_id, start, n=12, base=10.0):
    """n snapshots 5 s apart starting at `start` (a minute boundary), sh = base, base+1, ..."""
    for i in range(n):
        s.add(Snapshot(system_id=system_id, time=ts(start + i * 5), mode="cooling",
                       data={"mode": "cooling", "sh": base + i, "Y": True, "flags": [], "t_sup": None}))


def snaps(sessions, lo, hi):
    with sessions() as s:
        return s.execute(select(Snapshot.time, Snapshot.data).where(Snapshot.time >= ts(lo), Snapshot.time < ts(hi))
                         .order_by(Snapshot.time)).all()


def seed(sessions):
    with sessions() as s, s.begin():
        sid = s.scalar(select(System.id).where(System.site_id == "home"))
        old = (NOW - 40 * DAY) // 60 * 60
        add_minute(s, sid, old)
        add_minute(s, sid, old + 60, base=20.0)
        add_minute(s, sid, (NOW - 1 * DAY) // 60 * 60)                      # recent: untouched
        s.add(Snapshot(system_id=sid, time=ts(NOW - 400 * DAY), mode="idle", data={"mode": "idle"}))
        d = Device(system_id=sid, node="outdoor")
        s.add(d)
        s.flush()
        for k in range(5):                                                   # all raw messages are old
            s.add(Telemetry(device_id=d.id, time=ts(NOW - 50 * DAY + k * 5), data={"k": k}))
    return sid


def test_prune_thins_old_minutes_and_keeps_recent_detail(sessions, seeded):
    seed(sessions)
    stats = maintenance.prune(sessions, now=NOW, full_days=30, keep_days=365)
    old = snaps(sessions, NOW - 41 * DAY, NOW - 39 * DAY)
    assert len(old) == 2                                     # one row per minute
    assert old[0].data["sh"] == 15.5 and old[1].data["sh"] == 25.5   # averages of 10..21 and 20..31
    assert old[0].data["Y"] is True and old[0].data["t_sup"] is None and old[0].data["flags"] == []
    assert len(snaps(sessions, NOW - 2 * DAY, NOW)) == 12   # last 30 days at full detail
    assert snaps(sessions, NOW - 401 * DAY, NOW - 399 * DAY) == []   # past KEEP_DAYS
    with sessions() as s:
        raw = s.scalars(select(Telemetry.data)).all()
    assert raw == [{"k": 4}]                                 # newest message per node survives
    assert stats["snapshots_removed"] == 22 and stats["expired"] == 1 and stats["telemetry_removed"] == 4


def test_prune_again_is_a_no_op(sessions, seeded):
    seed(sessions)
    maintenance.prune(sessions, now=NOW, full_days=30, keep_days=365)
    again = maintenance.prune(sessions, now=NOW + 60, full_days=30, keep_days=365)
    assert again == {"snapshots_removed": 0, "telemetry_removed": 0, "expired": 0}


def test_prune_crosses_days_without_data(sessions, seeded):
    sid = seed(sessions)
    with sessions() as s, s.begin():
        add_minute(s, sid, (NOW - 33 * DAY) // 60 * 60, base=50.0)        # days 34..39 have no data
    maintenance.prune(sessions, now=NOW, full_days=30, keep_days=365)
    assert len(snaps(sessions, NOW - 34 * DAY, NOW - 32 * DAY)) == 1
    assert len(snaps(sessions, NOW - 41 * DAY, NOW - 39 * DAY)) == 2


def test_backup_is_readable_and_rotates(tmp_path):
    db = tmp_path / "dev.db"
    url = "sqlite:///" + str(db).replace("\\", "/")
    engine = make_engine(url)
    init_db(engine)
    with session_factory(engine)() as s, s.begin():
        acct = Account(name="T", email="t@example.com")
        s.add(acct)
        s.flush()
        s.add(System(account_id=acct.id, name="Home", site_id="home"))
    out = tmp_path / "backups"
    paths = [maintenance.backup(url, str(out), keep=2, now=NOW + k * DAY) for k in range(3)]
    assert sorted(os.listdir(out)) == sorted(os.path.basename(p) for p in paths[1:])
    with sqlite3.connect(paths[-1]) as c:
        assert c.execute("select site_id from systems").fetchall() == [("home",)]
    engine.dispose()


def test_backup_skips_non_sqlite(tmp_path):
    assert maintenance.backup("postgresql+psycopg://x@db/hvac", str(tmp_path)) is None


def test_nightly_due(tmp_path):
    d = str(tmp_path)
    assert maintenance.nightly_due(d, now=NOW)                               # never backed up
    f = tmp_path / "dev-20260921-0300.db"
    f.write_bytes(b"")
    made = dt.datetime(2026, 9, 21, 3, 5).timestamp()
    os.utime(f, (made, made))
    assert not maintenance.nightly_due(d, now=made + 600)                    # just ran
    assert not maintenance.nightly_due(d, now=dt.datetime(2026, 9, 21, 23, 0).timestamp())
    assert maintenance.nightly_due(d, now=dt.datetime(2026, 9, 22, 3, 1).timestamp())   # next night
    assert not maintenance.nightly_due(d, now=dt.datetime(2026, 9, 22, 2, 0).timestamp())
    assert maintenance.nightly_due(d, now=made + 27 * 3600)                  # missed 3 AM: catch up
