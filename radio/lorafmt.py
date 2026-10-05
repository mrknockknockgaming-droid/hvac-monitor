"""Compact LoRa frames for the nodes, gateway reference implementation (format version 1).

The WiFi firmware sends ~400-byte JSON. Over LoRa the same readings go in ~45-byte binary
frames, encrypted and authenticated with AES-128-CCM. The gateway decodes a frame back into the
exact JSON the WiFi firmware publishes, so the cloud and the PC dashboard need no changes.

Frame:  header (9 bytes, authenticated, not encrypted)  |  encrypted payload  |  8-byte tag
  header  = byte 0: version (high 4 bits) | frame type (low 4 bits)
            bytes 1-4: device id (uint32, little-endian)
            bytes 5-8: frame counter (uint32, little-endian; never repeats for a device)
  nonce   = device id | counter | byte 0 | 4 zero bytes   (13 bytes, unique per frame)

This file is the reference: the node and gateway firmware (C++, mbedTLS CCM) must produce and
accept the same bytes; vectors.json holds test frames for that. See README.md for the design.
"""
import json
import math
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESCCM

VERSION = 1
TAG_LEN = 8
HEADER = struct.Struct("<BII")              # byte 0, device id, counter
NULL16 = -32768                             # int16 "no reading"
NULL8 = 255                                 # uint8 "no reading"
NULLU16 = 0xFFFF                            # uint16 "no reading"

# frame types
OUTDOOR, INDOOR, STATUS, COMMAND, REPLY = 1, 2, 3, 4, 5
TYPE_NAMES = {OUTDOOR: "outdoor", INDOOR: "indoor", STATUS: "status", COMMAND: "command", REPLY: "reply"}

# error codes the firmware reports, as bit positions (anything else sets OTHER_ERR)
OUTDOOR_ERRS = ["ads1", "ads2", "sht30", "rail5v", "p_liq", "p_vap", "p_tsuc", "t_suc", "t_liq", "t_tsuc", "t_dis"]
INDOOR_ERRS = ["ds18b20_missing", "t_sup", "t_ret"]
OTHER_ERR = 15
MODE_BITS = ["Y", "OB", "W", "G"]

# telemetry payloads (after the header)
OUT_FMT = struct.Struct("<HBHhhhhhhhhHBI")  # age, modes, errs, p liq/vap/tsuc, t suc/liq/tsuc/dis, air t, rh x10, v5, uptime
IN_FMT = struct.Struct("<HBHhhI")           # age, modes, errs, supply, return, uptime
STATUS_FMT = struct.Struct("<BBBHBH")       # fw major/minor/patch, interval s, backlog, dropped

# commands: the firmware's JSON commands as (id, channel, value)
COMMANDS = ["status", "reboot", "interval", "cal_zero", "cal_span", "cal_ref", "cal_set", "cal_reset",
            "range", "fitted", "ntc_b", "v33", "ds_swap", "rescan"]
CHANNELS = [None, "p_liq", "p_vap", "p_tsuc", "t_suc", "t_liq", "t_tsuc", "t_dis", "t_sup", "t_ret"]
CMD_FMT = struct.Struct("<BBfB")            # command, channel, value, flag (fitted on / cal_set has scale)
REPLY_FMT = struct.Struct("<BBBff")         # command, ok, error code, offset/first value, scale/second value
REPLY_ERRORS = ["", "unknown command", "unknown channel", "channel has no valid reading", "need ref and a valid reading",
                "apply at least ~50 psi before spanning", "value out of range", "bad frame"]


# ---------------------------------------------------------------- channel plan
def channel_mhz(site):
    """The site's channel: one of eight 500 kHz channels, 903.0 + 1.6 n MHz (README "Channel plan"),
    from a FNV-1a hash of the site id, so neighbouring systems usually differ."""
    h = 0x811C9DC5
    for b in site.encode():
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return round(903.0 + 1.6 * (h % 8), 1)


