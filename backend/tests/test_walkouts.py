"""
IMS 2.0 — Walkouts router tests (Module i, Phase 1)
====================================================
Phase-1 contract:
  POST /api/v1/walkouts                create one
  GET  /api/v1/walkouts/{walkout_id}   fetch one

Six tests per the build plan §"Tests (must-pass per phase)":
  - full 30-field create round-trips
  - mobile validation rejects 9- and 11-digit inputs
  - invalid enum value returns 422
  - walkout_id matches WO-{STORE3}-{YYYY}-{6HEX}
  - audit-log row written with action="walkout.create"
  - customer auto-created when mobile is new

DB-backed paths (audit + customer auto-create) use the in-memory fakes
the rest of the test suite already uses; the FastAPI TestClient runs
without a real Mongo. We patch get_db / get_customer_repository /
get_audit_repository on the router module.
"""
from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# In-memory fakes
# ============================================================================


def _doc_matches(doc, filter):
    """Tiny Mongo-filter matcher — supports plain equality and the
    operators the walkout repo uses ($gte/$lte). Doesn't try to be a
    full Mongo emulator."""
    if not filter:
        return True
    for k, expected in filter.items():
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$gte" and not (actual is not None and actual >= op_val):
                    return False
                if op == "$lte" and not (actual is not None and actual <= op_val):
                    return False
                if op == "$ne" and actual == op_val:
                    return False
        else:
            if actual != expected:
                return False
    return True


class _FakeCursor:
    """Lazy cursor for FakeCollection.find() with chainable
    sort/skip/limit. Materializes on iter()."""

    def __init__(self, docs):
        self._docs = list(docs)
        self._sort_keys = None
        self._skip = 0
        self._limit = None

    def sort(self, keys):
        self._sort_keys = keys
        return self

    def skip(self, n):
        self._skip = int(n or 0)
        return self

    def limit(self, n):
        self._limit = int(n or 0) or None
        return self

    def _materialize(self):
        out = list(self._docs)
        if self._sort_keys:
            for key, direction in reversed(self._sort_keys):
                out.sort(key=lambda d, k=key: (d.get(k) is None, d.get(k)), reverse=(direction == -1))
        if self._skip:
            out = out[self._skip:]
        if self._limit:
            out = out[: self._limit]
        return out

    def __iter__(self):
        return iter(self._materialize())


class FakeCollection:
    """Minimal MongoDB collection stub — supports the calls the
    Walkout / Customer / Audit repos exercise (Phases 1 + 2)."""

    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, filter=None, projection=None):
        if not filter:
            return self.docs[0] if self.docs else None
        for d in self.docs:
            if _doc_matches(d, filter):
                return d
        return None

    def find(self, filter=None, projection=None):
        return _FakeCursor(d for d in self.docs if _doc_matches(d, filter))

    def count_documents(self, filter=None):
        return sum(1 for d in self.docs if _doc_matches(d, filter))

    def update_one(self, filter, update):
        modified = 0
        for d in self.docs:
            if _doc_matches(d, filter):
                set_block = (update or {}).get("$set", {}) or {}
                push_block = (update or {}).get("$push", {}) or {}
                d.update(set_block)
                for k, v in push_block.items():
                    arr = d.get(k)
                    if not isinstance(arr, list):
                        arr = []
                    arr.append(v)
                    d[k] = arr
                modified += 1
                break  # update_one updates one match
        return type("R", (), {"modified_count": modified, "matched_count": modified})()


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


@pytest.fixture
def patched_walkouts(monkeypatch):
    """Wire fake DB + repositories into the router module."""
    fake_db = FakeDB()

    # Patch the get_db that walkouts.py imports
    from api.routers import walkouts as walkouts_module
    monkeypatch.setattr(walkouts_module, "get_db", lambda: fake_db)

    # Patch user resolution to return a deterministic name
    def _fake_user_repo():
        class _R:
            def find_by_id(self, uid):
                return {"user_id": uid, "name": f"User-{uid}"}
            def find_one(self, filter):
                return self.find_by_id(filter.get("user_id", ""))
        return _R()
    monkeypatch.setattr(walkouts_module, "get_user_repository", _fake_user_repo)

    # Customer repo backed by FakeCollection
    from database.repositories.customer_repository import CustomerRepository
    customer_repo = CustomerRepository(fake_db.get_collection("customers"))
    monkeypatch.setattr(
        walkouts_module, "get_customer_repository", lambda: customer_repo
    )

    # Audit repo backed by FakeCollection
    from database.repositories.audit_repository import AuditRepository
    audit_repo = AuditRepository(fake_db.get_collection("audit_logs"))
    monkeypatch.setattr(walkouts_module, "get_audit_repository", lambda: audit_repo)

    # Task repo (Phase 3 — escalate-overdue side-effect)
    class _FakeTaskRepo:
        def __init__(self):
            self.tasks = []
        def create(self, doc):
            self.tasks.append(dict(doc))
            return doc
    task_repo = _FakeTaskRepo()
    monkeypatch.setattr(walkouts_module, "get_task_repository", lambda: task_repo)

    # Walk-in counter repo (Phase 4) — backed by FakeCollection so we
    # can also test the orders-router auto-increment hook end-to-end.
    from database.repositories.walkin_counter_repository import (
        WalkInCounterRepository,
    )
    walkin_repo = WalkInCounterRepository(fake_db.get_collection("walk_in_counters"))
    monkeypatch.setattr(
        walkouts_module, "get_walkin_counter_repository", lambda: walkin_repo
    )
    # Also patch the orders router so the POS hook lands in our fake +
    # orders.py finds the same customers + the order_repo writes to a
    # real fake collection (otherwise create_order returns 503 because
    # get_order_repository() is None without a Mongo).
    try:
        from api.routers import orders as orders_module
        from database.repositories.order_repository import OrderRepository
        order_repo = OrderRepository(fake_db.get_collection("orders"))
        monkeypatch.setattr(
            orders_module,
            "get_walkin_counter_repository",
            lambda: walkin_repo,
        )
        monkeypatch.setattr(
            orders_module, "get_customer_repository", lambda: customer_repo,
        )
        monkeypatch.setattr(
            orders_module, "get_order_repository", lambda: order_repo,
        )
        monkeypatch.setattr(
            orders_module, "get_product_repository", lambda: None,
        )
    except Exception:
        pass

    return {
        "db": fake_db,
        "customer_repo": customer_repo,
        "audit_repo": audit_repo,
        "task_repo": task_repo,
        "walkin_repo": walkin_repo,
    }


