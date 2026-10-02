"""MQTT ingest worker: stores node messages and derived snapshots for every known system.

Listens on hvac/<site>/<node>/{telemetry,status,reply}, the topics the firmware already
uses. <site> must match a system's site_id (create it with manage.py); messages from
unknown sites are ignored.

Run:  python -m hvaccloud.ingest
"""
import datetime as dt
import json
import logging
import random
import time

from sqlalchemy import select

from . import calc, settings
from .db import Command, Device, Snapshot, System, Telemetry, as_utc, init_db, make_engine, session_factory
from .refrigerants import Tables

log = logging.getLogger("ingest")


def ts(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc)


class Ingest:
    def __init__(self, sessions, tables=None, stale=settings.STALE_SECONDS):
        self.sessions = sessions
        self.tables = tables or Tables(log.info)
        self.stale = stale
        self.latest = {}              # (system_id, node) -> (epoch, data)
        self._unknown_sites = set()

    # ---------- routing ----------
    def handle(self, topic, payload, now=None):
        """Process one MQTT message. Returns what was stored, for logging and tests."""
        now = time.time() if now is None else now
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
                return self.on_telemetry(s, system, device, data, now)
            if kind == "status":
                return self.on_status(device, data, now)
            if kind == "reply":
                return self.on_reply(s, device, data, now)
        return None

    @staticmethod
    def _device(s, system, node):
        d = s.scalar(select(Device).where(Device.system_id == system.id, Device.node == node))
        if d is None:
            d = Device(system_id=system.id, node=node)
            s.add(d)
            s.flush()
            log.info("new device: %s/%s", system.site_id, node)
        return d

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
        last = as_utc(s.scalar(select(Snapshot.time).where(Snapshot.system_id == system.id)
                               .order_by(Snapshot.time.desc()).limit(1)))
        if last is None or now - last.timestamp() > self.stale:
            run_start = None   # after an outage the compressor call can't be assumed to have continued
        snap, run_start = calc.compute(system.calc_config(), nodes,
                                       run_start.timestamp() if run_start else None, now, self.tables)
        system.run_started_at = ts(run_start) if run_start else None
        s.add(Snapshot(system_id=system.id, time=ts(now), mode=snap["mode"], data=snap))
        return snap

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
    ingest = Ingest(session_factory(engine))

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
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    run()
