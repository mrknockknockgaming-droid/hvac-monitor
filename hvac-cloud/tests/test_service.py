import datetime as dt
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from hvaccloud import service
from hvaccloud.api import create_app
from hvaccloud.db import RuntimeDay, System
from hvaccloud.ingest import Ingest
from tests.test_calc import COOL_IN, COOL_OUT

T0 = 1_790_000_000.0
TODAY = dt.date(2026, 10, 3)


def system_id(sessions):
    with sessions() as s:
        return s.scalar(select(System.id).where(System.site_id == "home"))


def add_days(sessions, sid, start, n, blower_h):
    with sessions() as s, s.begin():
        for k in range(n):
            s.add(RuntimeDay(system_id=sid, day=start + dt.timedelta(days=k), blower_s=blower_h * 3600,
                             compressor_s=blower_h * 3000))


def test_ingest_adds_up_run_time_and_skips_outages(sessions, seeded, tables):
    i = Ingest(sessions, tables)
    for k in range(60):                                  # 10 min of cooling, both nodes every 10 s
        i.handle("hvac/home/indoor/telemetry", json.dumps(COOL_IN).encode(), now=T0 + k * 10)
        i.handle("hvac/home/outdoor/telemetry", json.dumps(COOL_OUT).encode(), now=T0 + k * 10 + 5)
    i.handle("hvac/home/outdoor/telemetry", json.dumps(COOL_OUT).encode(), now=T0 + 3600)   # after an outage
    with sessions() as s:
        rows = list(s.scalars(select(RuntimeDay)))
    assert len(rows) == 1
    assert abs(rows[0].blower_s - 595) < 1 and abs(rows[0].compressor_s - 595) < 1      # the hour gap isn't counted


def test_defaults_before_anything_is_set(sessions, seeded):
    with sessions() as s:
        v = service.service_view(s, system_id(sessions), today=TODAY)
    assert v["contractor"] is None
    assert [(x["kind"], x["status"], x["interval_days"]) for x in v["items"]] == [("filter", "unset", 90), ("tuneup", "unset", 182)]


def test_filter_comes_due_on_run_hours_before_days(sessions, seeded):
    sid = system_id(sessions)
    add_days(sessions, sid, TODAY - dt.timedelta(days=29), 30, blower_h=16)         # hot month: 480 h
    with sessions() as s, s.begin():
        service.get_item(s, sid, "filter").last_done = TODAY - dt.timedelta(days=29)
    with sessions() as s:
        f = service.service_view(s, sid, today=TODAY)["items"][0]
    assert f["days_since"] == 29 and f["run_hours_since"] == 480.0
    assert f["status"] == "soon" and f["progress"] == 0.96                          # 480 / 500 h, not 29 / 90 days
    assert f["next_due"] == (TODAY + dt.timedelta(days=1)).isoformat()             # 20 h left at 16 h a day


def test_tuneup_by_days(sessions, seeded):
    sid = system_id(sessions)
    with sessions() as s, s.begin():
        service.get_item(s, sid, "tuneup").last_done = TODAY - dt.timedelta(days=200)
    with sessions() as s:
        t = service.service_view(s, sid, today=TODAY)["items"][1]
    assert t["status"] == "due" and t["run_hours_since"] == 0.0
    assert t["next_due"] == (TODAY - dt.timedelta(days=18)).isoformat()


def test_service_api(sessions, seeded):
    c = TestClient(create_app(sessions, publisher=lambda *a: True))
    h, h2 = {"X-API-Key": seeded["key1"]}, {"X-API-Key": seeded["key2"]}
    sid = c.get("/api/systems", headers=h).json()[0]["id"]
    base = f"/api/systems/{sid}/service"
    v = c.put(base + "/contractor", headers=h, json={"name": "Saguaro Heating & Air", "phone": " (480) 555-0142 ", "email": ""}).json()
    assert v["contractor"] == {"name": "Saguaro Heating & Air", "phone": "(480) 555-0142", "email": None}
    v = c.post(base + "/items/filter/done", headers=h, json={"date": "2026-09-01"}).json()
    assert v["items"][0]["last_done"] == "2026-09-01"
    v = c.patch(base + "/items/filter", headers=h, json={"interval_days": 60, "interval_run_hours": None}).json()
    assert v["items"][0]["interval_days"] == 60 and v["items"][0]["interval_run_hours"] is None
    assert v["items"][0]["last_done"] == "2026-09-01"                              # untouched fields stay
    assert c.patch(base + "/items/filter", headers=h, json={"interval_days": None}).status_code == 422
    assert c.post(base + "/items/coil/done", headers=h).status_code == 404
    assert c.get(base, headers=h2).status_code == 404
    c.put(base + "/contractor", headers=h, json={})
    assert c.get(base, headers=h).json()["contractor"] is None
