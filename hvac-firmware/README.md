# HVAC Monitor firmware (v0.2.0)

One PlatformIO project builds both nodes:

| Build env | Node | Sensors |
|---|---|---|
| `outdoor` | Refrigerant side | 3× XDB307 (2 fitted), 2× NTC (+2 future), SHT30, Y and O/B |
| `indoor`  | Air side | 2× DS18B20 (supply/return), Y, W, G, O/B |

Pins and net names match the rev A schematics.

## 1. Tools
1. Install **VS Code** and the **PlatformIO** extension.
2. Install an MQTT broker on your PC: **Mosquitto** (mosquitto.org). The web app will read from the same broker.
   - Mosquitto 2.x only accepts local connections by default. Create `mosquitto.conf` with:
     ```
     listener 1883 0.0.0.0
     allow_anonymous true
     ```
     and start it with `mosquitto -c mosquitto.conf -v`. Allow port 1883 through your PC firewall.
3. Give your PC a fixed IP (DHCP reservation in your router) so the nodes can always find it.

## 2. Configure
Copy `include/config.example.h` to `include/config.h` and fill in WiFi, the PC's IP (`MQTT_HOST`), and an OTA password. Use the same OTA password in the `*_ota` sections of `platformio.ini`.

**Bench / PC broker:** `MQTT_TLS 0`, port 1883 (as before; a 0.1.0 `config.h` still builds).

**Cloud server** (see `hvac-cloud/DEPLOY.md`): `MQTT_HOST` = the server's domain (the name in
its certificate), `MQTT_PORT 8883`, `MQTT_TLS 1`, `MQTT_USER` / `MQTT_PASS` = the server's
`MQTT_NODES_USER` / `MQTT_NODES_PASS`, and the server's `tls/ca.crt` pasted into `config.h` as

```cpp
static const char MQTT_CA_CERT[] = R"PEM(
-----BEGIN CERTIFICATE-----
...
-----END CERTIFICATE-----
)PEM";
```

