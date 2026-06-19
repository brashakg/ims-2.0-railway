"""
IMS 2.0 - SUPERADMIN post-creation order edit (build item #16)
==============================================================
Endpoint contract:

  PUT /api/v1/orders/{id}/superadmin-edit          (pre-invoice)
  PUT /api/v1/orders/{id}/superadmin-invoice-change (post-invoice)

Tests (per the build plan):
  * pre-invoice edit recomputes GST + writes an immutable audit row
  * pre-invoice edit by a non-SUPERADMIN -> 403
  * pre-invoice edit in a LOCKED accounting period -> 423
  * pre-invoice edit refused once an invoice is issued -> 409
  * post-invoice REVISED_INVOICE allocates a NEW serial + voids/supersedes old
  * post-invoice CREDIT_NOTE issues a delta note linked to the original invoice

DB-backed paths use in-memory fakes (the TestClient runs without a real Mongo).
The fakes support find_one_and_update so BOTH the audit hash-chain and the
invoice-serial counter exercise their real atomic paths.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")


# ============================================================================
# In-memory Mongo fakes (support find_one_and_update for chain + counters)
# ============================================================================


def _matches(doc, filt):
    if not filt:
        return True
    for k, expected in filt.items():
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, val in expected.items():
                if op == "$type":
                    if val == "string" and not isinstance(actual, str):
                        return False
                elif op == "$ne":
                    if actual == val:
                        return False
                elif op == "$exists":
                    if (actual is not None) != bool(val):
                        return False
                else:
                    return False
        elif actual != expected:
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_a, **_k):
        # Audit verify sorts by seq asc; emulate.
        try:
            self._docs.sort(key=lambda d: d.get("seq") or 0)
        except Exception:  # noqa: BLE001
            pass
        return self

    def __iter__(self):
        return iter(self._docs)


class _Coll:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, filt=None, projection=None):
        for d in self.docs:
            if _matches(d, filt):
                return dict(d)
        return None

    def find(self, filt=None, projection=None):
        return _Cursor(dict(d) for d in self.docs if _matches(d, filt))

    def count_documents(self, filt=None):
        return sum(1 for d in self.docs if _matches(d, filt))

    def update_one(self, filt, update, upsert=False):
        for d in self.docs:
            if _matches(d, filt):
                d.update((update or {}).get("$set", {}) or {})
                modified = 1
                return type(
                    "R", (), {"modified_count": modified, "matched_count": 1}
                )()
        if upsert:
            new = {}
            for k, v in (filt or {}).items():
                if not isinstance(v, dict):
                    new[k] = v
            new.update((update or {}).get("$set", {}) or {})
            new.update((update or {}).get("$setOnInsert", {}) or {})
            self.docs.append(new)
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def find_one_and_update(
        self, filt, update, upsert=False, return_document=True
    ):
        target = None
        for d in self.docs:
            if _matches(d, filt):
                target = d
                break
        if target is None:
            if not upsert:
                return None
            target = {}
            for k, v in (filt or {}).items():
                if not isinstance(v, dict):
                    target[k] = v
            target.update((update or {}).get("$setOnInsert", {}) or {})
            self.docs.append(target)
        for k, inc in ((update or {}).get("$inc", {}) or {}).items():
            target[k] = (target.get(k) or 0) + inc
        target.update((update or {}).get("$set", {}) or {})
        return dict(target)

    def create_index(self, *_a, **_k):
        return None


class _DB:
    is_connected = True

    def __init__(self):
        self._c = {}

    def get_collection(self, name):
        if name not in self._c:
            self._c[name] = _Coll()
        return self._c[name]

    # pymongo Collection exposes `.database`; the audit chain reads it.
    def __getattr__(self, name):
        return self.get_collection(name)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def wired(monkeypatch):
    """Wire fake DB + repos into orders/finance/returns."""
    db = _DB()

    from api.routers import orders as orders_module
    from api.routers import returns as returns_module
    from api.routers import finance as finance_module
    from database.repositories.order_repository import OrderRepository
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.audit_repository import AuditRepository

    orders_coll = db.get_collection("orders")
    # Give the order/audit/counters collections a back-pointer to the DB so the
    # audit chain (append_audit_entry) and invoice counter can reach the head /
    # counters collections via collection.database.
    for cname in ("orders", "audit_logs", "counters", "audit_chain_head", "stores"):
        setattr(db.get_collection(cname), "database", db)

    order_repo = OrderRepository(orders_coll)
    customer_repo = CustomerRepository(db.get_collection("customers"))
    audit_repo = AuditRepository(db.get_collection("audit_logs"))

    monkeypatch.setattr(orders_module, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(orders_module, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(orders_module, "get_product_repository", lambda: None)
    monkeypatch.setattr(orders_module, "get_walkin_counter_repository", lambda: None)
    monkeypatch.setattr(orders_module, "_get_db", lambda: db)
    # Period-lock + store-credit machinery read get_db on their own modules.
    monkeypatch.setattr(finance_module, "_get_db", lambda: db, raising=False)
    monkeypatch.setattr(returns_module, "_get_db", lambda: db, raising=False)
    monkeypatch.setattr(returns_module, "get_customer_repository", lambda: customer_repo)
    # The router imports get_audit_repository lazily from ..dependencies; patch
    # there so _write_order_edit_audit gets our fake.
    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_audit_repository", lambda: audit_repo)

    customer_repo.create(
        {
            "customer_id": "cust-x",
            "name": "Test Customer",
            "mobile": "9100000099",
            "phone": "9100000099",
        }
    )

    return {
        "db": db,
        "order_repo": order_repo,
        "audit_repo": audit_repo,
        "customer_repo": customer_repo,
        "orders_coll": orders_coll,
    }


def _seed_order(order_repo, **overrides):
    """Persist a CONFIRMED order (1 FRAME @ 1000 inclusive 5% GST)."""
    items = [
        {
            "item_id": "line-1",
            "item_type": "FRAME",
            "category": "FRAME",
            "product_name": "Test Frame",
            "quantity": 1,
            "unit_price": 1000.0,
            "discount_percent": 0,
            "discount_amount": 0,
            "item_total": 1000.0,
            "gst_rate": 5.0,
            "taxable_value": 952.38,
            "tax_amount": 47.62,
        }
    ]
    doc = {
        "order_id": "ord-16",
        "order_number": "ORD-BOK01-2026-AAA111",
        "store_id": "BV-TEST-01",
        "customer_id": "cust-x",
        "customer_name": "Test Customer",
        "status": "CONFIRMED",
        "items": items,
        "subtotal": 1000.0,
        "cart_discount_percent": 0.0,
        "cart_discount_amount": 0.0,
        "tax_rate": 5.0,
        "tax_amount": 47.62,
        "total_discount": 0.0,
        "grand_total": 1000.0,
        "amount_paid": 1000.0,
        "balance_due": 0.0,
        "payment_status": "PAID",
        "payments": [{"method": "CASH", "amount": 1000.0}],
    }
    doc.update(overrides)
    order_repo.create(doc)
    return doc


def _edit_item(unit_price=2000.0, qty=1, discount=0):
    return {
        "item_id": "line-1",
        "item_type": "FRAME",
        "category": "FRAME",
        "product_name": "Test Frame",
        "quantity": qty,
        "unit_price": unit_price,
        "discount_percent": discount,
    }


# ============================================================================
# Pre-invoice edit
# ============================================================================


def test_pre_invoice_edit_recomputes_gst_and_audits(client, auth_headers, wired):
    _seed_order(wired["order_repo"])
    resp = client.put(
        "/api/v1/orders/ord-16/superadmin-edit",
        json={
            "reason": "Customer agreed to upgraded frame price",
            "items": [_edit_item(unit_price=2000.0)],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # FRAME @ 2000 inclusive 5% -> grand 2000, tax extracted = 95.24
    assert body["grand_total"] == 2000.0
    assert body["tax_amount"] == 95.24

    saved = wired["order_repo"].find_by_id("ord-16")
    assert saved["grand_total"] == 2000.0
    assert saved["superadmin_edited"] is True
    assert saved["items"][0]["unit_price"] == 2000.0
    # balance recomputed: was PAID 1000, now owes 1000 more
    assert saved["balance_due"] == 1000.0
    assert saved["payment_status"] == "PARTIAL"

    # Immutable audit row written synchronously.
    audit_docs = wired["db"].get_collection("audit_logs").docs
    row = next(d for d in audit_docs if d.get("action") == "ORDER_SUPERADMIN_EDIT")
    assert row["entity_id"] == "ord-16"
    assert row["detail"] == "Customer agreed to upgraded frame price"
    assert row["before_state"]["grand_total"] == 1000.0
    assert row["after_state"]["grand_total"] == 2000.0
    assert "grand_total" in row["diff"]
    # Hash-chained (seq/entry_hash present because our fake supports
    # find_one_and_update on the head collection).
    assert row.get("seq") == 1
    assert isinstance(row.get("entry_hash"), str)


def test_pre_invoice_edit_non_superadmin_403(client, staff_headers, wired):
    _seed_order(wired["order_repo"])
    resp = client.put(
        "/api/v1/orders/ord-16/superadmin-edit",
        json={"reason": "trying to edit", "items": [_edit_item()]},
        headers=staff_headers,
    )
    assert resp.status_code == 403, resp.text


def test_pre_invoice_edit_admin_allowed(client, wired):
    """ADMIN (not only SUPERADMIN) may edit a created order/invoice -- owner
    decision 2026-06-19. Confirms the gate broadening didn't stay superadmin-only."""
    from api.routers.auth import create_access_token

    _seed_order(wired["order_repo"])
    admin_token = create_access_token(
        {
            "user_id": "test-admin-002",
            "username": "testadmin2",
            "roles": ["ADMIN"],
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
        }
    )
    resp = client.put(
        "/api/v1/orders/ord-16/superadmin-edit",
        json={"reason": "admin correction", "items": [_edit_item(unit_price=2000.0)]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    assert wired["order_repo"].find_by_id("ord-16")["superadmin_edited"] is True


def test_pre_invoice_edit_locked_period_423(client, auth_headers, wired):
    _seed_order(wired["order_repo"])
    # Lock the current IST month so check_period_locked raises 423.
    from api.utils.ist import ist_today

    today = ist_today()
    wired["db"].get_collection("period_locks").insert_one(
        {"month": today.month, "year": today.year, "locked": True}
    )
    resp = client.put(
        "/api/v1/orders/ord-16/superadmin-edit",
        json={"reason": "edit in locked month", "items": [_edit_item()]},
        headers=auth_headers,
    )
    assert resp.status_code == 423, resp.text


def test_pre_invoice_edit_refused_once_invoiced_409(client, auth_headers, wired):
    _seed_order(wired["order_repo"], invoice_number="INV/BOK-01/26-27/0001")
    resp = client.put(
        "/api/v1/orders/ord-16/superadmin-edit",
        json={"reason": "too late", "items": [_edit_item()]},
        headers=auth_headers,
    )
    assert resp.status_code == 409, resp.text


def test_pre_invoice_edit_draft_rejected_400(client, auth_headers, wired):
    _seed_order(wired["order_repo"], status="DRAFT")
    resp = client.put(
        "/api/v1/orders/ord-16/superadmin-edit",
        json={"reason": "use the draft path", "items": [_edit_item()]},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text


# ============================================================================
# Post-invoice correction
# ============================================================================


def test_post_invoice_revised_allocates_new_serial_and_voids_old(
    client, auth_headers, wired
):
    _seed_order(wired["order_repo"], invoice_number="INV/BOK-01/26-27/0001")
    resp = client.put(
        "/api/v1/orders/ord-16/superadmin-invoice-change",
        json={
            "mode": "REVISED_INVOICE",
            "reason": "Wrong price on the original invoice",
            "items": [_edit_item(unit_price=3000.0)],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "REVISED_INVOICE"
    assert body["original_invoice_number"] == "INV/BOK-01/26-27/0001"
    new_serial = body["revised_invoice_number"]
    assert new_serial and new_serial != "INV/BOK-01/26-27/0001"

    saved = wired["order_repo"].find_by_id("ord-16")
    # Original superseded; order now carries the new serial + corrected total.
    assert saved["invoice_number"] == new_serial
    assert saved["superseded_invoice_number"] == "INV/BOK-01/26-27/0001"
    assert saved["grand_total"] == 3000.0

    # Link record persisted.
    rev_docs = wired["db"].get_collection("revised_invoices").docs
    assert any(
        d.get("revised_invoice_number") == new_serial
        and d.get("original_invoice_number") == "INV/BOK-01/26-27/0001"
        for d in rev_docs
    )
    # Immutable audit row.
    audit_docs = wired["db"].get_collection("audit_logs").docs
    assert any(d.get("action") == "ORDER_INVOICE_REVISED" for d in audit_docs)


def test_post_invoice_credit_note_linked_and_intact(client, auth_headers, wired):
    _seed_order(wired["order_repo"], invoice_number="INV/BOK-01/26-27/0002")
    # Reduce the order (2000 -> via... actually lower from 1000 to 800) so a
    # CREDIT note is owed.
    resp = client.put(
        "/api/v1/orders/ord-16/superadmin-invoice-change",
        json={
            "mode": "CREDIT_NOTE",
            "reason": "Overcharged; refund the difference",
            "items": [_edit_item(unit_price=800.0)],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "CREDIT_NOTE"
    assert body["note_type"] == "CREDIT_NOTE"
    assert body["amount"] == 200.0  # 1000 -> 800
    assert body["original_invoice_number"] == "INV/BOK-01/26-27/0002"

    # Original invoice + order totals LEFT INTACT.
    saved = wired["order_repo"].find_by_id("ord-16")
    assert saved["invoice_number"] == "INV/BOK-01/26-27/0002"
    assert saved["grand_total"] == 1000.0

    # CN persisted to credit_note_ledger, linked to the original invoice.
    cn_docs = wired["db"].get_collection("credit_note_ledger").docs
    assert any(
        d.get("note_type") == "CREDIT_NOTE"
        and d.get("original_invoice_number") == "INV/BOK-01/26-27/0002"
        for d in cn_docs
    )
    # Immutable audit row.
    audit_docs = wired["db"].get_collection("audit_logs").docs
    assert any(d.get("action") == "ORDER_INVOICE_CREDIT_NOTE" for d in audit_docs)


def test_post_invoice_debit_note_on_increase(client, auth_headers, wired):
    _seed_order(wired["order_repo"], invoice_number="INV/BOK-01/26-27/0003")
    resp = client.put(
        "/api/v1/orders/ord-16/superadmin-invoice-change",
        json={
            "mode": "CREDIT_NOTE",
            "reason": "Undercharged; customer owes more",
            "items": [_edit_item(unit_price=1500.0)],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Increase -> DEBIT note.
    assert body["note_type"] == "DEBIT_NOTE"
    assert body["amount"] == 500.0
    dn_docs = wired["db"].get_collection("debit_note_ledger").docs
    assert any(d.get("note_type") == "DEBIT_NOTE" for d in dn_docs)


def test_post_invoice_no_delta_rejected_400(client, auth_headers, wired):
    _seed_order(wired["order_repo"], invoice_number="INV/BOK-01/26-27/0004")
    resp = client.put(
        "/api/v1/orders/ord-16/superadmin-invoice-change",
        json={
            "mode": "CREDIT_NOTE",
            "reason": "no money change",
            "items": [_edit_item(unit_price=1000.0)],  # same total
        },
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text


def test_invalid_mode_rejected_422(client, auth_headers, wired):
    _seed_order(wired["order_repo"], invoice_number="INV/BOK-01/26-27/0005")
    resp = client.put(
        "/api/v1/orders/ord-16/superadmin-invoice-change",
        json={"mode": "DELETE_INVOICE", "reason": "nope"},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text
