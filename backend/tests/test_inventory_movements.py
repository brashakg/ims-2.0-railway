"""Movements ledger -- GET /inventory/movements.

Merges RECEIVED (grns ACCEPTED), SOLD (orders in _SOLD_STATUSES),
TRANSFER_IN / TRANSFER_OUT (stock_transfers legs) and OPENING_STOCK
(opening_stock_batches commit summaries) into one reverse-chronological
ledger. Driven directly with a fake db monkeypatched over
inventory._get_db -- no Mongo, no HTTP (test_grn_cockpit.py style).

Covers: merge order, sign conventions, store scoping (incl. the 403 on a
foreign store and the transfer leg split), per-source fail-soft, type filter,
skip/limit paging, and the RBAC policy row.
"""
from __future__ import annotations

import os
import sys
import asyncio

import pytest
from fastapi import HTTPException

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import inventory as inv  # noqa: E402
from api.routers.inventory import get_stock_movements  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal fake Mongo: find(filter, projection).sort(...).limit(...) iteration
# with the operators the endpoint actually uses ($in, $gte, $or, $and, dotted
# items.product_id equality).
# ---------------------------------------------------------------------------


def _match(doc, flt):
    for key, cond in flt.items():
        if key == "$or":
            if not any(_match(doc, sub) for sub in cond):
                return False
            continue
        if key == "$and":
            if not all(_match(doc, sub) for sub in cond):
                return False
            continue
        if "." in key:
            head, tail = key.split(".", 1)
            arr = doc.get(head) or []
            if not any(_match(el, {tail: cond}) for el in arr if isinstance(el, dict)):
                return False
            continue
        value = doc.get(key)
        if isinstance(cond, dict):
            for op, operand in cond.items():
                if op == "$in":
                    if value not in operand:
                        return False
                elif op == "$gte":
                    if value is None or not (value >= operand):
                        return False
                else:
                    raise AssertionError(f"unsupported operator {op}")
        elif value != cond:
            return False
    return True


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=-1):
        self._docs.sort(key=lambda d: d.get(key) or "", reverse=direction == -1)
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeColl:
    def __init__(self, docs):
        self.docs = docs

    def find(self, flt=None, projection=None):
        return _FakeCursor([dict(d) for d in self.docs if _match(d, flt or {})])


class _BoomColl:
    def find(self, *a, **k):
        raise RuntimeError("source down")


class _FakeDB:
    def __init__(self, colls):
        self.colls = colls

    def get_collection(self, name):
        return self.colls.get(name, _FakeColl([]))


# ---------------------------------------------------------------------------
# Fixture data: two stores S1/S2, three products, all events "recent".
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta  # noqa: E402

_NOW = datetime.utcnow()


def _iso(days_ago):
    return (_NOW - timedelta(days=days_ago)).isoformat()


def _grns():
    return [
        {
            "grn_id": "G1",
            "grn_number": "GRN-1",
            "po_number": "PO-9",
            "store_id": "S1",
            "status": "ACCEPTED",
            "created_at": _iso(5),
            "accepted_at": _iso(5),
            "items": [
                {"product_id": "P1", "accepted_qty": 4, "received_qty": 4},
                {"product_id": "P2", "accepted_qty": 0, "received_qty": 2},
            ],
        },
        # PENDING GRN must never appear.
        {
            "grn_id": "G2",
            "grn_number": "GRN-2",
            "store_id": "S1",
            "status": "PENDING",
            "created_at": _iso(1),
            "items": [{"product_id": "P1", "accepted_qty": 9}],
        },
        {
            "grn_id": "G3",
            "grn_number": "GRN-3",
            "store_id": "S2",
            "status": "ACCEPTED",
            "created_at": _iso(2),
            "accepted_at": _iso(2),
            "items": [{"product_id": "P3", "accepted_qty": 6}],
        },
    ]


def _orders():
    return [
        {
            "order_id": "O1",
            "order_number": "ORD-1",
            "invoice_number": "INV-1",
            "store_id": "S1",
            "status": "PAID",
            "created_at": _NOW - timedelta(days=1),
            "items": [
                {"product_id": "P1", "product_name": "Frame A", "sku": "FA", "quantity": 2},
            ],
        },
        # DRAFT order is not a sale.
        {
            "order_id": "O2",
            "order_number": "ORD-2",
            "store_id": "S1",
            "status": "DRAFT",
            "created_at": _NOW - timedelta(days=1),
            "items": [{"product_id": "P1", "quantity": 5}],
        },
    ]


