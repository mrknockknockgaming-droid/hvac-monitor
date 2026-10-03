"""MQTT ingest worker: stores node messages and derived snapshots for every known system.

Listens on hvac/<site>/<node>/{telemetry,status,reply}, the topics the firmware already
uses. <site> must match a system's site_id (create it with manage.py); messages from
unknown sites are ignored. Every message also updates the system's alerts (alerts.py), and once a
minute systems whose nodes have gone quiet get a "no data" alert. Once a night the SQLite
database is backed up and old data thinned (maintenance.py).

Run:  python -m hvaccloud.ingest
"""
import datetime as dt
import json
import logging
import random
import threading
import time

from sqlalchemy import select

from . import alerts, calc, equipment, maintenance, settings
from .db import (Alert, Command, Device, Equipment, RuntimeDay, Snapshot, System, Telemetry, as_utc, init_db,
                 make_engine, session_factory, utcnow)
from .refrigerants import Tables

log = logging.getLogger("ingest")
RUN_GAP_S = 60          # longer gaps between snapshots don't count as run time (same rule as /summary)


def ts(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc)


class Ingest:
    def __init__(self, sessions, tables=None, stale=settings.STALE_SECONDS, hold=settings.ALERT_HOLD_SECONDS,
                 silence=settings.ALERT_NO_DATA_SECONDS, mailer=None,
                 cooldown_s=settings.ALERT_EMAIL_COOLDOWN_HOURS * 3600):
        self.sessions = sessions
        self.tables = tables or Tables(log.info)
        self.stale = stale
        self.hold, self.silence, self.cooldown_s = hold, silence, cooldown_s
        self.mailer = mailer          # None = no email (tests); run() passes alerts.Mailer()
        self.latest = {}              # (system_id, node) -> (epoch, data)
        self._unknown_sites = set()
        self._lock = threading.Lock()   # MQTT thread (handle) vs. the once-a-minute sweep

    # ---------- routing ----------
    def handle(self, topic, payload, now=None):
        """Process one MQTT message. Returns what was stored, for logging and tests."""
        now = time.time() if now is None else now
        with self._lock:
            return self._handle(topic, payload, now)

    def _handle(self, topic, payload, now):
        parts = topic.split("/")
        if len(parts) != 4 or parts[0] != "hvac" or parts[2] not in calc.NODES:
            return None
        site, node, kind = parts[1], parts[2], parts[3]
        try:
            data = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            log.warning("bad JSON on %s", topic)
            return None
        if not isinstance(data, dict):
            return None
        with self.sessions() as s, s.begin():
            system = s.scalar(select(System).where(System.site_id == site))
            if system is None:
                if site not in self._unknown_sites:
                    self._unknown_sites.add(site)
                    log.info("ignoring site %r: no system registered for it", site)
                return None
            device = self._device(s, system, node)
            if kind == "telemetry":
                snap = self.on_telemetry(s, system, device, data, now)
                events = alerts.sync(s, system, snap["flags"], now, self.hold)
                jobs = alerts.emails_for(s, system, events, now, self.cooldown_s)
            elif kind == "status":
                return self.on_status(device, data, now)
            elif kind == "reply":
                return self.on_reply(s, device, data, now)
            else:
                return None
        self._email(jobs)          # after commit, so the alerts exist when emailed_at is written
        return snap

    @staticmethod
    def _device(s, system, node):
        d = s.scalar(select(Device).where(Device.system_id == system.id, Device.node == node))
        if d is None:
            d = Device(system_id=system.id, node=node)
            s.add(d)
            s.flush()
            log.info("new device: %s/%s", system.site_id, node)
        return d

    # ---------- alerts ----------
    def sweep(self, now=None):
        """Raise (or keep open) "no data" alerts for systems whose nodes have gone quiet."""
        now = time.time() if now is None else now
        jobs = []
        with self._lock, self.sessions() as s, s.begin():
            for system in s.scalars(select(System).order_by(System.id)):
                flags = alerts.no_data_flags(s, system, now, self.silence)
                if flags is not None:
                    events = alerts.sync(s, system, flags, now, self.hold)
                    jobs += alerts.emails_for(s, system, events, now, self.cooldown_s)
        self._email(jobs)

    def _email(self, jobs):
        if jobs and self.mailer is not None:
            self.spawn(self._send, jobs)

    @staticmethod
    def spawn(fn, *args):
        """SMTP can take seconds; don't hold up MQTT messages. Tests replace this to run inline."""
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _send(self, jobs):
        for alert_id, to, subject, body in jobs:
            if self.mailer.send(to, subject, body) and alert_id is not None:
                with self.sessions() as s, s.begin():
                    a = s.get(Alert, alert_id)
                    if a is not None:
                        a.emailed_at = utcnow()

    # ---------- message kinds ----------
    def on_telemetry(self, s, system, device, data, now):
        device.last_seen = ts(now)
        device.connected = True
        for k in ("fw", "rssi"):
            if k in data:
                setattr(device, k, data[k])
        s.add(Telemetry(device_id=device.id, time=ts(now), data=data))
        self.latest[(system.id, device.node)] = (now, data)

        nodes = {n: self._node_state(s, system, n, now) for n in calc.NODES}
        run_start = as_utc(system.run_started_at)
        prev = s.execute(select(Snapshot.time, Snapshot.mode).where(Snapshot.system_id == system.id)
                         .order_by(Snapshot.time.desc()).limit(1)).first()
        last = as_utc(prev.time) if prev else None
        if prev is not None:
            self._add_runtime(s, system.id, prev.mode, now - last.timestamp(), now)
        if last is None or now - last.timestamp() > self.stale:
            run_start = None   # after an outage the compressor call can't be assumed to have continued
        snap, run_start = calc.compute(system.calc_config(), nodes,
                                       run_start.timestamp() if run_start else None, now, self.tables)
        snap = equipment.apply(snap, s.get(Equipment, system.id))     # nameplate targets (cloud only)
        system.run_started_at = ts(run_start) if run_start else None
        s.add(Snapshot(system_id=system.id, time=ts(now), mode=snap["mode"], data=snap))
        return snap

    @staticmethod
    def _add_runtime(s, system_id, prev_mode, gap, now):
        """Credit the time since the previous snapshot to its mode; a gap over RUN_GAP_S is an outage."""
        if prev_mode == "idle" or not 0 < gap <= RUN_GAP_S:
            return
        day = ts(now).date()
        row = s.get(RuntimeDay, (system_id, day))
        if row is None:
            row = RuntimeDay(system_id=system_id, day=day, blower_s=0.0, compressor_s=0.0)
            s.add(row)
        row.blower_s += gap
        if prev_mode in ("cooling", "heating"):
            row.compressor_s += gap

    def _node_state(self, s, system, node, now):
        e = self.latest.get((system.id, node))
        if e is None:   # after a restart, fall back to the stored reading
            row = s.execute(select(Telemetry.time, Telemetry.data).join(Device)
                            .where(Device.system_id == system.id, Device.node == node)
                            .order_by(Telemetry.time.desc()).limit(1)).first()
            if row:
                e = (as_utc(row.time).timestamp(), row.data)
                self.latest[(system.id, node)] = e
        if e is None:
            return {"online": False, "data": None}
        return {"online": now - e[0] <= self.stale, "data": e[1]}

    @staticmethod
    def on_status(device, data, now):
        device.connected = bool(data.get("online"))
        device.last_status = data
        if device.connected:
            device.last_seen = ts(now)
            for k in ("fw", "ip", "mac", "rssi"):
                if k in data:
                    setattr(device, k, data[k])
        return data

    @staticmethod
    def on_reply(s, device, data, now):
        """Attach the reply to the newest sent, unanswered command with the same verb.
        Nodes answer within a second, so an older unanswered one was lost, not queued."""
        q = (select(Command).where(Command.device_id == device.id, Command.replied_at.is_(None),
                                   Command.payload.is_not(None), Command.sent.is_(True))
             .order_by(Command.id.desc()))
        cmd = next((c for c in s.scalars(q) if (c.payload or {}).get("cmd") == data.get("cmd")), None)
        if cmd is None:
            cmd = Command(device_id=device.id, payload=None, sent=False)
            s.add(cmd)
        cmd.reply = data
        cmd.replied_at = ts(now)
        return data


