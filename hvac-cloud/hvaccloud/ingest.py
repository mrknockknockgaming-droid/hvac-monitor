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

from . import alerts, calc, display, electrical, equipment, maintenance, settings, thermostat
from .db import (Alert, Command, Device, Equipment, RuntimeDay, Snapshot, System, Telemetry, as_utc, init_db,
                 make_engine, session_factory, utcnow)
from .refrigerants import Tables

log = logging.getLogger("ingest")
ALL_NODES = calc.NODES + (electrical.NODE, thermostat.NODE)
RUN_GAP_S = 60          # longer gaps between snapshots don't count as run time (same rule as /summary)
TS_MAX_AGE_S = 24 * 3600   # a node's own reading time is trusted up to this old (firmware keeps 10 min)
TS_MAX_AHEAD_S = 60        # ... and this far in the future (clock drift)
DISPLAY_EVERY_S = 60       # the thermostat's display feed: at most this often, unless an alert changes


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
        self.publish = None           # (topic, payload) -> None, retained; run() sets it; None = no display feed
        self._display_at = {}         # system_id -> when its display feed was last published
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
        if len(parts) != 4 or parts[0] != "hvac" or parts[2] not in ALL_NODES:
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
            if kind == "telemetry" and node == thermostat.NODE:
                return self.on_thermostat(device, s, data, self.reading_time(data, now))
            if kind == "telemetry" and node == electrical.NODE:
                return self.on_electrical(s, system, device, data, self.reading_time(data, now))
            if kind == "telemetry":
                at = self.reading_time(data, now)
                snap = self.on_telemetry(s, system, device, data, at)
                events = alerts.sync(s, system, snap["flags"], at, self.hold, keep=alerts.unjudged(snap))
                jobs = alerts.emails_for(s, system, events, at, self.cooldown_s)
                show = self._display_due(s, system, events, now)
            elif kind == "status":
                return self.on_status(device, data, now)
            elif kind == "reply":
                return self.on_reply(s, device, data, now)
            else:
                return None
        self._email(jobs)          # after commit, so the alerts exist when emailed_at is written
        if show:
            self._publish_display(*show, now)
        return snap

    # ---------- the display thermostat's feed (display.py) ----------
    def _display_due(self, s, system, events, now):
        """(system_id, site) when this system's thermostat should get a fresh feed, else None."""
        if self.publish is None or self._thermostat_state(s, system, now) is None:
            return None
        if events or now - self._display_at.get(system.id, 0) >= DISPLAY_EVERY_S:
            return system.id, system.site_id
        return None

    def _publish_display(self, system_id, site, now):
        self._display_at[system_id] = now
        try:
            with self.sessions() as s:
                feed = display.build(s, s.get(System, system_id), now, self.stale)
            self.publish(f"hvac/{site}/{thermostat.NODE}/display", feed)
        except Exception:
            log.exception("display feed for %s failed", site)

    @staticmethod
    def reading_time(data, now):
        """When a reading was taken: the node's own "ts" (firmware 0.2+, UTC seconds) if it is
        plausible, so readings kept during an outage land where they belong; else arrival time."""
        v = data.get("ts")
        if isinstance(v, (int, float)) and not isinstance(v, bool) and now - TS_MAX_AGE_S <= v <= now + TS_MAX_AHEAD_S:
            return min(float(v), now)
        return now

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
        jobs, shows = [], []
        with self._lock:
            with self.sessions() as s, s.begin():
                for system in s.scalars(select(System).order_by(System.id)):
                    flags = alerts.no_data_flags(s, system, now, self.silence)
                    if flags is not None:
                        events = alerts.sync(s, system, flags, now, self.hold)
                        jobs += alerts.emails_for(s, system, events, now, self.cooldown_s)
                        if events and self.publish is not None and self._thermostat_state(s, system, now) is not None:
                            shows.append((system.id, system.site_id))
            for show in shows:
                self._publish_display(*show, now)
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
        # two readings on the same millisecond (both nodes catching up) would collide on the key
        while s.get(Snapshot, (system.id, ts(now))) is not None or s.get(Telemetry, (device.id, ts(now))) is not None:
            now += 0.001
        if device.last_seen is None or ts(now) > as_utc(device.last_seen):
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
        eq = s.get(Equipment, system.id)
        snap = equipment.apply(snap, eq)                                # nameplate targets (cloud only)
        snap = electrical.apply(snap, self._electrical_state(s, system, now), eq)
        snap = thermostat.apply(snap, self._thermostat_state(s, system, now))
        system.run_started_at = ts(run_start) if run_start else None
        s.add(Snapshot(system_id=system.id, time=ts(now), mode=snap["mode"], data=snap))
        return snap

    def on_thermostat(self, device, s, data, now):
        """Store the thermostat's report. It makes no snapshot of its own: the next indoor or
        outdoor reading (every 10 s) picks it up, with its flags, through _thermostat_state."""
        while s.get(Telemetry, (device.id, ts(now))) is not None:
            now += 0.001
        if device.last_seen is None or ts(now) > as_utc(device.last_seen):
            device.last_seen = ts(now)
        device.connected = True
        if "fw" in data:
            device.fw = data["fw"]
        s.add(Telemetry(device_id=device.id, time=ts(now), data=data))
        self.latest[(device.system_id, device.node)] = (now, data)
        return data

    def _thermostat_state(self, s, system, now):
        """None when the system has no thermostat (most don't)."""
        state = self._node_state(s, system, thermostat.NODE, now)
        return None if state["data"] is None else state

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

    def on_electrical(self, s, system, device, data, now):
        """Store the electrical module's reading; the next refrigerant snapshot uses it."""
        if s.get(Telemetry, (device.id, ts(now))) is None:
            s.add(Telemetry(device_id=device.id, time=ts(now), data=data))
        if device.last_seen is None or ts(now) > as_utc(device.last_seen):
            device.last_seen = ts(now)
        device.connected = True
        for k in ("fw", "rssi"):
            if k in data:
                setattr(device, k, data[k])
        self.latest[(system.id, electrical.NODE)] = (now, data)
        return data

    def _electrical_state(self, s, system, now):
        """None when this system has no electrical module, else its {"online", "data"}."""
        if (system.id, electrical.NODE) not in self.latest and s.scalar(
                select(Device.id).where(Device.system_id == system.id, Device.node == electrical.NODE)) is None:
            return None
        return self._node_state(s, system, electrical.NODE, now)

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
    ingest.publish = lambda topic, payload: client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=1, retain=True)
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
