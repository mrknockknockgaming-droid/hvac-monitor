"""Publish simulated outdoor + indoor node messages over MQTT, like the real firmware.

Uses the same cooling cycle as the PC dashboard's --demo mode (14 min on, 6 min off),
on its own site id so it never mixes with real nodes.

With --thermostat the site also gets a display thermostat: a simulated house whose room
temperature drifts toward the outdoor temperature, cooled or heated when the thermostat's
outputs say so. The thermostat runs hvaccloud.thermostat.Controller (the firmware reference)
and follows the config the cloud publishes (retained) on hvac/<site>/thermostat/config, so
changing the setpoint or schedule in the web app changes what the equipment does.

  python demo_publisher.py                    (site "demo", broker from MQTT_HOST/MQTT_PORT)
  python demo_publisher.py --site demo2 --interval 5
  python demo_publisher.py --thermostat
"""
import argparse
import json
import math
import random
import time

import paho.mqtt.client as mqtt

from hvaccloud import settings, thermostat


class House:
    """Room temperature: loses toward outdoors over ~5 h; the equipment moves it about 0.35 F/min."""
    LOSS_S, COOL_F_S, HEAT_F_S, AUX_F_S = 5 * 3600, 0.35 / 60, 0.30 / 60, 0.25 / 60

    def __init__(self, room=77.0):
        self.room = room

    def step(self, dt, oat, out, warm):
        self.room += dt * (oat - self.room) / self.LOSS_S
        if out["Y"]:
            cooling = out["OB"]                      # O energized in cooling (the default config)
            self.room += dt * warm * (-self.COOL_F_S if cooling else self.HEAT_F_S)
        if out["W"]:
            self.room += dt * self.AUX_F_S
        return round(self.room + random.uniform(-0.05, 0.05), 2)


class Thermostat:
    """The display thermostat: the reference controller plus the cloud's retained config."""

    def __init__(self, client, base, local_tz):
        self.ctrl, self.ver, self.base, self.client, self.local_tz = thermostat.Controller(), 0, base, client, local_tz
        client.on_message = self.on_config
        client.subscribe(base + "config", qos=1)

    def on_config(self, client, userdata, msg):
        try:
            cfg = json.loads(msg.payload)
        except ValueError:
            return
        if isinstance(cfg, dict) and isinstance(cfg.get("ver"), int):
            self.ctrl.configure(cfg.get("settings"), cfg.get("tech"))
            self.local_tz = (cfg.get("tech") or {}).get("tz", self.local_tz)
            self.ver = cfg["ver"]
            print(f"thermostat: config version {self.ver} ({self.ctrl.settings['mode']}, "
                  f"{self.ctrl.settings['heat_sp']:g}/{self.ctrl.settings['cool_sp']:g} F)")

    def step(self, room, oat, now, uptime):
        rep = self.ctrl.step(room, oat, now, thermostat.local_now({"tz": self.local_tz}, now))
        rep.update(node="thermostat", fw="demo", uptime=int(uptime), cfg_ver=self.ver, err=[], ts=round(now, 1))
        rep["room"]["rh"] = 45.0
        self.client.publish(self.base + "telemetry", json.dumps(rep))
        return rep


def outdoor(k, on, warm, oat):
    p_vap = 120 + random.uniform(-1, 1) if on else 300
    p_liq = 345 + 15 * warm + random.uniform(-2, 2) if on else 300
    return {"node": "outdoor", "fw": "demo", "rssi": -61, "uptime": int(k * 60),
            "mode": {"Y": on, "OB": on},
            "p": {"liq": round(p_liq, 1), "vap": round(p_vap, 1), "tsuc": None},
            "t": {"suc": round(52 + random.uniform(-0.5, 0.5), 1) if on else round(oat - 5, 1),
                  "liq": round(oat + 6 + random.uniform(-0.5, 0.5), 1) if on else round(oat - 2, 1),
                  "tsuc": None, "dis": None},
            "air": {"t": round(oat, 1), "rh": 12.0}, "v5": 5.02, "err": []}


def indoor(k, on, warm, room=76.0, out=None):
    out = out or {"Y": on, "W": False, "G": on, "OB": on}
    return {"node": "indoor", "fw": "demo", "rssi": -55, "uptime": int(k * 60),
            "mode": dict(out),
            "air": {"supply": round(room - 19 * warm + random.uniform(-0.3, 0.3), 1) if out["G"] else round(room + 1, 1),
                    "return": round(room + random.uniform(-0.3, 0.3), 1), "dt": None}, "err": []}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default="demo")
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between messages (firmware: 5)")
    ap.add_argument("--thermostat", action="store_true", help="add a display thermostat driving the equipment")
    args = ap.parse_args()

    clients = {}
    for node in ("outdoor", "indoor") + (("thermostat",) if args.thermostat else ()):
        base = f"hvac/{args.site}/{node}/"
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"hvac-demo-{node}-{random.randint(1000, 9999)}")
        if settings.MQTT_USER:
            c.username_pw_set(settings.MQTT_USER, settings.MQTT_PASS)
        c.will_set(base + "status", json.dumps({"online": False}), qos=1, retain=True)
        c.connect(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=30)
        c.loop_start()
        c.publish(base + "status", json.dumps({"online": True, "node": node, "fw": "demo",
                                               "ip": "0.0.0.0", "rssi": -60}), qos=1, retain=True)
        clients[node] = (c, base)
    print(f"publishing site {args.site!r} to {settings.MQTT_HOST}:{settings.MQTT_PORT} every {args.interval}s "
          f"(Ctrl+C to stop)")

    t0 = last = time.time()
    tstat = Thermostat(*clients["thermostat"], thermostat.DEFAULT_TECH["tz"]) if args.thermostat else None
    house, on_since = House(), None
    try:
        while True:
            now = time.time()
            k = (now - t0) / 60
            oat = 95 + 3 * math.sin(k / 30)
            if tstat:
                rep = tstat.step(house.room, oat, now, now - t0)
                out = rep["out"]
                on = out["Y"]
                on_since = (on_since or now) if on else None
                warm = min(1, (now - on_since) / 240) if on else 0      # coil takes ~4 min to settle
                house.step(now - last, oat, out, warm)
                payloads = (("outdoor", outdoor(k, on, warm, oat)), ("indoor", indoor(k, on, warm, house.room, out)))
            else:
                on = (k % 20) < 14
                warm = min(1, ((k % 20) / 4)) if on else 0
                payloads = (("outdoor", outdoor(k, on, warm, oat)), ("indoor", indoor(k, on, warm)))
            last = now
            for node, payload in payloads:
                c, base = clients[node]
                c.publish(base + "telemetry", json.dumps(payload))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        for c, base in clients.values():
            c.publish(base + "status", json.dumps({"online": False}), qos=1, retain=True).wait_for_publish(2)
            c.loop_stop()
            c.disconnect()


if __name__ == "__main__":
    main()