(a string constant: a `#define` can't hold the multi-line certificate). The node checks the
broker's certificate and name against it. TLS needs the right date, so the node sets its
clock by NTP after WiFi connects and waits up to 15 s for it before the first TLS attempt.

### What 0.2.0 changed
- MQTT over TLS (above). Builds: plain 66 % flash, TLS 76 %.
- Every reading carries `"ts"` (UTC seconds) once NTP has set the clock. While the broker
  can't be reached, up to 120 readings (10 min at 5 s) are kept in memory and sent oldest first
  on reconnect; the cloud and the PC dashboard file them under their own time. Status reports
  `backlog` and `dropped` (readings lost to a longer outage) and `tls`.
- After any successful command the node re-publishes its retained status, so new settings
  show up at once; status now includes `interval_ms`.
- No more `nvs_get_blob ... NOT_FOUND` lines at boot for settings that were never saved.

### LoRa instead of WiFi (optional)
With an SX1262 module wired up (pins in `config.example.h`), set `LORA_ENABLED 1` and the node's
`LORA_DEVICE_ID` / `LORA_KEY` from `radio/tools/new_device.py`. The node then talks only to the
site's LoRa gateway (`radio/README.md`), which republishes everything on the usual MQTT topics.
Readings carry no `ts` (the gateway stamps them), and over-the-air updates need WiFi.

## 3. Flash
With **24 VAC disconnected**, plug the ESP32 into USB, then:
```
pio run -e outdoor -t upload -t monitor
```
(or pick the env in the PlatformIO sidebar). Use `-e indoor` for the other board.
After the first USB flash you can update over WiFi: `pio run -e outdoor_ota -t upload`.

On boot the serial monitor shows which sensors were found, e.g.
`[hw] ADS1115 #1 ok, ADS1115 #2 ok, SHT30 ok`, then one JSON line per reading.

## 4. Status LED (GPIO2)
- Fast blink: no WiFi
- Slow blink: WiFi OK, can't reach MQTT broker
- Short flash every 3 s: all good

## 5. MQTT topics
`hvac/<SITE_ID>/<node>/telemetry`, `/status` (retained, last-will), `/cmd`, `/reply`

Watch everything:
```
mosquitto_sub -h localhost -t 'hvac/#' -v
```
Send a command:
```
mosquitto_pub -h localhost -t hvac/home/outdoor/cmd -m '{"cmd":"status"}'
```

### Outdoor telemetry
```json
{"node":"outdoor","fw":"0.1.0","uptime":312,"rssi":-58,
 "mode":{"Y":true,"OB":false},
 "p":{"liq":318.4,"vap":121.7,"tsuc":null},          // psig
 "t":{"suc":54.2,"liq":96.1,"tsuc":null,"dis":null}, // °F
 "air":{"t":104.5,"rh":11.8},
 "v5":5.03,
 "raw":{"p_liq":2.31,"p_vap":1.36,"t_suc":13880,"t_liq":4712},
 "err":[]}
```
`null` means not fitted or no valid reading; `err` lists what's wrong.

### Indoor telemetry
```json
{"node":"indoor","mode":{"Y":true,"W":false,"G":true,"OB":false},
 "air":{"supply":55.8,"return":75.4,"dt":19.6},"err":[]}
```
Saturation temps, superheat and subcooling are calculated by the web app, so refrigerant tables can be updated without reflashing.

## 6. Commands
Common: `reboot`, `status`, `{"cmd":"interval","ms":5000}`

Outdoor:
| Command | Use |
|---|---|
| `{"cmd":"cal_zero","ch":"p_liq"}` | Sensor open to atmosphere → sets 0 psig |
| `{"cmd":"cal_span","ch":"p_liq","ref":300}` | At a known pressure (≥50 psi) from your reference gauge |
| `{"cmd":"cal_ref","ch":"t_suc","ref":32}` | Probe at a known temp (ice bath = 32 °F) |
| `{"cmd":"cal_set","ch":"p_vap","offset":-1.5,"scale":1.0}` | Manual values |
| `{"cmd":"cal_reset","ch":"p_liq"}` | Back to factory |
| `{"cmd":"range","ch":"p_tsuc","bar":35}` | Transducer full-scale range |
| `{"cmd":"fitted","ch":"p_tsuc","on":true}` | Enable a channel once installed |
| `{"cmd":"ntc_b","value":3950}` | Thermistor B-value |
| `{"cmd":"v33","value":3.29}` | Measured 3V3 pin voltage (better thermistor accuracy) |

Channels: `p_liq`, `p_vap`, `p_tsuc`, `t_suc`, `t_liq`, `t_tsuc`, `t_dis`

Indoor: `ds_swap` (supply/return reversed), `rescan`, `cal_ref` / `cal_set` / `cal_reset` with `t_sup` or `t_ret`.

Calibration is stored in flash and survives reboots and firmware updates.

## 7. Bench calibration procedure
1. **Zero:** both transducers open to atmosphere → `cal_zero` on `p_liq` and `p_vap`.
2. **Span:** tee both transducers and your Fieldpiece probe on a nitrogen regulator, bring it to ~300 psi, let it settle → `cal_span` with the reference reading on each channel.
3. **Check:** step through 100 / 200 / 400 psi and compare. Expect within ~2–3 psi.
4. **Thermistors:** meter the 3V3 pin → `v33`. Put both probes in a stirred ice bath → `cal_ref` with 32.
5. **Mode inputs:** apply 24 VAC to Y, confirm `"Y":true` within ~0.1 s, remove it and confirm it drops.

## Notes
- The ESP32 uses a PCB antenna: mount it in a plastic enclosure outside sheet metal. Watch `rssi` (better than −75 dBm is comfortable).
- Never connect USB and 24 VAC at the same time.
