"""
IMS 2.0 - Marketing bulk-send consent gate
==========================================
POST /marketing/notifications/send-bulk must SKIP customers whose
marketing_consent is False (consent / DLT compliance), and send to consented
customers, customers with no preference (missing/None -> defaults consented),
and ad-hoc phone recipients (no customer_id). Self-contained: a fake bool-safe
DB wrapper + stubbed send_notification, no live DB / provider.
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
from api.routers import auth as auth_mod  # noqa: E402


class _FakeColl:
    """Minimal in-memory collection supporting find_one by query equality."""

    def __init__(self, docs):
        self._docs = list(docs)

    def find_one(self, query=None, projection=None):
        q = query or {}
        for d in self._docs:
            if all(d.get(k) == v for k, v in q.items()):
                return dict(d)
        return None


class _FakeDB:
    """Bool-safe wrapper (has get_collection); mirrors the real connection."""

    is_connected = True

    def __init__(self, collections):
        self._collections = collections

    def get_collection(self, name):
        return self._collections.get(name, _FakeColl([]))


def _staff_token(roles, store_id="BV-PUN-01", uid="u1"):
    return jwt.encode(
        {
            "sub": uid,
            "user_id": uid,
            "username": "tester",
            "roles": roles,
            "active_store_id": store_id,
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )


def test_bulk_send_skips_opted_out_customers(monkeypatch):
    """Opted-out (marketing_consent False) -> skipped; consented / no-pref /
    ad-hoc phone -> queued; response reports the skipped count."""
    db = _FakeDB(
        {
            "customers": _FakeColl(
                [
                    {"customer_id": "C_OPTED_IN", "marketing_consent": True},
                    {"customer_id": "C_OPTED_OUT", "marketing_consent": False},
                    {"customer_id": "C_NO_PREF"},  # missing -> consented
                ]
            )
        }
    )
    monkeypatch.setattr(marketing_router, "_get_db", lambda: db)

    async def _fake_send(**kwargs):
        return {"status": "queued"}

    monkeypatch.setattr(marketing_router, "send_notification", _fake_send)
    # Defuse the per-user rate limiter so the fan-out isn't throttled.
    monkeypatch.setattr(
        marketing_router, "_check_notification_rate", lambda *_a, **_k: None
    )

    app = FastAPI()
    app.include_router(marketing_router.router, prefix="/api/v1/marketing")
    client = TestClient(app)

    tok = _staff_token(["STORE_MANAGER"])
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