# ---------- MQTT loop ----------
def run():
    import paho.mqtt.client as mqtt
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    engine = make_engine()
    init_db(engine)
    mailer = alerts.Mailer()
    ingest = Ingest(session_factory(engine), mailer=mailer)
    log.info("alert email %s", f"on via {mailer.host}" if mailer.enabled else "off (set SMTP_HOST in .env)")

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            log.info("connected to %s:%s", settings.MQTT_HOST, settings.MQTT_PORT)
            for kind in ("telemetry", "status", "reply"):
                client.subscribe(f"hvac/+/+/{kind}", qos=1)
        else:
            log.warning("connect refused: %s", reason_code)

    def on_message(client, userdata, msg):
        try:
            ingest.handle(msg.topic, msg.payload)
        except Exception:
            log.exception("failed to store %s", msg.topic)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"hvac-cloud-ingest-{random.randint(1000, 9999)}")
    if settings.MQTT_USER:
        client.username_pw_set(settings.MQTT_USER, settings.MQTT_PASS)
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(1, 30)
    client.connect_async(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=30)
    client.loop_start()
    while True:
        time.sleep(60)
        try:
            ingest.sweep()
        except Exception:
            log.exception("alert sweep failed")
        if maintenance.sqlite_path(settings.DATABASE_URL) and maintenance.nightly_due():
            try:
                maintenance.backup()
                maintenance.prune(ingest.sessions, lock=ingest._lock)
            except Exception:
                log.exception("nightly backup / prune failed")


if __name__ == "__main__":
    run()
