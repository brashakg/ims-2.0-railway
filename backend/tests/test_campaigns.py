"""
IMS 2.0 - Campaign layer tests
==============================
Covers the new campaign layer (routers/campaigns.py + services/campaign_segments.py):

  - Campaign CRUD + lifecycle (create DRAFT -> schedule SCHEDULED -> send ->
    COMPLETED/ACTIVE; pause/resume; duplicate; delete guards).
  - Segment audience counting (rx_expiry / birthday / winback / by_store /
    by_customer_type / recent_buyers) against a fake in-memory DB.
  - Send is SIMULATED under DISPATCH_MODE!=live (no real MSG91), tags every
    notification_logs row with campaign_id, skips opted-out customers, and writes
    an immutable campaign_audit row.
  - Analytics aggregation from notification_logs by campaign_id.
  - Routes are role-gated (a non-privileged role is 403'd) and catalogued in
    rbac_policy.POLICY (the coverage-lock test enforces the latter separately;
    here we assert the gate behaviour).

Self-contained: a fake in-memory DB + stubbed send_notification (NO live DB, NO
provider calls). DISPATCH_MODE is irrelevant because send_notification is stubbed
to never touch a provider -- but we also assert the production code path tags the
row and never imports/calls MSG91.
"""

from __future__ import annotations

import os
import sys
import copy
import uuid
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("DISPATCH_MODE", "off")

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import campaigns as camp_mod  # noqa: E402
from api.routers import auth as auth_mod  # noqa: E402
from api.services import campaign_segments as seg  # noqa: E402


# ---------------------------------------------------------------------------
# Auth token helper
# ---------------------------------------------------------------------------


