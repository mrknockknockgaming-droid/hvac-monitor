"""Phone and browser notifications (Web Push) for alerts, next to email.

A browser that turns on notifications (Account page) gives us a push-service address (endpoint) and
two keys. To notify it we POST the message, encrypted for that browser (RFC 8291, aes128gcm), to
the endpoint, signed with this server's VAPID key (RFC 8292) so the push service knows who sent it.
The VAPID key pair is made once and kept in the server_keys table, so the Python and C# versions
share it and subscriptions survive a switch between them.

On iPhone this works once the app is added to the home screen (iOS 16.4 or later).
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import settings
from .db import PushSubscription, ServerKey, utcnow

log = logging.getLogger("webpush")

TTL_S = 24 * 3600                 # a phone that is off for a day still gets it
JWT_HOURS = 12
RECORD_SIZE = 4096
# Push services of the browsers we support; a subscription elsewhere is refused (the server POSTs there).
PUSH_HOSTS = ("fcm.googleapis.com", ".googleapis.com", ".mozilla.com", ".push.apple.com", ".notify.windows.com")


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def endpoint_ok(url):
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == h or (h.startswith(".") and host.endswith(h)) for h in PUSH_HOSTS)


def _ec():
    from cryptography.hazmat.primitives.asymmetric import ec
    return ec


def public_bytes(private_key):
    from cryptography.hazmat.primitives import serialization
    return private_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def private_from(d):
    ec = _ec()
    return ec.derive_private_key(int.from_bytes(d, "big"), ec.SECP256R1())


def new_key_pair():
    """(private d, public uncompressed point), both base64url."""
    ec = _ec()
    k = ec.generate_private_key(ec.SECP256R1())
    return b64u(k.private_numbers().private_value.to_bytes(32, "big")), b64u(public_bytes(k))


def vapid_keys(s, create=False):
    """(private, public) base64url, made on first use when `create`; None if there are none yet."""
    rows = {r.name: r.value for r in s.scalars(select(ServerKey).where(ServerKey.name.in_(("vapid_private", "vapid_public"))))}
    if "vapid_private" in rows and "vapid_public" in rows:
        return rows["vapid_private"], rows["vapid_public"]
    if not create:
        return None
    priv, pub = new_key_pair()
    try:
        s.add_all([ServerKey(name="vapid_private", value=priv), ServerKey(name="vapid_public", value=pub)])
        s.commit()
    except IntegrityError:                     # the other process made them at the same moment
        s.rollback()
        return vapid_keys(s)
    return priv, pub


def _hmac(key, data):
    return hmac.new(key, data, hashlib.sha256).digest()


def encrypt(payload, ua_public, auth_secret, salt=None, as_private=None):
    """The request body for one browser (RFC 8291). `ua_public` and `auth_secret` are the subscription's
    p256dh and auth keys (bytes); salt and as_private are random unless given (tests)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    ec = _ec()
    salt = salt or os.urandom(16)
    as_key = private_from(as_private) if as_private else ec.generate_private_key(ec.SECP256R1())
    as_public = public_bytes(as_key)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = as_key.exchange(ec.ECDH(), ua_key)
    ikm = _hmac(_hmac(auth_secret, shared), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)          # \x02 = last (only) record
    return salt + struct.pack(">IB", RECORD_SIZE, len(as_public)) + as_public + body


def vapid_subject():
    sender = settings.SMTP_FROM or settings.SMTP_USER
    if sender and "@" in sender:
        return "mailto:" + sender.split("<")[-1].strip(" >")
    u = urllib.parse.urlsplit(settings.APP_URL)
    return f"{u.scheme}://{u.netloc}"


def vapid_header(endpoint, private, public, now=None):
    """Authorization header value: a JWT for the push service's origin, signed with ES256."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    ec = _ec()
    u = urllib.parse.urlsplit(endpoint)
    now = time.time() if now is None else now
    enc = lambda o: b64u(json.dumps(o, separators=(",", ":")).encode())
    signing = enc({"typ": "JWT", "alg": "ES256"}) + "." + enc({"aud": f"{u.scheme}://{u.netloc}",
                                                              "exp": int(now) + JWT_HOURS * 3600, "sub": vapid_subject()})
    r, s_ = decode_dss_signature(private_from(unb64u(private)).sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
    jwt = signing + "." + b64u(r.to_bytes(32, "big") + s_.to_bytes(32, "big"))
    return f"vapid t={jwt}, k={public}"


def post(endpoint, body, headers, timeout=10):
    """POST to the push service; returns the HTTP status (0 when it couldn't be reached)."""
    req = urllib.request.Request(endpoint, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (OSError, ValueError) as e:
        log.warning("push to %s failed: %s", urllib.parse.urlsplit(endpoint).netloc, e)
        return 0


class Pusher:
    """Sends a message to every browser a user turned notifications on in. `post` is replaced in tests."""

    def __init__(self, sessions, post=post):
        self.sessions, self.post = sessions, post

    def send_to_user(self, user_id, message):
        """How many of the user's browsers took it. Browsers that unsubscribed are forgotten."""
        with self.sessions() as s:
            keys = vapid_keys(s)
            subs = list(s.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)))
            if keys is None or not subs:
                return 0
            payload = json.dumps(message).encode()
            ok = 0
            for sub in subs:
                try:
                    body = encrypt(payload, unb64u(sub.p256dh), unb64u(sub.auth))
                except (ValueError, TypeError) as e:
                    log.warning("push subscription %s has bad keys: %s", sub.id, e)
                    s.delete(sub)
                    continue
                status = self.post(sub.endpoint, body, {
                    "Authorization": vapid_header(sub.endpoint, *keys), "Content-Encoding": "aes128gcm",
                    "Content-Type": "application/octet-stream", "TTL": str(TTL_S), "Urgency": "high"})
                if 200 <= status < 300:
                    ok += 1
                    sub.last_ok = utcnow()
                elif status in (404, 410):              # the browser unsubscribed or the subscription expired
                    s.delete(sub)
                else:
                    log.warning("push to %s: HTTP %s", urllib.parse.urlsplit(sub.endpoint).netloc, status)
            s.commit()
            return ok
