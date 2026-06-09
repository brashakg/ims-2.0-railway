"""
IMS 2.0 -- F45 Walkout / lost-sale CRM (#45 / N1) acceptance tests
==================================================================
Covers the F45 DELTA on top of the existing walkouts module:

  T1  reason-driven policy_suggestion computed on create (D3)
  T2  50/50 sale-credit write on CONVERTED (D2)
  T3  same-person conversion -> two 50% rows pointing at one user (D2)
  T4  POS compliance-check counter (D5)
  T5  E6 freq-cap gates the DARK FU outbound; Task still created (D4)
  T6  STAFF BEHAVIOUR -> immediate P1 manager Task in `tasks` (D3)
  T7  legacy marketing.py walkout stub retired -> 410 (D1)
  T8  additive enum values accepted by the validators (D6)
  T9  NO LIVE SEND: the dark outbound rides send_notification (PENDING /
      DISPATCH_MODE-gated), never a direct provider call (COMMS dark)
  T10 conversion feed credits the closing associate too (D2)

A hollow shell (no policy compute, no credit write, no 410, etc.) MUST FAIL.

These tests use a richer in-memory Mongo fake than test_walkouts.py because the
50/50 credit write uses find_one_and_update + upsert ($setOnInsert), which the
older fake does not implement.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_f45_walkout.py -q
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")


# ============================================================================
# In-memory Mongo fake (supports find_one_and_update + $setOnInsert + upsert)
# ============================================================================


def _matches(doc, flt):
    if not flt:
        return True
    for k, expected in flt.items():
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$gte" and not (actual is not None and actual >= op_val):
                    return False
                if op == "$lte" and not (actual is not None and actual <= op_val):
                    return False
                if op == "$ne" and actual == op_val:
                    return False
        elif actual != expected:
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_a, **_k):
        return self

    def skip(self, n):
        self._docs = self._docs[int(n or 0):]
        return self

    def limit(self, n):
        if n:
            self._docs = self._docs[: int(n)]
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, flt=None, projection=None):
        if not flt:
            return self.docs[0] if self.docs else None
        for d in self.docs:
            if _matches(d, flt):
                return d
        return None

    def find(self, flt=None, projection=None):
        return _Cursor(d for d in self.docs if _matches(d, flt))

    def count_documents(self, flt=None):
        return sum(1 for d in self.docs if _matches(d, flt))

    def update_one(self, flt, update, upsert=False):
        for d in self.docs:
            if _matches(d, flt):
                d.update((update or {}).get("$set", {}) or {})
                for k, v in ((update or {}).get("$push", {}) or {}).items():
                    arr = d.get(k)
                    if not isinstance(arr, list):
                        arr = []
                    arr.append(v)
                    d[k] = arr
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        if upsert:
            new = {}
            new.update((update or {}).get("$set", {}) or {})
            new.update((update or {}).get("$setOnInsert", {}) or {})
            self.docs.append(new)
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def find_one_and_update(self, flt, update, upsert=False, **_kw):
        for d in self.docs:
            if _matches(d, flt):
                d.update((update or {}).get("$set", {}) or {})
                return d
        if upsert:
            new = {}
            new.update((update or {}).get("$set", {}) or {})
            new.update((update or {}).get("$setOnInsert", {}) or {})
            self.docs.append(new)
            return None
        return None


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections = {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]

    def __getattr__(self, name):
        return self.get_collection(name)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def patched(monkeypatch):
    """Wire the fake DB + repos into the walkouts router (mirrors the
    test_walkouts.py pattern but with our richer fake)."""
    fake_db = FakeDB()
    from api.routers import walkouts as wm

    monkeypatch.setattr(wm, "get_db", lambda: fake_db)

    class _UserRepo:
        def find_by_id(self, uid):
            return {"user_id": uid, "name": f"User-{uid}"}

        def find_one(self, flt):
            return self.find_by_id(flt.get("user_id", ""))

        def find_many(self, flt):
            # A STORE_MANAGER for any store -> deterministic id.
            return [{"user_id": "mgr-001", "name": "Store Manager", "roles": ["STORE_MANAGER"]}]

    monkeypatch.setattr(wm, "get_user_repository", _UserRepo)

    from database.repositories.customer_repository import CustomerRepository

    customer_repo = CustomerRepository(fake_db.get_collection("customers"))
    monkeypatch.setattr(wm, "get_customer_repository", lambda: customer_repo)

    from database.repositories.audit_repository import AuditRepository

    audit_repo = AuditRepository(fake_db.get_collection("audit_logs"))
    monkeypatch.setattr(wm, "get_audit_repository", lambda: audit_repo)

    class _TaskRepo:
        def __init__(self, coll):
            self.coll = coll

        def create(self, doc):
            self.coll.insert_one(doc)
            return doc

    task_repo = _TaskRepo(fake_db.get_collection("tasks"))
    monkeypatch.setattr(wm, "get_task_repository", lambda: task_repo)

    from database.repositories.walkin_counter_repository import (
        WalkInCounterRepository,
    )

    walkin_repo = WalkInCounterRepository(fake_db.get_collection("walk_in_counters"))
    monkeypatch.setattr(wm, "get_walkin_counter_repository", lambda: walkin_repo)

    return {"db": fake_db, "tasks": fake_db.get_collection("tasks")}


def _payload(**ov):
    p = {
        "customer_name": "Test Customer",
        "mobile": "9473457157",
        "age_group": "26-35",
        "gender": "MALE",
        "product_interested": "FRAME",
        "has_prescription": "YES",
        "displayed_price_range": "5000-10000",
        "required_price_range": "3000-5000",
        "primary_walkout_reason": "BUDGET/PRICE",
        "purchase_planned_in": "1-7 DAYS",
        "sales_person_id": "staff-A",
        "action_remarks": "Will return",
    }
    p.update(ov)
    return p


def _create(client, headers, **ov):
    r = client.post("/api/v1/walkouts", json=_payload(**ov), headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# ============================================================================
# T1 -- reason-driven policy computed on create (D3)
# ============================================================================


def test_t1_policy_suggestion_computed_on_create(client, auth_headers, patched):
    # BUDGET/PRICE -> voucher eligible
    body = _create(client, auth_headers, primary_walkout_reason="BUDGET/PRICE")
    ps = body.get("policy_suggestion")
    assert ps is not None, "policy_suggestion missing -- hollow shell"
    assert ps["action"] == "PROMO_VOUCHER"
    assert ps["voucher_eligible"] is True

    # NOT AVAILABLE -> restock watch
    body = _create(client, auth_headers, primary_walkout_reason="NOT AVAILABLE")
    assert body["policy_suggestion"]["restock_watch"] is True
    assert body["policy_suggestion"]["action"] == "RESTOCK_WATCH"

    # STAFF BEHAVIOUR -> manager escalate
    body = _create(client, auth_headers, primary_walkout_reason="STAFF BEHAVIOUR")
    assert body["policy_suggestion"]["action"] == "MANAGER_ESCALATE"
    assert body["policy_suggestion"]["escalate_immediate"] is True


# ============================================================================
# T2 -- 50/50 sale-credit write on CONVERTED (D2)
# ============================================================================


def test_t2_sale_credit_5050_split_on_converted(client, auth_headers, patched):
    db = patched["db"]
    # An order closed by a DIFFERENT associate (staff-B).
    db.get_collection("orders").insert_one(
        {"order_id": "ORD-001", "sales_person_id": "staff-B"}
    )
    wo = _create(client, auth_headers, sales_person_id="staff-A")
    wid = wo["walkout_id"]

    r = client.patch(
        f"/api/v1/walkouts/{wid}/result",
        json={"result": "CONVERTED", "converted_order_id": "ORD-001"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    credits = db.get_collection("walkout_sale_credits").docs
    assert len(credits) == 2, f"expected 2 sale-credit rows, got {len(credits)}"
    by_type = {c["credit_type"]: c for c in credits}
    assert set(by_type) == {"LOGGING", "CLOSING"}
    assert by_type["LOGGING"]["user_id"] == "staff-A"
    assert by_type["CLOSING"]["user_id"] == "staff-B"
    assert by_type["LOGGING"]["pct"] == 50
    assert by_type["CLOSING"]["pct"] == 50

    # Embedded mirror present on the walkout doc.
    body = r.json()
    assert len(body.get("sale_credits") or []) == 2


def test_t2b_credit_upsert_is_idempotent(client, auth_headers, patched):
    """Re-running result=CONVERTED must NOT double the credit rows
    (deterministic _id + $setOnInsert)."""
    db = patched["db"]
    db.get_collection("orders").insert_one(
        {"order_id": "ORD-IDEM", "sales_person_id": "staff-B"}
    )
    wo = _create(client, auth_headers, sales_person_id="staff-A")
    wid = wo["walkout_id"]
    for _ in range(2):
        client.patch(
            f"/api/v1/walkouts/{wid}/result",
            json={"result": "CONVERTED", "converted_order_id": "ORD-IDEM"},
            headers=auth_headers,
        )
    assert len(db.get_collection("walkout_sale_credits").docs) == 2


# ============================================================================
# T3 -- same-person conversion (one associate, two 50% rows) (D2)
# ============================================================================


def test_t3_same_person_conversion(client, auth_headers, patched):
    db = patched["db"]
    db.get_collection("orders").insert_one(
        {"order_id": "ORD-SAME", "sales_person_id": "staff-A"}
    )
    wo = _create(client, auth_headers, sales_person_id="staff-A")
    wid = wo["walkout_id"]
    client.patch(
        f"/api/v1/walkouts/{wid}/result",
        json={"result": "CONVERTED", "converted_order_id": "ORD-SAME"},
        headers=auth_headers,
    )
    credits = db.get_collection("walkout_sale_credits").docs
    assert len(credits) == 2
    # Both rows point at the same user -> SC sums 50 + 50 = 100%.
    assert {c["user_id"] for c in credits} == {"staff-A"}
    assert {c["credit_type"] for c in credits} == {"LOGGING", "CLOSING"}


# ============================================================================
# T4 -- POS compliance check (D5)
# ============================================================================


def test_t4_pos_compliance_check(client, auth_headers, patched):
    db = patched["db"]
    # 3 open walkouts for staff-S1 in ST1.
    ids = []
    for _ in range(3):
        wo = _create(client, auth_headers, sales_person_id="staff-S1")
        ids.append(wo["walkout_id"])

    r = client.get(
        "/api/v1/walkouts/pos-compliance-check",
        params={"store_id": "BV-TEST-01", "sales_person_id": "staff-S1"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["open_count"] == 3, "hollow shell returns 0"

    # Resolve one -> count drops to 2.
    db.get_collection("orders").insert_one(
        {"order_id": "ORD-C", "sales_person_id": "staff-S1"}
    )
    client.patch(
        f"/api/v1/walkouts/{ids[0]}/result",
        json={"result": "CONVERTED", "converted_order_id": "ORD-C"},
        headers=auth_headers,
    )
    r2 = client.get(
        "/api/v1/walkouts/pos-compliance-check",
        params={"store_id": "BV-TEST-01", "sales_person_id": "staff-S1"},
        headers=auth_headers,
    )
    assert r2.json()["open_count"] == 2


# ============================================================================
# T5 / T9 -- E6 dark outbound: freq-cap gate + Task always created + NO LIVE SEND
# ============================================================================


def _seed_overdue_whatsapp_fu(client, headers, db, customer_id, mobile):
    """Create a walkout + a WHATSAPP FU scheduled in the past (overdue)."""
    wo = _create(client, headers, mobile=mobile)
    wid = wo["walkout_id"]
    # Attach a WHATSAPP follow-up then back-date its scheduled_date to overdue.
    client.post(
        f"/api/v1/walkouts/{wid}/followups",
        json={
            "round": 1,
            "scheduled_date": "2020-01-01",
            "mode": "WHATSAPP",
            "notes": "chase",
        },
        headers=headers,
    )
    # Force the linked customer_id so the dark outbound has a recipient.
    wo_coll = db.get_collection("walkouts")
    doc = wo_coll.find_one({"walkout_id": wid})
    doc["customer_id"] = customer_id
    return wid


def test_t5_freqcap_gates_dark_outbound_and_task_always_created(
    client, auth_headers, patched, monkeypatch
):
    db = patched["db"]

    # Capture send_notification calls (the DARK path); never a real provider.
    sends = []

    async def _fake_send(**kw):
        sends.append(kw)
        return {"notification_id": f"N-{len(sends)}", "status": "PENDING"}

    import api.services.notification_service as ns

    monkeypatch.setattr(ns, "send_notification", _fake_send)

    # Customer at the 3/30 cap -> outbound suppressed.
    cust_id = "cust-capped"
    import api.services.reminder_rail as rr

    monkeypatch.setattr(rr, "check_frequency_cap", lambda *a, **k: False)

    _seed_overdue_whatsapp_fu(client, auth_headers, db, cust_id, "9000000001")
    r = client.post("/api/v1/walkouts/followups/escalate-overdue", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # A Task IS created regardless of the cap.
    assert body["escalated"] >= 1
    assert len(db.get_collection("tasks").docs) >= 1
    # Capped -> NO send queued.
    assert body["outbound"]["capped"] >= 1
    assert len(sends) == 0, "capped customer must not be sent"


def test_t5b_under_cap_queues_dark_send_pending(
    client, auth_headers, patched, monkeypatch
):
    db = patched["db"]
    sends = []

    async def _fake_send(**kw):
        sends.append(kw)
        return {"notification_id": f"N-{len(sends)}", "status": "PENDING"}

    import api.services.notification_service as ns
    import api.services.reminder_rail as rr

    monkeypatch.setattr(ns, "send_notification", _fake_send)
    # Under the cap -> allowed.
    monkeypatch.setattr(rr, "check_frequency_cap", lambda *a, **k: True)
    ledger = []
    monkeypatch.setattr(rr, "record_outbound", lambda *a, **k: ledger.append(k))

    _seed_overdue_whatsapp_fu(client, auth_headers, db, "cust-ok", "9000000002")
    r = client.post("/api/v1/walkouts/followups/escalate-overdue", headers=auth_headers)
    body = r.json()
    assert body["outbound"]["queued"] >= 1
    # NO LIVE SEND: the only send path is send_notification, which returns a
    # PENDING status (DISPATCH_MODE-gated). We never call a provider directly.
    assert len(sends) == 1
    assert sends[0]["category"] == "PROMOTIONAL"
    assert sends[0]["channel"] == "WHATSAPP"
    # The ledger row was recorded so the 30-day cap stays accurate.
    assert len(ledger) == 1


def test_t9_call_mode_fu_is_task_only_no_send(
    client, auth_headers, patched, monkeypatch
):
    """CALL-mode overdue FU -> staff Task only, NEVER an outbound message."""
    db = patched["db"]
    sends = []

    async def _fake_send(**kw):
        sends.append(kw)
        return {"notification_id": "N-1", "status": "PENDING"}

    import api.services.notification_service as ns

    monkeypatch.setattr(ns, "send_notification", _fake_send)

    wo = _create(client, auth_headers, mobile="9000000003")
    wid = wo["walkout_id"]
    client.post(
        f"/api/v1/walkouts/{wid}/followups",
        json={"round": 1, "scheduled_date": "2020-01-01", "mode": "CALL"},
        headers=auth_headers,
    )
    r = client.post("/api/v1/walkouts/followups/escalate-overdue", headers=auth_headers)
    body = r.json()
    assert body["escalated"] >= 1
    assert len(db.get_collection("tasks").docs) >= 1
    assert len(sends) == 0, "CALL mode must never trigger an outbound send"


# ============================================================================
# T6 -- STAFF BEHAVIOUR immediate manager escalation Task (D3)
# ============================================================================


def test_t6_staff_behaviour_creates_p1_manager_task(client, auth_headers, patched):
    db = patched["db"]
    body = _create(
        client, auth_headers, primary_walkout_reason="STAFF BEHAVIOUR",
        sales_person_id="staff-X",
    )
    tasks = db.get_collection("tasks").docs
    staff_tasks = [
        t for t in tasks
        if (t.get("source") or {}).get("type") == "walkout_staff_behaviour"
    ]
    assert len(staff_tasks) == 1, "STAFF BEHAVIOUR must create exactly one Task"
    t = staff_tasks[0]
    assert t["priority"] == "P1"
    # Assigned to the resolved STORE_MANAGER (mgr-001) for the store.
    assert t["assigned_to"] == "mgr-001"
    assert t["source"]["walkout_id"] == body["walkout_id"]


# ============================================================================
# T7 -- legacy marketing.py walkout stub retired (D1)
# ============================================================================


def test_t7_legacy_marketing_walkout_retired(client, auth_headers):
    r1 = client.post(
        "/api/v1/marketing/walkout/cust-123",
        json={"reason": "price", "frames_tried": [], "notes": ""},
        headers=auth_headers,
    )
    assert r1.status_code == 410, r1.text
    r2 = client.get("/api/v1/marketing/walkout-recoveries", headers=auth_headers)
    assert r2.status_code == 410, r2.text


# ============================================================================
# T8 -- additive enum values accepted (D6)
# ============================================================================


def test_t8_additive_enums_accepted(client, auth_headers, patched):
    from api.routers.walkouts import FollowUpStatus, WalkoutResult

    # New FollowUpStatus values exist.
    assert FollowUpStatus.RESCHEDULED.value == "RESCHEDULED"
    assert FollowUpStatus.NOT_INTERESTED.value == "NOT INTERESTED"
    # New WalkoutResult values exist; existing ones unchanged.
    assert WalkoutResult.WON.value == "WON"
    assert WalkoutResult.LOST.value == "LOST"
    assert WalkoutResult.CONVERTED.value == "CONVERTED"

    # The FU update validator accepts a RESCHEDULED status on a real FU.
    wo = _create(client, auth_headers)
    wid = wo["walkout_id"]
    client.post(
        f"/api/v1/walkouts/{wid}/followups",
        json={"round": 1, "scheduled_date": "2030-01-01", "mode": "CALL"},
        headers=auth_headers,
    )
    r = client.patch(
        f"/api/v1/walkouts/{wid}/followups/1",
        json={"status": "RESCHEDULED"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text


# ============================================================================
# T10 -- conversion feed credits the closing associate too (D2)
# ============================================================================


def test_t10_conversion_feed_credits_closing_associate(
    client, auth_headers, patched
):
    """A walkout logged by staff-A, converted via an order sold by staff-B,
    writes a CLOSING credit to staff-B (so SC credits both associates)."""
    db = patched["db"]
    db.get_collection("orders").insert_one(
        {"order_id": "ORD-FEED", "sales_person_id": "staff-B"}
    )
    wo = _create(client, auth_headers, sales_person_id="staff-A")
    wid = wo["walkout_id"]
    client.patch(
        f"/api/v1/walkouts/{wid}/result",
        json={"result": "CONVERTED", "converted_order_id": "ORD-FEED"},
        headers=auth_headers,
    )
    credits = db.get_collection("walkout_sale_credits").docs
    closing = [c for c in credits if c["credit_type"] == "CLOSING"]
    logging = [c for c in credits if c["credit_type"] == "LOGGING"]
    assert closing and closing[0]["user_id"] == "staff-B"
    assert logging and logging[0]["user_id"] == "staff-A"