# ---------------------------------------------------------------- airtime
def airtime_ms(payload_len, sf, bw_khz, cr=1, preamble=8, explicit_header=True, crc=True):
    """LoRa time on air (Semtech SX1261/2 datasheet, section 6.1.4). cr=1 means 4/5."""
    tsym = (2 ** sf) / (bw_khz * 1000.0)
    ldro = 1 if tsym > 0.016 else 0
    num = 8 * payload_len - 4 * sf + 28 + 16 * crc - 20 * (0 if explicit_header else 1)
    payload_symbols = 8 + max(math.ceil(num / (4 * (sf - 2 * ldro))) * (cr + 4), 0)
    return ((preamble + 4.25) + payload_symbols) * tsym * 1000.0


# ---------------------------------------------------------------- value packing
def _i16(v, scale=10):
    if v is None:
        return NULL16
    return max(-32767, min(32767, round(v * scale)))


def _f16(n, scale=10):
    return None if n == NULL16 else round(n / scale, 1)


def _bits(names, present):
    out = 0
    for name in present:
        out |= 1 << (names.index(name) if name in names else OTHER_ERR)
    return out


def _unbits(names, value):
    out = [n for i, n in enumerate(names) if value >> i & 1]
    if value >> OTHER_ERR & 1:
        out.append("other")
    return out


def _modes(mode):
    return sum(1 << i for i, k in enumerate(MODE_BITS) if (mode or {}).get(k))


def _unmodes(bits, keys):
    return {k: bool(bits >> MODE_BITS.index(k) & 1) for k in keys}


# ---------------------------------------------------------------- payloads <-> firmware JSON
def pack_reading(doc, age_s=0):
    """Firmware telemetry JSON (dict) -> (frame type, payload bytes). age_s: seconds since it was
    measured (non-zero for readings kept during an outage)."""
    age = max(0, min(65535, int(age_s)))
    if doc["node"] == "outdoor":
        p, t, air = doc.get("p") or {}, doc.get("t") or {}, doc.get("air") or {}
        rh, v5 = air.get("rh"), doc.get("v5")
        return OUTDOOR, OUT_FMT.pack(
            age, _modes(doc.get("mode")), _bits(OUTDOOR_ERRS, doc.get("err") or []),
            _i16(p.get("liq")), _i16(p.get("vap")), _i16(p.get("tsuc")),
            _i16(t.get("suc")), _i16(t.get("liq")), _i16(t.get("tsuc")), _i16(t.get("dis")),
            _i16(air.get("t")), NULLU16 if rh is None else max(0, min(1000, round(rh * 10))),
            NULL8 if v5 is None else max(0, min(254, round((v5 - 4.0) * 100))), int(doc.get("uptime") or 0))
    if doc["node"] == "indoor":
        air = doc.get("air") or {}
        return INDOOR, IN_FMT.pack(
            age, _modes(doc.get("mode")), _bits(INDOOR_ERRS, doc.get("err") or []),
            _i16(air.get("supply")), _i16(air.get("return")), int(doc.get("uptime") or 0))
    raise ValueError("node must be outdoor or indoor")


def unpack_reading(ftype, payload, received_at=None):
    """(frame type, payload) -> the firmware's telemetry JSON (without fw / rssi, which the gateway
    adds). With received_at (UTC seconds), "ts" is set to when the reading was measured."""
    if ftype == OUTDOOR:
        (age, modes, errs, pl, pv, pt, ts_, tl, tt, td, at, rh, v5, up) = OUT_FMT.unpack(payload)
        doc = {"node": "outdoor", "uptime": up, "mode": _unmodes(modes, ["Y", "OB"]),
               "p": {"liq": _f16(pl), "vap": _f16(pv), "tsuc": _f16(pt)},
               "t": {"suc": _f16(ts_), "liq": _f16(tl), "tsuc": _f16(tt), "dis": _f16(td)},
               "air": {"t": _f16(at), "rh": None if rh == NULLU16 else rh / 10},
               "v5": None if v5 == NULL8 else round(4.0 + v5 / 100, 2), "err": _unbits(OUTDOOR_ERRS, errs)}
    elif ftype == INDOOR:
        age, modes, errs, sup, ret, up = IN_FMT.unpack(payload)
        s, r = _f16(sup), _f16(ret)
        doc = {"node": "indoor", "uptime": up, "mode": _unmodes(modes, ["Y", "W", "G", "OB"]),
               "air": {"supply": s, "return": r, "dt": round(r - s, 1) if s is not None and r is not None else None},
               "err": _unbits(INDOOR_ERRS, errs)}
    else:
        raise ValueError(f"frame type {ftype} is not a reading")
    if received_at is not None:
        doc["ts"] = round(received_at - age, 3)
    return doc


