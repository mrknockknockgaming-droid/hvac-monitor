"""Passwords, sign-in sessions and who is asking.

Passwords are hashed with scrypt (Python standard library). A sign-in creates a random session
token that goes to the browser in an HttpOnly cookie; only its SHA-256 is stored. Requests can
also carry an account API key (X-API-Key), which acts as a contractor of that account.

Browsers send cookies with every request to the site, so a request that changes something and is
authenticated by the cookie must also carry the X-Requested-With header. Other sites can't add
that header to a request without the browser asking this server first (CORS), which it never allows.
"""
import base64
import datetime as dt
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass

from . import settings
from .db import UserSession, as_utc, hash_key, utcnow

COOKIE = "fs_session"
CSRF_HEADER = "X-Requested-With"
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
MIN_PASSWORD = 10
TOUCH_EVERY = dt.timedelta(minutes=5)       # how often a session's last_seen is written


@dataclass
class Principal:
    """Who is making a request."""
    role: str                    # "contractor" | "homeowner"
    account_id: int | None       # contractor's account
    user_id: int | None = None   # None for an API key
    via: str = "session"         # "session" | "key"

    @property
    def is_tech(self):
        return self.role == "contractor"


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    b64 = lambda b: base64.b64encode(b).decode()
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${b64(salt)}${b64(digest)}"


def check_password(password, stored):
    try:
        kind, n, r, p, salt, digest = (stored or "").split("$")
        if kind != "scrypt":
            return False
        want = base64.b64decode(digest)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), dklen=len(want),
                             n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(got, want)
    except (ValueError, TypeError):
        return False


def password_problem(password):
    """A reason the password is too weak, or None."""
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if password.strip() != password:
        return "The password can't start or end with a space."
    return None


def new_session(s, user_id, now=None):
    """Create a session; returns the token for the cookie."""
    now = now or utcnow()
    token = secrets.token_urlsafe(32)
    s.add(UserSession(token_hash=hash_key(token), user_id=user_id, created_at=now, last_seen=now,
                      expires_at=now + dt.timedelta(days=settings.SESSION_DAYS)))
    return token


def session_user_id(s, token, now=None):
    """The user id of a valid session token (sliding expiry), or None."""
    if not token:
        return None
    now = now or utcnow()
    row = s.get(UserSession, hash_key(token))
    if row is None:
        return None
    if as_utc(row.expires_at) <= now:
        s.delete(row)
        s.commit()
        return None
    if now - as_utc(row.last_seen) >= TOUCH_EVERY:
        row.last_seen = now
        row.expires_at = now + dt.timedelta(days=settings.SESSION_DAYS)
        s.commit()
    return row.user_id


def end_session(s, token):
    row = s.get(UserSession, hash_key(token)) if token else None
    if row is not None:
        s.delete(row)
        s.commit()


class Throttle:
    """Slows down password guessing: after `limit` failures for one email within `window`
    seconds, sign-in for that email is refused until the window passes."""

    def __init__(self, limit=8, window=15 * 60):
        self.limit, self.window = limit, window
        self._fails = {}
        self._lock = threading.Lock()

    def _recent(self, key, now):
        return [t for t in self._fails.get(key, []) if now - t < self.window]

    def blocked(self, key, now=None):
        now = time.time() if now is None else now
        with self._lock:
            return len(self._recent(key, now)) >= self.limit

    def fail(self, key, now=None):
        now = time.time() if now is None else now
        with self._lock:
            self._fails[key] = self._recent(key, now) + [now]

    def clear(self, key):
        with self._lock:
            self._fails.pop(key, None)
