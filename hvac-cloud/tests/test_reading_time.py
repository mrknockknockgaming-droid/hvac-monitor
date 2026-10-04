"""Firmware 0.2 stamps each reading ("ts") and keeps readings taken during an outage; the cloud
files them under their own time."""
import json

from sqlalchemy import select

from hvaccloud.db import Device, Snapshot, Telemetry, as_utc
from hvaccloud.ingest import Ingest
from tests.test_calc import COOL_IN, COOL_OUT

NOW = 1_790_000_000.0


def send(i, node, base, at, now):
    return i.handle(f"hvac/home/{node}/telemetry", json.dumps({**base, "ts": at}).encode(), now=now)


def snap_times(sessions):
    with sessions() as s:
        return [as_utc(t).timestamp() for t in s.scalars(select(Snapshot.time).order_by(Snapshot.time))]


def test_plausible_timestamps_are_used_and_odd_ones_ignored():
    r = Ingest.reading_time
    assert r({"ts": NOW - 300}, NOW) == NOW - 300                 # kept during an outage
    assert r({"ts": NOW + 20}, NOW) == NOW                        # clock slightly ahead: arrival time caps it
    for bad in (NOW + 3600, NOW - 2 * 86400, "1790000000", True, None):
        assert r({"ts": bad}, NOW) == NOW
    assert r({}, NOW) == NOW                                      # firmware 0.1 sends no ts


def test_outage_backlog_lands_where_it_belongs(sessions, seeded, tables):
    i = Ingest(sessions, tables)
    send(i, "indoor", COOL_IN, NOW - 400, NOW - 400)               # live before the outage
    send(i, "outdoor", COOL_OUT, NOW - 399, NOW - 399)
    for k in range(60):                                           # 5 min kept on the nodes, all sent at NOW
        at = NOW - 300 + k * 5
        send(i, "indoor", COOL_IN, at, NOW)
        send(i, "outdoor", COOL_OUT, at, NOW)                     # same second as indoor: must not collide
    times = snap_times(sessions)
    assert len(times) == 122 and len(set(times)) == 122
    assert abs(times[-1] - (NOW - 5)) < 0.01 and min(times) == NOW - 400
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert max(gaps) < 100                                        # no hole where the outage was
    with sessions() as s:
        assert as_utc(s.scalar(select(Telemetry.time).order_by(Telemetry.time.desc()).limit(1))).timestamp() > NOW - 6
        last_seen = {d.node: as_utc(d.last_seen).timestamp() for d in s.scalars(select(Device))}
    assert all(v > NOW - 6 for v in last_seen.values())


def test_a_late_reading_does_not_move_last_seen_back(sessions, seeded, tables):
    i = Ingest(sessions, tables)
    send(i, "outdoor", COOL_OUT, NOW, NOW)
    send(i, "outdoor", COOL_OUT, NOW - 200, NOW + 1)              # an old one arriving late
    with sessions() as s:
        assert as_utc(s.scalar(select(Device.last_seen))).timestamp() == NOW
