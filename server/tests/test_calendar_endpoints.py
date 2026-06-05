from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
import main, calendar_scope


@pytest.fixture
def client(monkeypatch):
    # StaticPool keeps a single shared connection so every Session(engine) in the
    # routes sees the same in-memory DB the tables were created on.
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    main._Base.metadata.create_all(eng)
    monkeypatch.setattr(main, "engine", eng)
    # one fake soccer match so the ICS has a VEVENT
    monkeypatch.setattr(calendar_scope, "collect_matches",
        lambda session, scope, params, user_token: [
            {"_sport":"soccer","id":"bsd-1","competition":"world_cup",
             "kickoff":"2026-06-12T18:00:00Z","status":"notstarted",
             "home":{"name":"France"},"away":{"name":"Argentina"},"venue":{"id":1}}])
    return TestClient(main.app)


def test_subscribe_returns_urls(client):
    r = client.post("/calendar/subscribe", json={"user_token":"tok","scope":"world_cup"})
    assert r.status_code == 200
    b = r.json()
    assert b["webcal_url"].startswith("webcal://clutchalarm.com/cal/")
    assert b["https_url"].startswith("https://clutchalarm.com/cal/")
    assert b["token"] and b["token"] in b["webcal_url"]


def test_subscribe_reuses_token_same_scope(client):
    a = client.post("/calendar/subscribe", json={"user_token":"tok","scope":"world_cup"}).json()
    b = client.post("/calendar/subscribe", json={"user_token":"tok","scope":"world_cup"}).json()
    assert a["token"] == b["token"]


def test_invalid_scope_400(client):
    assert client.post("/calendar/subscribe", json={"user_token":"tok","scope":"bogus"}).status_code == 400


def test_ics_feed(client):
    token = client.post("/calendar/subscribe", json={"user_token":"tok","scope":"world_cup"}).json()["token"]
    r = client.get(f"/calendar/{token}.ics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    body = r.text
    assert "BEGIN:VCALENDAR" in body and "BEGIN:VEVENT" in body
    assert "🇫🇷 France vs Argentina 🇦🇷" in body


def test_unknown_token_404(client):
    assert client.get("/calendar/nope.ics").status_code == 404


def test_revoke_then_404(client):
    token = client.post("/calendar/subscribe", json={"user_token":"tok","scope":"world_cup"}).json()["token"]
    assert client.post("/calendar/revoke", json={"user_token":"tok","token":token}).status_code == 200
    assert client.get(f"/calendar/{token}.ics").status_code == 404


def test_list_subscriptions(client):
    token = client.post("/calendar/subscribe", json={"user_token":"tok","scope":"all"}).json()["token"]
    subs = client.get("/calendar/subscriptions", params={"user_token":"tok"}).json()["subscriptions"]
    assert any(s["token"] == token and s["scope"] == "all" for s in subs)
