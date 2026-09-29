"""Publish simulated outdoor + indoor node messages over MQTT, like the real firmware.

Uses the same cooling cycle as the PC dashboard's --demo mode (14 min on, 6 min off),
on its own site id so it never mixes with real nodes.

  python demo_publisher.py                    (site "demo", broker from MQTT_HOST/MQTT_PORT)
  python demo_publisher.py --site demo2 --interval 5
"""
import argparse
import json
import math
import random
import time

import paho.mqtt.client as mqtt

from hvaccloud import settings


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


def indoor(k, on, warm):
    return {"node": "indoor", "fw": "demo", "rssi": -55, "uptime": int(k * 60),
            "mode": {"Y": on, "W": False, "G": on, "OB": on},
            "air": {"supply": round(76 - 19 * warm + random.uniform(-0.3, 0.3), 1),
                    "return": round(76 + random.uniform(-0.3, 0.3), 1), "dt": None}, "err": []}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default="demo")
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between messages (firmware: 5)")
    args = ap.parse_args()

    clients = {}
    for node in ("outdoor", "indoor"):
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

    t0 = time.time()
    try:
        while True:
            k = (time.time() - t0) / 60
            on = (k % 20) < 14
            warm = min(1, ((k % 20) / 4)) if on else 0
            oat = 95 + 3 * math.sin(k / 30)
            for node, payload in (("outdoor", outdoor(k, on, warm, oat)), ("indoor", indoor(k, on, warm))):
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
