# HVAC Monitor — PC dashboard

Receives data from the ESP32 nodes over MQTT, stores it, calculates saturation
temperatures, superheat, subcooling and air delta-T, and shows it all live in your browser.

## Start it (Windows)
1. Install **Python 3.12** from python.org. On the first installer screen, tick **"Add python.exe to PATH"**.
2. Keep Mosquitto running (the `mosquitto -c hvac.conf -v` window).
3. Double-click **`start.bat`**. The first run sets up a Python environment and installs two packages
   (paho-mqtt and CoolProp), which takes a minute. It also builds the refrigerant tables once.
4. Your browser opens **http://localhost:8080**.

Want to look around before the hardware is wired? Double-click **`start-demo.bat`** for simulated data.

You can open the dashboard from your phone on the same WiFi at `http://<your-PC-IP>:8080`
(allow Python through Windows Firewall on private networks when asked).

## What it calculates
| Mode | Low side | High side | Superheat | Subcooling |
|---|---|---|---|---|
| Cooling | vapor port | liquid line | suction line temp − dew point | bubble point − liquid line temp |
| Heating (heat pump) | true suction | vapor line (discharge) | true-suction temp − dew point | bubble point − liquid line temp |

Also: air ΔT (return − supply), condensing temperature over ambient, and liquid approach (cooling).
Mode comes from the Y, W, G and O/B inputs. Heating superheat appears once the true-suction
transducer and thermistor are installed and marked as fitted.

Refrigerants: R-410A, R-32, R-454B, R-22, R-134a, R-407C. Blends use separate bubble and dew curves.

## Setup panel
- **Refrigerant** for the connected system
- **Heat pump / air conditioner**
- **Reversing valve:** O (energized in cooling, most brands) or B (energized in heating)
- **Atmospheric pressure:** 14.7 at sea level, about 14.0 around 1,200 ft. Gauge readings are converted to absolute for the tables.

Settings are saved in `config.json`. MQTT broker address, site ID and web port can be changed there too.

## Calibration panel
Sends the calibration commands to the nodes (zero, span, temperature reference, reset, mark
sensor installed). Replies from the node appear in the log below the form.

## Data
Stored in `hvac_data.db` (SQLite), kept for 90 days. **Export CSV** downloads the selected time range.

## Flags
Conservative first-pass checks, judged only after 10 minutes of steady running: floodback risk,
high or low superheat and subcooling, low air ΔT, high condensing-over-ambient, plus offline nodes
and sensor faults. Thresholds live in `Hub.flags()` in `server.py`.
