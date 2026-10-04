import json
import os

import pytest

import lorafmt as lf

KEY = bytes(range(16))
OUT = {"node": "outdoor", "fw": "0.2.0", "uptime": 312, "rssi": -58, "ts": 1790000000.123,
       "mode": {"Y": True, "OB": False},
       "p": {"liq": 318.4, "vap": 121.7, "tsuc": None},
       "t": {"suc": 54.2, "liq": 96.1, "tsuc": None, "dis": None},
       "air": {"t": 104.5, "rh": 11.8}, "v5": 5.02, "raw": {"p_liq": 2.1}, "err": ["sht30"]}
IN = {"node": "indoor", "fw": "0.2.0", "uptime": 99, "rssi": -55,
      "mode": {"Y": True, "W": False, "G": True, "OB": True},
      "air": {"supply": 55.8, "return": 75.9, "dt": 20.1}, "err": []}
VECTORS = os.path.join(os.path.dirname(__file__), "vectors.json")


def test_airtime_matches_published_figures():
    assert lf.airtime_ms(12, 9, 125) == pytest.approx(144.4, abs=0.1)
    assert lf.airtime_ms(12, 9, 500) == pytest.approx(36.1, abs=0.1)
    assert lf.airtime_ms(12, 12, 125) == pytest.approx(1155.1, abs=0.5)   # low data rate optimisation on


def test_frames_are_small_enough():
    out = len(lf.seal(KEY, 1, 1, *lf.pack_reading(OUT)))
    assert out == 45 and len(lf.seal(KEY, 1, 1, *lf.pack_reading(IN))) == 30
    assert lf.airtime_ms(out, 9, 500) < 100           # planned setting
    assert lf.airtime_ms(out, 11, 500) < 400          # even the slowest fallback stays under 400 ms


def test_readings_round_trip_to_the_firmware_json():
    got = lf.unpack_reading(*lf.pack_reading(OUT, age_s=30), received_at=1790000100.0)
    assert got["ts"] == 1790000070.0                   # measured 30 s before it arrived
    for k in ("mode", "p", "t", "air", "v5", "err", "uptime", "node"):
        assert got[k] == OUT[k], k
    assert "raw" not in got and "fw" not in got        # calibration raw values stay on WiFi / status
    got = lf.unpack_reading(*lf.pack_reading(IN))
    assert got["air"] == IN["air"] and got["mode"] == IN["mode"] and "ts" not in got


def test_value_limits_and_unknown_errors():
    doc = {**OUT, "p": {"liq": 9999.9, "vap": -5.04, "tsuc": None}, "air": {"t": None, "rh": None}, "v5": None,
           "err": ["p_liq", "something_new"]}
    got = lf.unpack_reading(*lf.pack_reading(doc))
    assert got["p"]["liq"] == 3276.7 and got["p"]["vap"] == -5.0
    assert got["air"] == {"t": None, "rh": None} and got["v5"] is None
    assert got["err"] == ["p_liq", "other"]


def test_commands_and_replies_cover_the_calibration_page():
    for cmd in [{"cmd": "cal_zero", "ch": "p_liq"}, {"cmd": "cal_span", "ch": "p_vap", "ref": 300.0},
                {"cmd": "cal_ref", "ch": "t_suc", "ref": 32.0}, {"cmd": "cal_reset", "ch": "t_liq"},
                {"cmd": "range", "ch": "p_tsuc", "bar": 35.0}, {"cmd": "fitted", "ch": "t_tsuc", "on": True},
                {"cmd": "ntc_b", "value": 3950.0}, {"cmd": "v33", "value": 3.31}, {"cmd": "interval", "ms": 10000},
                {"cmd": "cal_set", "ch": "t_ret", "offset": -0.4}, {"cmd": "ds_swap"}, {"cmd": "rescan"},
                {"cmd": "status"}, {"cmd": "reboot"}]:
        assert lf.unpack_command(lf.pack_command(cmd)[1]) == cmd
    with pytest.raises(ValueError):
        lf.pack_command({"cmd": "format_flash"})
    with pytest.raises(ValueError):
        lf.pack_command({"cmd": "cal_set", "ch": "p_liq", "offset": 1, "scale": 1.02})
    rep = lf.unpack_reply(lf.pack_reply({"cmd": "cal_zero", "ok": True, "ch": "p_liq", "offset": -1.716, "scale": 1.0})[1])
    assert rep == {"cmd": "cal_zero", "ok": True, "offset": -1.716, "scale": 1.0}
    rep = lf.unpack_reply(lf.pack_reply({"cmd": "cal_span", "ok": False, "error": "apply at least ~50 psi before spanning"})[1])
    assert rep == {"cmd": "cal_span", "ok": False, "error": "apply at least ~50 psi before spanning"}