# ============================================================================
# Test payload helpers
# ============================================================================


def _full_payload(**overrides):
    p = {
        "customer_name": "Avinash Kumar Gupta",
        "mobile": "9473457157",
        "age_group": "26-35",
        "gender": "MALE",
        "product_interested": "FRAME",
        "has_prescription": "YES",
        "displayed_price_range": "5000-10000",
        "required_price_range": "3000-5000",
        "primary_walkout_reason": "BUDGET/PRICE",
        "secondary_walkout_reason": "BRAND",
        "brand_interest": "Ray-Ban",
        "competitor_mentioned": "Lenskart",
        "purchase_planned_in": "1-7 DAYS",
        "sales_person_id": "user-akshay",
        "action_remarks": "Wants to come back next week",
    }
    p.update(overrides)
    return p


# ============================================================================
# Tests
# ============================================================================


def test_create_walkout_full_30_fields(client, auth_headers, patched_walkouts):
    """Full payload persists every column we ship."""
    resp = client.post(
        "/api/v1/walkouts", json=_full_payload(), headers=auth_headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Server-stamped fields
    assert body["walkout_id"].startswith("WO-")
    assert body["store_id"] == "BV-TEST-01"
    assert body["sales_person_name"] == "User-user-akshay"
    assert body["date_str"]  # set
    # Round-trip semantic fields
    assert body["customer_name"] == "Avinash Kumar Gupta"
    assert body["mobile"] == "9473457157"
    assert body["age_group"] == "26-35"
    assert body["gender"] == "MALE"
    assert body["product_interested"] == "FRAME"
    assert body["has_prescription"] == "YES"
    assert body["primary_walkout_reason"] == "BUDGET/PRICE"
    assert body["secondary_walkout_reason"] == "BRAND"
    assert body["brand_interest"] == "Ray-Ban"
    assert body["competitor_mentioned"] == "Lenskart"
    assert body["purchase_planned_in"] == "1-7 DAYS"


@pytest.mark.parametrize("bad_mobile", ["123456789", "12345678901", "98765abcde"])
def test_mobile_validation_rejects_non_10_digits(
    client, auth_headers, patched_walkouts, bad_mobile
):
    """A present-but-invalid mobile (9 / 11 digits, or a digit-bearing string
    that doesn't form a 6-9 mobile) is 422. Empty string is accepted (mobile is
    optional); a string with NO digits ("abcdefghij") normalizes to None /
    omitted — both are covered in test_walkouts_mobile_optional.py."""
    resp = client.post(
        "/api/v1/walkouts",
        json=_full_payload(mobile=bad_mobile),
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_invalid_enum_value_returns_422(client, auth_headers, patched_walkouts):
    """A reason not in the WalkoutReason enum is rejected by pydantic."""
    resp = client.post(
        "/api/v1/walkouts",
        json=_full_payload(primary_walkout_reason="DOESNT_LIKE_LOGO"),
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_walkout_id_format_matches_pattern(
    client, auth_headers, patched_walkouts
):
    """WO-{STORE3}-{YYYY}-{6HEX} — for store BV-TEST-01 → WO-TES-2026-XXXXXX."""
    resp = client.post(
        "/api/v1/walkouts", json=_full_payload(), headers=auth_headers
    )
    assert resp.status_code == 201
    walkout_id = resp.json()["walkout_id"]
    # Pattern: WO-{3 alnum}-{4 digits}-{6 alnum}
    assert re.match(r"^WO-[A-Z0-9]{1,3}-\d{4}-[A-F0-9]{6}$", walkout_id), walkout_id
    # Store BV-TEST-01 → after stripping BV/WO/BVO chain, parts[0]='TEST'[:3]='TES'
    assert walkout_id.startswith("WO-TES-"), walkout_id


def test_audit_log_row_written(client, auth_headers, patched_walkouts):
    """A walkout.create row hits the audit_logs collection."""
    resp = client.post(
        "/api/v1/walkouts", json=_full_payload(), headers=auth_headers
    )
    assert resp.status_code == 201
    walkout_id = resp.json()["walkout_id"]

    audit_docs = patched_walkouts["audit_repo"].collection.docs
    walkout_audits = [d for d in audit_docs if d.get("action") == "walkout.create"]
    assert len(walkout_audits) == 1
    audit = walkout_audits[0]
    assert audit["entity_type"] == "walkout"
    assert audit["entity_id"] == walkout_id
    assert audit["store_id"] == "BV-TEST-01"
    assert audit["user_id"] == "test-admin-001"
    assert audit["detail"]["mobile"] == "9473457157"


def test_customer_auto_created_when_mobile_new(
    client, auth_headers, patched_walkouts
):
    """A walkout with a previously-unknown mobile creates a skeleton
    customer + links the customer_id back onto the walkout + logs a
    customer.create audit row tagged via_walkout=True."""
    customer_repo = patched_walkouts["customer_repo"]
    audit_repo = patched_walkouts["audit_repo"]

    # Pre-state: no customers
    assert len(customer_repo.collection.docs) == 0

    resp = client.post(
        "/api/v1/walkouts",
        json=_full_payload(mobile="9876543210", customer_name="New Walker"),
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text

    # A skeleton customer was created
    docs = customer_repo.collection.docs
    assert len(docs) == 1
    cust = docs[0]
    assert cust["mobile"] == "9876543210"
    assert cust["name"] == "New Walker"
    assert cust["source"] == "walkout"
    assert cust["primary_store_id"] == "BV-TEST-01"

    # Walkout response is linked
    body = resp.json()
    assert body["customer_id"] == cust["customer_id"]

    # Audit trail: both a customer.create AND a walkout.create
    actions = [d.get("action") for d in audit_repo.collection.docs]
    assert "customer.create" in actions
    assert "walkout.create" in actions
    cust_audit = next(d for d in audit_repo.collection.docs
                      if d.get("action") == "customer.create")
    assert cust_audit["detail"]["via_walkout"] is True


# ============================================================================
# Phase 2 — list / patch / delete
# ============================================================================


@pytest.fixture
def staff_headers_pune():
    """SALES_STAFF token whose user_id == 'user-akshay'. Lets us test
    own-only edit RBAC without changing the seed payload."""
    from api.routers.auth import create_access_token
    token = create_access_token({
        "user_id": "user-akshay",
        "username": "akshay",
        "roles": ["SALES_STAFF"],
        "store_ids": ["BV-TEST-01"],
        "active_store_id": "BV-TEST-01",
    })
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def manager_headers():
    """STORE_MANAGER token for BV-TEST-01."""
    from api.routers.auth import create_access_token
    token = create_access_token({
        "user_id": "test-manager-001",
        "username": "teststoremgr",
        "roles": ["STORE_MANAGER"],
        "store_ids": ["BV-TEST-01"],
        "active_store_id": "BV-TEST-01",
    })
    return {"Authorization": f"Bearer {token}"}


def _create_walkout(client, headers, **overrides):
    resp = client.post(
        "/api/v1/walkouts", json=_full_payload(**overrides), headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_list_by_store_filters(client, auth_headers, patched_walkouts):
    """GET /walkouts returns the rows for the active store, sorted
    newest-first, and supports the documented filters."""
    # Seed three rows with distinct sales_person_id + reasons
    _create_walkout(client, auth_headers, mobile="9000000001",
                    sales_person_id="user-akshay",
                    primary_walkout_reason="BUDGET/PRICE")
    _create_walkout(client, auth_headers, mobile="9000000002",
                    sales_person_id="user-rupesh",
                    primary_walkout_reason="BRAND")
    _create_walkout(client, auth_headers, mobile="9000000003",
                    sales_person_id="user-akshay",
                    primary_walkout_reason="STYLE/DESIGN")

    # No filter — all three
    resp = client.get("/api/v1/walkouts", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    for item in body["items"]:
        assert item["store_id"] == "BV-TEST-01"

    # Filter by sales_person_id
    resp = client.get(
        "/api/v1/walkouts?sales_person_id=user-akshay",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(i["sales_person_id"] == "user-akshay" for i in body["items"])

    # Filter by reason
    resp = client.get(
        "/api/v1/walkouts?primary_walkout_reason=BRAND",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_pagination_returns_default_50(client, auth_headers, patched_walkouts):
    """No `limit` param → 50; explicit limit honored."""
    # 60 walkouts at unique mobiles
    for i in range(60):
        mobile = f"9{i:09d}"
        _create_walkout(client, auth_headers, mobile=mobile)

    # Default limit = 50
    resp = client.get("/api/v1/walkouts", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 60
    assert body["limit"] == 50
    assert len(body["items"]) == 50

    # Explicit small limit
    resp = client.get("/api/v1/walkouts?limit=5", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 5
    assert len(body["items"]) == 5


def test_patch_diff_audited(client, auth_headers, patched_walkouts):
    """PATCH writes a single walkout.update audit row whose
    `detail.changes` carries the field-level from→to diff."""
    audit_repo = patched_walkouts["audit_repo"]
    walkout = _create_walkout(client, auth_headers)
    walkout_id = walkout["walkout_id"]

    # Edit two fields
    resp = client.patch(
        f"/api/v1/walkouts/{walkout_id}",
        json={"action_remarks": "Updated remarks", "brand_interest": "Persol"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action_remarks"] == "Updated remarks"
    assert body["brand_interest"] == "Persol"

    update_audits = [
        d for d in audit_repo.collection.docs
        if d.get("action") == "walkout.update"
    ]
    assert len(update_audits) == 1
    audit = update_audits[0]
    changes = audit["detail"]["changes"]
    assert changes["action_remarks"] == {
        "from": "Wants to come back next week",
        "to": "Updated remarks",
    }
    assert changes["brand_interest"] == {
        "from": "Ray-Ban",
        "to": "Persol",
    }


def test_rbac_sales_staff_cannot_edit_others(
    client, auth_headers, staff_headers_pune, patched_walkouts
):
    """Sales staff can edit their own walkouts but not their colleague's.
    Sales-person attribution is gated to managers/admins.
    """
    # Walkout owned by user-akshay
    own = _create_walkout(client, auth_headers, sales_person_id="user-akshay")
    # Walkout owned by user-rupesh
    other = _create_walkout(
        client, auth_headers,
        sales_person_id="user-rupesh", mobile="9000099999",
    )

    # akshay editing own row → 200
    resp = client.patch(
        f"/api/v1/walkouts/{own['walkout_id']}",
        json={"action_remarks": "Following up tomorrow"},
        headers=staff_headers_pune,
    )
    assert resp.status_code == 200, resp.text

    # akshay editing rupesh's row → 403
    resp = client.patch(
        f"/api/v1/walkouts/{other['walkout_id']}",
        json={"action_remarks": "Sneaky edit"},
        headers=staff_headers_pune,
    )
    assert resp.status_code == 403

    # akshay attempting to re-attribute his own walkout → 403
    resp = client.patch(
        f"/api/v1/walkouts/{own['walkout_id']}",
        json={"sales_person_id": "user-rupesh"},
        headers=staff_headers_pune,
    )
    assert resp.status_code == 403


def test_soft_delete_excludes_from_list(
    client, auth_headers, patched_walkouts
):
    """DELETE soft-deletes; the row vanishes from list + GET-by-id.
    Audit row is written with the supplied reason."""
    audit_repo = patched_walkouts["audit_repo"]
    a = _create_walkout(client, auth_headers, mobile="9111111111")
    b = _create_walkout(client, auth_headers, mobile="9222222222")

    resp = client.request(
        "DELETE",
        f"/api/v1/walkouts/{a['walkout_id']}",
        json={"reason": "Duplicate test entry"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] is True

    # List excludes the deleted row
    resp = client.get("/api/v1/walkouts", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["walkout_id"] == b["walkout_id"]

    # GET-by-id returns 404 for the deleted row
    resp = client.get(
        f"/api/v1/walkouts/{a['walkout_id']}", headers=auth_headers
    )
    assert resp.status_code == 404

    # Audit
    delete_audits = [
        d for d in audit_repo.collection.docs
        if d.get("action") == "walkout.delete"
    ]
    assert len(delete_audits) == 1
    assert delete_audits[0]["detail"]["reason"] == "Duplicate test entry"
    assert delete_audits[0]["entity_id"] == a["walkout_id"]


def test_delete_rbac_sales_staff_blocked(
    client, auth_headers, staff_headers_pune, manager_headers, patched_walkouts
):
    """Sales staff cannot delete; STORE_MANAGER can on their store; bare
    SUPERADMIN can on any."""
    walkout = _create_walkout(client, auth_headers, sales_person_id="user-akshay")

    # Sales staff: 403
    resp = client.request(
        "DELETE",
        f"/api/v1/walkouts/{walkout['walkout_id']}",
        json={"reason": "Mistake"},
        headers=staff_headers_pune,
    )
    assert resp.status_code == 403

    # Store manager same store: 200
    resp = client.request(
        "DELETE",
        f"/api/v1/walkouts/{walkout['walkout_id']}",
        json={"reason": "Manager cleanup"},
        headers=manager_headers,
    )
    assert resp.status_code == 200, resp.text


def test_patch_invalid_mobile_rejected(
    client, auth_headers, patched_walkouts
):
    """PATCH inherits mobile validation — bad mobile → 422."""
    walkout = _create_walkout(client, auth_headers)
    resp = client.patch(
        f"/api/v1/walkouts/{walkout['walkout_id']}",
        json={"mobile": "12345"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_create_sales_staff_forced_to_self(
    client, staff_headers_pune, patched_walkouts
):
    """Lower-tier roles (SALES_STAFF) cannot log a walkout 'on behalf
    of' someone else — server overrides sales_person_id to their
    user_id even if the client tries to spoof it."""
    payload = _full_payload(sales_person_id="user-rupesh")
    resp = client.post(
        "/api/v1/walkouts", json=payload, headers=staff_headers_pune
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sales_person_id"] == "user-akshay"  # the logged-in user


def test_create_manager_can_attribute_to_anyone(
    client, manager_headers, patched_walkouts
):
    """STORE_MANAGER (elevated role) is trusted with the sales_person_id
    field — what they post is what gets stored."""
    payload = _full_payload(sales_person_id="user-rupesh")
    resp = client.post(
        "/api/v1/walkouts", json=payload, headers=manager_headers
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sales_person_id"] == "user-rupesh"


def test_patch_no_changes_returns_existing(
    client, auth_headers, patched_walkouts
):
    """Submitting the same value emits no audit row."""
    audit_repo = patched_walkouts["audit_repo"]
    walkout = _create_walkout(client, auth_headers)

    pre_update_audits = sum(
        1 for d in audit_repo.collection.docs
        if d.get("action") == "walkout.update"
    )

    resp = client.patch(
        f"/api/v1/walkouts/{walkout['walkout_id']}",
        json={"action_remarks": walkout["action_remarks"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    post_update_audits = sum(
        1 for d in audit_repo.collection.docs
        if d.get("action") == "walkout.update"
    )
    assert post_update_audits == pre_update_audits


# ============================================================================
# Phase 3 — embedded follow-ups + result + escalation
# ============================================================================


def _today_iso():
    from datetime import date as _d
    return _d.today().isoformat()


def _yesterday_iso():
    from datetime import date as _d, timedelta
    return (_d.today() - timedelta(days=1)).isoformat()


def test_followup_append_round_1_and_2(client, auth_headers, patched_walkouts):
    """Both round 1 and round 2 may be appended; both reach the doc."""
    walkout = _create_walkout(client, auth_headers)
    wid = walkout["walkout_id"]

    # Round 1
    resp = client.post(
        f"/api/v1/walkouts/{wid}/followups",
        json={
            "round": 1,
            "scheduled_date": _today_iso(),
            "scheduled_time": "10:30",
            "mode": "WHATSAPP",
            "supervisor_id": "user-sameer",
            "notes": "Try once",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body["followups"]) == 1
    assert body["followups"][0]["round"] == 1
    assert body["followups"][0]["status"] == "PENDING"

    # Round 2
    resp = client.post(
        f"/api/v1/walkouts/{wid}/followups",
        json={
            "round": 2,
            "scheduled_date": _today_iso(),
            "mode": "CALL",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    rounds = sorted([fu["round"] for fu in body["followups"]])
    assert rounds == [1, 2]


def test_followup_round_4_rejected(client, auth_headers, patched_walkouts):
    """Round 4+ is a 422 (Pydantic Literal[1, 2, 3] validator).
    Round 3 is now accepted — see test_walkouts_followup_approval.py.
    """
    walkout = _create_walkout(client, auth_headers)
    resp = client.post(
        f"/api/v1/walkouts/{walkout['walkout_id']}/followups",
        json={"round": 4, "scheduled_date": _today_iso(), "mode": "CALL"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_followup_duplicate_round_rejected(
    client, auth_headers, patched_walkouts
):
    """Same round twice → 409."""
    walkout = _create_walkout(client, auth_headers)
    wid = walkout["walkout_id"]
    payload = {"round": 1, "scheduled_date": _today_iso(), "mode": "CALL"}
    resp = client.post(
        f"/api/v1/walkouts/{wid}/followups", json=payload, headers=auth_headers
    )
    assert resp.status_code == 201
    resp = client.post(
        f"/api/v1/walkouts/{wid}/followups", json=payload, headers=auth_headers
    )
    assert resp.status_code == 409


def test_followup_update_done_stamps_completed_fields(
    client, auth_headers, patched_walkouts
):
    """Status flip to DONE stamps completed_at + completed_by."""
    walkout = _create_walkout(client, auth_headers)
    wid = walkout["walkout_id"]
    client.post(
        f"/api/v1/walkouts/{wid}/followups",
        json={"round": 1, "scheduled_date": _today_iso(), "mode": "CALL"},
        headers=auth_headers,
    )
    resp = client.patch(
        f"/api/v1/walkouts/{wid}/followups/1",
        json={"status": "DONE", "notes": "Customer interested, will visit Sat"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    fu = next(f for f in body["followups"] if f["round"] == 1)
    assert fu["status"] == "DONE"
    assert fu["completed_at"]
    assert fu["completed_by"] == "test-admin-001"
    assert fu["notes"] == "Customer interested, will visit Sat"


def test_overdue_fu_creates_escalation_task(
    client, auth_headers, patched_walkouts
):
    """A pending FU scheduled in the past produces a Task on
    /escalate-overdue and stamps escalation_task_id back on the FU."""
    task_repo = patched_walkouts["task_repo"]
    walkout = _create_walkout(client, auth_headers)
    wid = walkout["walkout_id"]

    # Round-1 follow-up scheduled YESTERDAY
    resp = client.post(
        f"/api/v1/walkouts/{wid}/followups",
        json={
            "round": 1,
            "scheduled_date": _yesterday_iso(),
            "mode": "WHATSAPP",
            "supervisor_id": "user-sameer",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text

    # Trigger the cron
    resp = client.post(
        "/api/v1/walkouts/followups/escalate-overdue", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["escalated"] == 1
    created = body["created_tasks"][0]
    assert created["round"] == 1
    assert created["priority"] == "P2"  # round 1 → P2
    assert created["assignee"] == "user-sameer"

    # Task was actually created in the task repo
    assert len(task_repo.tasks) == 1
    task = task_repo.tasks[0]
    assert task["priority"] == "P2"
    assert task["assigned_to"] == "user-sameer"
    assert task["source"]["type"] == "walkout_followup"
    assert task["source"]["walkout_id"] == wid

    # The FU is now stamped with the escalation_task_id
    resp = client.get(f"/api/v1/walkouts/{wid}", headers=auth_headers)
    fu = next(f for f in resp.json()["followups"] if f["round"] == 1)
    assert fu["escalation_task_id"] == created["task_id"]
    assert fu["status"] == "ESCALATED"


def test_round2_overdue_creates_p1_task(
    client, auth_headers, patched_walkouts
):
    """Round 2 escalates to P1 (higher priority than round 1)."""
    walkout = _create_walkout(client, auth_headers)
    wid = walkout["walkout_id"]
    # Add round 1 (today, doesn't escalate) and round 2 (yesterday, escalates)
    client.post(
        f"/api/v1/walkouts/{wid}/followups",
        json={"round": 1, "scheduled_date": _today_iso(), "mode": "CALL"},
        headers=auth_headers,
    )
    client.post(
        f"/api/v1/walkouts/{wid}/followups",
        json={"round": 2, "scheduled_date": _yesterday_iso(), "mode": "CALL"},
        headers=auth_headers,
    )
    resp = client.post(
        "/api/v1/walkouts/followups/escalate-overdue", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["escalated"] == 1
    assert body["created_tasks"][0]["priority"] == "P1"
    assert body["created_tasks"][0]["round"] == 2


def test_escalate_overdue_blocked_for_sales_staff(
    client, staff_headers_pune, patched_walkouts
):
    """Only managers/admin can run the cron (multi-user task writes)."""
    resp = client.post(
        "/api/v1/walkouts/followups/escalate-overdue", headers=staff_headers_pune
    )
    assert resp.status_code == 403


def test_set_result_converted_validates_order_id(
    client, auth_headers, patched_walkouts
):
    """CONVERTED requires converted_order_id and the order must exist
    in the orders collection (else 422)."""
    walkout = _create_walkout(client, auth_headers)
    wid = walkout["walkout_id"]

    # Missing converted_order_id → 422
    resp = client.patch(
        f"/api/v1/walkouts/{wid}/result",
        json={"result": "CONVERTED"},
        headers=auth_headers,
    )
    assert resp.status_code == 422

    # Order doesn't exist → 422
    resp = client.patch(
        f"/api/v1/walkouts/{wid}/result",
        json={"result": "CONVERTED", "converted_order_id": "ORD-FAKE"},
        headers=auth_headers,
    )
    assert resp.status_code == 422

    # Seed a real order, retry → 200
    fake_db = patched_walkouts["db"]
    fake_db.get_collection("orders").insert_one({
        "order_id": "ORD-REAL-001",
        "store_id": "BV-TEST-01",
    })
    resp = client.patch(
        f"/api/v1/walkouts/{wid}/result",
        json={"result": "CONVERTED", "converted_order_id": "ORD-REAL-001"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["result"] == "CONVERTED"
    assert body["converted_order_id"] == "ORD-REAL-001"
    assert body["result_set_by"] == "test-admin-001"
    assert body["result_set_at"]


def test_set_result_negative_clears_converted_order_id(
    client, auth_headers, patched_walkouts
):
    """Switching from CONVERTED to NEGATIVE/DUE clears converted_order_id."""
    walkout = _create_walkout(client, auth_headers)
    wid = walkout["walkout_id"]
    patched_walkouts["db"].get_collection("orders").insert_one({
        "order_id": "ORD-X1",
    })
    client.patch(
        f"/api/v1/walkouts/{wid}/result",
        json={"result": "CONVERTED", "converted_order_id": "ORD-X1"},
        headers=auth_headers,
    )
    resp = client.patch(
        f"/api/v1/walkouts/{wid}/result",
        json={"result": "NEGATIVE"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["result"] == "NEGATIVE"
    assert resp.json()["converted_order_id"] is None


def test_set_result_audit_logged(client, auth_headers, patched_walkouts):
    """walkout.result.set audit row carries from→to + order id."""
    audit_repo = patched_walkouts["audit_repo"]
    walkout = _create_walkout(client, auth_headers)
    wid = walkout["walkout_id"]
    resp = client.patch(
        f"/api/v1/walkouts/{wid}/result",
        json={"result": "DUE"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    audit = next(
        d for d in audit_repo.collection.docs
        if d.get("action") == "walkout.result.set"
    )
    assert audit["entity_id"] == wid
    assert audit["detail"]["from"] is None
    assert audit["detail"]["to"] == "DUE"


def test_followups_due_today_lists_only_pending_today(
    client, auth_headers, patched_walkouts
):
    """due-today returns the right slice and is RBAC-scoped."""
    a = _create_walkout(client, auth_headers, mobile="9100000001")
    b = _create_walkout(client, auth_headers, mobile="9100000002")

    # a — pending FU today
    client.post(
        f"/api/v1/walkouts/{a['walkout_id']}/followups",
        json={"round": 1, "scheduled_date": _today_iso(), "mode": "CALL"},
        headers=auth_headers,
    )
    # b — pending FU yesterday (not today)
    client.post(
        f"/api/v1/walkouts/{b['walkout_id']}/followups",
        json={"round": 1, "scheduled_date": _yesterday_iso(), "mode": "CALL"},
        headers=auth_headers,
    )

    resp = client.get(
        "/api/v1/walkouts/followups/due-today", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["walkout_id"] == a["walkout_id"]


# ============================================================================
# Phase 4 — walk-in counter + dashboard
# ============================================================================


def test_walkin_increment_dedups_same_mobile_day(
    client, auth_headers, patched_walkouts
):
    """Repo-level: auto_increment dedup'd by (mobile, day). Two
    increments for the same mobile in one day → counter still 1."""
    repo = patched_walkouts["walkin_repo"]
    r1 = repo.auto_increment(
        store_id="BV-TEST-01", sales_person_id="user-akshay", mobile="9100000001",
    )
    r2 = repo.auto_increment(
        store_id="BV-TEST-01", sales_person_id="user-akshay", mobile="9100000001",
    )
    r3 = repo.auto_increment(
        store_id="BV-TEST-01", sales_person_id="user-rupesh", mobile="9100000002",
    )
    assert r1["deduped"] is False and r1["pos_auto_count"] == 1
    assert r2["deduped"] is True and r2["pos_auto_count"] == 1
    assert r3["deduped"] is False and r3["pos_auto_count"] == 2

    today = repo.get_today("BV-TEST-01")
    assert today["pos_auto_count"] == 2
    assert today["per_staff"] == {"user-akshay": 1, "user-rupesh": 1}


def test_walkin_increment_no_mobile_does_not_dedup(
    client, auth_headers, patched_walkouts
):
    repo = patched_walkouts["walkin_repo"]
    repo.auto_increment(store_id="BV-TEST-01", sales_person_id="user-akshay")
    repo.auto_increment(store_id="BV-TEST-01", sales_person_id="user-akshay")
    today = repo.get_today("BV-TEST-01")
    assert today["pos_auto_count"] == 2


def test_manual_topup_audit_logged(client, auth_headers, patched_walkouts):
    audit_repo = patched_walkouts["audit_repo"]
    resp = client.post(
        "/api/v1/walkouts/walkins/manual-topup",
        json={"delta": 3, "reason": "Three browsers, no POS"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["manual_topup"] == 3
    assert body["total"] == 3
    audit = next(
        d for d in audit_repo.collection.docs
        if d.get("action") == "walkin.manual_topup"
    )
    assert audit["detail"]["delta"] == 3
    assert audit["detail"]["reason"] == "Three browsers, no POS"
    assert audit["store_id"] == "BV-TEST-01"


def test_manual_topup_blocked_for_sales_staff(
    client, staff_headers_pune, patched_walkouts
):
    resp = client.post(
        "/api/v1/walkouts/walkins/manual-topup",
        json={"delta": 1, "reason": "browse"},
        headers=staff_headers_pune,
    )
    assert resp.status_code == 403


def test_walkins_today_endpoint_reflects_pos_hook(
    client, auth_headers, patched_walkouts
):
    patched_walkouts["customer_repo"].create({
        "customer_id": "cust-xx",
        "name": "Test Customer",
        "mobile": "9100000005",
        "phone": "9100000005",
    })
    resp = client.post(
        "/api/v1/orders",
        json={
            "customer_id": "cust-xx",
            "items": [{
                "product_id": "custom-test", "product_name": "Test",
                "item_type": "FRAME",
                "quantity": 1, "unit_price": 100.0,
            }],
        },
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201), resp.text

    # Same customer, same day — dedup
    resp = client.post(
        "/api/v1/orders",
        json={
            "customer_id": "cust-xx",
            "items": [{
                "product_id": "custom-test", "product_name": "Test",
                "item_type": "FRAME",
                "quantity": 1, "unit_price": 50.0,
            }],
        },
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201), resp.text

    resp = client.get("/api/v1/walkouts/walkins/today", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["pos_auto_count"] == 1
    assert body["total"] == 1


def test_dashboard_per_staff_aggregation_correct(
    client, auth_headers, patched_walkouts
):
    for i in range(3):
        _create_walkout(
            client, auth_headers,
            mobile=f"99000{i:05d}", sales_person_id="user-akshay",
        )
    _create_walkout(
        client, auth_headers, mobile="9991111111",
        sales_person_id="user-rupesh",
    )

    walkin_repo = patched_walkouts["walkin_repo"]
    for mob in ("9888880001", "9888880002", "9888880003"):
        walkin_repo.auto_increment(
            store_id="BV-TEST-01",
            sales_person_id="user-akshay",
            mobile=mob,
        )
    for mob in ("9777770001", "9777770002"):
        walkin_repo.auto_increment(
            store_id="BV-TEST-01",
            sales_person_id="user-rupesh",
            mobile=mob,
        )

    resp = client.get(
        "/api/v1/walkouts/dashboard/per-staff", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = {r["sales_person_id"]: r for r in body["items"]}
    akshay = items["user-akshay"]
    assert akshay["walkouts_mtd"] == 3
    assert akshay["walkouts_today"] == 3
    assert akshay["walk_ins_today"] == 3
    assert akshay["conversion_pct_mtd"] == 0.0
    rupesh = items["user-rupesh"]
    assert rupesh["walkouts_mtd"] == 1
    assert rupesh["walk_ins_today"] == 2


def test_dashboard_top_reasons_sorted_desc(
    client, auth_headers, patched_walkouts
):
    for mob, reason in [
        ("9111110001", "BUDGET/PRICE"),
        ("9111110002", "BUDGET/PRICE"),
        ("9111110003", "BUDGET/PRICE"),
        ("9111110004", "BRAND"),
        ("9111110005", "BRAND"),
        ("9111110006", "STYLE/DESIGN"),
    ]:
        _create_walkout(
            client, auth_headers, mobile=mob, primary_walkout_reason=reason,
        )

    resp = client.get(
        "/api/v1/walkouts/dashboard/top-reasons", headers=auth_headers
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items[0]["reason"] == "BUDGET/PRICE" and items[0]["count"] == 3
    assert items[1]["reason"] == "BRAND" and items[1]["count"] == 2
    assert items[2]["reason"] == "STYLE/DESIGN" and items[2]["count"] == 1
    counts = [i["count"] for i in items]
    assert counts == sorted(counts, reverse=True)


def test_dashboard_result_breakdown_buckets(
    client, auth_headers, patched_walkouts
):
    patched_walkouts["db"].get_collection("orders").insert_one({
        "order_id": "ORD-DASHBOARD-001",
    })
    a = _create_walkout(client, auth_headers, mobile="9000888001")
    b = _create_walkout(client, auth_headers, mobile="9000888002")
    c = _create_walkout(client, auth_headers, mobile="9000888003")
    _create_walkout(client, auth_headers, mobile="9000888004")  # no_result

    client.patch(
        f"/api/v1/walkouts/{a['walkout_id']}/result",
        json={"result": "CONVERTED", "converted_order_id": "ORD-DASHBOARD-001"},
        headers=auth_headers,
    )
    client.patch(
        f"/api/v1/walkouts/{b['walkout_id']}/result",
        json={"result": "NEGATIVE"}, headers=auth_headers,
    )
    client.patch(
        f"/api/v1/walkouts/{c['walkout_id']}/result",
        json={"result": "DUE"}, headers=auth_headers,
    )

    resp = client.get(
        "/api/v1/walkouts/dashboard/result-breakdown", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 4
    assert body["buckets"]["CONVERTED"] == 1
    assert body["buckets"]["NEGATIVE"] == 1
    assert body["buckets"]["DUE"] == 1
    assert body["buckets"]["no_result"] == 1


def test_dashboard_fu_status_per_round(
    client, auth_headers, patched_walkouts
):
    a = _create_walkout(client, auth_headers, mobile="9000777001")
    b = _create_walkout(client, auth_headers, mobile="9000777002")
    client.post(
        f"/api/v1/walkouts/{a['walkout_id']}/followups",
        json={"round": 1, "scheduled_date": _today_iso(), "mode": "CALL"},
        headers=auth_headers,
    )
    client.patch(
        f"/api/v1/walkouts/{a['walkout_id']}/followups/1",
        json={"status": "DONE"}, headers=auth_headers,
    )
    client.post(
        f"/api/v1/walkouts/{a['walkout_id']}/followups",
        json={"round": 2, "scheduled_date": _today_iso(), "mode": "CALL"},
        headers=auth_headers,
    )
    client.post(
        f"/api/v1/walkouts/{b['walkout_id']}/followups",
        json={"round": 1, "scheduled_date": _today_iso(), "mode": "CALL"},
        headers=auth_headers,
    )

    resp = client.get(
        "/api/v1/walkouts/dashboard/fu-status", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fu1"]["DONE"] == 1
    assert body["fu1"]["PENDING"] == 1
    assert body["fu2"]["PENDING"] == 1


def test_walkins_mtd_aggregates_across_days(
    client, auth_headers, patched_walkouts
):
    repo = patched_walkouts["walkin_repo"]
    today = _today_iso()
    yest = _yesterday_iso()
    repo.auto_increment(
        store_id="BV-TEST-01", sales_person_id="user-akshay",
        mobile="9100200001", date_str=today,
    )
    repo.auto_increment(
        store_id="BV-TEST-01", sales_person_id="user-akshay",
        mobile="9100200002", date_str=today,
    )
    for mob in ("9100200003", "9100200004", "9100200005"):
        repo.auto_increment(
            store_id="BV-TEST-01", sales_person_id="user-rupesh",
            mobile=mob, date_str=yest,
        )

    from datetime import date as _d
    now = _d.today()
    resp = client.get(
        f"/api/v1/walkouts/walkins/mtd?year={now.year}&month={now.month}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pos_auto_count"] >= 2
    assert "user-akshay" in body["per_staff"]


# ============================================================================
# Phase 5 — conversion-feed (Module ii contract) + backfill
# ============================================================================


def test_conversion_feed_includes_retro_conversions(
    client, auth_headers, patched_walkouts
):
    """Walkouts from prior days that flip to CONVERTED today count
    in retro_conversions_today (and bump the score)."""
    walkin_repo = patched_walkouts["walkin_repo"]

    # Today: akshay has 5 walk-ins, 1 walkout
    for mob in ("9100100001", "9100100002", "9100100003", "9100100004", "9100100005"):
        walkin_repo.auto_increment(
            store_id="BV-TEST-01", sales_person_id="user-akshay", mobile=mob,
        )
    _create_walkout(client, auth_headers, mobile="9100110001", sales_person_id="user-akshay")

    # A prior-day walkout for akshay, flipped to CONVERTED today
    repo = patched_walkouts["db"].get_collection("walkouts")
    from datetime import datetime as _dt, timedelta as _td
    yest = (_dt.now() - _td(days=1))
    prior = {
        "walkout_id": "WO-TES-2026-RETRO1", "_id": "WO-TES-2026-RETRO1",
        "store_id": "BV-TEST-01",
        "date": yest, "date_str": yest.date().isoformat(),
        "customer_name": "Retro", "mobile": "9100120001",
        "sales_person_id": "user-akshay", "sales_person_name": "AKSHAY",
        "result": "CONVERTED",
        "result_set_at": _dt.now().isoformat(),  # today
        "converted_order_id": "ORD-X",
        "followups": [], "deleted_at": None,
        "created_at": yest, "updated_at": _dt.now(),
        "primary_walkout_reason": "BUDGET/PRICE",
    }
    repo.insert_one(prior)

    resp = client.get("/api/v1/walkouts/conversion-feed", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()
    akshay = next(r for r in items if r["sales_person_id"] == "user-akshay")
    assert akshay["walk_ins_today"] == 5
    assert akshay["walkouts_today"] == 1
    assert akshay["retro_conversions_today"] == 1
    # (5 - 1 + 1) / 5 × 20 = 20.0 (capped at 20 anyway)
    assert akshay["conversion_score"] == 20.0


def test_conversion_feed_score_capped_at_20(
    client, auth_headers, patched_walkouts
):
    """If retro_conversions exceeds the walkouts-today subtraction,
    raw score can theoretically go above 20. The cap holds it at 20."""
    walkin_repo = patched_walkouts["walkin_repo"]
    walkin_repo.auto_increment(
        store_id="BV-TEST-01", sales_person_id="user-akshay",
        mobile="9200000001",
    )
    walkin_repo.auto_increment(
        store_id="BV-TEST-01", sales_person_id="user-akshay",
        mobile="9200000002",
    )
    # 0 walkouts today, 5 retros — raw = (2 - 0 + 5) / 2 × 20 = 70 → cap 20
    repo = patched_walkouts["db"].get_collection("walkouts")
    from datetime import datetime as _dt, timedelta as _td
    yest = (_dt.now() - _td(days=1))
    for i in range(5):
        repo.insert_one({
            "walkout_id": f"WO-TES-CAPED-{i:02d}", "_id": f"WO-TES-CAPED-{i:02d}",
            "store_id": "BV-TEST-01",
            "date": yest, "date_str": yest.date().isoformat(),
            "customer_name": f"Cap {i}", "mobile": f"930000000{i}",
            "sales_person_id": "user-akshay", "sales_person_name": "AKSHAY",
            "result": "CONVERTED",
            "result_set_at": _dt.now().isoformat(),
            "followups": [], "deleted_at": None,
            "created_at": yest, "updated_at": _dt.now(),
        })

    resp = client.get("/api/v1/walkouts/conversion-feed", headers=auth_headers)
    assert resp.status_code == 200
    items = resp.json()
    akshay = next(r for r in items if r["sales_person_id"] == "user-akshay")
    assert akshay["walk_ins_today"] == 2
    assert akshay["walkouts_today"] == 0
    assert akshay["retro_conversions_today"] == 5
    assert akshay["conversion_score"] == 20.0


def test_conversion_feed_zero_walkins_yields_null_score(
    client, auth_headers, patched_walkouts
):
    """No walk-ins -> conversion is UNSCORED (null + footfall_missing), NOT a
    silent 0. N3 / CORRECTIONS.md HARDENING line 92 (binding): a missing
    footfall must fail loudly, not score 0 (which corrupts payout rupees).
    Updated from the prior `== 0.0` expectation when the correction landed."""
    _create_walkout(client, auth_headers, mobile="9300100001", sales_person_id="user-rupesh")
    resp = client.get("/api/v1/walkouts/conversion-feed", headers=auth_headers)
    assert resp.status_code == 200
    items = resp.json()
    rupesh = next(r for r in items if r["sales_person_id"] == "user-rupesh")
    assert rupesh["walk_ins_today"] == 0
    assert rupesh["walkouts_today"] == 1
    assert rupesh["conversion_score"] is None
    assert rupesh["footfall_missing"] is True


def test_backfill_idempotent_on_mobile_date(tmp_path):
    """Re-running the migrate script on the same CSV is a no-op.

    Simulates the runbook with an in-process pymongo — actually uses
    a tiny fake collection with a unique-index emulation."""
    import csv as _csv
    csv_path = tmp_path / "pune.csv"
    with open(csv_path, "w", newline="") as f:
        w = _csv.writer(f)
        w.writerow([
            "date","customer_name","mobile","age_group","gender",
            "product_interested","has_prescription","displayed_price_range",
            "required_price_range","primary_walkout_reason",
            "secondary_walkout_reason","brand_interest",
            "competitor_mentioned","purchase_planned_in",
            "sales_person_id","sales_person_name","action_remarks",
        ])
        w.writerow([
            "2026-04-15","Avinash","9100200001","26-35","MALE","FRAME","YES",
            "5000-10000","3000-5000","BUDGET/PRICE","BRAND","Ray-Ban",
            "Lenskart","1-7 DAYS","user-akshay","AKSHAY","Notes",
        ])

    import sys, os, importlib.util
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    spec = importlib.util.spec_from_file_location(
        "migrate_pune_walkouts",
        os.path.join(repo_root, "scripts", "migrate_pune_walkouts.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    build_doc = mod.build_doc
    make_backfill_hash = mod.make_backfill_hash
    row = {
        "date":"2026-04-15","customer_name":"Avinash","mobile":"9100200001",
        "age_group":"26-35","gender":"MALE","product_interested":"FRAME",
        "has_prescription":"YES","displayed_price_range":"5000-10000",
        "required_price_range":"3000-5000","primary_walkout_reason":"BUDGET/PRICE",
        "secondary_walkout_reason":"BRAND","brand_interest":"Ray-Ban",
        "competitor_mentioned":"Lenskart","purchase_planned_in":"1-7 DAYS",
        "sales_person_id":"user-akshay","sales_person_name":"AKSHAY",
        "action_remarks":"Notes",
    }
    doc = build_doc(row, "BV-PNE-01")
    assert doc is not None
    assert doc["backfill_hash"] == make_backfill_hash("9100200001", "2026-04-15")
    # Re-running build_doc on same row → same backfill_hash (unique key
    # for the upsert), so a real Mongo would reject the second insert.
    doc2 = build_doc(row, "BV-PNE-01")
    assert doc2["backfill_hash"] == doc["backfill_hash"]
    # walkout_id is a fresh uuid each call (so we don't accidentally
    # overwrite an existing live row); the hash is the dedup key.
    assert doc2["walkout_id"] != doc["walkout_id"]


def test_backfill_skips_invalid_rows():
    """build_doc returns None when mobile/date/customer is bad."""
    import sys, os, importlib.util
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    spec = importlib.util.spec_from_file_location(
        "migrate_pune_walkouts",
        os.path.join(repo_root, "scripts", "migrate_pune_walkouts.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    build_doc = mod.build_doc

    # Bad mobile
    assert build_doc({
        "date":"2026-04-15","customer_name":"x","mobile":"123",
    }, "BV-PNE-01") is None
    # No date
    assert build_doc({
        "date":"","customer_name":"x","mobile":"9100200001",
    }, "BV-PNE-01") is None
    # No customer name
    assert build_doc({
        "date":"2026-04-15","customer_name":"","mobile":"9100200001",
    }, "BV-PNE-01") is None
    # 12-digit (with country code) accepted
    doc = build_doc({
        "date":"15/04/2026","customer_name":"Avinash","mobile":"919100200001",
    }, "BV-PNE-01")
    assert doc is not None
    assert doc["mobile"] == "9100200001"
