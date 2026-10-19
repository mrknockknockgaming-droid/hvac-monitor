"""`manage.py check`: is the running system doing everything it should? Run it on the server.

One line per finding, OK / WARN / FAIL, from what the database shows: each node heard from recently,
fresh readings and history, alerts and whether they were emailed or sent to phones, remote commands
answered by the nodes, a compressor call seen lately (the diagnostics only run during one), email and
notification setup, the nightly backup, and the HTTPS cookie setting. Any FAIL makes the exit code 1.
"""
import datetime as dt
import os
import time

from sqlalchemy import func, select

from . import maintenance, settings
from .db import Alert, Command, Device, PushSubscription, ServerKey, Snapshot, System, User, as_utc

NODE_LATE_S = 120            # a node reports every few seconds; two minutes of silence is a problem
READING_LATE_S = 300
CALL_DAYS = 7
BACKUP_LATE_H = 36


def ago(seconds):
    if seconds is None:
        return "never"
    if seconds < 90:
        return f"{seconds:.0f} s ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min ago"
    if seconds < 2 * 86400:
        return f"{seconds / 3600:.1f} h ago"
    return f"{seconds / 86400:.0f} days ago"


def age(t, now):
    return now - as_utc(t).timestamp() if t else None


def run(s, now=None, email_on=None):
    """[(level, text)]; level is OK, WARN or FAIL."""
    now = time.time() if now is None else now
    out = []
    systems = list(s.scalars(select(System).order_by(System.id)))
    if not systems:
        out.append(("FAIL", "no systems registered (manage.py create-system)"))
    for sy in systems:
        tag = f"{sy.name} (site {sy.site_id})"
        devices = list(s.scalars(select(Device).where(Device.system_id == sy.id).order_by(Device.node)))
        if not devices:
            out.append(("FAIL", f"{tag}: no node has reported yet"))
        for d in devices:
            a = age(d.last_seen, now)
            level = "OK" if a is not None and a <= NODE_LATE_S else "FAIL"
            out.append((level, f"{tag}: {d.node} node last heard {ago(a)}" + (f", firmware {d.fw}" if d.fw else "")
                        + (f", WiFi {d.rssi} dBm" if d.rssi is not None else "")))
        latest = s.scalar(select(func.max(Snapshot.time)).where(Snapshot.system_id == sy.id))
        a = age(latest, now)
        day = s.scalar(select(func.count()).select_from(Snapshot).where(
            Snapshot.system_id == sy.id, Snapshot.time >= dt.datetime.fromtimestamp(now - 86400, dt.timezone.utc)))
        oldest = s.scalar(select(func.min(Snapshot.time)).where(Snapshot.system_id == sy.id))
        out.append(("OK" if a is not None and a <= READING_LATE_S else "FAIL",
                    f"{tag}: newest reading {ago(a)}, {day} in the last 24 h, history since "
                    + (as_utc(oldest).strftime("%Y-%m-%d") if oldest else "-")))
        calls = s.scalar(select(func.count()).select_from(Snapshot).where(
            Snapshot.system_id == sy.id, Snapshot.mode.in_(("cooling", "heating")),
            Snapshot.time >= dt.datetime.fromtimestamp(now - CALL_DAYS * 86400, dt.timezone.utc)))
        out.append(("OK" if calls else "WARN",
                    f"{tag}: compressor running in {calls} readings over {CALL_DAYS} days" if calls else
                    f"{tag}: no cooling or heating call seen in {CALL_DAYS} days; superheat, subcooling and delta-T only "
                    "run during a call, so check the Y (and O/B) wires if the system has run"))
        open_alerts = list(s.scalars(select(Alert).where(Alert.system_id == sy.id, Alert.raised_at.is_not(None),
                                                         Alert.cleared_at.is_(None)).order_by(Alert.raised_at)))
        out.append(("OK" if not open_alerts else "WARN", f"{tag}: {len(open_alerts)} open alert(s)" +
                    "".join(f"; {x.code} since {ago(age(x.raised_at, now))}" for x in open_alerts[:3])))
        last = s.scalar(select(Alert).where(Alert.system_id == sy.id, Alert.raised_at.is_not(None))
                        .order_by(Alert.raised_at.desc()).limit(1))
        if last is not None:
            how = [w for w, t in (("emailed", last.emailed_at), ("sent to phones", last.pushed_at)) if t]
            level = "OK" if how or not email_on else "WARN"
            out.append((level, f"{tag}: last alert {last.code} raised {ago(age(last.raised_at, now))}, "
                        + (" and ".join(how) if how else "not emailed or sent to phones")))
        recent = s.scalars(select(Command).join(Device, Device.id == Command.device_id).where(Device.system_id == sy.id)
                           .order_by(Command.created_at.desc()).limit(20))
        cmd = next((c for c in recent if c.payload is not None), None)        # not replies nobody asked for
        if cmd is None:
            out.append(("WARN", f"{tag}: no command sent to a node yet; press Refresh on Sensors & calibration to test it"))
        else:
            node = s.get(Device, cmd.device_id).node
            c_age = age(cmd.created_at, now)
            if cmd.replied_at is not None:
                out.append(("OK", f"{tag}: last command to the {node} node ({ago(c_age)}) was answered"))
            else:
                out.append(("WARN" if c_age is not None and c_age < 60 else "FAIL",
                            f"{tag}: last command to the {node} node ({ago(c_age)}) has no reply"
                            + ("" if cmd.sent else "; it never reached the broker")))
    if email_on is not None:
        out.append(("OK" if email_on else "WARN", "alert email is on" if email_on else "alert email is off (SMTP_* not set)"))
    keys = s.scalar(select(func.count()).select_from(ServerKey).where(ServerKey.name == "vapid_public"))
    subs = s.scalar(select(func.count()).select_from(PushSubscription))
    out.append(("OK", f"phone notifications: {subs} device(s) turned on" if keys else
                "phone notifications: not set up yet (turn them on from the Account page)"))
    users = s.scalar(select(func.count()).select_from(User))
    tfa = s.scalar(select(func.count()).select_from(User).where(User.totp_on.is_not(None)))
    out.append(("OK" if tfa or users == 0 else "WARN", f"two-step sign-in on for {tfa} of {users} user(s)"))
    if maintenance.sqlite_path(settings.DATABASE_URL):
        files = maintenance.backups(settings.BACKUP_DIR)
        if not files:
            out.append(("WARN", f"no nightly database backup in {settings.BACKUP_DIR} yet"))
        else:
            b_age = now - os.path.getmtime(files[-1])
            out.append(("OK" if b_age <= BACKUP_LATE_H * 3600 else "WARN",
                        f"newest nightly database backup {ago(b_age)} ({len(files)} kept)"))
    if settings.APP_URL.startswith("https://") and not settings.COOKIE_SECURE:
        out.append(("FAIL", "APP_URL is https but COOKIE_SECURE is off"))
    return out
