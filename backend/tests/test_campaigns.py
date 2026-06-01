"""
IMS 2.0 - Campaign Manager (campaigns router)
=============================================
Covers the campaign LAYER added on top of the marketing send infra:
  * CRUD lifecycle (create DRAFT -> get -> update -> duplicate -> delete),
  * segment listing + preview with a LIVE audience count (store-scoped),
  * SEND fans out via the SHARED send_notification and tags each produced
    notification_logs row with campaign_id/run_id (no parallel sender),
  * analytics rolls up the tagged logs,
  * lifecycle pause/resume/schedule,
  * RBAC: reads AUTHENTICATED, writes/sends gated to the bulk-send roles.

Self-contained: an in-memory bool-safe fake DB + a stubbed send_notification,
no live Mongo / provider. Mirrors test_marketing_referrals.py's pattern.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import campaigns as campaigns_router  # noqa: E402
from api.routers import auth as auth_mod  # noqa: E402


# ---------------------------------------------------------------------------
# In-memory fakes
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def limit(self, _n=None):
        return self

    def __iter__(self):
        return iter(self._docs)


def _matches(doc, query):
    for k, v in (query or {}).items():
        if k == "$or":
            if not any(_matches(doc, sub) for sub in v):
                return False
            continue
        dv = doc.get(k)
        if isinstance(v, dict):
            if "$ne" in v and dv == v["$ne"]:
                return False
            if "$in" in v and dv not in v["$in"]:
                return False
            if "$regex" in v:
                import re

                if dv is None or not re.search(v["$regex"], str(dv)):
                    return False
            if "$lte" in v and not (dv is not None and dv <= v["$lte"]):
                return False
            if "$gt" in v and not (dv is not None and dv > v["$gt"]):
                return False
        else:
            if dv != v:
                return False
    return True


class _FakeColl:
    def __init__(self, docs=None):
        self._docs = [dict(d) for d in (docs or [])]

    def find(self, query=None, projection=None):
        return _Cursor([dict(d) for d in self._docs if _matches(d, query or {})])

    def find_one(self, query=None, projection=None):
        for d in self._docs:
            if _matches(d, query or {}):
                return dict(d)
        return None

    def insert_one(self, doc):
        self._docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("campaign_id")})()

    def update_one(self, query, update):
        for d in self._docs:
            if _matches(d, query):
                d.update(update.get("$set", {}))
                return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()

    def delete_one(self, query):
        for i, d in enumerate(self._docs):
            if _matches(d, query):
                del self._docs[i]
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()

    def aggregate(self, pipeline):
        # Minimal support for the winback last-order-per-customer pipeline.
        match = {}
        group = None
        for stage in pipeline:
            if "$match" in stage:
                match = stage["$match"]
            if "$group" in stage:
                group = stage["$group"]
        rows = [d for d in self._docs if _matches(d, match)]
        if not group:
            return iter(rows)
        out = {}
        for d in rows:
            cid = d.get("customer_id")
            val = d.get("order_date") or d.get("created_at")
            if cid not in out or (val and val > out[cid]):
                out[cid] = val
        return iter([{"_id": k, "last": v} for k, v in out.items()])


class _FakeDB:
    is_connected = True

    def __init__(self, collections=None):
        self._collections = collections or {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = _FakeColl([])
        return self._collections[name]


def _token(roles, store_id="BV-PUN-01", uid="u1"):
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


@pytest.fixture
def env(monkeypatch):
    """Wire a fresh app + fake DB + stubbed sender. Returns (client, db, sent)."""
    db = _FakeDB()
    sent = []

    async def _fake_send(**kwargs):
        nid = f"NTF-TEST-{len(sent)}"
        sent.append(kwargs)
        # Mimic the real send_notification side effect: write a PENDING log row
        # so analytics + the campaign_id stamp have something to update.
        db.get_collection("notification_logs").insert_one(
            {
                "notification_id": nid,
                "status": "PENDING",
                "delivery_status": "QUEUED",
                "channel": kwargs.get("channel", "WHATSAPP"),
                "customer_id": kwargs.get("customer_id", ""),
            }
        )
        return {"notification_id": nid, "dispatched": False, "status": "PENDING"}

    monkeypatch.setattr(campaigns_router, "_get_db", lambda: db)
    monkeypatch.setattr(campaigns_router, "send_notification", _fake_send)
    monkeypatch.setattr(campaigns_router, "_dispatch_mode", lambda: "off")

    app = FastAPI()
    app.include_router(
        campaigns_router.router, prefix="/api/v1/marketing/campaigns"
    )
    client = TestClient(app)
    return client, db, sent


def _admin():
    return {"Authorization": f"Bearer {_token(['ADMIN'])}"}


def _staff():
    return {"Authorization": f"Bearer {_token(['SALES_STAFF'])}"}


# ---------------------------------------------------------------------------
# CRUD lifecycle
# ---------------------------------------------------------------------------


def test_create_list_get_update_delete(env):
    client, db, _ = env
    # Create
    r = client.post(
        "/api/v1/marketing/campaigns",
        json={
            "name": "Spring Rx Renewal",
            "campaign_type": "rx_renewal",
            "segment_id": "rx_renewal_due",
            "channels": ["WHATSAPP", "SMS"],
            "template_id": "PRESCRIPTION_EXPIRY",
        },
        headers=_admin(),
    )
    assert r.status_code == 200, r.text
    cid = r.json()["campaign"]["campaign_id"]
    assert r.json()["campaign"]["status"] == "DRAFT"
    assert r.json()["campaign"]["stats"] == {
        "sent": 0,
        "delivered": 0,
        "failed": 0,
        "converted": 0,
    }

    # List + summary
    r = client.get("/api/v1/marketing/campaigns", headers=_admin())
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["total"] == 1
    assert any(c["campaign_id"] == cid for c in body["campaigns"])

    # Get one
    r = client.get(f"/api/v1/marketing/campaigns/{cid}", headers=_admin())
    assert r.status_code == 200
    assert r.json()["campaign"]["name"] == "Spring Rx Renewal"

    # Update
    r = client.put(
        f"/api/v1/marketing/campaigns/{cid}",
        json={"name": "Spring Rx Renewal v2", "channels": ["WHATSAPP"]},
        headers=_admin(),
    )
    assert r.status_code == 200
    assert r.json()["campaign"]["name"] == "Spring Rx Renewal v2"
    assert r.json()["campaign"]["channels"] == ["WHATSAPP"]

    # Delete
    r = client.delete(f"/api/v1/marketing/campaigns/{cid}", headers=_admin())
    assert r.status_code == 200
    r = client.get(f"/api/v1/marketing/campaigns/{cid}", headers=_admin())
    assert r.status_code == 404


def test_create_rejects_unknown_segment(env):
    client, _, _ = env
    r = client.post(
        "/api/v1/marketing/campaigns",
        json={"name": "Bad", "segment_id": "does_not_exist"},
        headers=_admin(),
    )
    assert r.status_code == 422


def test_duplicate_resets_stats_and_status(env):
    client, db, _ = env
    # Seed a SENT campaign with stats directly.
    db.get_collection(campaigns_router.CAMPAIGNS_COLLECTION).insert_one(
        {
            "campaign_id": "CMP-SRC",
            "name": "Source",
            "campaign_type": "custom",
            "segment_id": "all_consented",
            "channels": ["WHATSAPP"],
            "template_id": "ANNUAL_CHECKUP_REMINDER",
            "schedule": {"kind": "one_time"},
            "status": "COMPLETED",
            "store_id": "BV-PUN-01",
            "stats": {"sent": 50, "delivered": 40, "failed": 2, "converted": 5},
        }
    )
    r = client.post(
        "/api/v1/marketing/campaigns/CMP-SRC/duplicate", headers=_admin()
    )
    assert r.status_code == 200, r.text
    clone = r.json()["campaign"]
    assert clone["campaign_id"] != "CMP-SRC"
    assert clone["status"] == "DRAFT"
    assert clone["stats"] == {"sent": 0, "delivered": 0, "failed": 0, "converted": 0}
    assert clone["name"].endswith("(copy)")


# ---------------------------------------------------------------------------
# Segments + preview
# ---------------------------------------------------------------------------


def test_segments_list_with_counts(env):
    client, db, _ = env
    # Two consented customers in this store, one opted out.
    db.get_collection("customers")._docs = [
        {"customer_id": "C1", "name": "Asha", "mobile": "9000000001",
         "home_store_id": "BV-PUN-01", "marketing_consent": True},
        {"customer_id": "C2", "name": "Ravi", "mobile": "9000000002",
         "preferred_store_id": "BV-PUN-01"},  # no pref -> consented
        {"customer_id": "C3", "name": "Out", "mobile": "9000000003",
         "home_store_id": "BV-PUN-01", "marketing_consent": False},
    ]
    r = client.get("/api/v1/marketing/campaigns/segments", headers=_admin())
    assert r.status_code == 200, r.text
    segs = {s["id"]: s for s in r.json()["segments"]}
    assert "all_consented" in segs
    # HQ admin with no store_id -> all stores; 2 consented contactable.
    assert segs["all_consented"]["audience_count"] == 2


def test_segment_preview_masks_phone(env):
    client, db, _ = env
    db.get_collection("customers")._docs = [
        {"customer_id": "C1", "name": "Asha Kumar", "mobile": "9812345678",
         "home_store_id": "BV-PUN-01", "marketing_consent": True},
    ]
    r = client.post(
        "/api/v1/marketing/campaigns/segments/preview",
        json={"segment_id": "all_consented", "sample_size": 5},
        headers=_admin(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["audience_count"] == 1
    assert body["sample"][0]["masked_phone"].endswith("5678")
    assert body["sample"][0]["masked_phone"].startswith("*")


def test_winback_segment_aggregates_lapsed(env):
    client, db, _ = env
    old = (datetime.now() - timedelta(days=400)).isoformat()
    recent = (datetime.now() - timedelta(days=10)).isoformat()
    db.get_collection("orders")._docs = [
        {"customer_id": "LAPSED", "store_id": "BV-PUN-01", "order_date": old},
        {"customer_id": "ACTIVE", "store_id": "BV-PUN-01", "order_date": recent},
    ]
    db.get_collection("customers")._docs = [
        {"customer_id": "LAPSED", "name": "Old Buyer", "mobile": "9000000001",
         "home_store_id": "BV-PUN-01", "marketing_consent": True},
        {"customer_id": "ACTIVE", "name": "New Buyer", "mobile": "9000000002",
         "home_store_id": "BV-PUN-01", "marketing_consent": True},
    ]
    r = client.post(
        "/api/v1/marketing/campaigns/segments/preview",
        json={"segment_id": "winback_lapsed"},
        headers=_admin(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["audience_count"] == 1  # only LAPSED


# ---------------------------------------------------------------------------
# SEND - reuses send_notification + tags campaign_id; analytics rolls up
# ---------------------------------------------------------------------------


def test_send_fans_out_and_tags_then_analytics(env):
    client, db, sent = env
    db.get_collection("customers")._docs = [
        {"customer_id": "C1", "name": "Asha", "mobile": "9000000001",
         "home_store_id": "BV-PUN-01", "marketing_consent": True},
        {"customer_id": "C2", "name": "Ravi", "mobile": "9000000002",
         "home_store_id": "BV-PUN-01", "marketing_consent": True},
    ]
    # Create a one-time custom campaign over all consented.
    r = client.post(
        "/api/v1/marketing/campaigns",
        json={
            "name": "Checkup blast",
            "segment_id": "all_consented",
            "channels": ["WHATSAPP"],
            "template_id": "ANNUAL_CHECKUP_REMINDER",
            "store_id": "BV-PUN-01",
        },
        headers=_admin(),
    )
    cid = r.json()["campaign"]["campaign_id"]

    # Send now.
    r = client.post(f"/api/v1/marketing/campaigns/{cid}/send", headers=_admin())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["queued"] == 2
    assert body["audience"] == 2
    assert body["dispatch_mode"] == "off"
    assert body["status"] == "COMPLETED"  # one-time -> completed
    # The shared sender was used (not a parallel sender).
    assert len(sent) == 2
    assert all(s["related_entity_type"] == "campaign" for s in sent)

    # Each produced log row got tagged with campaign_id.
    logs = list(db.get_collection("notification_logs").find({"campaign_id": cid}))
    assert len(logs) == 2

    # Analytics rolls up the tagged logs.
    r = client.get(
        f"/api/v1/marketing/campaigns/{cid}/analytics", headers=_admin()
    )
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["totals"]["audience_messages"] == 2
    assert a["totals"]["queued"] == 2  # PENDING rows


def test_send_blocked_when_paused(env):
    client, db, _ = env
    db.get_collection(campaigns_router.CAMPAIGNS_COLLECTION).insert_one(
        {
            "campaign_id": "CMP-PAUSED",
            "name": "Paused",
            "segment_id": "all_consented",
            "channels": ["WHATSAPP"],
            "template_id": "ANNUAL_CHECKUP_REMINDER",
            "schedule": {"kind": "one_time"},
            "status": "PAUSED",
            "store_id": "BV-PUN-01",
            "stats": {"sent": 0, "delivered": 0, "failed": 0, "converted": 0},
        }
    )
    r = client.post(
        "/api/v1/marketing/campaigns/CMP-PAUSED/send", headers=_admin()
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_schedule_pause_resume(env):
    client, db, _ = env
    r = client.post(
        "/api/v1/marketing/campaigns",
        json={
            "name": "Recurring",
            "segment_id": "all_consented",
            "channels": ["WHATSAPP"],
            "template_id": "ANNUAL_CHECKUP_REMINDER",
        },
        headers=_admin(),
    )
    cid = r.json()["campaign"]["campaign_id"]

    r = client.post(
        f"/api/v1/marketing/campaigns/{cid}/schedule",
        json={"schedule": {"kind": "recurring", "frequency": "weekly", "send_at": "2026-06-10T09:00:00"}},
        headers=_admin(),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "SCHEDULED"

    r = client.post(f"/api/v1/marketing/campaigns/{cid}/pause", headers=_admin())
    assert r.json()["status"] == "PAUSED"

    r = client.post(f"/api/v1/marketing/campaigns/{cid}/resume", headers=_admin())
    # Recurring schedule resumes to SCHEDULED.
    assert r.json()["status"] == "SCHEDULED"


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


def test_reads_open_to_staff_writes_require_role(env):
    client, _, _ = env
    # Read is fine for any logged-in staff.
    r = client.get("/api/v1/marketing/campaigns", headers=_staff())
    assert r.status_code == 200
    r = client.get("/api/v1/marketing/campaigns/segments", headers=_staff())
    assert r.status_code == 200
    # Create is forbidden for sales staff (not a bulk-send role).
    r = client.post(
        "/api/v1/marketing/campaigns",
        json={"name": "X", "segment_id": "all_consented"},
        headers=_staff(),
    )
    assert r.status_code == 403


def test_store_role_cannot_open_other_store_campaign(env):
    client, db, _ = env
    db.get_collection(campaigns_router.CAMPAIGNS_COLLECTION).insert_one(
        {
            "campaign_id": "CMP-OTHER",
            "name": "Other store",
            "segment_id": "all_consented",
            "channels": ["WHATSAPP"],
            "template_id": "ANNUAL_CHECKUP_REMINDER",
            "schedule": {"kind": "one_time"},
            "status": "DRAFT",
            "store_id": "BV-OTHER-99",
            "stats": {"sent": 0, "delivered": 0, "failed": 0, "converted": 0},
        }
    )
    # A STORE_MANAGER pinned to BV-PUN-01 must not see BV-OTHER-99's campaign.
    headers = {"Authorization": f"Bearer {_token(['STORE_MANAGER'], store_id='BV-PUN-01')}"}
    r = client.get("/api/v1/marketing/campaigns/CMP-OTHER", headers=headers)
    assert r.status_code == 404