def _transfers():
    return [
        {
            "id": "T1",
            "transfer_number": "TRF-1",
            "from_location_id": "S1",
            "from_location_name": "Store One",
            "to_location_id": "S2",
            "to_location_name": "Store Two",
            "shipped_at": _iso(3),
            "received_at": _iso(2.5),
            "items": [
                {
                    "product_id": "P2",
                    "sku": "SB",
                    "product_name": "Sun B",
                    "quantity_requested": 3,
                    "quantity_shipped": 3,
                    "quantity_received": 2,
                }
            ],
        }
    ]


def _db(**overrides):
    colls = {
        "grns": _FakeColl(_grns()),
        "orders": _FakeColl(_orders()),
        "stock_transfers": _FakeColl(_transfers()),
        "products": _FakeColl(
            [
                {"product_id": "P1", "name": "Frame A", "sku": "FA", "brand": "BR"},
                {"product_id": "P2", "name": "Sun B", "sku": "SB", "brand": "BR"},
                {"product_id": "P3", "brand": "Lux", "model": "Aviator", "sku": "LX"},
            ]
        ),
    }
    colls.update(overrides)
    return _FakeDB(colls)


_ADMIN = {"user_id": "u1", "roles": ["ADMIN"], "active_store_id": None, "store_ids": []}
_S1_MANAGER = {
    "user_id": "u2",
    "roles": ["STORE_MANAGER"],
    "active_store_id": "S1",
    "store_ids": ["S1"],
}


def _run(user=_ADMIN, **params):
    """Drive the endpoint directly. The db comes from inventory._get_db, which
    the autouse fixture patches to a fresh fake; tests needing a special db
    re-patch it themselves."""
    return asyncio.run(
        get_stock_movements(
            store_id=params.get("store_id"),
            product_id=params.get("product_id"),
            movement_type=params.get("movement_type"),
            days=params.get("days", 90),
            limit=params.get("limit", 50),
            skip=params.get("skip", 0),
            current_user=user,
        )
    )


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):
    monkeypatch.setattr(inv, "_get_db", lambda: _db())


# ---------------------------------------------------------------------------
# Merge order + sign conventions
# ---------------------------------------------------------------------------


def test_merge_is_reverse_chronological_across_sources():
    res = _run()
    ats = [e["at"] for e in res["items"]]
    assert ats == sorted(ats, reverse=True)
    # Newest first: the SOLD event (1 day ago) leads the ledger.
    assert res["items"][0]["type"] == "SOLD"
    assert res["sources"] == {
        "grns": "ok",
        "orders": "ok",
        "transfers": "ok",
        "opening_stock": "ok",
    }


def test_sign_conventions_and_qtys():
    res = _run()
    by_id = {e["id"]: e for e in res["items"]}
    assert by_id["RECEIVED:G1:P1:0"]["qty"] == 4  # in -> positive
    assert by_id["SOLD:O1:P1:0"]["qty"] == -2  # out -> negative
    assert by_id["TRANSFER_OUT:T1:P2:0"]["qty"] == -3  # shipped leg negative
    assert by_id["TRANSFER_IN:T1:P2:0"]["qty"] == 2  # received leg positive
    # Zero-accepted GRN line (G1/P2), PENDING GRN (G2) and DRAFT order (O2)
    # must not create events.
    assert not any(e["ref"] == "GRN-2" for e in res["items"])
    assert not any(e["ref_id"] == "O2" for e in res["items"])
    assert "RECEIVED:G1:P2:1" not in by_id


def test_refs_and_details():
    res = _run()
    by_id = {e["id"]: e for e in res["items"]}
    assert by_id["RECEIVED:G1:P1:0"]["ref"] == "GRN-1"
    assert "PO-9" in by_id["RECEIVED:G1:P1:0"]["detail"]
    assert by_id["SOLD:O1:P1:0"]["ref"] == "INV-1"  # invoice wins over order no
    assert by_id["TRANSFER_OUT:T1:P2:0"]["detail"] == "Transfer TRF-1 to Store Two"
    assert by_id["TRANSFER_IN:T1:P2:0"]["detail"] == "Transfer TRF-1 from Store One"


