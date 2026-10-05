"""HVAC Monitor — PC server and web dashboard.

Subscribes to the ESP32 nodes over MQTT, stores readings in SQLite, calculates
saturation temperatures, superheat, subcooling and delta-T, and serves a live
dashboard at http://localhost:8080

Run:   python server.py            (normal, needs Mosquitto running)
       python server.py --demo     (simulated data, no hardware needed)
"""
import json, os, sys, time, math, random, sqlite3, threading, queue, csv, io, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from refrigerants import Tables

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
DB_PATH = os.path.join(HERE, "hvac_data.db")
WEB_DIR = os.path.join(HERE, "web")

DEFAULTS = {
    "mqtt_host": "localhost", "mqtt_port": 1883, "mqtt_user": "", "mqtt_pass": "",
    "site": "home", "web_port": 8080,
    "refrigerant": "R-410A",
    "heat_pump": True,
    "ob_energized": "cool",     # "cool" = O terminal (most brands), "heat" = B terminal
    "atm_psia": 14.7,           # local atmospheric pressure; ~14.0 at 1,200 ft elevation
    "stale_seconds": 30,
    "keep_days": 90,
}

EDITABLE = {"refrigerant", "heat_pump", "ob_energized", "atm_psia"}


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            cfg.update(json.load(f))
    else:
        save_config(cfg)
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def r1(v):
    return None if v is None else round(v, 1)


