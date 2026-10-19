import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from sqlalchemy import select

from hvaccloud import alerts, webpush
from hvaccloud.db import Alert, AlertPrefs, PushSubscription, Snapshot, User
from tests.test_alert_routing import FLAG, T0, add_homeowner, system
from tests.test_auth import XRW, client, login, people  # noqa: F401  (fixture)

# RFC 8291 appendix A
UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
RFC_BODY = ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_"
            "yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")
ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


def test_encryption_matches_rfc8291():
    out = webpush.encrypt(b"When I grow up, I want to be a watermelon", webpush.unb64u(UA_PUBLIC), webpush.unb64u(AUTH),
                          salt=webpush.unb64u("DGv6ra1nlYgDCS1FRnbzlw"),
                          as_private=webpush.unb64u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"))
    assert webpush.b64u(out) == RFC_BODY


def decrypt(body, ua_private=UA_PRIVATE, auth=AUTH):
    """What the browser does (RFC 8291), to check our messages."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, idlen = body[:16], body[20]
    as_public = body[21:21 + idlen]
    ua = webpush.private_from(webpush.unb64u(ua_private))
    shared = ua.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = webpush._hmac(webpush._hmac(webpush.unb64u(auth), shared),
                        b"WebPush: info\x00" + webpush.public_bytes(ua) + as_public + b"\x01")
    prk = webpush._hmac(salt, ikm)
    plain = AESGCM(webpush._hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]).decrypt(
        webpush._hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12], body[21 + idlen:], None)
    assert plain.endswith(b"\x02")
    return json.loads(plain[:-1])


def test_vapid_header_is_a_signed_jwt():
    priv, pub = webpush.new_key_pair()
    h = webpush.vapid_header(ENDPOINT, priv, pub, now=1000)
    t, k = h[len("vapid t="):].split(", k=")
    assert k == pub
    head, claims, sig = t.split(".")
    c = json.loads(webpush.unb64u(claims))
    assert c["aud"] == "https://fcm.googleapis.com" and c["exp"] == 1000 + 12 * 3600 and c["sub"]
    raw = webpush.unb64u(sig)
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.unb64u(pub))
    key.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
               f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))       # raises if wrong


def test_only_known_push_services():
    assert webpush.endpoint_ok(ENDPOINT)
    assert webpush.endpoint_ok("https://web.push.apple.com/QGx")
    assert webpush.endpoint_ok("https://updates.push.services.mozilla.com/wpush/v2/x")
    assert not webpush.endpoint_ok("http://fcm.googleapis.com/x")
    assert not webpush.endpoint_ok("https://192.168.1.1/x")
    assert not webpush.endpoint_ok("https://evilgoogleapis.com/x")


class FakePushService:
    def __init__(self, status=201):
        self.status, self.sent = status, []

    def __call__(self, endpoint, body, headers, timeout=10):
        self.sent.append((endpoint, decrypt(body), headers))
        return self.status


def subscribe(c, endpoint=ENDPOINT):
    return c.post("/api/push/subscribe", json={"endpoint": endpoint, "p256dh": UA_PUBLIC, "auth": AUTH}, headers=XRW)


def test_subscribe_and_test_notification(sessions, people):
    from fastapi.testclient import TestClient
    from hvaccloud.api import create_app
    fake = FakePushService()
    c = TestClient(create_app(sessions, publisher=lambda *a: True, push_post=fake))
    login(c, "owner@example.com")
    key = c.get("/api/push/key").json()["public_key"]
    assert len(webpush.unb64u(key)) == 65 and c.get("/api/push/key").json()["public_key"] == key   # made once
    assert subscribe(c, "https://10.0.0.1/hook").status_code == 422
    assert c.post("/api/push/subscribe", json={"endpoint": ENDPOINT, "p256dh": "x", "auth": AUTH}, headers=XRW).status_code == 422
    assert subscribe(c).json() == {"ok": True, "devices": 1}
    assert subscribe(c).json()["devices"] == 1                             # same browser again
    r = c.post("/api/push/test", headers=XRW).json()
    assert r == {"sent": 1, "devices": 1}
    endpoint, msg, headers = fake.sent[0]
    assert msg["title"] == "Fullscope" and headers["Content-Encoding"] == "aes128gcm" and headers["Authorization"].endswith("k=" + key)

    fake.status = 410                                                      # the browser unsubscribed
    assert c.post("/api/push/test", headers=XRW).json()["sent"] == 0
    with sessions() as s:
        assert s.scalar(select(PushSubscription)) is None
    subscribe(c)
    assert c.post("/api/push/unsubscribe", json={"endpoint": ENDPOINT}, headers=XRW).json() == {"ok": True}
    with sessions() as s:
        assert s.scalar(select(PushSubscription)) is None


def test_api_keys_cant_subscribe(sessions, seeded):
    assert client(sessions).get("/api/push/key", headers={"X-API-Key": seeded["key1"]}).status_code == 400


def add_sub(sessions, user_id, endpoint):
    with sessions() as s, s.begin():
        s.add(PushSubscription(user_id=user_id, endpoint=endpoint, p256dh=UA_PUBLIC, auth=AUTH))


def test_alerts_go_to_subscribed_owners_and_contractors(sessions, seeded):
    add_homeowner(sessions)
    with sessions() as s, s.begin():
        sy = system(s)
        tech = User(email="tech@example.com", role="contractor", account_id=sy.account_id)
        s.add(tech)
        s.flush()
        owner = s.scalar(select(User).where(User.email == "pat@example.com"))
        ids = {"owner": owner.id, "tech": tech.id}
    with sessions() as s, s.begin():
        sy = system(s)
        assert alerts.pushes_for(s, sy, alerts.sync(s, sy, [FLAG], T0, 0), T0, 6 * 3600) == []   # nobody subscribed
    add_sub(sessions, ids["owner"], ENDPOINT)
    add_sub(sessions, ids["tech"], ENDPOINT + "x")
    with sessions() as s, s.begin():
        sy = system(s)
        sy_id = sy.id
        ev = alerts.sync(s, sy, [], T0 + 60, 0) + alerts.sync(s, sy, [FLAG], T0 + 120, 0)
        jobs = alerts.pushes_for(s, sy, [e for e in ev if e[0] == "raised"], T0 + 120, 6 * 3600)
    by = {j[1]: j for j in jobs}
    assert set(by) == {ids["owner"], ids["tech"]}
    assert by[ids["owner"]][2]["title"] == "Home: Air from your vents isn't as cool as it should be"
    assert by[ids["owner"]][2]["url"].endswith(f"#/home/{sy_id}")
    assert by[ids["tech"]][2] == {"title": "Home: Warning", "body": FLAG["text"], "tag": f"fullscope-{sy_id}-dt_low",
                                  "url": by[ids["tech"]][2]["url"]}
    with sessions() as s, s.begin():
        s.add(AlertPrefs(system_id=sy_id, email_owner=False, email_contractor=True))
    with sessions() as s, s.begin():
        sy = system(s)
        assert [u for u, _ in alerts.push_users(s, sy)] == [ids["tech"]]   # the email switches apply


def test_ingest_sends_and_marks_pushed(sessions, seeded):
    from hvaccloud.ingest import Ingest
    add_homeowner(sessions)
    with sessions() as s:
        owner = s.scalar(select(User).where(User.email == "pat@example.com")).id
    add_sub(sessions, owner, ENDPOINT)
    fake = FakePushService()
    ing = Ingest(sessions, stale=60, hold=0, silence=600)
    ing.pusher = webpush.Pusher(sessions, post=fake)
    ing.spawn = lambda fn, *a: fn(*a)
    with sessions() as s:
        webpush.vapid_keys(s, create=True)
    with sessions() as s, s.begin():
        s.add(Snapshot(system_id=system(s).id, time=alerts.ts(T0 - 3600), mode="idle", data={"flags": []}))
    ing.sweep(now=T0)                                                     # silent for an hour: no data
    with sessions() as s:
        a = s.scalar(select(Alert))
    assert a is not None and a.pushed_at is not None
    assert len(fake.sent) == 1 and fake.sent[0][1]["url"].endswith("#/home/1")
    ing.sweep(now=T0 + 60)
    assert len(fake.sent) == 1                                            # still the same alert