def test_product_enrichment_fills_grn_lines():
    # GRN items carry no product_name/sku; the batched products join fills them
    # (P3 has no `name` -> brand+model fallback).
    res = _run()
    by_id = {e["id"]: e for e in res["items"]}
    assert by_id["RECEIVED:G1:P1:0"]["product_name"] == "Frame A"
    assert by_id["RECEIVED:G1:P1:0"]["sku"] == "FA"
    assert by_id["RECEIVED:G3:P3:0"]["product_name"] == "Lux Aviator"


# ---------------------------------------------------------------------------
# Store scoping
# ---------------------------------------------------------------------------


def test_store_scoped_role_defaults_to_own_store_and_splits_transfer_legs():
    res = _run(user=_S1_MANAGER)
    types = {e["type"] for e in res["items"]}
    stores = {e["store_id"] for e in res["items"]}
    assert stores == {"S1"}
    # S1 shipped T1 -> only the OUT leg; the IN leg belongs to S2's ledger.
    assert "TRANSFER_OUT" in types and "TRANSFER_IN" not in types
    # S2's GRN (G3) is invisible to S1.
    assert not any(e["ref"] == "GRN-3" for e in res["items"])
    assert res["store_id"] == "S1"


def test_store_scoped_role_gets_403_on_foreign_store():
    with pytest.raises(HTTPException) as exc:
        _run(user=_S1_MANAGER, store_id="S2")
    assert exc.value.status_code == 403


def test_admin_sees_all_stores_and_both_transfer_legs():
    res = _run(user=_ADMIN)
    stores = {e["store_id"] for e in res["items"]}
    types = [e["type"] for e in res["items"]]
    assert stores == {"S1", "S2"}
    assert types.count("TRANSFER_OUT") == 1 and types.count("TRANSFER_IN") == 1


def test_receiving_store_sees_only_the_in_leg(monkeypatch):
    s2_user = {
        "user_id": "u3",
        "roles": ["STORE_MANAGER"],
        "active_store_id": "S2",
        "store_ids": ["S2"],
    }
    res = _run(user=s2_user)
    types = {e["type"] for e in res["items"]}
    assert "TRANSFER_IN" in types and "TRANSFER_OUT" not in types
    assert any(e["ref"] == "GRN-3" for e in res["items"])


# ---------------------------------------------------------------------------
# Filters + paging
# ---------------------------------------------------------------------------


def test_type_filter():
    assert {e["type"] for e in _run(movement_type="SOLD")["items"]} == {"SOLD"}
    assert {e["type"] for e in _run(movement_type="received")["items"]} == {
        "RECEIVED"
    }
    # TRANSFER = both legs.
    assert {e["type"] for e in _run(movement_type="TRANSFER")["items"]} == {
        "TRANSFER_IN",
        "TRANSFER_OUT",
    }


def test_invalid_type_is_a_400():
    with pytest.raises(HTTPException) as exc:
        _run(movement_type="TELEPORTED")
    assert exc.value.status_code == 400


def test_product_filter():
    res = _run(product_id="P1")
    assert res["items"] and all(e["product_id"] == "P1" for e in res["items"])


def test_skip_limit_paging_and_has_more():
    full = _run()
    assert full["total"] == 5  # G1/P1, G3/P3, O1/P1, T1 out, T1 in
    page1 = _run(limit=2, skip=0)
    page2 = _run(limit=2, skip=2)
    page3 = _run(limit=2, skip=4)
    assert [len(p["items"]) for p in (page1, page2, page3)] == [2, 2, 1]
    assert (page1["has_more"], page3["has_more"]) == (True, False)
    ids = [e["id"] for p in (page1, page2, page3) for e in p["items"]]
    assert len(ids) == len(set(ids)) == 5  # no overlap, nothing dropped


def test_days_window_excludes_old_events(monkeypatch):
    old = _grns()
    old[0]["created_at"] = _iso(200)
    old[0]["accepted_at"] = _iso(200)
    db = _db(grns=_FakeColl(old))
    monkeypatch.setattr(inv, "_get_db", lambda: db)
    res = _run(days=90)
    assert not any(e["ref"] == "GRN-1" for e in res["items"])
    assert any(e["ref"] == "GRN-3" for e in res["items"])


