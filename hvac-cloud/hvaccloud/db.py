"""Database models. SQLite for development, TimescaleDB (PostgreSQL) in production."""
import datetime as dt
import hashlib
import secrets

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint,
                        create_engine, event, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from . import settings

Json = JSON().with_variant(JSONB(), "postgresql")


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def as_utc(t):
    """SQLite returns naive datetimes; everything is stored in UTC."""
    return None if t is None else (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc))


class Base(DeclarativeBase):
    type_annotation_map = {dt.datetime: DateTime(timezone=True)}


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    systems: Mapped[list["System"]] = relationship(back_populates="account")


class ApiKey(Base):
    """Only a SHA-256 of the key is stored; the key itself is shown once when created."""
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    prefix: Mapped[str] = mapped_column(String(12))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    last_used: Mapped[dt.datetime | None]
    account: Mapped[Account] = relationship()

    @staticmethod
    def new(account_id):
        key = "hvk_" + secrets.token_urlsafe(32)
        return ApiKey(account_id=account_id, prefix=key[:12], key_hash=hash_key(key)), key


def hash_key(key):
    return hashlib.sha256(key.encode()).hexdigest()


class System(Base):
    """One installation. site_id is the <site> in the nodes' MQTT topics (SITE_ID in config.h)."""
    __tablename__ = "systems"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    name: Mapped[str] = mapped_column(String(200))
    site_id: Mapped[str] = mapped_column(String(64), unique=True)
    refrigerant: Mapped[str] = mapped_column(String(20), default="R-410A")
    heat_pump: Mapped[bool] = mapped_column(Boolean, default=True)
    ob_energized: Mapped[str] = mapped_column(String(4), default="cool")   # "cool" = O, "heat" = B
    atm_psia: Mapped[float] = mapped_column(Float, default=14.7)
    run_started_at: Mapped[dt.datetime | None]
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    account: Mapped[Account] = relationship(back_populates="systems")
    devices: Mapped[list["Device"]] = relationship(back_populates="system")

    def calc_config(self):
        return {"refrigerant": self.refrigerant, "heat_pump": self.heat_pump,
                "ob_energized": self.ob_energized, "atm_psia": self.atm_psia}


class Device(Base):
    """A node (outdoor or indoor) of a system; created on its first message."""
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("system_id", "node"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    system_id: Mapped[int] = mapped_column(ForeignKey("systems.id"))
    node: Mapped[str] = mapped_column(String(16))
    fw: Mapped[str | None] = mapped_column(String(32))
    ip: Mapped[str | None] = mapped_column(String(45))
    mac: Mapped[str | None] = mapped_column(String(17))
    rssi: Mapped[int | None]
    connected: Mapped[bool] = mapped_column(Boolean, default=False)   # from the retained status / will
    last_seen: Mapped[dt.datetime | None]
    last_status = mapped_column(Json, nullable=True)
    system: Mapped[System] = relationship(back_populates="devices")


class Telemetry(Base):
    """Raw telemetry JSON as each node sent it."""
    __tablename__ = "telemetry"
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), primary_key=True)
    time: Mapped[dt.datetime] = mapped_column(primary_key=True)
    data = mapped_column(Json)


class Snapshot(Base):
    """Derived system state (calc.compute) after each telemetry message."""
    __tablename__ = "snapshots"
    system_id: Mapped[int] = mapped_column(ForeignKey("systems.id"), primary_key=True)
    time: Mapped[dt.datetime] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(12))
    data = mapped_column(Json)


class Command(Base):
    """A command sent to a node, and the node's reply once it arrives."""
    __tablename__ = "commands"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"))
    payload = mapped_column(Json, nullable=True)       # None = a reply nobody here asked for
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    sent: Mapped[bool] = mapped_column(Boolean, default=False)
    reply = mapped_column(Json, nullable=True)
    replied_at: Mapped[dt.datetime | None]


def make_engine(url=None):
    url = url or settings.DATABASE_URL
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")   # ingest and API share the file
        return eng
    return create_engine(url, pool_pre_ping=True)


def init_db(engine):
    Base.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as c:
            c.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
            for table in ("telemetry", "snapshots"):
                c.execute(text(f"SELECT create_hypertable('{table}', 'time', if_not_exists => TRUE, "
                               f"migrate_data => TRUE)"))
                c.execute(text(f"SELECT add_retention_policy('{table}', INTERVAL '{settings.KEEP_DAYS} days', "
                               f"if_not_exists => TRUE)"))


def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False)
