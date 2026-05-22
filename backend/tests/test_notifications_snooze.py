"""
IMS 2.0 - notification snooze rules
===================================
Snooze must (1) reject past times, (2) reject once the per-notification cap of
3 snoozes is hit, and (3) otherwise stamp snoozed_until + increment the count.
Mounts the router on a bare app with a fake collection (no DB needed).
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import notifications  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

FUTURE = "2099-01-01T09:00:00"
PAST = "2000-01-01T09:00:00"


class _FakeCol:
    def __init__(self, doc):
        self._doc = doc
        self.updated = None

    def find_one(self, *_a, **_k):
        return self._doc

    def update_one(self, _filter, update, *_a, **_k):
        self.updated = update
        return type("R", (), {"matched_count": 1, "modified_count": 1})()


def _client(monkeypatch, doc):
    app = FastAPI()
    app.include_router(notifications.router, prefix="/notifications")

    async def _user():
        return {"user_id": "u1", "roles": ["SALES_STAFF"]}

    app.dependency_overrides[get_current_user] = _user
    col = _FakeCol(doc)
    monkeypatch.setattr(notifications, "_coll", lambda: col)
    return TestClient(app), col


def test_snooze_rejects_past_time(monkeypatch):
    client, _ = _client(monkeypatch, {"notification_id": "n1", "user_id": "u1", "snooze_count": 0})
    r = client.post("/notifications/n1/snooze", params={"until": PAST})
    assert r.status_code == 400


def test_snooze_caps_at_three(monkeypatch):
    client, _ = _client(monkeypatch, {"notification_id": "n1", "user_id": "u1", "snooze_count": 3})
    r = client.post("/notifications/n1/snooze", params={"until": FUTURE})
    assert r.status_code == 400
    assert "Maximum" in r.json()["detail"]


def test_snooze_not_found(monkeypatch):
    client, _ = _client(monkeypatch, None)
    r = client.post("/notifications/n1/snooze", params={"until": FUTURE})
    assert r.status_code == 404


def test_snooze_success_increments(monkeypatch):
    client, col = _client(monkeypatch, {"notification_id": "n1", "user_id": "u1", "snooze_count": 1})
    r = client.post("/notifications/n1/snooze", params={"until": FUTURE})
    assert r.status_code == 200
    body = r.json()
    assert body["snooze_count"] == 2
    assert body["snoozes_remaining"] == 1
    # Wrote snoozed_until + incremented the counter.
    assert "$set" in col.updated and "snoozed_until" in col.updated["$set"]
    assert col.updated["$inc"]["snooze_count"] == 1