# ---------------------------------------------------------------------------
# Fail-soft + no-DB
# ---------------------------------------------------------------------------


def test_one_source_failing_never_5xxes(monkeypatch):
    db = _db(grns=_BoomColl())
    monkeypatch.setattr(inv, "_get_db", lambda: db)
    res = _run()
    assert res["sources"]["grns"] == "error"
    assert res["sources"]["orders"] == "ok"
    # Ledger is shorter but alive: the SOLD + transfer events still flow.
    assert any(e["type"] == "SOLD" for e in res["items"])
    assert not any(e["type"] == "RECEIVED" for e in res["items"])


def test_no_db_returns_empty_envelope(monkeypatch):
    monkeypatch.setattr(inv, "_get_db", lambda: None)
    res = _run()
    assert res["items"] == [] and res["total"] == 0 and res["has_more"] is False


# ---------------------------------------------------------------------------
# OPENING_STOCK source (opening_stock_batches summary docs)
# ---------------------------------------------------------------------------


def _opening_batches():
    return [
        {
            "batch_id": "OSB-1",
            "store_id": "S1",
            "committed_by": "u1",
            "committed_at": _iso(4),
            "lines": [
                {
                    "product_id": "P1",
                    "sku": "FA",
                    "product_name": "Frame A",
                    "qty": 12,
                    "unit_cost": 900.0,
                },
                # Zero-qty line (nothing actually minted) -> no event.
                {"product_id": "P2", "sku": "SB", "product_name": "Sun B", "qty": 0},
            ],
            "total_units": 12,
            "total_value": 10800.0,
        }
    ]


def test_opening_stock_events_come_from_batch_docs(monkeypatch):
    db = _db(opening_stock_batches=_FakeColl(_opening_batches()))
    monkeypatch.setattr(inv, "_get_db", lambda: db)
    res = _run()
    by_id = {e["id"]: e for e in res["items"]}
    ev = by_id["OPENING_STOCK:OSB-1:P1:0"]
    assert ev["type"] == "OPENING_STOCK"
    assert ev["qty"] == 12  # stock in -> positive
    assert ev["ref"] == "OSB-1" and ev["ref_id"] == "OSB-1"
    assert ev["store_id"] == "S1"
    assert ev["product_name"] == "Frame A" and ev["sku"] == "FA"
    assert "Opening stock" in ev["detail"]
    # The zero-qty line never becomes an event.
    assert "OPENING_STOCK:OSB-1:P2:1" not in by_id
    assert res["sources"]["opening_stock"] == "ok"


def test_opening_stock_type_filter_and_store_scope(monkeypatch):
    db = _db(opening_stock_batches=_FakeColl(_opening_batches()))
    monkeypatch.setattr(inv, "_get_db", lambda: db)
    only = _run(movement_type="OPENING_STOCK")
    assert only["items"]
    assert {e["type"] for e in only["items"]} == {"OPENING_STOCK"}
    # The S1 batch is invisible in an S2-scoped ledger.
    s2_user = {
        "user_id": "u3",
        "roles": ["STORE_MANAGER"],
        "active_store_id": "S2",
        "store_ids": ["S2"],
    }
    res = _run(user=s2_user)
    assert not any(e["type"] == "OPENING_STOCK" for e in res["items"])


def test_opening_stock_product_filter(monkeypatch):
    db = _db(opening_stock_batches=_FakeColl(_opening_batches()))
    monkeypatch.setattr(inv, "_get_db", lambda: db)
    res = _run(product_id="P1", movement_type="OPENING_STOCK")
    assert [e["product_id"] for e in res["items"]] == ["P1"]


def test_opening_stock_source_fail_soft(monkeypatch):
    db = _db(opening_stock_batches=_BoomColl())
    monkeypatch.setattr(inv, "_get_db", lambda: db)
    res = _run()
    assert res["sources"]["opening_stock"] == "error"
    # The rest of the ledger still flows.
    assert any(e["type"] == "SOLD" for e in res["items"])


# ---------------------------------------------------------------------------
# RBAC row
# ---------------------------------------------------------------------------


def test_rbac_row_mirrors_stock_row():
    from api.services.rbac_policy import policy_for

    row = policy_for("GET", "/api/v1/inventory/movements")
    assert row is not None
    assert row["allowed"] == "AUTHENTICATED"
    assert row.get("store_scoped") is True
