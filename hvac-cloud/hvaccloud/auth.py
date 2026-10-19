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
from sqlalchemy import delete

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


# Checked when an email has no sign-in, so the answer takes as long as a wrong password and
# doesn't reveal which emails exist.
DUMMY_HASH = hash_password(secrets.token_hex(16))


def check_password(password, stored):
    """True when `password` matches the stored scrypt hash."""
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


def end_other_sessions(s, user_id, keep=None):
    """Sign the user out everywhere except the session `keep` (a cookie token, or None for all)."""
    q = delete(UserSession).where(UserSession.user_id == user_id)
    if keep:
        q = q.where(UserSession.token_hash != hash_key(keep))
    s.execute(q)


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
            if len(self._fails) > 1000:                # forget emails whose failures have all aged out
                self._fails = {k: v for k, v in self._fails.items() if self._recent(k, now)}
            self._fails[key] = self._recent(key, now) + [now]

    def clear(self, key):
        with self._lock:
            self._fails.pop(key, None)


# ---------- two-factor sign-in (RFC 6238 TOTP, as authenticator apps make it) ----------
TOTP_STEP = 30              # seconds per code
TOTP_DIGITS = 6
TOTP_SKEW = 1               # also accept the code just before and after, for phone clocks a little off
RECOVERY_CODES = 8
ISSUER = "Fullscope"


def totp_new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode()          # 32 characters, no padding


def totp_code(secret, step):
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    mac = hmac.new(key, step.to_bytes(8, "big"), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    n = int.from_bytes(mac[off:off + 4], "big") & 0x7FFFFFFF
    return str(n % 10 ** TOTP_DIGITS).zfill(TOTP_DIGITS)


def totp_check(secret, code, last_step=None, now=None):
    """The time step `code` belongs to when it is right and newer than `last_step`, else None."""
    code = "".join((code or "").split())
    if not secret or len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    step = int((time.time() if now is None else now) // TOTP_STEP)
    for st in range(step - TOTP_SKEW, step + TOTP_SKEW + 1):
        if (last_step is None or st > last_step) and hmac.compare_digest(totp_code(secret, st), code):
            return st
    return None


def totp_uri(secret, email):
    from urllib.parse import quote
    return (f"otpauth://totp/{quote(ISSUER)}:{quote(email)}?secret={secret}&issuer={quote(ISSUER)}"
            f"&digits={TOTP_DIGITS}&period={TOTP_STEP}")


def new_recovery_codes():
    """(codes to show once, their hashes to store). Each is 10 letters and digits as xxxxx-xxxxx."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = "".join(secrets.choice(alphabet) for _ in range(10))
        codes.append(raw[:5] + "-" + raw[5:])
    return codes, [hash_key(recovery_norm(c)) for c in codes]


def recovery_norm(code):
    return "".join(ch for ch in (code or "").lower() if ch.isalnum())


def second_factor(user, code, now=None):
    """Check a sign-in code against a user with two-factor on: an authenticator code (remembered so it
    can't be used twice) or one of their recovery codes (crossed off). True when accepted; the caller commits."""
    step = totp_check(user.totp_secret, code, user.totp_last_step, now)
    if step is not None:
        user.totp_last_step = step
        return True
    norm = recovery_norm(code)
    if len(norm) != 10:
        return False
    import json
    left = json.loads(user.recovery_codes or "[]")
    h = hash_key(norm)
    if h not in left:
        return False
    left.remove(h)
    user.recovery_codes = json.dumps(left)
    return True


def client_ip(request):
    """The browser's address. Behind the Cloudflare Tunnel every request arrives from this machine,
    and cloudflared names the real one in CF-Connecting-IP."""
    host = request.client.host if request.client else ""
    if host in ("127.0.0.1", "::1", "localhost", "") and request.headers.get("cf-connecting-ip"):
        return request.headers["cf-connecting-ip"].strip()
    return host
