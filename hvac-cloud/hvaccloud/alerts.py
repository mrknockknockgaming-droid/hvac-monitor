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
from .db import Account, Alert, AlertMute, AlertPrefs, ServiceInfo, Snapshot, SystemMember, User, as_utc

log = logging.getLogger("alerts")

NO_DATA = "no_data"
CLEAR_AT_ONCE = {NO_DATA}       # readings are back: no reason to wait
SLACK_S = 0.001                 # stored times are rounded to microseconds, so "now - started" can be a hair negative
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
        if a.raised_at is None and now - as_utc(a.started_at).timestamp() >= hold - SLACK_S:
            a.raised_at = ts(now)
            events.append(("raised", a))
    for key, a in open_.items():
        if key in seen:
            continue
        gone = now - as_utc(a.last_seen).timestamp()
        if a.code in CLEAR_AT_ONCE or gone >= hold - SLACK_S:
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


# Homeowner wording for owner emails. Keep in step with FLAG_INFO in web/app.js.
PLAIN = {
    "sh_low": ("Refrigerant flow needs a check", "Liquid refrigerant may be reaching the compressor, which can damage it over time.",
               "Request a service visit."),
    "sh_high": ("Refrigerant may be low or restricted", "Your system works harder and cools less.",
                "Request a service visit, and check that the outdoor unit isn't blocked."),
    "sc_low": ("Refrigerant charge may be low", "Low charge reduces cooling and usually means a leak.", "Request a service visit."),
    "sc_high": ("Refrigerant may be overcharged or restricted", "This raises pressures and strains the compressor.",
                "Request a service visit."),
    "dt_low": ("Air from your vents isn't as cool as it should be", "Your system runs longer and uses more electricity.",
               "Check your air filter and replace it if it looks grey or dusty, and make sure vents aren't blocked."),
    "ctoa_high": ("Outdoor unit isn't releasing heat well", "The compressor runs hotter and uses more electricity.",
                  "Clear debris from around the outdoor unit (about 2 ft), then request a coil cleaning."),
    "node_offline": ("A monitor isn't reporting", "Heating and cooling are not affected; some checks are paused.",
                     "Nothing to do on your own; your contractor can check it."),
    "sensor_issue": ("A monitoring sensor isn't reporting", "Heating and cooling are not affected.",
                     "Nothing to do on your own; your contractor can check it."),
    "room_hot": ("It's very hot inside", "The room has reached 90 F or more.",
                 "Check the thermostat is set to Cool, then request a service visit."),
    "room_cold": ("It's very cold inside", "The room has dropped to 50 F or less; pipes can freeze.",
                  "Check the thermostat is set to Heat, then request a service visit."),
    "setpoint_not_reached": ("Your system isn't keeping up", "It has run a long time without reaching your setting.",
                             "Check your air filter and that windows and doors are shut; if it keeps happening, request a service visit."),
    "call_mismatch": ("The thermostat and the equipment disagree", "The equipment isn't doing what the thermostat asks.",
                      "Request a service visit."),
    NO_DATA: ("We lost contact with your monitors", "No checks can run until readings come back.",
              "Check that your WiFi is working."),
}


# Homeowner level of each flag code (FLAG_INFO in web/app.js): fault = service needed,
# caution = check soon, advisory = good to know. Used to sort the contractor's fleet page.
LEVEL = {"sh_low": "fault", "sh_high": "caution", "sc_low": "caution", "sc_high": "caution", "dt_low": "caution",
         "ctoa_high": "caution", "node_offline": "advisory", "sensor_issue": "advisory", NO_DATA: "advisory",
         "room_hot": "fault", "room_cold": "fault", "setpoint_not_reached": "caution", "call_mismatch": "caution"}


def homeowner_emails(s, system_id):
    return list(s.scalars(select(User.email).join(SystemMember, SystemMember.user_id == User.id)
                          .where(SystemMember.system_id == system_id).order_by(User.email)))


def contractor_email(s, system):
    """The contractor-panel email, else the contractor account's own email."""
    info = s.get(ServiceInfo, system.id)
    if info is not None and info.email:
        return info.email
    acct = s.get(Account, system.account_id)
    return acct.email if acct is not None and acct.email else None


def wants(s, system_id):
    """(email the homeowners, email the contractor); both on unless changed."""
    prefs = s.get(AlertPrefs, system_id)
    return (True, True) if prefs is None else (prefs.email_owner, prefs.email_contractor)


def recipients(s, system):
    """[(address, "owner" | "contractor")]: the system's homeowners get plain language, its
    contractor the technical version. alert_prefs.email_owner means "the homeowners"."""
    owners_on, contractor_on = wants(s, system.id)
    out = [(e, "owner") for e in homeowner_emails(s, system.id)] if owners_on else []
    c = contractor_email(s, system) if contractor_on else None
    if c and all(c != a for a, _ in out):
        out.append((c, "contractor"))
    return out


def muted(s, system_id, code, now):
    m = s.get(AlertMute, (system_id, code))
    return m is not None and as_utc(m.until).timestamp() > now


def compose(system, a, kind, who):
    """(subject, body) for one recipient. Owners get plain words, contractors the technical text."""
    if who == "contractor":
        tag = f"{system.name} (site {system.site_id})"
        if kind == "raised":
            subject = f"[Fullscope] {tag}: {a.text}"
            lines = [f"{LEVEL_WORD.get(a.level, 'Alert')}: {a.text}.",
                     f"First seen {local(a.started_at)}, still present at {local(a.raised_at)}."]
        else:
            subject = f"[Fullscope] {tag}: cleared - {a.text}"
            lines = [f"Cleared: {a.text}.", f"Started {local(a.started_at)}, cleared {local(a.cleared_at)}."]
        return subject, "\n\n".join(lines + [f"Technician view: {settings.APP_URL}#/monitor/{system.id}"])
    title, why, todo = PLAIN.get(a.code, (a.text, "", ""))
    if kind == "raised":
        subject = f"[Fullscope] {system.name}: {title}"
        lines = [f"{title}.", why, f"What you can do: {todo}" if todo else "",
                 f"For your contractor: {a.text} (since {local(a.started_at)})."]
    else:
        subject = f"[Fullscope] {system.name}: back to normal - {title}"
        lines = [f"Back to normal: {title.lower()}.", f"Started {local(a.started_at)}, cleared {local(a.cleared_at)}."]
    return subject, "\n\n".join([x for x in lines if x] + [f"Your system: {settings.APP_URL}#/home/{system.id}"])


def emails_for(s, system, events, now, cooldown_s):
    """Email jobs for these events: (alert id or None, to, subject, body). Nothing for muted codes;
    raises are skipped when the same code was emailed within the cooldown; clears only follow an
    emailed raise that nobody acknowledged."""
    if not events:
        return []
    to = recipients(s, system)
    if not to:
        return []
    jobs = []
    for kind, a in events:
        if muted(s, system.id, a.code, now):
            continue
        if kind == "raised":
            recent = s.scalar(select(Alert.id).where(
                Alert.system_id == system.id, Alert.code == a.code, Alert.id != a.id,
                Alert.emailed_at >= ts(now - cooldown_s)).limit(1))
            if recent is not None:
                continue
        elif a.emailed_at is None or a.acked_at is not None:
            continue
        for addr, who in to:
            subject, body = compose(system, a, kind, who)
            jobs.append((a.id if kind == "raised" else None, addr, subject, body))
    return jobs


def local(t):
    return as_utc(t).astimezone().strftime("%a %b %d, %I:%M %p").replace(" 0", " ")


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