def test_tampering_wrong_keys_and_replays_are_refused():
    frame = lf.seal(KEY, 0xA1B2C3D4, 7, *lf.pack_reading(OUT))
    for i in (0, 3, 6, 12, len(frame) - 1):           # header, counter, payload and tag
        bad = bytearray(frame)
        bad[i] ^= 0x01
        with pytest.raises(ValueError):
            lf.open_frame(KEY, bytes(bad))
    with pytest.raises(ValueError):
        lf.open_frame(bytes(16), frame)
    gw = lf.Gateway({0xA1B2C3D4: {"key": KEY, "site": "home", "node": "outdoor"}})
    topic, doc = gw.receive(frame, 1790000000.0, rssi=-97, snr=6.5)
    assert topic == "hvac/home/outdoor/telemetry" and doc["radio"] == {"rssi": -97, "snr": 6.5}
    with pytest.raises(ValueError, match="replayed"):
        gw.receive(frame, 1790000005.0)
    with pytest.raises(ValueError, match="unknown device"):
        gw.receive(lf.seal(KEY, 0x1234, 1, *lf.pack_reading(IN)), 1790000006.0)


def test_status_frames():
    gw = lf.Gateway({5: {"key": KEY, "site": "home", "node": "indoor"}})
    topic, doc = gw.receive(lf.seal(KEY, 5, 1, *lf.pack_status("0.3.0", 5000, backlog=4, dropped=0)), 1.0)
    assert topic == "hvac/home/indoor/status" and doc["fw"] == "0.3.0" and doc["interval_ms"] == 5000


def test_vectors_file_is_current():
    """vectors.json gives the firmware (C++) exact bytes to match; regenerate with make_vectors()."""
    with open(VECTORS, encoding="utf-8") as f:
        assert json.load(f) == make_vectors()


def make_vectors():
    cases = [("outdoor reading", 0x0A0B0C0D, 1, lf.pack_reading(OUT, age_s=0)),
             ("outdoor reading kept 300 s", 0x0A0B0C0D, 2, lf.pack_reading(OUT, age_s=300)),
             ("indoor reading", 0x01020304, 1, lf.pack_reading(IN)),
             ("status", 0x01020304, 2, lf.pack_status("0.3.0", 5000)),
             ("command cal_span", 0x0A0B0C0D, 3, lf.pack_command({"cmd": "cal_span", "ch": "p_vap", "ref": 300.0})),
             ("reply cal_zero", 0x0A0B0C0D, 4, lf.pack_reply({"cmd": "cal_zero", "ok": True, "offset": -1.5, "scale": 1.0}))]
    return {"format_version": lf.VERSION, "key_hex": KEY.hex(), "tag_len": lf.TAG_LEN,
            "frames": [{"name": n, "device_id": d, "counter": c, "type": t, "payload_hex": p.hex(),
                        "frame_hex": lf.seal(KEY, d, c, t, p).hex()} for n, d, c, (t, p) in cases]}


if __name__ == "__main__":
    with open(VECTORS, "w", encoding="utf-8", newline="\n") as f:
        json.dump(make_vectors(), f, indent=2)
        f.write("\n")
    print("wrote", VECTORS)
