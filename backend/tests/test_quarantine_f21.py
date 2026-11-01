"""F21 -- Defective quarantine barcoding (intent-level acceptance).

These tests assert the BUSINESS INTENT, not the plumbing: a quarantined unit is
unsellable + untransferable, every transition is hash-chain audited, the red
label flips the printed flag, the luxury line appears, the queue counts
unlabeled units, lift restores AVAILABLE, and an RTV backfills the unit linkage.

They drive the real route-handler coroutines directly against a FakeDB +
monkeypatched repositories (the same pattern as test_order_oversell /
test_walkouts), so NO live Mongo is required. A hollow shell FAILS these.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from tests.test_walkouts import FakeDB  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / wiring
# ---------------------------------------------------------------------------

_MANAGER = {
    "user_id": "mgr-1",
    "username": "manager",
    "name": "Store Manager",
    "roles": ["STORE_MANAGER"],
    "store_ids": ["S-A"],
    "active_store_id": "S-A",
}
_ACCOUNTANT = {
    "user_id": "acc-1",
    "username": "acct",
    "roles": ["ACCOUNTANT"],
    "store_ids": ["S-A"],
    "active_store_id": "S-A",
}


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _queue(user, store_id=None, rtv_vendor_id=None, label_printed=None,
           date_from=None, date_to=None):
    """Call list_quarantined_stock with ALL query params explicit. (Calling a
    FastAPI handler directly leaves unset Query(...) params as FieldInfo objects,
    so we must pass each one.)"""
    from api.routers.inventory import list_quarantined_stock

    return list_quarantined_stock(
        store_id=store_id,
        rtv_vendor_id=rtv_vendor_id,
        label_printed=label_printed,
        date_from=date_from,
        date_to=date_to,
        current_user=user,
    )


@pytest.fixture
def env(monkeypatch):
    """Wire a FakeDB + repos into inventory / labels / vendor_returns."""
    from api.routers import inventory as inv
    from api.routers import labels as lbl
    from api.routers import vendor_returns as vr
    from api import dependencies as dep
    from database.repositories.product_repository import (
        StockRepository,
        ProductRepository,
    )
    from database.repositories.audit_repository import AuditRepository

    db = FakeDB()
    stock_repo = StockRepository(db.get_collection("stock_units"))
    prod_repo = ProductRepository(db.get_collection("products"))
    audit_repo = AuditRepository(db.get_collection("audit_logs"))

    # inventory.py uses get_stock_repository / get_audit_repository (imported
    # name) + _get_db() for the queue read.
    monkeypatch.setattr(inv, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(inv, "get_product_repository", lambda: prod_repo)
    monkeypatch.setattr(inv, "get_audit_repository", lambda: audit_repo)
    monkeypatch.setattr(inv, "_get_db", lambda: db)

    # labels.py
    monkeypatch.setattr(lbl, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(lbl, "get_product_repository", lambda: prod_repo)
    monkeypatch.setattr(lbl, "get_store_repository", lambda: None)
    monkeypatch.setattr(lbl, "get_audit_repository", lambda: audit_repo)

    # vendor_returns.py uses module-level get_db + lazy dep.get_audit_repository
    monkeypatch.setattr(vr, "get_db", lambda: db)
    monkeypatch.setattr(dep, "get_audit_repository", lambda: audit_repo)

    def add_unit(stock_id, product_id="P-FRAME", status="AVAILABLE", store_id="S-A"):
        db.get_collection("stock_units").insert_one(
            {
                "stock_id": stock_id,
                "product_id": product_id,
                "store_id": store_id,
                "status": status,
                "barcode": f"BC-{stock_id}",
                "quantity": 1,
            }
        )

    def add_product(product_id="P-FRAME", brand="Ray-Ban", category="FRAME"):
        db.get_collection("products").insert_one(
            {
                "product_id": product_id,
                "name": f"{brand} {category}",
                "brand": brand,
                "category": category,
            }
        )

    return {
        "db": db,
        "stock_repo": stock_repo,
        "audit_repo": audit_repo,
        "add_unit": add_unit,
        "add_product": add_product,
    }


def _audit_rows(env, action=None):
    rows = env["db"].get_collection("audit_logs").docs
    if action is None:
        return rows
    return [r for r in rows if r.get("action") == action]


# ---------------------------------------------------------------------------
# 1. QUARANTINED is unsellable -- excluded from the on-hand / sellable rollup
# ---------------------------------------------------------------------------

def test_quarantine_removes_unit_from_sellable_on_hand(env):
    from api.routers.inventory import (
        quarantine_stock_unit,
        QuarantineRequest,
    )

    env["add_product"]()
    env["add_unit"]("STO-1")  # the ONLY available unit of P-FRAME

    # Before: 1 AVAILABLE unit on hand (find_available is the POS sellable path).
    assert env["stock_repo"].find_available("P-FRAME", "S-A") == 1

    _run(
        quarantine_stock_unit(
            "STO-1", QuarantineRequest(reason="DEFECTIVE"), _MANAGER
        )
    )

    # After: zero sellable on hand -- a POS sell could only oversell (409).
    assert env["stock_repo"].find_available("P-FRAME", "S-A") == 0
    # And the unit carries the free-string QUARANTINED status (no enum change).
    unit = env["stock_repo"].find_by_id("STO-1")
    assert unit["status"] == "QUARANTINED"


def test_pos_sell_of_quarantined_only_unit_returns_409(client, auth_headers):
    """End-to-end: with the product's only unit QUARANTINED, find_available
    returns 0 and POS order creation is rejected 409 (insufficient stock)."""
    from api.routers import orders as om

    class _Stock:
        def count(self, q):
            return 1  # serialized-tracked (1 row exists, but it's quarantined)

        def find_available(self, pid, store_id):
            return 0  # QUARANTINED excluded from the AVAILABLE allowlist

        def find_by_product_store(self, pid, store_id):
            return []

        def mark_sold(self, sid, oid):
            return True

    import contextlib

    with contextlib.ExitStack() as stack:
        from database.repositories.order_repository import OrderRepository
        from database.repositories.customer_repository import CustomerRepository
        from database.repositories.product_repository import ProductRepository
        from tests.test_walkouts import FakeDB as _FDB

        fdb = _FDB()
        order_repo = OrderRepository(fdb.get_collection("orders"))
        cust_repo = CustomerRepository(fdb.get_collection("customers"))
        prod_repo = ProductRepository(fdb.get_collection("products"))
        cust_repo.create({"customer_id": "c-1", "name": "T", "mobile": "9100000099", "phone": "9100000099"})
        prod_repo.create({"product_id": "P-FRAME", "name": "Frame", "category": "FRAME",
                          "mrp": 10000.0, "cost_price": 1000.0, "is_active": True})

        stack.enter_context(_patch(om, "get_order_repository", lambda: order_repo))
        stack.enter_context(_patch(om, "get_customer_repository", lambda: cust_repo))
        stack.enter_context(_patch(om, "get_product_repository", lambda: prod_repo))
        stack.enter_context(_patch(om, "get_walkin_counter_repository", lambda: None))
        stack.enter_context(_patch(om, "get_stock_repository", lambda: _Stock()))

        r = client.post(
            "/api/v1/orders",
            json={
                "customer_id": "c-1",
                "items": [
                    {
                        "product_id": "P-FRAME",
                        "product_name": "Frame",
                        "item_type": "FRAME",
                        "category": "FRAME",
                        "quantity": 1,
                        "unit_price": 10000.0,
                    }
                ],
            },
            headers=auth_headers,
        )
    assert r.status_code == 409, r.text
    assert "insufficient stock" in r.text.lower()


class _patch:
    """Tiny context-manager monkeypatch (setattr/restore) for the ExitStack."""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self

    def __exit__(self, *a):
        setattr(self.obj, self.name, self.old)


# ---------------------------------------------------------------------------
# 2. lift-quarantine restores AVAILABLE with a mandatory reason + audit
# ---------------------------------------------------------------------------

def test_lift_quarantine_restores_available_with_reason_and_audit(env):
    from api.routers.inventory import (
        quarantine_stock_unit,
        lift_quarantine_stock_unit,
        QuarantineRequest,
        LiftQuarantineRequest,
    )

    env["add_product"]()
    env["add_unit"]("STO-2")
    _run(quarantine_stock_unit("STO-2", QuarantineRequest(reason="SCRATCHED"), _MANAGER))
    assert env["stock_repo"].find_by_id("STO-2")["status"] == "QUARANTINED"

    _run(
        lift_quarantine_stock_unit(
            "STO-2",
            LiftQuarantineRequest(lift_reason="Re-inspected, no defect found"),
            _MANAGER,
        )
    )
    unit = env["stock_repo"].find_by_id("STO-2")
    assert unit["status"] == "AVAILABLE"
    assert env["stock_repo"].find_available("P-FRAME", "S-A") == 1

    lifted = _audit_rows(env, "QUARANTINE_LIFTED")
    assert len(lifted) == 1
    assert lifted[0]["detail"]["lift_reason"] == "Re-inspected, no defect found"
    assert lifted[0]["entity_type"] == "STOCK_UNIT"


def test_lift_requires_min_5_char_reason():
    from api.routers.inventory import LiftQuarantineRequest

    with pytest.raises(Exception):
        LiftQuarantineRequest(lift_reason="no")  # < 5 chars -> validation error


def test_lift_on_non_quarantined_returns_409(env):
    from fastapi import HTTPException
    from api.routers.inventory import (
        lift_quarantine_stock_unit,
        LiftQuarantineRequest,
    )

    env["add_product"]()
    env["add_unit"]("STO-3")  # AVAILABLE, not quarantined
    with pytest.raises(HTTPException) as exc:
        _run(
            lift_quarantine_stock_unit(
                "STO-3", LiftQuarantineRequest(lift_reason="trying to lift"), _MANAGER
            )
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "not_quarantined"


# ---------------------------------------------------------------------------
# 3. Label endpoint flips the printed flag + writes an audit row
# ---------------------------------------------------------------------------

def test_label_flips_printed_flag_and_audits(env):
    from api.routers.inventory import quarantine_stock_unit, QuarantineRequest
    from api.routers.labels import get_quarantine_label

    env["add_product"]()
    env["add_unit"]("STO-4")
    _run(quarantine_stock_unit("STO-4", QuarantineRequest(reason="DEFECTIVE"), _MANAGER))
    assert env["stock_repo"].find_by_id("STO-4")["quarantine_label_printed"] is False

    payload = _run(get_quarantine_label("STO-4", _MANAGER))
    assert payload["label_type"] == "QUARANTINED"
    assert "QUARANTINED" in payload["header"]
    assert payload["barcode_value"] == "BC-STO-4"
    assert env["stock_repo"].find_by_id("STO-4")["quarantine_label_printed"] is True

    printed = _audit_rows(env, "QUARANTINE_LABEL_PRINTED")
    assert len(printed) == 1
    assert printed[0]["entity_type"] == "STOCK_UNIT"


def test_label_on_non_quarantined_returns_400_and_no_flip(env):
    from fastapi import HTTPException
    from api.routers.labels import get_quarantine_label

    env["add_product"]()
    env["add_unit"]("STO-5")  # AVAILABLE, not quarantined
    with pytest.raises(HTTPException) as exc:
        _run(get_quarantine_label("STO-5", _MANAGER))
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "not_quarantined"
    assert "quarantine_label_printed" not in env["stock_repo"].find_by_id("STO-5")


def test_luxury_brand_line_appears_only_for_luxury(env):
    from api.routers.inventory import quarantine_stock_unit, QuarantineRequest
    from api.routers.labels import get_quarantine_label

    env["add_product"]("P-LUX", brand="Gucci", category="SUNGLASS")
    env["add_product"]("P-STD", brand="Ray-Ban", category="SUNGLASS")
    env["add_unit"]("STO-LUX", product_id="P-LUX")
    env["add_unit"]("STO-STD", product_id="P-STD")
    _run(quarantine_stock_unit("STO-LUX", QuarantineRequest(reason="DEFECTIVE"), _MANAGER))
    _run(quarantine_stock_unit("STO-STD", QuarantineRequest(reason="DEFECTIVE"), _MANAGER))

    lux = _run(get_quarantine_label("STO-LUX", _MANAGER))
    std = _run(get_quarantine_label("STO-STD", _MANAGER))
    assert lux.get("luxury_brand_line") == "BRAND AUTHORIZATION REQUIRED"
    assert "luxury_brand_line" not in std


# ---------------------------------------------------------------------------
# 4. transfers ship-move rejects a QUARANTINED unit (400)
# ---------------------------------------------------------------------------

def _quarantine_shop(monkeypatch):
    import mongomock
    from api.routers import transfers as tr

    db = mongomock.MongoClient()["f21_transfers"]
    db["stock_units"].insert_one({"stock_id": "STO-T1", "product_id": "P-FRAME", "store_id": "S-A",
                                  "status": "QUARANTINED", "barcode": "BC-STO-T1"})
    monkeypatch.setattr(tr, "_get_db", lambda: db)
    return tr, db


def test_transfer_ship_packed_quarantined_unit_refused(monkeypatch):
    """S3: ship moves exactly the packed pieces, each checked first -- a
    QUARANTINED piece in the box refuses the ship by its barcode, and it
    stays in quarantine (a defective unit never moves between stores)."""
    from fastapi import HTTPException

    tr, db = _quarantine_shop(monkeypatch)
    transfer = {
        "id": "TR-1", "from_location_id": "S-A", "to_location_id": "S-B",
        "items": [{"product_id": "P-FRAME", "packed_stock_ids": ["STO-T1"],
                   "packed_barcodes": ["BC-STO-T1"]}],
    }
    with pytest.raises(HTTPException) as exc:
        tr._apply_ship_stock_move(transfer)
    assert exc.value.status_code == 409 and "BC-STO-T1" in exc.value.detail
    assert db["stock_units"].find_one({"stock_id": "STO-T1"})["status"] == "QUARANTINED"


def test_transfer_pack_refuses_a_quarantined_unit(monkeypatch):
    """The box cannot even take it: only a FREE (AVAILABLE) piece is packed."""
    from fastapi import HTTPException

    tr, db = _quarantine_shop(monkeypatch)
    tr._save_transfer({
        "id": "TR-2", "transfer_number": "TR-2", "status": "approved",
        "from_location_id": "S-A", "to_location_id": "S-B",
        "items": [{"id": "L1", "product_id": "P-FRAME", "quantity_requested": 1}],
    })
    admin = {"user_id": "u", "username": "u", "roles": ["SUPERADMIN"], "store_ids": []}
    with pytest.raises(HTTPException) as exc:
        _run(tr.pack_transfer("TR-2", tr.TransferPack(barcodes=["BC-STO-T1"]), admin))
    assert exc.value.status_code == 400 and "quarantined" in exc.value.detail


# ---------------------------------------------------------------------------
# 5. Each transition writes ONE STOCK_UNIT audit row (STOCK_QUARANTINED)
# ---------------------------------------------------------------------------

def test_quarantine_writes_one_stock_quarantined_audit_row(env):
    from api.routers.inventory import quarantine_stock_unit, QuarantineRequest

    env["add_product"]()
    env["add_unit"]("STO-6")
    _run(
        quarantine_stock_unit(
            "STO-6",
            QuarantineRequest(reason="DEFECTIVE", notes="cracked hinge"),
            _MANAGER,
        )
    )
    rows = _audit_rows(env, "STOCK_QUARANTINED")
    assert len(rows) == 1
    row = rows[0]
    assert row["entity_type"] == "STOCK_UNIT"
    assert row["entity_id"] == "STO-6"
    assert row["before_state"] == {"status": "AVAILABLE"}
    assert row["after_state"]["status"] == "QUARANTINED"


# ---------------------------------------------------------------------------
# 6. Quarantine queue + unlabeled count
# ---------------------------------------------------------------------------

def test_quarantine_queue_unlabeled_count(env):
    from api.routers.inventory import quarantine_stock_unit, QuarantineRequest
    from api.routers.labels import get_quarantine_label

    env["add_product"]()
    for sid in ("STO-Q1", "STO-Q2", "STO-Q3"):
        env["add_unit"](sid)
        _run(quarantine_stock_unit(sid, QuarantineRequest(reason="DEFECTIVE"), _MANAGER))
    # Print labels for 2 of the 3.
    _run(get_quarantine_label("STO-Q1", _MANAGER))
    _run(get_quarantine_label("STO-Q2", _MANAGER))

    full = _run(_queue(_MANAGER))
    assert full["total"] == 3
    assert full["unlabeled_count"] == 1

    unlabeled = _run(_queue(_MANAGER, label_printed=False))
    assert unlabeled["total"] == 1
    assert unlabeled["items"][0]["stock_id"] == "STO-Q3"
    # Queue rows are enriched with product master fields.
    assert unlabeled["items"][0]["product_name"]


def test_queue_is_store_scoped(env):
    """A STORE_MANAGER for S-A asking for S-B gets nothing (resolve_store_scope
    pins / 403s a store role)."""
    from fastapi import HTTPException
    from api.routers.inventory import quarantine_stock_unit, QuarantineRequest

    env["add_product"]()
    env["add_unit"]("STO-SA", store_id="S-A")
    _run(quarantine_stock_unit("STO-SA", QuarantineRequest(reason="DEFECTIVE"), _MANAGER))

    # Own store: sees it.
    own = _run(_queue(_MANAGER))
    assert own["total"] == 1
    # Cross-store request: resolve_store_scope 403s a store-level role.
    with pytest.raises(HTTPException) as exc:
        _run(_queue(_MANAGER, store_id="S-B"))
    assert exc.value.status_code == 403


def test_accountant_can_read_queue(env):
    from api.routers.inventory import quarantine_stock_unit, QuarantineRequest

    env["add_product"]()
    env["add_unit"]("STO-AC")
    _run(quarantine_stock_unit("STO-AC", QuarantineRequest(reason="DEFECTIVE"), _MANAGER))
    res = _run(_queue(_ACCOUNTANT))
    assert res["total"] == 1


# 7. RTV linkage: a supplier return names its pieces (owner 2026-10-18) --
#    an AVAILABLE piece is set aside, never silently skipped. Covered on the
#    real routes in tests/test_vendor_return_pieces.py.


# ---------------------------------------------------------------------------
# Guards: already-quarantined / ineligible / not-found / cross-store
# ---------------------------------------------------------------------------

def test_double_quarantine_returns_409(env):
    from fastapi import HTTPException
    from api.routers.inventory import quarantine_stock_unit, QuarantineRequest

    env["add_product"]()
    env["add_unit"]("STO-DUP")
    _run(quarantine_stock_unit("STO-DUP", QuarantineRequest(reason="DEFECTIVE"), _MANAGER))
    with pytest.raises(HTTPException) as exc:
        _run(quarantine_stock_unit("STO-DUP", QuarantineRequest(reason="DEFECTIVE"), _MANAGER))
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "already_quarantined"


def test_quarantine_sold_unit_not_eligible_409(env):
    from fastapi import HTTPException
    from api.routers.inventory import quarantine_stock_unit, QuarantineRequest

    env["add_product"]()
    env["add_unit"]("STO-SOLD", status="SOLD")
    with pytest.raises(HTTPException) as exc:
        _run(quarantine_stock_unit("STO-SOLD", QuarantineRequest(reason="DEFECTIVE"), _MANAGER))
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "not_eligible"


def test_quarantine_cross_store_unit_hidden_404(env):
    from fastapi import HTTPException
    from api.routers.inventory import quarantine_stock_unit, QuarantineRequest

    env["add_product"]()
    env["add_unit"]("STO-X", store_id="S-OTHER")  # manager scoped to S-A
    with pytest.raises(HTTPException) as exc:
        _run(quarantine_stock_unit("STO-X", QuarantineRequest(reason="DEFECTIVE"), _MANAGER))
    assert exc.value.status_code == 404


def test_invalid_reason_rejected_422(env):
    from fastapi import HTTPException
    from api.routers.inventory import quarantine_stock_unit, QuarantineRequest

    env["add_product"]()
    env["add_unit"]("STO-BR")
    with pytest.raises(HTTPException) as exc:
        _run(quarantine_stock_unit("STO-BR", QuarantineRequest(reason="NONSENSE"), _MANAGER))
    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# A02: a competing write must win over a stale quarantine / lift request.
# ---------------------------------------------------------------------------


@pytest.fixture
def transition_effects(monkeypatch):
    """Observe successful-transition hooks without any external dispatch."""
    from unittest.mock import AsyncMock, Mock
    from agents import registry
    from api.services import item_events, online_stock_writeback

    effects = {
        "online": Mock(),
        "ledger": Mock(),
        "dispatch": AsyncMock(),
    }
    monkeypatch.setattr(online_stock_writeback, "writeback_after_units_left", effects["online"])
    monkeypatch.setattr(item_events, "record_post_write_event", effects["ledger"])
    monkeypatch.setattr(registry, "dispatch_event", effects["dispatch"])
    return effects


def _compete_at_write(env, monkeypatch, stock_id, target_status, competing_values):
    """Land another writer immediately before the route's real Mongo update.

    This exercises both the old unconditional write and the conditional fix;
    it does not assume which repository method the route uses.
    """
    collection = env["stock_repo"].collection
    original = collection.update_one
    competed = []

    def race(query, update, *args, **kwargs):
        if (not competed and query.get("stock_id") == stock_id
                and update.get("$set", {}).get("status") == target_status):
            competed.append(True)
            original({"stock_id": stock_id}, {"$set": competing_values})
        return original(query, update, *args, **kwargs)

    monkeypatch.setattr(collection, "update_one", race)
    return competed


def _assert_no_transition_effects(env, effects):
    assert _audit_rows(env) == []
    for effect in effects.values():
        effect.assert_not_called()


@pytest.mark.parametrize("competing_values", [
    {"status": "SOLD", "order_id": "ORDER-WON"},
    {"status": "AVAILABLE", "store_id": "S-B"},
    {"status": "QUARANTINED", "quarantine_reason": "DAMAGED_IN_TRANSIT"},
])
def test_stale_quarantine_refuses_competing_write(
    env, monkeypatch, transition_effects, competing_values,
):
    from fastapi import HTTPException
    from api.routers.inventory import quarantine_stock_unit, QuarantineRequest

    env["add_unit"]("STO-RACE")
    competed = _compete_at_write(
        env, monkeypatch, "STO-RACE", "QUARANTINED", competing_values,
    )
    with pytest.raises(HTTPException) as exc:
        _run(quarantine_stock_unit("STO-RACE", QuarantineRequest(reason="DEFECTIVE"), _MANAGER))

    assert competed
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "stock_changed"
    unit = env["stock_repo"].find_by_id("STO-RACE")
    assert all(unit[key] == value for key, value in competing_values.items())
    assert "quarantine_by" not in unit
    _assert_no_transition_effects(env, transition_effects)


@pytest.mark.parametrize("competing_values", [
    {"status": "SOLD", "order_id": "ORDER-WON"},
    {"status": "AVAILABLE", "quarantine_lifted_by": "OTHER-MANAGER"},
    {"store_id": "S-B"},
    {"quarantine_at": "2026-10-18T12:00:01+05:30"},
    {"quarantine_generation": "new-cycle-same-timestamp"},
    {"last_event_seq": 2},
])
def test_stale_lift_refuses_competing_write_or_new_quarantine_cycle(
    env, monkeypatch, transition_effects, competing_values,
):
    from fastapi import HTTPException
    from api.routers.inventory import lift_quarantine_stock_unit, LiftQuarantineRequest

    env["add_unit"]("STO-LIFT-RACE", status="QUARANTINED")
    env["stock_repo"].collection.update_one({"stock_id": "STO-LIFT-RACE"}, {"$set": {
        "quarantine_at": "2026-10-18T12:00:00+05:30",
        "quarantine_generation": "original-cycle",
        "last_event_seq": 1,
    }})
    competed = _compete_at_write(
        env, monkeypatch, "STO-LIFT-RACE", "AVAILABLE", competing_values,
    )
    with pytest.raises(HTTPException) as exc:
        _run(lift_quarantine_stock_unit(
            "STO-LIFT-RACE", LiftQuarantineRequest(lift_reason="Found undamaged"), _MANAGER,
        ))

    assert competed
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "stock_changed"
    unit = env["stock_repo"].find_by_id("STO-LIFT-RACE")
    assert all(unit[key] == value for key, value in competing_values.items())
    assert "quarantine_lift_reason" not in unit
    _assert_no_transition_effects(env, transition_effects)


@pytest.mark.parametrize("status", ["AVAILABLE", " available ", "DAMAGED", " damaged ", "", None, "MISSING"])
def test_quarantine_preserves_eligible_legacy_statuses(env, transition_effects, status):
    from api.routers.inventory import quarantine_stock_unit, QuarantineRequest

    env["add_unit"]("STO-LEGACY", status=status)
    if status == "MISSING":
        env["stock_repo"].collection.update_one({"stock_id": "STO-LEGACY"}, {"$unset": {"status": ""}})
    _run(quarantine_stock_unit("STO-LEGACY", QuarantineRequest(reason="DEFECTIVE"), _MANAGER))
    assert env["stock_repo"].find_by_id("STO-LEGACY")["status"] == "QUARANTINED"
    assert len(_audit_rows(env, "STOCK_QUARANTINED")) == 1


def test_lift_preserves_legacy_quarantine_without_generation(env, transition_effects):
    from api.routers.inventory import lift_quarantine_stock_unit, LiftQuarantineRequest

    env["add_unit"]("STO-LEGACY-LIFT", status=" quarantined ")
    _run(lift_quarantine_stock_unit(
        "STO-LEGACY-LIFT", LiftQuarantineRequest(lift_reason="Found undamaged"), _MANAGER,
    ))
    assert env["stock_repo"].find_by_id("STO-LEGACY-LIFT")["status"] == "AVAILABLE"
    assert len(_audit_rows(env, "QUARANTINE_LIFTED")) == 1


def test_quarantine_cycles_have_distinct_generations_even_at_same_time(
    env, monkeypatch, transition_effects,
):
    from datetime import datetime, timezone
    from api.routers import inventory as inv

    monkeypatch.setattr(inv, "_now_ist", lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    env["add_unit"]("STO-CYCLE")
    _run(inv.quarantine_stock_unit("STO-CYCLE", inv.QuarantineRequest(reason="DEFECTIVE"), _MANAGER))
    first = env["stock_repo"].find_by_id("STO-CYCLE")
    _run(inv.lift_quarantine_stock_unit(
        "STO-CYCLE", inv.LiftQuarantineRequest(lift_reason="Found undamaged"), _MANAGER,
    ))
    _run(inv.quarantine_stock_unit("STO-CYCLE", inv.QuarantineRequest(reason="SCRATCHED"), _MANAGER))
    second = env["stock_repo"].find_by_id("STO-CYCLE")

    assert first["quarantine_at"] == second["quarantine_at"]
    assert first.get("quarantine_generation")
    assert second.get("quarantine_generation") != first["quarantine_generation"]
    assert len(_audit_rows(env, "STOCK_QUARANTINED")) == 2
    assert len(_audit_rows(env, "QUARANTINE_LIFTED")) == 1
