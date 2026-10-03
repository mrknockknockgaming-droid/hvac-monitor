"""Nightly database upkeep for the SQLite development database: backup, then prune.

- backup(): a consistent copy of dev.db (SQLite's online backup, safe while the ingest
  worker writes) into BACKUP_DIR as dev-YYYYMMDD-HHMM.db, keeping the newest BACKUP_KEEP.
- prune(): snapshots older than FULL_DETAIL_DAYS are averaged into one row per minute; raw
  telemetry older than that is deleted (except each node's newest message); everything older
  than KEEP_DAYS is deleted. Charts, history, summaries and CSV export keep working on the
  thinned data. On PostgreSQL TimescaleDB's retention policy does this instead.

The ingest worker runs both once a day (nightly_due), and manage.py backup / prune run them by hand.
"""
import datetime as dt
import glob
import logging
import os
import sqlite3
import time

from sqlalchemy import delete, func, select

from . import settings
from .db import Device, Snapshot, System, Telemetry, as_utc

log = logging.getLogger("maintenance")

BACKUP_PATTERN = "dev-*.db"
NIGHTLY_HOUR = 3            # local time
OVERDUE_HOURS = 26          # PC was off at 3 AM: catch up on the next start


def sqlite_path(url):
    """File path of a sqlite:/// URL, or None for other databases (and in-memory SQLite)."""
    if not url.startswith("sqlite:///"):
        return None
    path = url[len("sqlite:///"):]
    return path or None


def backups(backup_dir):
    return sorted(glob.glob(os.path.join(backup_dir, BACKUP_PATTERN)))


def backup(db_url=None, backup_dir=None, keep=None, now=None):
    """Copy the SQLite database into backup_dir and delete all but the newest `keep` copies.
    Returns the new file's path, or None when the database is not a SQLite file."""
    db_url = db_url or settings.DATABASE_URL
    backup_dir = backup_dir or settings.BACKUP_DIR
    keep = settings.BACKUP_KEEP if keep is None else keep
    src_path = sqlite_path(db_url)
    if src_path is None:
        log.info("backup skipped: not a SQLite file (use pg_dump for PostgreSQL)")
        return None
    os.makedirs(backup_dir, exist_ok=True)
    stamp = dt.datetime.fromtimestamp(time.time() if now is None else now).strftime("%Y%m%d-%H%M")
    dest_path = os.path.join(backup_dir, f"dev-{stamp}.db")
    src = sqlite3.connect(src_path, timeout=30)
    dest = sqlite3.connect(dest_path)
    try:
        src.backup(dest, pages=2000, sleep=0.05)    # copies in steps so the ingest worker isn't blocked
    finally:
        dest.close()
        src.close()
    for old in backups(backup_dir)[:-keep] if keep > 0 else []:
        os.remove(old)
    log.info("backup written: %s (%.1f MB)", dest_path, os.path.getsize(dest_path) / 1e6)
    return dest_path


def nightly_due(backup_dir=None, now=None):
    """True at NIGHTLY_HOUR when today's backup hasn't run, or whenever the last one is overdue."""
    backup_dir = backup_dir or settings.BACKUP_DIR
    now = time.time() if now is None else now
    files = backups(backup_dir)
    if not files:
        return True
    last = os.path.getmtime(files[-1])
    if now - last >= OVERDUE_HOURS * 3600:
        return True
    local = dt.datetime.fromtimestamp(now)
    return local.hour == NIGHTLY_HOUR and dt.datetime.fromtimestamp(last).date() < local.date()


def _ts(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc)


def average_rows(rows):
    """One snapshot dict for a minute: the middle row, with every numeric value replaced by the
    minute's average (booleans, mode, flags and notes are taken from that middle row)."""
    base = dict(rows[len(rows) // 2])
    sums, counts = {}, {}
    for r in rows:
        for k, v in r.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                sums[k] = sums.get(k, 0.0) + v
                counts[k] = counts.get(k, 0) + 1
    for k in sums:
        base[k] = round(sums[k] / counts[k], 2)
    return base


def prune(sessions, now=None, full_days=None, keep_days=None, lock=None):
    """Thin and expire old rows. Works one system-day at a time so each transaction stays short;
    `lock` (the ingest worker's) is held per step so the worker never waits on a long delete."""
    now = time.time() if now is None else now
    full_days = settings.FULL_DETAIL_DAYS if full_days is None else full_days
    keep_days = settings.KEEP_DAYS if keep_days is None else keep_days
    detail_cut = _ts((now - full_days * 86400) // 60 * 60)     # on a minute boundary
    keep_cut = _ts(now - keep_days * 86400)
    stats = {"snapshots_removed": 0, "telemetry_removed": 0, "expired": 0}
    with sessions() as s:
        if s.get_bind().dialect.name != "sqlite":
            log.info("prune skipped: TimescaleDB retention handles PostgreSQL")
            return stats

    def step(fn):
        if lock is None:
            return fn()
        with lock:
            return fn()

    def expire():
        with sessions() as s, s.begin():
            n = s.execute(delete(Snapshot).where(Snapshot.time < keep_cut)).rowcount
            n += s.execute(delete(Telemetry).where(Telemetry.time < keep_cut)).rowcount
            return n
    stats["expired"] = step(expire)

    with sessions() as s:
        system_ids = list(s.scalars(select(System.id)))
        device_ids = list(s.scalars(select(Device.id)))

    # Newest day first. A day that has data and needed no thinning was done on an earlier night,
    # and so was everything before it, so the nightly run only touches the day that just aged out.
    for sid in system_ids:
        with sessions() as s:
            first = as_utc(s.scalar(select(func.min(Snapshot.time)).where(Snapshot.system_id == sid)))
        end = detail_cut
        while first is not None and end > first:
            start = end - dt.timedelta(days=1)
            removed, had_rows = step(lambda: _thin_snapshots(sessions, sid, start, end))
            stats["snapshots_removed"] += removed
            if had_rows and removed == 0:
                break
            end = start

    for did in device_ids:
        def drop_raw():
            with sessions() as s, s.begin():
                newest = s.scalar(select(func.max(Telemetry.time)).where(Telemetry.device_id == did))
                q = delete(Telemetry).where(Telemetry.device_id == did, Telemetry.time < detail_cut)
                if newest is not None:
                    q = q.where(Telemetry.time != newest)
                return s.execute(q).rowcount
        stats["telemetry_removed"] += step(drop_raw)

    log.info("prune: %s", stats)
    return stats


def _thin_snapshots(sessions, system_id, start, end):
    """Average each minute in [start, end) that has more than one snapshot into a single row.
    Returns (rows removed, whether the range had any rows)."""
    removed = 0
    with sessions() as s, s.begin():
        rows = s.execute(select(Snapshot.time, Snapshot.data, Snapshot.mode)
                         .where(Snapshot.system_id == system_id, Snapshot.time >= start, Snapshot.time < end)
                         .order_by(Snapshot.time)).all()
        minutes = {}
        for r in rows:
            minutes.setdefault(int(as_utc(r.time).timestamp() // 60), []).append(r)
        for minute, group in minutes.items():
            if len(group) < 2:
                continue
            data = average_rows([g.data or {} for g in group])
            mid = group[len(group) // 2]
            s.execute(delete(Snapshot).where(Snapshot.system_id == system_id,
                                             Snapshot.time.in_([g.time for g in group])))
            s.add(Snapshot(system_id=system_id, time=mid.time, mode=mid.mode, data=data))
            removed += len(group) - 1
    return removed, bool(rows)