class Hub:
    """Holds live state, does the calculations, stores history, pushes updates to browsers."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.tables = Tables(log)
        self.latest = {}        # node -> {"ts":..., "data":{...}}
        self.status = {}        # node -> status json
        self.replies = []       # recent command replies
        self.derived = {}
        self.run_start = None   # when the compressor call started
        self.clients = []       # SSE queues
        self.mqtt = None
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS telemetry (ts REAL, node TEXT, data TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS snapshots (ts REAL, data TEXT)")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_snap_ts ON snapshots(ts)")
        self.db.execute("CREATE INDEX IF NOT EXISTS ix_tel_ts ON telemetry(ts)")
        self.db.commit()
        self._last_prune = 0

    # ---------- incoming data ----------
    def on_telemetry(self, node, data):
        now = time.time()
        # Firmware 0.2+ stamps readings ("ts") and sends the ones kept during an outage on reconnect:
        # store those at their own time (if plausible) so history has no burst; "online" uses arrival.
        ts = data.get("ts")
        at = min(float(ts), now) if isinstance(ts, (int, float)) and not isinstance(ts, bool) \
            and now - 86400 <= ts <= now + 60 else now
        with self.lock:
            self.latest[node] = {"ts": now, "data": data}
            self.db.execute("INSERT INTO telemetry VALUES (?,?,?)", (at, node, json.dumps(data)))
            snap = self.compute(now)
            self.derived = snap
            self.db.execute("INSERT INTO snapshots VALUES (?,?)", (at, json.dumps(snap)))
            self.db.commit()
            self._prune(now)
        self.push({"type": "state", "state": self.state()})

    def on_status(self, node, data):
        with self.lock:
            self.status[node] = data
        self.push({"type": "state", "state": self.state()})

    def on_reply(self, node, data):
        entry = {"ts": time.time(), "node": node, "reply": data}
        with self.lock:
            self.replies = (self.replies + [entry])[-30:]
        self.push({"type": "reply", "entry": entry})

    def _prune(self, now):
        if now - self._last_prune < 3600:
            return
        self._last_prune = now
        cutoff = now - self.cfg["keep_days"] * 86400
        self.db.execute("DELETE FROM telemetry WHERE ts < ?", (cutoff,))
        self.db.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))

    # ---------- calculations ----------
    def _fresh(self, node, now):
        e = self.latest.get(node)
        if not e or now - e["ts"] > self.cfg["stale_seconds"]:
            return None
        return e["data"]

    def compute(self, now):
        cfg, ref, T = self.cfg, self.cfg["refrigerant"], self.tables
        od = self._fresh("outdoor", now) or {}
        ind = self._fresh("indoor", now) or {}
        p, t = od.get("p", {}), od.get("t", {})
        air_o, air_i = od.get("air", {}), ind.get("air", {})
        om, im = od.get("mode", {}), ind.get("mode", {})

        def call(k):
            if k in om: return bool(om[k])
            if k in im: return bool(im[k])
            return False
        Y, W, G, OB = call("Y"), call("W"), call("G"), call("OB")

        heating_rv = cfg["heat_pump"] and (OB == (cfg["ob_energized"] == "heat"))
        if Y and heating_rv:
            mode = "heating"
        elif Y:
            mode = "cooling"
        elif W:
            mode = "heat_aux"
        elif G:
            mode = "fan"
        else:
            mode = "idle"

        if mode in ("cooling", "heating"):
            if self.run_start is None:
                self.run_start = now
        else:
            self.run_start = None
        run_min = (now - self.run_start) / 60 if self.run_start else 0

        atm = cfg["atm_psia"]
        abs_ = lambda g: None if g is None else g + atm
        p_liq, p_vap, p_tsuc = num(p.get("liq")), num(p.get("vap")), num(p.get("tsuc"))
        t_suc, t_liq, t_tsuc, t_dis = num(t.get("suc")), num(t.get("liq")), num(t.get("tsuc")), num(t.get("dis"))
        oat, orh = num(air_o.get("t")), num(air_o.get("rh"))
        t_sup, t_ret = num(air_i.get("supply")), num(air_i.get("return"))

        s = {"mode": mode, "run_min": round(run_min, 1), "Y": Y, "W": W, "G": G, "OB": OB,
             "p_liq": p_liq, "p_vap": p_vap, "p_tsuc": p_tsuc,
             "t_suc": t_suc, "t_liq": t_liq, "t_tsuc": t_tsuc, "t_dis": t_dis,
             "oat": oat, "orh": orh, "t_sup": t_sup, "t_ret": t_ret,
             "dt": r1(t_ret - t_sup) if t_ret is not None and t_sup is not None else None,
             "p_low": None, "p_high": None, "sat_low": None, "sat_high": None,
             "sh": None, "sc": None, "ctoa": None, "approach": None, "notes": []}

        if mode == "heating":
            low_p, low_t = p_tsuc, t_tsuc
            high_p = p_vap if p_vap is not None else p_liq
            if p_tsuc is None:
                s["notes"].append("Heating-mode superheat needs the true-suction transducer and thermistor.")
        else:
            low_p, low_t, high_p = p_vap, t_suc, p_liq

        s["p_low"], s["p_high"] = low_p, high_p
        s["sat_low"] = r1(T.dew_f(ref, abs_(low_p)))
        s["sat_high"] = r1(T.bubble_f(ref, abs_(high_p)))
        sat_liq = T.bubble_f(ref, abs_(p_liq))

        if mode in ("cooling", "heating"):
            if s["sat_low"] is not None and low_t is not None:
                s["sh"] = r1(low_t - s["sat_low"])
            if sat_liq is not None and t_liq is not None:
                s["sc"] = r1(sat_liq - t_liq)
            if mode == "cooling" and oat is not None:
                if s["sat_high"] is not None:
                    s["ctoa"] = r1(s["sat_high"] - oat)
                if t_liq is not None:
                    s["approach"] = r1(t_liq - oat)
        s["flags"] = self.flags(s)
        return s

    def flags(self, s):
        """Conservative first-pass checks. Only judged after 10 minutes of steady running."""
        f = []
        now = time.time()
        for node in ("outdoor", "indoor"):
            e = self.latest.get(node)
            if not e or now - e["ts"] > self.cfg["stale_seconds"]:
                f.append({"level": "warn", "code": "node_offline", "node": node, "text": f"{node.title()} node is offline"})
            elif e["data"].get("err"):
                f.append({"level": "warn", "code": "sensor_issue", "node": node, "text": f"{node.title()} sensor issue: {', '.join(e['data']['err'])}"})
        if s["mode"] not in ("cooling", "heating") or s["run_min"] < 10:
            return f
        sh, sc, dt = s["sh"], s["sc"], s["dt"]
        if sh is not None and sh < 3:
            f.append({"level": "alert", "code": "sh_low", "text": f"Superheat {sh}°F: risk of liquid floodback"})
        if sh is not None and sh > 30:
            f.append({"level": "warn", "code": "sh_high", "text": f"Superheat {sh}°F is high: possible undercharge or restriction"})
        if sc is not None and sc < 3:
            f.append({"level": "warn", "code": "sc_low", "text": f"Subcooling {sc}°F is low: possible undercharge"})
        if sc is not None and sc > 20:
            f.append({"level": "warn", "code": "sc_high", "text": f"Subcooling {sc}°F is high: possible overcharge or restriction"})
        if s["mode"] == "cooling":
            if dt is not None and s["G"] and dt < 12:
                f.append({"level": "warn", "code": "dt_low", "text": f"Air delta-T {dt}°F is low"})
            if s["ctoa"] is not None and s["ctoa"] > 35:
                f.append({"level": "warn", "code": "ctoa_high", "text": f"Condensing {s['ctoa']}°F over ambient: check condenser coil and fan"})
        return f

    # ---------- outputs ----------
    def state(self):
        now = time.time()
        nodes = {}
        for node in ("outdoor", "indoor"):
            e = self.latest.get(node)
            nodes[node] = {
                "online": bool(e and now - e["ts"] <= self.cfg["stale_seconds"]),
                "age": round(now - e["ts"], 1) if e else None,
                "data": e["data"] if e else None,
                "status": self.status.get(node),
            }
        return {"ts": now, "nodes": nodes, "derived": self.derived,
                "config": {k: self.cfg[k] for k in EDITABLE},
                "refrigerants": self.tables.available(), "pt_error": self.tables.error,
                "replies": self.replies[-10:], "demo": self.mqtt is None}

    def history(self, minutes):
        since = time.time() - minutes * 60
        with self.lock:
            rows = self.db.execute("SELECT ts, data FROM snapshots WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
        keys = ["p_low", "p_high", "sat_low", "sat_high", "t_suc", "t_liq", "sh", "sc", "oat", "t_sup", "t_ret", "dt"]
        bucket = max(1, int(minutes * 60 / 600))     # at most ~600 points per series
        out, acc, cur = [], {}, None
        for ts, d in rows:
            d = json.loads(d)
            b = int(ts // bucket)
            if cur is not None and b != cur:
                out.append(self._avg(cur * bucket, acc))
                acc = {}
            cur = b
            for k in keys:
                v = num(d.get(k))
                if v is not None:
                    acc.setdefault(k, []).append(v)
            acc.setdefault("_on", []).append(1 if d.get("mode") in ("cooling", "heating") else 0)
        if acc:
            out.append(self._avg(cur * bucket, acc))
        return out

    @staticmethod
    def _avg(ts, acc):
        row = {"ts": ts}
        for k, vals in acc.items():
            row[k] = round(sum(vals) / len(vals), 1)
        return row

    def export_csv(self, minutes):
        since = time.time() - minutes * 60
        with self.lock:
            rows = self.db.execute("SELECT ts, data FROM snapshots WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
        cols = ["mode", "p_low", "p_high", "sat_low", "sat_high", "t_suc", "t_liq", "sh", "sc",
                "oat", "orh", "t_ret", "t_sup", "dt", "ctoa", "approach", "Y", "W", "G", "OB"]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["time"] + cols)
        for ts, d in rows:
            d = json.loads(d)
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))] + [d.get(c) for c in cols])
        return buf.getvalue()

    def send_cmd(self, node, cmd):
        if self.mqtt is None:
            return False, "Demo mode: commands are not sent"
        topic = f"hvac/{self.cfg['site']}/{node}/cmd"
        self.mqtt.publish(topic, json.dumps(cmd), qos=1)
        log(f"[cmd] {topic} {cmd}")
        return True, "sent"

    def set_config(self, changes):
        with self.lock:
            for k, v in changes.items():
                if k in EDITABLE:
                    self.cfg[k] = v
            save_config(self.cfg)
            self.derived = self.compute(time.time())
        self.push({"type": "state", "state": self.state()})

    def push(self, msg):
        data = json.dumps(msg)
        for q in list(self.clients):
            try:
                q.put_nowait(data)
            except queue.Full:
                pass


# ---------- MQTT ----------
def start_mqtt(hub):
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        sys.exit("paho-mqtt is not installed. Run:  pip install -r requirements.txt")
    cfg = hub.cfg
    base = f"hvac/{cfg['site']}/"

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            log(f"[mqtt] connected to {cfg['mqtt_host']}:{cfg['mqtt_port']}")
            client.subscribe(base + "+/#", qos=1)
        else:
            log(f"[mqtt] connect refused: {reason_code}")

    def on_message(client, userdata, msg):
        parts = msg.topic.split("/")
        if len(parts) != 4:
            return
        node, kind = parts[2], parts[3]
        if node not in ("outdoor", "indoor"):      # e.g. the cloud-only electrical module
            return
        try:
            data = json.loads(msg.payload.decode())
        except Exception:
            return
        if kind == "telemetry":
            hub.on_telemetry(node, data)
        elif kind == "status":
            hub.on_status(node, data)
        elif kind == "reply":
            hub.on_reply(node, data)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"hvac-server-{random.randint(1000, 9999)}")
    if cfg["mqtt_user"]:
        client.username_pw_set(cfg["mqtt_user"], cfg["mqtt_pass"])
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(1, 30)
    client.connect_async(cfg["mqtt_host"], cfg["mqtt_port"], keepalive=30)
    client.loop_start()
    hub.mqtt = client


# ---------- Demo data ----------
def start_demo(hub):
    def run():
        t0 = time.time()
        while True:
            k = (time.time() - t0) / 60
            on = (k % 20) < 14                       # 14 min on, 6 min off cycle
            warm = min(1, ((k % 20) / 4)) if on else 0
            oat = 95 + 3 * math.sin(k / 30)
            p_vap = 120 + random.uniform(-1, 1) if on else 300
            p_liq = 345 + 15 * warm + random.uniform(-2, 2) if on else 300
            hub.on_telemetry("outdoor", {
                "node": "outdoor", "fw": "demo", "rssi": -61, "uptime": int(k * 60),
                "mode": {"Y": on, "OB": on},   # O energized = cooling
                "p": {"liq": round(p_liq, 1), "vap": round(p_vap, 1), "tsuc": None},
                "t": {"suc": round(52 + random.uniform(-0.5, 0.5), 1) if on else round(oat - 5, 1),
                      "liq": round(oat + 6 + random.uniform(-0.5, 0.5), 1) if on else round(oat - 2, 1),
                      "tsuc": None, "dis": None},
                "air": {"t": round(oat, 1), "rh": 12.0}, "v5": 5.02, "err": []})
            hub.on_telemetry("indoor", {
                "node": "indoor", "fw": "demo", "rssi": -55, "uptime": int(k * 60),
                "mode": {"Y": on, "W": False, "G": on, "OB": on},
                "air": {"supply": round(76 - 19 * warm + random.uniform(-0.3, 0.3), 1),
                        "return": round(76 + random.uniform(-0.3, 0.3), 1), "dt": None}, "err": []})
            time.sleep(2)
    threading.Thread(target=run, daemon=True).start()


# ---------- HTTP ----------
def make_handler(hub):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json", extra=None):
            b = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(b)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj))

        def do_GET(self):
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if u.path == "/api/state":
                with hub.lock:
                    st = hub.state()
                return self._json(st)
            if u.path == "/api/history":
                return self._json(hub.history(float(qs.get("minutes", ["60"])[0])))
            if u.path == "/api/export.csv":
                minutes = float(qs.get("minutes", ["1440"])[0])
                return self._send(200, hub.export_csv(minutes), "text/csv",
                                  {"Content-Disposition": "attachment; filename=hvac-export.csv"})
            if u.path == "/api/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                q = queue.Queue(maxsize=100)
                hub.clients.append(q)
                try:
                    while True:
                        try:
                            data = q.get(timeout=15)
                            self.wfile.write(f"data: {data}\n\n".encode())
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    pass
                finally:
                    hub.clients.remove(q)
                return
            self._send(404, "not found", "text/plain")

        def do_POST(self):
            u = urlparse(self.path)
            n = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._json({"ok": False, "error": "bad JSON"}, 400)
            if u.path == "/api/cmd":
                node, cmd = body.get("node"), body.get("cmd")
                if node not in ("outdoor", "indoor") or not isinstance(cmd, dict):
                    return self._json({"ok": False, "error": "need node and cmd"}, 400)
                ok, msg = hub.send_cmd(node, cmd)
                return self._json({"ok": ok, "message": msg})
            if u.path == "/api/config":
                hub.set_config(body)
                return self._json({"ok": True})
            self._send(404, "not found", "text/plain")
    return H


def main():
    demo = "--demo" in sys.argv
    cfg = load_config()
    hub = Hub(cfg)
    if demo:
        log("[demo] generating simulated data (no MQTT)")
        start_demo(hub)
    else:
        start_mqtt(hub)
    port = cfg["web_port"]
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(hub))
    except OSError:
        sys.exit(f"Port {port} is already in use. Close the other HVAC Monitor window or change web_port in config.json.")
    srv.daemon_threads = True
    url = f"http://localhost:{port}"
    log(f"[web] dashboard at {url}  (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("stopping")


if __name__ == "__main__":
    main()