def _tok(roles, uid="u1", store_id="BV-PUN-01"):
    return jwt.encode(
        {
            "sub": uid,
            "user_id": uid,
            "username": "tester",
            "roles": list(roles),
            "active_store_id": store_id,
            "store_ids": [store_id],
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )


# ---------------------------------------------------------------------------
# Fake Mongo (richer than the marketing test's -- supports update/$inc/delete)
# ---------------------------------------------------------------------------


def _matches(doc, query):
    for key, val in (query or {}).items():
        if key == "$or":
            if not any(_matches(doc, sub) for sub in val):
                return False
            continue
        actual = doc.get(key)
        if isinstance(val, dict):
            if "$in" in val and actual not in val["$in"]:
                return False
            if "$nin" in val and actual in val["$nin"]:
                return False
            if "$gte" in val and (actual is None or actual < val["$gte"]):
                return False
            if "$lte" in val and (actual is None or actual > val["$lte"]):
                return False
            if "$ne" in val and actual == val["$ne"]:
                return False
        else:
            if actual != val:
                return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = [copy.deepcopy(d) for d in (docs or [])]

    def find_one(self, query=None, projection=None, sort=None):
        for d in self.docs:
            if _matches(d, query or {}):
                return copy.deepcopy(d)
        return None

    def find(self, query=None, projection=None):
        return _Cursor([copy.deepcopy(d) for d in self.docs if _matches(d, query or {})])

    def count_documents(self, query=None):
        return sum(1 for d in self.docs if _matches(d, query or {}))

    def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    def _apply_update(self, doc, update):
        if "$set" in update:
            for k, v in update["$set"].items():
                doc[k] = v
        if "$inc" in update:
            for k, v in update["$inc"].items():
                doc[k] = (doc.get(k, 0) or 0) + v

    def update_one(self, query, update, **kw):
        for d in self.docs:
            if _matches(d, query or {}):
                self._apply_update(d, update)
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def update_many(self, query, update):
        n = 0
        for d in self.docs:
            if _matches(d, query or {}):
                self._apply_update(d, update)
                n += 1
        return type("R", (), {"modified_count": n})()

    def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _matches(d, query or {}):
                del self.docs[i]
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()


class _FakeDB:
    is_connected = True

    def __init__(self, collections=None):
        self._cols = collections or {}

    def get_collection(self, name):
        if name not in self._cols:
            self._cols[name] = _FakeColl()
        return self._cols[name]


# A capturing fake send_notification: records calls + writes a notification_logs
# row exactly like the real one (so the router's post-send campaign_id tag, and
# analytics, can read it back). NEVER touches a provider.
class _SendRecorder:
    def __init__(self, db):
        self.db = db
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        nid = f"NTF-{uuid.uuid4().hex[:8].upper()}"
        row = {
            "notification_id": nid,
            "store_id": kwargs.get("store_id", ""),
            "customer_id": kwargs.get("customer_id", ""),
            "customer_phone": kwargs.get("customer_phone", ""),
            "template_id": kwargs.get("template_id", ""),
            "channel": kwargs.get("channel", "WHATSAPP"),
            "status": "SIMULATED",  # DISPATCH_MODE off -> drain would SIMULATE
            "delivery_status": "QUEUED",
            "created_at": datetime.now().isoformat(),
        }
        self.db.get_collection("notification_logs").insert_one(row)
        return {"notification_id": nid, "dispatched": False, "status": "PENDING"}


def _mk_client(db, *, monkeypatch, recorder=None):
    monkeypatch.setattr(camp_mod, "_marketing_get_db", lambda: db)
    if recorder is not None:
        monkeypatch.setattr(camp_mod, "send_notification", recorder)
    monkeypatch.setattr(camp_mod, "_check_notification_rate", lambda *_a, **_k: None)
    # Force the promo quiet-hours window OPEN so a CI run at night doesn't 409.
    from agents import quiet_hours as _qh

    monkeypatch.setattr(_qh, "in_quiet_hours", lambda now=None: False)
    app = FastAPI()
    app.include_router(camp_mod.router, prefix="/api/v1/marketing")
    return TestClient(app)


def _hdr(roles=("ADMIN",), store_id="BV-PUN-01"):
    return {"Authorization": f"Bearer {_tok(roles, store_id=store_id)}"}


# ===========================================================================
# SEGMENTS (pure-ish service)
# ===========================================================================


def _seed_customers():
    today = datetime.now().date()
    return _FakeColl(
        [
            {"customer_id": "C1", "name": "Alpha", "mobile": "9000000001", "home_store_id": "BV-PUN-01", "customer_type": "B2C", "dob": today.replace(year=1990).isoformat()},
            {"customer_id": "C2", "name": "Beta", "mobile": "9000000002", "home_store_id": "BV-PUN-01", "customer_type": "B2B", "dob": "1985-01-01", "marketing_consent": False},
            {"customer_id": "C3", "name": "Gamma", "mobile": "9000000003", "home_store_id": "BV-PUN-01", "customer_type": "B2C"},
        ]
    )


def test_segment_by_store_counts_all():
    db = _FakeDB({"customers": _seed_customers(), "prescriptions": _FakeColl(), "orders": _FakeColl()})
    assert seg.count_segment(db, "by_store", store_id="BV-PUN-01") == 3


def test_segment_by_customer_type_b2b():
    db = _FakeDB({"customers": _seed_customers(), "prescriptions": _FakeColl(), "orders": _FakeColl()})
    rows = seg.resolve_segment(db, "by_customer_type", store_id="BV-PUN-01", params={"customer_type": "B2B"})
    assert {r["customer_id"] for r in rows} == {"C2"}


def test_segment_birthday_matches_today(monkeypatch):
    # IST-boundary determinism: _resolve_birthday windows off now_ist_naive()
    # (IST), but _seed_customers() stamps C1's DOB with datetime.now() (UTC).
    # In the 00:00-05:30 IST (18:30-24:00 UTC) window the IST date is a day
    # ahead of the UTC date, so the UTC-dated DOB falls just before the IST
    # forward window and C1 drops out. Freeze the segment clock and seed the
    # DOB for that SAME frozen IST date so the match is clock-independent.
    frozen = datetime(2026, 6, 15, 12, 0, 0)
    monkeypatch.setattr(seg, "now_ist_naive", lambda: frozen)
    customers = _FakeColl(
        [
            {"customer_id": "C1", "name": "Alpha", "mobile": "9000000001",
             "home_store_id": "BV-PUN-01", "customer_type": "B2C",
             "dob": frozen.date().replace(year=1990).isoformat()},
        ]
    )
    db = _FakeDB({"customers": customers, "prescriptions": _FakeColl(), "orders": _FakeColl()})
    rows = seg.resolve_segment(db, "birthday", store_id="BV-PUN-01")
    # C1's DOB is set to the frozen IST today's month/day -> in the 7-day window.
    assert "C1" in {r["customer_id"] for r in rows}


def test_segment_winback_excludes_recent_buyer():
    custs = _seed_customers()
    # C1 ordered yesterday (recent), C2/C3 never -> winback = C2, C3.
    orders = _FakeColl(
        [{"order_id": "O1", "customer_id": "C1", "store_id": "BV-PUN-01", "created_at": datetime.now() - timedelta(days=1)}]
    )
    db = _FakeDB({"customers": custs, "prescriptions": _FakeColl(), "orders": orders})
    rows = seg.resolve_segment(db, "winback", store_id="BV-PUN-01", params={"inactive_months": 6})
    ids = {r["customer_id"] for r in rows}
    assert "C1" not in ids
    assert {"C2", "C3"} <= ids


def test_segment_recent_buyers():
    custs = _seed_customers()
    orders = _FakeColl(
        [{"order_id": "O1", "customer_id": "C1", "store_id": "BV-PUN-01", "created_at": datetime.now() - timedelta(days=2)}]
    )
    db = _FakeDB({"customers": custs, "prescriptions": _FakeColl(), "orders": orders})
    rows = seg.resolve_segment(db, "recent_buyers", store_id="BV-PUN-01", params={"recent_days": 30})
    assert {r["customer_id"] for r in rows} == {"C1"}


def test_segment_rx_expiry_window():
    custs = _seed_customers()
    # Rx created ~700 days ago -> expires in ~30 days (inside the 90d window).
    rx = _FakeColl(
        [{"prescription_id": "RX1", "customer_id": "C3", "store_id": "BV-PUN-01", "created_at": (datetime.now() - timedelta(days=700)).isoformat()}]
    )
    db = _FakeDB({"customers": custs, "prescriptions": rx, "orders": _FakeColl()})
    rows = seg.resolve_segment(db, "rx_expiry", store_id="BV-PUN-01")
    assert {r["customer_id"] for r in rows} == {"C3"}
    assert rows[0]["variables"].get("expiry_date")  # carries the computed date


def test_segment_failsoft_no_db():
    assert seg.count_segment(None, "by_store") == 0
    assert seg.resolve_segment(None, "by_store") == []
    assert seg.preview_segment(None, "by_store")["count"] == 0


def test_segment_unknown_key():
    db = _FakeDB({"customers": _seed_customers()})
    assert seg.resolve_segment(db, "no_such_segment") == []


# ===========================================================================
# CRUD + LIFECYCLE
# ===========================================================================


def _create_payload(**over):
    base = {
        "name": "Rx Renewal June",
        "type": "rx_renewal",
        "segment_key": "rx_expiry",
        "channels": ["WHATSAPP"],
        "template_id": "PRESCRIPTION_EXPIRY",
    }
    base.update(over)
    return base


def test_create_campaign_is_draft(monkeypatch):
    db = _FakeDB({"campaigns": _FakeColl()})
    client = _mk_client(db, monkeypatch=monkeypatch)
    r = client.post("/api/v1/marketing/campaigns", json=_create_payload(), headers=_hdr())
    assert r.status_code == 200, r.text
    body = r.json()["campaign"]
    assert body["status"] == "DRAFT"
    assert body["segment_key"] == "rx_expiry"
    assert body["sent_count"] == 0
    assert body["campaign_id"].startswith("CMP-")


def test_create_rejects_bad_segment(monkeypatch):
    db = _FakeDB({"campaigns": _FakeColl()})
    client = _mk_client(db, monkeypatch=monkeypatch)
    r = client.post("/api/v1/marketing/campaigns", json=_create_payload(segment_key="bogus"), headers=_hdr())
    assert r.status_code == 422, r.text


def test_create_rejects_bad_channel(monkeypatch):
    db = _FakeDB({"campaigns": _FakeColl()})
    client = _mk_client(db, monkeypatch=monkeypatch)
    r = client.post("/api/v1/marketing/campaigns", json=_create_payload(channels=["TELEGRAM"]), headers=_hdr())
    assert r.status_code == 422, r.text


def test_list_campaigns_summary(monkeypatch):
    db = _FakeDB({"campaigns": _FakeColl()})
    client = _mk_client(db, monkeypatch=monkeypatch)
    client.post("/api/v1/marketing/campaigns", json=_create_payload(), headers=_hdr())
    r = client.get("/api/v1/marketing/campaigns", headers=_hdr())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert "summary" in body
    assert set(["active", "total_sent", "open_rate", "conversion"]).issubset(body["summary"].keys())


def test_schedule_sets_scheduled(monkeypatch):
    db = _FakeDB({"campaigns": _FakeColl()})
    client = _mk_client(db, monkeypatch=monkeypatch)
    cid = client.post("/api/v1/marketing/campaigns", json=_create_payload(), headers=_hdr()).json()["campaign"]["campaign_id"]
    r = client.post(
        f"/api/v1/marketing/campaigns/{cid}/schedule",
        json={"kind": "ONE_TIME", "send_at": (datetime.now() + timedelta(days=1)).isoformat()},
        headers=_hdr(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["campaign"]["status"] == "SCHEDULED"


def test_schedule_one_time_requires_send_at(monkeypatch):
    db = _FakeDB({"campaigns": _FakeColl()})
    client = _mk_client(db, monkeypatch=monkeypatch)
    cid = client.post("/api/v1/marketing/campaigns", json=_create_payload(), headers=_hdr()).json()["campaign"]["campaign_id"]
    r = client.post(f"/api/v1/marketing/campaigns/{cid}/schedule", json={"kind": "ONE_TIME"}, headers=_hdr())
    assert r.status_code == 422, r.text


def test_pause_resume_cycle(monkeypatch):
    db = _FakeDB({"campaigns": _FakeColl()})
    client = _mk_client(db, monkeypatch=monkeypatch)
    cid = client.post("/api/v1/marketing/campaigns", json=_create_payload(), headers=_hdr()).json()["campaign"]["campaign_id"]
    client.post(
        f"/api/v1/marketing/campaigns/{cid}/schedule",
        json={"kind": "RECURRING", "frequency": "WEEKLY"},
        headers=_hdr(),
    )
    rp = client.post(f"/api/v1/marketing/campaigns/{cid}/pause", headers=_hdr())
    assert rp.status_code == 200, rp.text
    assert rp.json()["campaign"]["status"] == "PAUSED"
    rr = client.post(f"/api/v1/marketing/campaigns/{cid}/resume", headers=_hdr())
    assert rr.status_code == 200, rr.text
    # has a schedule -> returns to SCHEDULED
    assert rr.json()["campaign"]["status"] == "SCHEDULED"


def test_pause_draft_rejected(monkeypatch):
    db = _FakeDB({"campaigns": _FakeColl()})
    client = _mk_client(db, monkeypatch=monkeypatch)
    cid = client.post("/api/v1/marketing/campaigns", json=_create_payload(), headers=_hdr()).json()["campaign"]["campaign_id"]
    r = client.post(f"/api/v1/marketing/campaigns/{cid}/pause", headers=_hdr())
    assert r.status_code == 409, r.text


def test_duplicate_resets_counters(monkeypatch):
    db = _FakeDB({"campaigns": _FakeColl()})
    client = _mk_client(db, monkeypatch=monkeypatch)
    cid = client.post("/api/v1/marketing/campaigns", json=_create_payload(), headers=_hdr()).json()["campaign"]["campaign_id"]
    r = client.post(f"/api/v1/marketing/campaigns/{cid}/duplicate", headers=_hdr())
    assert r.status_code == 200, r.text
    clone = r.json()["campaign"]
    assert clone["campaign_id"] != cid
    assert clone["status"] == "DRAFT"
    assert clone["sent_count"] == 0
    assert clone["name"].endswith("(copy)")


def test_update_then_get(monkeypatch):
    db = _FakeDB({"campaigns": _FakeColl()})
    client = _mk_client(db, monkeypatch=monkeypatch)
    cid = client.post("/api/v1/marketing/campaigns", json=_create_payload(), headers=_hdr()).json()["campaign"]["campaign_id"]
    ru = client.put(f"/api/v1/marketing/campaigns/{cid}", json={"name": "Renamed"}, headers=_hdr())
    assert ru.status_code == 200, ru.text
    rg = client.get(f"/api/v1/marketing/campaigns/{cid}", headers=_hdr())
    assert rg.status_code == 200, rg.text
    assert rg.json()["name"] == "Renamed"
    assert "audience_estimate" in rg.json()


def test_delete_active_rejected_then_pause_delete(monkeypatch):
    db = _FakeDB({"campaigns": _FakeColl()})
    client = _mk_client(db, monkeypatch=monkeypatch)
    cid = client.post("/api/v1/marketing/campaigns", json=_create_payload(), headers=_hdr()).json()["campaign"]["campaign_id"]
    # force ACTIVE directly in fake db
    db.get_collection("campaigns").update_one({"campaign_id": cid}, {"$set": {"status": "ACTIVE"}})
    rd = client.delete(f"/api/v1/marketing/campaigns/{cid}", headers=_hdr())
    assert rd.status_code == 409, rd.text
    # DRAFT can be deleted
    db.get_collection("campaigns").update_one({"campaign_id": cid}, {"$set": {"status": "DRAFT"}})
    rd2 = client.delete(f"/api/v1/marketing/campaigns/{cid}", headers=_hdr())
    assert rd2.status_code == 200, rd2.text


def test_get_missing_404(monkeypatch):
    db = _FakeDB({"campaigns": _FakeColl()})
    client = _mk_client(db, monkeypatch=monkeypatch)
    r = client.get("/api/v1/marketing/campaigns/CMP-nope", headers=_hdr())
    assert r.status_code == 404, r.text


# ===========================================================================
# SEND
# ===========================================================================


def test_send_tags_campaign_id_and_simulated(monkeypatch):
    custs = _seed_customers()
    db = _FakeDB({"campaigns": _FakeColl(), "customers": custs, "prescriptions": _FakeColl(), "orders": _FakeColl(), "notification_logs": _FakeColl()})
    recorder = _SendRecorder(db)
    client = _mk_client(db, monkeypatch=monkeypatch, recorder=recorder)
    # by_store segment -> C1, C2(opted out), C3
    cid = client.post(
        "/api/v1/marketing/campaigns",
        json=_create_payload(segment_key="by_store", type="custom", template_id="BIRTHDAY_WISH", store_id="BV-PUN-01"),
        headers=_hdr(),
    ).json()["campaign"]["campaign_id"]
    r = client.post(f"/api/v1/marketing/campaigns/{cid}/send", headers=_hdr())
    assert r.status_code == 200, r.text
    body = r.json()
    # C2 is opted out -> skipped; C1 + C3 queued.
    assert body["queued"] == 2
    assert body["skipped"] == 1
    assert body["status"] == "COMPLETED"  # ONE_TIME default
    # NO real MSG91: send_notification was the stub, called exactly twice.
    assert len(recorder.calls) == 2
    # Every queued notification_logs row carries campaign_id.
    logs = list(db.get_collection("notification_logs").find({"campaign_id": cid}))
    assert len(logs) == 2
    # The campaign_id was passed into variables too (for downstream templates).
    assert all(c["variables"].get("campaign_id") == cid for c in recorder.calls)


def test_send_writes_audit_row(monkeypatch):
    custs = _seed_customers()
    db = _FakeDB({"campaigns": _FakeColl(), "customers": custs, "prescriptions": _FakeColl(), "orders": _FakeColl(), "notification_logs": _FakeColl(), "campaign_audit": _FakeColl()})
    recorder = _SendRecorder(db)
    client = _mk_client(db, monkeypatch=monkeypatch, recorder=recorder)
    cid = client.post(
        "/api/v1/marketing/campaigns",
        json=_create_payload(segment_key="by_store", type="custom", template_id="BIRTHDAY_WISH", store_id="BV-PUN-01"),
        headers=_hdr(),
    ).json()["campaign"]["campaign_id"]
    client.post(f"/api/v1/marketing/campaigns/{cid}/send", headers=_hdr())
    audits = list(db.get_collection("campaign_audit").find({"campaign_id": cid, "action": "SEND"}))
    assert len(audits) == 1
    assert audits[0]["detail"]["queued"] == 2


def test_send_recurring_stays_active(monkeypatch):
    custs = _seed_customers()
    db = _FakeDB({"campaigns": _FakeColl(), "customers": custs, "prescriptions": _FakeColl(), "orders": _FakeColl(), "notification_logs": _FakeColl()})
    recorder = _SendRecorder(db)
    client = _mk_client(db, monkeypatch=monkeypatch, recorder=recorder)
    cid = client.post(
        "/api/v1/marketing/campaigns",
        json=_create_payload(
            segment_key="by_store", type="custom", template_id="BIRTHDAY_WISH", store_id="BV-PUN-01",
            schedule={"kind": "RECURRING", "frequency": "WEEKLY"},
        ),
        headers=_hdr(),
    ).json()["campaign"]["campaign_id"]
    r = client.post(f"/api/v1/marketing/campaigns/{cid}/send", headers=_hdr())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ACTIVE"  # recurring -> stays active for next tick


# ===========================================================================
# ANALYTICS
# ===========================================================================


def test_analytics_aggregation(monkeypatch):
    cid = "CMP-TEST-1"
    logs = _FakeColl(
        [
            {"notification_id": "N1", "campaign_id": cid, "channel": "WHATSAPP", "status": "SENT", "delivery_status": "DELIVERED"},
            {"notification_id": "N2", "campaign_id": cid, "channel": "WHATSAPP", "status": "SIMULATED", "delivery_status": "QUEUED"},
            {"notification_id": "N3", "campaign_id": cid, "channel": "SMS", "status": "FAILED", "delivery_status": "FAILED"},
            {"notification_id": "N4", "campaign_id": cid, "channel": "WHATSAPP", "status": "SENT", "delivery_status": "DELIVERED", "converted": True},
            {"notification_id": "N5", "campaign_id": "OTHER", "channel": "WHATSAPP", "status": "SENT"},
        ]
    )
    camps = _FakeColl([{"campaign_id": cid, "name": "Test", "status": "COMPLETED", "store_id": "BV-PUN-01"}])
    db = _FakeDB({"campaigns": camps, "notification_logs": logs})
    client = _mk_client(db, monkeypatch=monkeypatch)
    r = client.get(f"/api/v1/marketing/campaigns/{cid}/analytics", headers=_hdr())
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["total"] == 4  # OTHER excluded
    assert a["sent"] == 3  # SENT x2 + SIMULATED x1
    assert a["failed"] == 1
    assert a["delivered"] == 2
    assert a["converted"] == 1
    assert a["by_channel"]["WHATSAPP"]["sent"] == 3
    assert a["by_channel"]["SMS"]["failed"] == 1


# ===========================================================================
# SEGMENTS ENDPOINTS + ROLE GATE
# ===========================================================================


def test_segments_endpoint_live_counts(monkeypatch):
    db = _FakeDB({"customers": _seed_customers(), "prescriptions": _FakeColl(), "orders": _FakeColl()})
    client = _mk_client(db, monkeypatch=monkeypatch)
    r = client.get("/api/v1/marketing/segments", headers=_hdr())
    assert r.status_code == 200, r.text
    segs = {s["key"]: s for s in r.json()["segments"]}
    assert "by_store" in segs
    assert segs["by_store"]["count"] == 3
    assert "label" in segs["by_store"]


def test_segment_preview_endpoint(monkeypatch):
    db = _FakeDB({"customers": _seed_customers(), "prescriptions": _FakeColl(), "orders": _FakeColl()})
    client = _mk_client(db, monkeypatch=monkeypatch)
    r = client.get("/api/v1/marketing/segments/by_store/preview?store_id=BV-PUN-01", headers=_hdr())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 3
    assert len(body["sample"]) == 3
    # phone is masked in the preview sample
    assert all("phone_masked" in s for s in body["sample"])


def test_role_gate_blocks_cashier(monkeypatch):
    db = _FakeDB({"campaigns": _FakeColl(), "customers": _seed_customers(), "prescriptions": _FakeColl(), "orders": _FakeColl()})
    client = _mk_client(db, monkeypatch=monkeypatch)
    # SALES_CASHIER is not a campaign role -> 403 on every campaign route.
    h = _hdr(roles=("SALES_CASHIER",))
    assert client.get("/api/v1/marketing/campaigns", headers=h).status_code == 403
    assert client.post("/api/v1/marketing/campaigns", json=_create_payload(), headers=h).status_code == 403
    assert client.get("/api/v1/marketing/segments", headers=h).status_code == 403


def test_store_manager_allowed(monkeypatch):
    db = _FakeDB({"campaigns": _FakeColl(), "customers": _seed_customers(), "prescriptions": _FakeColl(), "orders": _FakeColl()})
    client = _mk_client(db, monkeypatch=monkeypatch)
    h = _hdr(roles=("STORE_MANAGER",))
    r = client.post(
        "/api/v1/marketing/campaigns",
        json=_create_payload(segment_key="by_store", type="custom", template_id="BIRTHDAY_WISH", store_id="BV-PUN-01"),
        headers=h,
    )
    assert r.status_code == 200, r.text


def test_store_manager_blocked_other_store(monkeypatch):
    db = _FakeDB({"campaigns": _FakeColl(), "customers": _seed_customers(), "prescriptions": _FakeColl(), "orders": _FakeColl()})
    client = _mk_client(db, monkeypatch=monkeypatch)
    # store-scoped to a DIFFERENT store than the manager's token store_ids -> 403.
    h = _hdr(roles=("STORE_MANAGER",), store_id="BV-PUN-01")
    r = client.post(
        "/api/v1/marketing/campaigns",
        json=_create_payload(segment_key="by_store", type="custom", template_id="BIRTHDAY_WISH", store_id="BV-MUM-99"),
        headers=h,
    )
    assert r.status_code == 403, r.text