def pack_status(fw, interval_ms, backlog=0, dropped=0):
    major, minor, patch = (int(x) for x in fw.split("."))
    return STATUS, STATUS_FMT.pack(major, minor, patch, min(65535, interval_ms // 1000), min(255, backlog), min(65535, dropped))


def unpack_status(payload):
    major, minor, patch, interval_s, backlog, dropped = STATUS_FMT.unpack(payload)
    return {"online": True, "fw": f"{major}.{minor}.{patch}", "interval_ms": interval_s * 1000,
            "backlog": backlog, "dropped": dropped, "link": "lora"}


def pack_command(cmd):
    """Firmware command JSON -> payload. Raises ValueError for commands the radio can't carry."""
    name = cmd.get("cmd")
    if name not in COMMANDS:
        raise ValueError(f"{name!r} can't be sent over LoRa")
    ch = cmd.get("ch")
    if ch is not None and ch not in CHANNELS[1:]:
        raise ValueError(f"unknown channel {ch!r}")
    value, flag = 0.0, 0
    if name == "interval":
        value = cmd["ms"] / 1000
    elif name in ("cal_span", "cal_ref"):
        value = cmd["ref"]
    elif name == "range":
        value = cmd["bar"]
    elif name in ("ntc_b", "v33"):
        value = cmd["value"]
    elif name == "fitted":
        flag = 1 if cmd.get("on") else 0
    elif name == "cal_set":
        if "scale" in cmd:                   # offset and scale don't both fit: scale needs a second command
            raise ValueError("cal_set over LoRa takes offset only; use cal_span for the scale")
        value = cmd["offset"]
    return COMMAND, CMD_FMT.pack(COMMANDS.index(name), CHANNELS.index(ch), float(value), flag)


def unpack_command(payload):
    c, ch, value, flag = CMD_FMT.unpack(payload)
    name, chan = COMMANDS[c], CHANNELS[ch]
    cmd = {"cmd": name}
    if chan:
        cmd["ch"] = chan
    value = round(value, 4)
    if name == "interval":
        cmd["ms"] = int(round(value * 1000))
    elif name in ("cal_span", "cal_ref"):
        cmd["ref"] = value
    elif name == "range":
        cmd["bar"] = value
    elif name in ("ntc_b", "v33"):
        cmd["value"] = value
    elif name == "fitted":
        cmd["on"] = bool(flag)
    elif name == "cal_set":
        cmd["offset"] = value
    return cmd


def pack_reply(reply):
    name = reply.get("cmd")
    err = reply.get("error") or ""
    code = REPLY_ERRORS.index(err) if err in REPLY_ERRORS else len(REPLY_ERRORS) - 1
    first = reply.get("offset", reply.get("ms", reply.get("ntc_b", reply.get("v33", reply.get("fs_bar", 0.0)))))
    return REPLY, REPLY_FMT.pack(COMMANDS.index(name) if name in COMMANDS else 255, 1 if reply.get("ok") else 0,
                                 code, float(first or 0.0), float(reply.get("scale", 1.0)))


def unpack_reply(payload):
    c, ok, code, first, scale = REPLY_FMT.unpack(payload)
    out = {"cmd": COMMANDS[c] if c < len(COMMANDS) else None, "ok": bool(ok)}
    if code:
        out["error"] = REPLY_ERRORS[code]
    if out["cmd"] in ("cal_zero", "cal_span", "cal_ref", "cal_set", "cal_reset") and ok:
        out["offset"], out["scale"] = round(first, 4), round(scale, 6)
    return out


# ---------------------------------------------------------------- framing + encryption
def _nonce(byte0, device_id, counter):
    return struct.pack("<IIB", device_id, counter, byte0) + b"\x00" * 4


def seal(key, device_id, counter, ftype, payload):
    """Encrypt and authenticate a payload into a complete frame."""
    byte0 = VERSION << 4 | ftype
    header = HEADER.pack(byte0, device_id, counter)
    return header + AESCCM(key, tag_length=TAG_LEN).encrypt(_nonce(byte0, device_id, counter), payload, header)


def open_frame(key, frame):
    """Check and decrypt a frame. Returns (type, device id, counter, payload); raises ValueError
    for a wrong version, a frame too short to be ours, or a failed authentication."""
    if len(frame) < HEADER.size + TAG_LEN:
        raise ValueError("frame too short")
    byte0, device_id, counter = HEADER.unpack(frame[:HEADER.size])
    if byte0 >> 4 != VERSION:
        raise ValueError(f"unsupported frame version {byte0 >> 4}")
    try:
        payload = AESCCM(key, tag_length=TAG_LEN).decrypt(_nonce(byte0, device_id, counter), frame[HEADER.size:], frame[:HEADER.size])
    except Exception:
        raise ValueError("authentication failed (wrong key or altered frame)") from None
    return byte0 & 0x0F, device_id, counter, payload


def peek_device(frame):
    """Device id from the header, to look up its key before opening the frame."""
    return HEADER.unpack(frame[:HEADER.size])[1]


class Gateway:
    """What the gateway does with each received frame: find the device's key, authenticate,
    refuse replays, and turn readings into the firmware's JSON for MQTT."""

    def __init__(self, devices):
        self.devices = devices               # device id -> {"key": bytes, "site": str, "node": str}
        self.last_counter = {}               # device id -> highest counter accepted (kept in flash on the gateway)

    def receive(self, frame, received_at, rssi=None, snr=None):
        """Returns (topic, JSON dict) to publish, or raises ValueError."""
        dev_id = peek_device(frame)
        dev = self.devices.get(dev_id)
        if dev is None:
            raise ValueError(f"unknown device {dev_id:#010x}")
        ftype, dev_id, counter, payload = open_frame(dev["key"], frame)
        if counter <= self.last_counter.get(dev_id, -1):
            raise ValueError(f"replayed or repeated frame (counter {counter})")
        self.last_counter[dev_id] = counter
        base = f"hvac/{dev['site']}/{dev['node']}/"
        if ftype in (OUTDOOR, INDOOR):
            doc = unpack_reading(ftype, payload, received_at)
            if rssi is not None:
                doc["radio"] = {"rssi": rssi, "snr": snr}
            return base + "telemetry", doc
        if ftype == STATUS:
            return base + "status", unpack_status(payload)
        if ftype == REPLY:
            return base + "reply", unpack_reply(payload)
        raise ValueError(f"nodes don't send frame type {ftype}")


if __name__ == "__main__":
    # Airtime table for the largest frame (outdoor reading) at the settings worth considering.
    size = HEADER.size + OUT_FMT.size + TAG_LEN
    print(f"outdoor frame {size} bytes, indoor {HEADER.size + IN_FMT.size + TAG_LEN} bytes\n")
    print("SF  BW kHz  airtime ms   (FCC: 500 kHz = DTS, no dwell limit; 125 kHz needs hopping, 400 ms dwell)")
    for bw in (500, 125):
        for sf in range(7, 13):
            print(f"{sf:<3} {bw:<7} {airtime_ms(size, sf, bw):8.1f}")
