"""Alerts: diagnostic flags that persist, and the emails about them.

A flag opens a pending alert the first time it shows up. The alert is raised once the flag has
held for `hold` seconds, and cleared once the flag has been gone for `hold` seconds, so a value
hovering at a limit gives one alert instead of one per reading. Pending alerts that go away
before they are raised are deleted. Raised and cleared alerts are emailed to the account.
"""
import datetime as dt
import logging
import smtplib
import ssl
from email.message import EmailMessage

from sqlalchemy import select

from . import settings
from .db import Account, Alert, Snapshot, as_utc

log = logging.getLogger("alerts")

NO_DATA = "no_data"
CLEAR_AT_ONCE = {NO_DATA}       # readings are back: no reason to wait
LEVEL_WORD = {"alert": "Fault", "warn": "Warning"}


def ts(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc)


def sync(s, system, flags, now, hold):
    """Update the system's open alerts from the current flags. Returns [("raised"|"cleared", Alert)]."""
    open_ = {(a.code, a.node): a for a in s.scalars(
        select(Alert).where(Alert.system_id == system.id, Alert.cleared_at.is_(None)))}
    events, seen = [], set()
    for f in flags:
        key = (f["code"], f.get("node"))
        seen.add(key)
        a = open_.get(key)
        if a is None:
            a = Alert(system_id=system.id, code=key[0], node=key[1], started_at=ts(now))
            s.add(a)
            open_[key] = a
        a.level, a.text, a.last_seen = f["level"], f["text"][:300], ts(now)
        if a.raised_at is None and now - as_utc(a.started_at).timestamp() >= hold:
            a.raised_at = ts(now)
            events.append(("raised", a))
    for key, a in open_.items():
        if key in seen:
            continue
        gone = now - as_utc(a.last_seen).timestamp()
        if a.code in CLEAR_AT_ONCE or gone >= hold:
            if a.raised_at is None:
                s.delete(a)
            else:
                a.cleared_at = ts(now)
                events.append(("cleared", a))
    s.flush()
    return events


def no_data_flags(s, system, now, silence):
    """Flags for a system whose nodes have gone quiet: the last snapshot's flags (they can't be
    re-checked, so they stay open) plus a no-data flag. None while readings are recent or never came."""
    row = s.execute(select(Snapshot.time, Snapshot.data).where(Snapshot.system_id == system.id)
                    .order_by(Snapshot.time.desc()).limit(1)).first()
    if row is None:
        return None
    quiet = now - as_utc(row.time).timestamp()
    if quiet < silence:
        return None
    return list((row.data or {}).get("flags") or []) + [
        {"level": "warn", "code": NO_DATA, "text": f"No readings from either node for {round(quiet / 60)} min"}]


def emails_for(s, system, events, now, cooldown_s):
    """Email jobs for these events: (alert id or None, to, subject, body). Raises are skipped when the
    same code was emailed for this system within the cooldown; clears only follow an emailed raise."""
    if not events:
        return []
    acct = s.get(Account, system.account_id)
    if acct is None or not acct.email:
        return []
    jobs = []
    for kind, a in events:
        if kind == "raised":
            recent = s.scalar(select(Alert.id).where(
                Alert.system_id == system.id, Alert.code == a.code, Alert.id != a.id,
                Alert.emailed_at >= ts(now - cooldown_s)).limit(1))
            if recent is not None:
                continue
            subject = f"[Fullscope] {system.name}: {a.text}"
            lead = f"{LEVEL_WORD.get(a.level, 'Alert')} on {system.name}: {a.text}."
            when = f"First seen {local(a.started_at)}, still present at {local(a.raised_at)}."
            jobs.append((a.id, acct.email, subject, "\n\n".join([lead, when, link(system)])))
        elif a.emailed_at is not None:
            subject = f"[Fullscope] {system.name}: cleared - {a.text}"
            lead = f"Cleared on {system.name}: {a.text}."
            when = f"Started {local(a.started_at)}, cleared {local(a.cleared_at)}."
            jobs.append((None, acct.email, subject, "\n\n".join([lead, when, link(system)])))
    return jobs


def local(t):
    return as_utc(t).astimezone().strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


def link(system):
    return f"Details: {settings.APP_URL}#/monitor/{system.id}"


class Mailer:
    """Sends plain-text email through SMTP_HOST. Disabled (send returns False) when it is not set."""

    def __init__(self, host=None, port=None, user=None, password=None, sender=None):
        self.host = settings.SMTP_HOST if host is None else host
        self.port = port or settings.SMTP_PORT
        self.user = settings.SMTP_USER if user is None else user
        self.password = settings.SMTP_PASS if password is None else password
        self.sender = sender or settings.SMTP_FROM or self.user

    @property
    def enabled(self):
        return bool(self.host and self.sender)

    def send(self, to, subject, body):
        if not self.enabled:
            log.info("email off (no SMTP_HOST): %s", subject)
            return False
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(body)
        ctx = ssl.create_default_context()
        try:
            if self.port == 465:
                smtp = smtplib.SMTP_SSL(self.host, self.port, context=ctx, timeout=20)
            else:
                smtp = smtplib.SMTP(self.host, self.port, timeout=20)
                smtp.starttls(context=ctx)
            with smtp:
                if self.user:
                    smtp.login(self.user, self.password)
                smtp.send_message(msg)
        except (OSError, smtplib.SMTPException) as e:
            log.warning("email to %s failed: %s", to, e)
            return False
        log.info("emailed %s: %s", to, subject)
        return True
