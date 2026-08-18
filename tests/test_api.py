"""API contract tests (no InfluxDB required).

Every data endpoint must degrade to an empty result — not a 500 — when the
database is unreachable, and the layout/groups stores must round-trip.
InfluxDB-dependent paths are exercised only for their failure behaviour.
"""

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def test_version(client):
    r = client.get("/api/version")
    assert r.status_code == 200
    assert "version" in r.json()


# ── Layout persistence ────────────────────────────────────────────────────────

def test_layout_roundtrip(client):
    payload = {"layout": [{"id": "tacho", "x": 0, "y": 0, "w": 3, "h": 3}], "hidden": ["engine"]}
    assert client.post("/api/layout", json=payload).json() == {"ok": True}
    assert client.get("/api/layout").json() == payload
    assert client.delete("/api/layout").json() == {"ok": True}
    assert client.get("/api/layout").json() == {}


def test_layout_rejects_bad_payloads(client):
    assert client.post("/api/layout", json={"nope": 1}).status_code == 400
    assert client.post("/api/layout", json=[1, 2]).status_code == 400


# ── Groups / renames ──────────────────────────────────────────────────────────

def test_groups_roundtrip(client):
    payload = {"groups": [{"name": "Test", "session_ids": ["a"]}], "renames": {"a": "My Run"}}
    assert client.post("/api/groups", json=payload).json() == {"ok": True}
    assert client.get("/api/groups").json() == payload


def test_groups_rejects_non_object(client):
    assert client.post("/api/groups", json=[1, 2]).status_code == 400


# ── Data endpoints degrade without InfluxDB ───────────────────────────────────

def test_analysis_laps_empty_and_garbage(client):
    assert client.get("/api/analysis/laps").json() == []
    assert client.get("/api/analysis/laps?picks=not-a-pick").json() == []
    # Valid shape, unreachable DB -> empty, not 500.
    assert client.get("/api/analysis/laps?picks=abcd1234:1").json() == []


def test_compare_wrapper(client):
    assert client.get("/api/sessions/abcd1234/compare").json() == []
    assert client.get("/api/sessions/abcd1234/compare?laps=x,y").json() == []


def test_session_id_injection_rejected(client):
    # A quote in the id must be rejected before it reaches a Flux literal.
    r = client.get('/api/sessions/x%22y/laps')
    assert r.status_code == 200
    assert r.json() == []
    r = client.get('/api/sessions/x%22y/track')
    assert r.json()["points"] == []


def test_live_snapshot_offline(client):
    # No relay file written in tests -> stream must report not-live.
    assert client.get("/api/live").json() == {"is_live": False}


# ── Static serving + cache policy ─────────────────────────────────────────────

def test_static_pages_served(client):
    for path in ("/", "/index.html", "/analytics.html", "/vendor/uplot.iife.min.js"):
        assert client.get(path).status_code == 200


def test_html_revalidates_but_vendor_caches(client):
    html = client.get("/index.html")
    assert html.headers.get("cache-control") == "no-cache, must-revalidate"

    # ETag revalidation stays cheap: 304 with an empty body.
    etag = html.headers["etag"]
    r304 = client.get("/index.html", headers={"If-None-Match": etag})
    assert r304.status_code == 304

    vendor = client.get("/vendor/uplot.iife.min.js")
    assert "no-cache" not in (vendor.headers.get("cache-control") or "")


def test_api_json_not_cached(client):
    r = client.get("/api/version")
    assert r.headers.get("cache-control") == "no-cache, must-revalidate"
