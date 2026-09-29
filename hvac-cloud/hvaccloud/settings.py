"""Configuration from environment variables (see .env.example)."""
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(HERE, "dev.db").replace("\\", "/"))

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")

STALE_SECONDS = float(os.environ.get("STALE_SECONDS", "30"))   # node counts as offline after this
KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "365"))              # TimescaleDB retention
