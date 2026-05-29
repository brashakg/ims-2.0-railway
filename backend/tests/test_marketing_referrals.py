"""
IMS 2.0 - marketing bulk-send consent gating
============================================
Endpoint smoke test for the marketing router via a FastAPI TestClient with a
monkeypatched in-memory DB (no live Mongo) and a real signed JWT. Restores the
file header that had gone missing — the body referenced _client / _token /
marketing_router with no definitions, so it failed at runtime with
`NameError: name '_client' is not defined`.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import marketing as marketing_router  # noqa: E402

SECRET = os.environ["JWT_SECRET_KEY"]


class _FakeCollection:
    def __init__(self, docs):
        self._docs = list(docs or [])

    def find_one(self, query):
        cid = (query or {}).get("customer_id")
        for d in self._docs:
            if d.get("customer_id") == cid:
                return d
        return None


class _FakeDB:
    def __init__(self, data):
        self._data = data or {}

    def get_collection(self, name):
        return _FakeCollection(self._data.get(name, []))


def _client(monkeypatch, docs):
    """Mount the marketing router on a bare app with a fake in-memory DB.
    Returns (TestClient, fake_db)."""
    app = FastAPI()
    app.include_router(marketing_router.router, prefix="/api/v1/marketing")
    db = _FakeDB(docs)
    monkeypatch.setattr(marketing_router, "_get_db", lambda: db)
    return TestClient(app), db


def _token(roles, store_ids=None, active_store="S1"):
    return jwt.encode(
        {
            "sub": "u1",
            "user_id": "u1",
            "roles": roles,
            "store_ids": store_ids if store_ids is not None else ["S1"],
            "active_store_id": active_store,
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        SECRET,
        algorithm="HS256",
    )


async def test_bulk_send_skips_opted_out_customers(monkeypatch):
    """POST /marketing/notifications/send-bulk must SKIP customers whose
    marketing_consent is False (consent/DLT compliance), send to consented +
    None/missing (defaults to consented), and report a skipped count.
    send_notification is stubbed so no real provider is hit."""
    docs = {
        "customers": [
            {"customer_id": "C_OPTED_IN", "marketing_consent": True},
            {"customer_id": "C_OPTED_OUT", "marketing_consent": False},
            {"customer_id": "C_NO_PREF"},  # missing -> defaults consented
        ]
    }
    client, _db = _client(monkeypatch, docs)

    async def _fake_send(**kwargs):
        return {"status": "queued"}

    monkeypatch.setattr(marketing_router, "send_notification", _fake_send)

    tok = _token(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/notifications/send-bulk",
        json={
            "template_id": "promo",
            "channel": "WHATSAPP",
            "recipients": [
                {"customer_id": "C_OPTED_IN", "phone": "9000000001"},
                {"customer_id": "C_OPTED_OUT", "phone": "9000000002"},
                {"customer_id": "C_NO_PREF", "phone": "9000000003"},
                {"phone": "9000000004"},  # ad-hoc, no customer_id -> sent
            ],
        },
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["skipped"] == 1
    statuses = {x["phone"]: x["status"] for x in body["results"]}
    assert statuses["9000000002"] == "skipped"  # opted out
    assert statuses["9000000001"] == "queued"  # opted in
    assert statuses["9000000003"] == "queued"  # no pref -> consented
    assert statuses["9000000004"] == "queued"  # ad-hoc phone
