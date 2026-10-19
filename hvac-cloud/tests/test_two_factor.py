import time

from fastapi.testclient import TestClient
from sqlalchemy import select

from hvaccloud.api import create_app

from hvaccloud import auth
from hvaccloud.db import PasswordReset, User, hash_key, utcnow
from tests.test_auth import PW, XRW, client, login, people  # noqa: F401  (fixture)


def test_totp_matches_rfc6238():
    # RFC 6238 appendix B, SHA-1 key "12345678901234567890", last 6 digits
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    for t, want in [(59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037")]:
        assert auth.totp_code(secret, t // 30) == want
    assert auth.totp_check(secret, "287 082", now=59) == 1
    assert auth.totp_check(secret, "287082", now=59 + 30) == 1            # one step late is fine
    assert auth.totp_check(secret, "287082", now=59 + 90) is None
    assert auth.totp_check(secret, "287082", last_step=1, now=59) is None  # already used
    assert auth.totp_check(secret, "28708", now=59) is None
    uri = auth.totp_uri(secret, "a@b.com")
    assert uri.startswith("otpauth://totp/Fullscope:a%40b.com?secret=" + secret) and "issuer=Fullscope" in uri


def setup_2fa(c, email="tech@example.com"):
    assert login(c, email).status_code == 200
    r = c.post("/api/auth/2fa/setup", json={"password": PW}, headers=XRW)
    assert r.status_code == 200
    secret = r.json()["secret"]
    assert "secret=" + secret in r.json()["uri"]
    bad = c.post("/api/auth/2fa/enable", json={"code": "000000" if auth.totp_check(secret, "000000") is None else "111111"}, headers=XRW)
    assert bad.status_code == 422
    r = c.post("/api/auth/2fa/enable", json={"code": auth.totp_code(secret, int(time.time() // 30))}, headers=XRW)
    assert r.status_code == 200
    codes = r.json()["recovery_codes"]
    assert len(codes) == 8 and len(set(codes)) == 8
    return secret, codes


def test_sign_in_asks_for_the_code(sessions, people, monkeypatch):
    c = client(sessions)
    assert c.post("/api/auth/2fa/setup", json={"password": "wrong"}, headers=XRW).status_code == 401   # not signed in
    login(c, "tech@example.com")
    assert c.post("/api/auth/2fa/setup", json={"password": "wrong pass"}, headers=XRW).status_code == 401
    secret, codes = setup_2fa(c)
    assert c.get("/api/auth/2fa").json() == {"enabled": True, "recovery_left": 8}
    assert c.post("/api/auth/2fa/setup", json={"password": PW}, headers=XRW).status_code == 409

    d = client(sessions)
    r = login(d, "tech@example.com")
    assert r.status_code == 200 and r.json() == {"totp_required": True} and auth.COOKIE not in r.cookies
    assert d.get("/api/auth/me").status_code == 401
    r = d.post("/api/auth/login", json={"email": "tech@example.com", "password": PW, "code": "12345"})
    assert r.status_code == 401
    # the code used to turn it on can't be used again; the next one works once
    with sessions() as s:
        last = s.scalar(select(User).where(User.email == "tech@example.com")).totp_last_step
    assert d.post("/api/auth/login", json={"email": "tech@example.com", "password": PW,
                                           "code": auth.totp_code(secret, last)}).status_code == 401
    real = time.time
    monkeypatch.setattr(time, "time", lambda: (last + 1) * 30 + 1)
    nxt = auth.totp_code(secret, last + 1)
    r = d.post("/api/auth/login", json={"email": "tech@example.com", "password": PW, "code": nxt})
    assert r.status_code == 200 and r.json()["role"] == "contractor"
    r2 = client(sessions).post("/api/auth/login", json={"email": "tech@example.com", "password": PW, "code": nxt})
    assert r2.status_code == 401                                       # replayed
    monkeypatch.setattr(time, "time", real)

    # a recovery code works once
    e = client(sessions)
    r = e.post("/api/auth/login", json={"email": "tech@example.com", "password": PW, "code": codes[0].upper()})
    assert r.status_code == 200
    assert e.post("/api/auth/login", json={"email": "tech@example.com", "password": PW, "code": codes[0]}).status_code == 401
    assert e.get("/api/auth/2fa").json()["recovery_left"] == 7

    # the wrong password never reaches the code question
    assert login(client(sessions), "tech@example.com", "nope nope nope").status_code == 401


def test_turning_it_off_needs_the_password(sessions, people):
    c = client(sessions)
    setup_2fa(c)
    assert c.post("/api/auth/2fa/disable", json={"password": "wrong pass"}, headers=XRW).status_code == 401
    assert c.post("/api/auth/2fa/disable", json={"password": PW}, headers=XRW).json() == {"enabled": False}
    assert login(client(sessions), "tech@example.com").json()["role"] == "contractor"


def test_turning_it_on_signs_out_other_browsers(sessions, people):
    other = client(sessions)
    login(other, "tech@example.com")
    c = client(sessions)
    setup_2fa(c)
    assert other.get("/api/auth/me").status_code == 401
    assert c.get("/api/auth/me").status_code == 200


def test_reset_link_does_not_skip_the_code(sessions, people):
    c = client(sessions)
    setup_2fa(c)
    with sessions() as s, s.begin():
        user = s.scalar(select(User).where(User.email == "tech@example.com"))
        s.add(PasswordReset(token_hash=hash_key("tok"), user_id=user.id, expires_at=utcnow().replace(year=2100)))
    d = client(sessions)
    r = d.post("/api/auth/reset", json={"token": "tok", "password": "a brand new password"})
    assert r.json() == {"totp_required": True, "email": "tech@example.com"} and auth.COOKIE not in r.cookies
    assert c.get("/api/auth/me").status_code == 401                    # the old session is gone too
    assert login(d, "tech@example.com", "a brand new password").json() == {"totp_required": True}


def test_api_keys_have_no_second_factor(sessions, seeded):
    c = client(sessions)
    assert c.get("/api/auth/2fa", headers={"X-API-Key": seeded["key1"]}).status_code == 400


def test_failed_sign_ins_from_one_address_are_limited(sessions, people):
    c = TestClient(create_app(sessions, publisher=lambda *a: True), client=("127.0.0.1", 50000))   # as cloudflared
    tunnel = lambda ip: {"CF-Connecting-IP": ip}
    for i in range(30):
        c.post("/api/auth/login", json={"email": f"guess{i}@example.com", "password": "nope"}, headers=tunnel("198.51.100.7"))
    r = c.post("/api/auth/login", json={"email": "tech@example.com", "password": PW}, headers=tunnel("198.51.100.7"))
    assert r.status_code == 429                                        # even the right password, from there
    assert c.post("/api/auth/login", json={"email": "tech@example.com", "password": PW}, headers=tunnel("203.0.113.9")).status_code == 200
