"""Configuration from environment variables, or KEY=value lines in hvac-cloud/.env (see .env.example)."""
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_env_file(path):
    """Real environment variables win over the file."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
    except FileNotFoundError:
        pass


_load_env_file(os.path.join(HERE, ".env"))

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(HERE, "dev.db").replace("\\", "/"))

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")

STALE_SECONDS = float(os.environ.get("STALE_SECONDS", "30"))   # node counts as offline after this
KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "365"))              # older data is deleted
FULL_DETAIL_DAYS = int(os.environ.get("FULL_DETAIL_DAYS", "30"))  # then thinned to 1-minute averages (SQLite)
BACKUP_DIR = os.environ.get("BACKUP_DIR", os.path.join(HERE, "backups"))
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "7"))            # nightly copies of dev.db to keep

# Alerts: a flag must hold this long to raise an alert, and be gone this long to clear it
ALERT_HOLD_SECONDS = float(os.environ.get("ALERT_HOLD_SECONDS", "300"))
ALERT_NO_DATA_SECONDS = float(os.environ.get("ALERT_NO_DATA_SECONDS", "600"))   # silence before "no data"
ALERT_EMAIL_COOLDOWN_HOURS = float(os.environ.get("ALERT_EMAIL_COOLDOWN_HOURS", "6"))   # per system + code
APP_URL = os.environ.get("APP_URL", "http://localhost:8000/app/")

# Email: alerts go to the account's address. Leave SMTP_HOST empty to turn email off.
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))             # 587 = STARTTLS, 465 = SSL
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "") or SMTP_USER
