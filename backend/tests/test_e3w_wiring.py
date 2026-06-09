"""E3w -- wire the EXISTING stock state-change paths through the merged E3
item-event ledger + the return-side serial-mismatch guard (CORRECTIONS R2).

These tests assert the WIRING INTENT of E3w, NOT the E3 engine internals (that
is test_e3_item_event.py). For every wired non-POS path we prove three things:

  (i)   the EXISTING behavior / response is UNCHANGED (status writes, return
        amounts, idempotency flags still land exactly as before);
  (ii)  an `item_events` ledger row is NOW recorded for the transition;
  (iii) a record_event FAILURE does NOT break the path -- injecting a raising
        recorder leaves the existing path fully successful (ADDITIVE + fail-soft
        contract: the ledger emit can never roll back / alter the business write).

Wired paths (priority #1 first):
  1. F21 quarantine  (inventory.quarantine_stock_unit / lift_quarantine) ->
     QUARANTINE_IN / QUARANTINE_OUT.
  2. transfers ship / receive (_apply_ship/_apply_receive) -> TRANSFER_SHIP /
     TRANSFER_RECEIVE.
  3. GRN goods-receipt mint (vendors.accept_grn) -> MINT.
  4. lens reserve / release (lens_stock.reserve_cell / release_cell) ->
     RESERVE / RELEASE (aggregate cell event; lens_line_id + cell_key, no CAS).

Return-serial-block (E3 acceptance #8, return half, returns.create_return):
  * mismatched scanned-vs-recorded serial -> HTTP 409 reason=SERIAL_MISMATCH;
  * EITHER serial absent -> allowed (permissive);
  * a consumed manager override -> allowed + override_by stamped.

They drive the real route-handler coroutines + helpers against the CAS-capable
in-memory FakeDB from test_e3_item_event (the same monkeypatch pattern), so NO
live Mongo is required.
"""

from __future__ import annotations

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from tests.test_e3_item_event import CasDB, _run  # noqa: E402


_MANAGER = {
    "user_id": "mgr-1",
    "username": "manager",
    "name": "Store Manager",
    "roles": ["STORE_MANAGER"],
    "store_ids": ["S-A"],
    "active_store_id": "S-A",
}
_CASHIER = {
    "user_id": "cash-1",
    "username": "cashier",
    "roles": ["SALES_CASHIER"],
    "store_ids": ["S-A"],
    "active_store_id": "S-A",
}


def _events(db, stock_id=None, event_type=None):
    rows = db.get_collection("item_events").docs
    if stock_id is not None:
        rows = [r for r in rows if r.get("stock_id") == stock_id]
    if event_type is not None:
        rows = [r for r in rows if r.get("event_type") == event_type]
    return rows


# ===========================================================================
# Shared fixtures
# ===========================================================================


@pytest.fixture
def inv_env(monkeypatch):
    """FakeDB + repos wired into inventory.py for the F21 quarantine paths."""
    from api.routers import inventory as inv
    from api import dependencies as dep
    from database.repositories.product_repository import StockRepository
    from database.repositories.audit_repository import AuditRepository

    db = CasDB()
    stock_repo = StockRepository(db.get_collection("stock_units"))
    audit_repo = AuditRepository(db.get_collection("audit_logs"))

    monkeypatch.setattr(inv, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(inv, "_get_db", lambda: db)
    monkeypatch.setattr(inv, "get_audit_repository", lambda: audit_repo)
    monkeypatch.setattr(dep, "get_audit_repository", lambda: audit_repo)

    # Neutralise the event-bus dispatch + period-lock import in the handlers so
    # the test stays self-contained (both are already fail-soft in prod).
    async def _noop_dispatch(*a, **k):
        return None

    import agents.registry as reg
    monkeypatch.setattr(reg, "dispatch_event", _noop_dispatch, raising=False)

    def add_unit(stock_id, product_id="P-FRAME", status="AVAILABLE", store_id="S-A"):
        db.get_collection("stock_units").insert_one(
            {"stock_id": stock_id, "product_id": product_id, "store_id": store_id,
             "status": status, "barcode": f"BC-{stock_id}", "quantity": 1}
        )

    return {"db": db, "stock_repo": stock_repo, "add_unit": add_unit}


# ===========================================================================
# 1. F21 quarantine  (priority -- converge the two divergent QUARANTINED paths)
# ===========================================================================


def test_quarantine_unchanged_behavior_and_ledger_row(inv_env):
    from api.routers.inventory import quarantine_stock_unit, QuarantineRequest

    inv_env["add_unit"]("Q1")
    out = _run(quarantine_stock_unit("Q1", QuarantineRequest(reason="DEFECTIVE"), _MANAGER))
    # (i) existing behavior: status flips to QUARANTINED, response unchanged shape.
    assert out["stock_unit"]["status"] == "QUARANTINED"
    assert inv_env["stock_repo"].find_by_id("Q1")["status"] == "QUARANTINED"
    # (ii) a QUARANTINE_IN ledger row is now recorded for the transition.
    rows = _events(inv_env["db"], "Q1", "quarantine.in")
    assert len(rows) == 1
    assert rows[0]["from_state"] == "AVAILABLE"
    assert rows[0]["to_state"] == "QUARANTINED"
    assert rows[0]["source_type"] == "F21"


def test_lift_quarantine_unchanged_behavior_and_ledger_row(inv_env):
    from api.routers.inventory import (
        quarantine_stock_unit, lift_quarantine_stock_unit,
        QuarantineRequest, LiftQuarantineRequest,
    )

    inv_env["add_unit"]("Q2")
    _run(quarantine_stock_unit("Q2", QuarantineRequest(reason="DEFECTIVE"), _MANAGER))
    out = _run(lift_quarantine_stock_unit(
        "Q2", LiftQuarantineRequest(lift_reason="mis-quarantine correction"), _MANAGER))
    # (i) existing behavior: restored to AVAILABLE.
    assert out["stock_unit"]["status"] == "AVAILABLE"
    assert inv_env["stock_repo"].find_by_id("Q2")["status"] == "AVAILABLE"
    # (ii) a QUARANTINE_OUT row recorded (QUARANTINED -> AVAILABLE).
    rows = _events(inv_env["db"], "Q2", "quarantine.out")
    assert len(rows) == 1
    assert rows[0]["from_state"] == "QUARANTINED"
    assert rows[0]["to_state"] == "AVAILABLE"


def test_quarantine_legacy_lowercase_status_canonicalised(inv_env):
    """A unit minted with legacy lowercase 'available' still quarantines and the
    ledger row carries the canonical AVAILABLE (never a raw lowercase string)."""
    from api.routers.inventory import quarantine_stock_unit, QuarantineRequest

    inv_env["add_unit"]("Q3", status="available")
    out = _run(quarantine_stock_unit("Q3", QuarantineRequest(reason="DEFECTIVE"), _MANAGER))
    assert out["stock_unit"]["status"] == "QUARANTINED"
    row = _events(inv_env["db"], "Q3", "quarantine.in")[0]
    assert row["from_state"] == "AVAILABLE"
    assert row["from_state"] not in ("available", "in_stock", "IN_STOCK")


def test_quarantine_ledger_failure_does_not_break_path(inv_env, monkeypatch):
    from api.routers.inventory import quarantine_stock_unit, QuarantineRequest
    from api.services import item_events as ie

    def _boom(*a, **k):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(ie, "record_post_write_event", _boom)
    inv_env["add_unit"]("Q4")
    # (iii) the path still SUCCEEDS even though the ledger recorder raises.
    out = _run(quarantine_stock_unit("Q4", QuarantineRequest(reason="DEFECTIVE"), _MANAGER))
    assert out["stock_unit"]["status"] == "QUARANTINED"
    assert inv_env["stock_repo"].find_by_id("Q4")["status"] == "QUARANTINED"
    # No ledger row landed (the recorder raised), but the business write stuck.
    assert _events(inv_env["db"], "Q4") == []


# ===========================================================================
# 2. transfers ship / receive
# ===========================================================================


@pytest.fixture
def xfer_env(monkeypatch):
    from api.routers import transfers as tr
    from database.repositories.product_repository import StockRepository

    db = CasDB()
    stock_repo = StockRepository(db.get_collection("stock_units"))
    monkeypatch.setattr(tr, "_get_db", lambda: db)
    monkeypatch.setattr(tr, "get_stock_repository", lambda: stock_repo)

    def add_unit(stock_id, product_id="P-FRAME", status="AVAILABLE", store_id="S-A"):
        db.get_collection("stock_units").insert_one(
            {"stock_id": stock_id, "product_id": product_id, "store_id": store_id,
             "status": status, "barcode": f"BC-{stock_id}", "quantity": 1}
        )

    return {"db": db, "stock_repo": stock_repo, "add_unit": add_unit, "tr": tr}


def _transfer(items, **extra):
    base = {
        "id": "T1",
        "transfer_number": "TR-001",
        "from_location_id": "S-A",
        "to_location_id": "S-B",
        "items": items,
    }
    base.update(extra)
    return base


def test_transfer_ship_unchanged_and_ledger_row(xfer_env):
    tr = xfer_env["tr"]
    xfer_env["add_unit"]("X1")
    xfer_env["add_unit"]("X2")
    transfer = _transfer([{"product_id": "P-FRAME", "quantity_requested": 2}])
    out = tr._apply_ship_stock_move(transfer)
    # (i) existing behavior: 2 units flipped to TRANSFERRED, idempotency flag set.
    assert out["stock_shipped"] is True
    assert out["stock_units_moved_out"] == 2
    assert xfer_env["stock_repo"].find_by_id("X1")["status"] == "TRANSFERRED"
    # (ii) two TRANSFER_SHIP ledger rows (AVAILABLE -> TRANSFERRED), to_store set.
    rows = _events(xfer_env["db"], event_type="transfer.ship")
    assert len(rows) == 2
    assert all(r["from_state"] == "AVAILABLE" and r["to_state"] == "TRANSFERRED"
               for r in rows)
    assert all(r["store_id"] == "S-A" and r["to_store_id"] == "S-B" for r in rows)


def test_transfer_receive_unchanged_and_ledger_row(xfer_env):
    tr = xfer_env["tr"]
    xfer_env["add_unit"]("X3")
    transfer = _transfer([{"product_id": "P-FRAME", "quantity_requested": 1}])
    transfer = tr._apply_ship_stock_move(transfer)
    # Mark the line received and re-home.
    transfer["items"][0]["quantity_received"] = 1
    out = tr._apply_receive_stock_move(transfer)
    # (i) existing behavior: unit re-homed to S-B as AVAILABLE.
    assert out["stock_units_moved_in"] == 1
    rehomed = xfer_env["stock_repo"].find_by_id("X3")
    assert rehomed["status"] == "AVAILABLE"
    assert rehomed["store_id"] == "S-B"
    # (ii) a TRANSFER_RECEIVE row (TRANSFERRED -> AVAILABLE) at the new home.
    rows = _events(xfer_env["db"], "X3", "transfer.receive")
    assert len(rows) == 1
    assert rows[0]["from_state"] == "TRANSFERRED"
    assert rows[0]["to_state"] == "AVAILABLE"
    assert rows[0]["store_id"] == "S-B"


def test_transfer_ledger_failure_does_not_break_ship(xfer_env, monkeypatch):
    from api.services import item_events as ie
    tr = xfer_env["tr"]

    def _boom(*a, **k):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(ie, "record_post_write_event", _boom)
    xfer_env["add_unit"]("X4")
    transfer = _transfer([{"product_id": "P-FRAME", "quantity_requested": 1}])
    # (iii) ship still succeeds + the unit still flips, despite the raising recorder.
    out = tr._apply_ship_stock_move(transfer)
    assert out["stock_shipped"] is True
    assert xfer_env["stock_repo"].find_by_id("X4")["status"] == "TRANSFERRED"


# ===========================================================================
# 3. GRN goods-receipt mint  (vendors.accept_grn)
# ===========================================================================


class _FakeGRNRepo:
    def __init__(self, grn):
        self._grn = grn
        self.updated = None

    def find_by_id(self, grn_id):
        return self._grn if grn_id == self._grn.get("grn_id") else None

    def update(self, grn_id, patch):
        self.updated = patch
        self._grn.update(patch)
        return True

    def find_many(self, *a, **k):
        return [self._grn]

    def find(self, *a, **k):
        return [self._grn]


@pytest.fixture
def grn_env(monkeypatch):
    from api.routers import vendors as vd
    from database.repositories.product_repository import StockRepository

    db = CasDB()
    stock_repo = StockRepository(db.get_collection("stock_units"))
    grn = {
        "grn_id": "GRN-1",
        "grn_number": "GRN-001",
        "store_id": "S-A",
        "po_id": None,
        "status": "PENDING",
        "items": [{"product_id": "P-FRAME", "accepted_qty": 2}],
    }
    grn_repo = _FakeGRNRepo(grn)

    monkeypatch.setattr(vd, "get_grn_repository", lambda: grn_repo)
    monkeypatch.setattr(vd, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(vd, "get_purchase_order_repository", lambda: None)
    monkeypatch.setattr(vd, "_get_db", lambda: db)
    # _cumulative_received_by_product reads the grn repo; keep it simple.
    monkeypatch.setattr(vd, "_cumulative_received_by_product",
                        lambda repo, po_id: {}, raising=False)

    return {"db": db, "stock_repo": stock_repo, "grn_repo": grn_repo, "vd": vd}


def test_grn_accept_mints_and_ledger_row(grn_env):
    vd = grn_env["vd"]
    out = _run(vd.accept_grn("GRN-1", _MANAGER))
    # (i) existing behavior: 2 AVAILABLE units minted, GRN marked ACCEPTED.
    assert out["units_added"] == 2
    assert grn_env["grn_repo"]._grn["status"] == "ACCEPTED"
    minted = grn_env["stock_repo"].find_many({"product_id": "P-FRAME"})
    assert len(minted) == 2 and all(u["status"] == "AVAILABLE" for u in minted)
    # (ii) one MINT ledger row per minted unit (None -> AVAILABLE).
    rows = _events(grn_env["db"], event_type="mint")
    assert len(rows) == 2
    assert all(r["from_state"] is None and r["to_state"] == "AVAILABLE" for r in rows)
    assert all(r["source_type"] == "GRN" and r["source_id"] == "GRN-1" for r in rows)


def test_grn_accept_ledger_failure_does_not_break_mint(grn_env, monkeypatch):
    from api.services import item_events as ie
    vd = grn_env["vd"]

    def _boom(*a, **k):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(ie, "record_post_write_event", _boom)
    # (iii) the GRN still mints + accepts despite the raising recorder.
    out = _run(vd.accept_grn("GRN-1", _MANAGER))
    assert out["units_added"] == 2
    assert grn_env["grn_repo"]._grn["status"] == "ACCEPTED"


# ===========================================================================
# 4. lens reserve / release  (aggregate cell events; lens_line_id + cell_key)
# ===========================================================================


class _HybridLensDB:
    """The lens CAS uses a Mongo $expr predicate the CasCollection can't match,
    so the lens cell + audit live in the lens-test FakeColl (which understands
    $expr), while `counters` (the monotonic event_seq, needs $inc + upsert) and
    `item_events` live in the richer CasCollection. One shim, two backends."""

    is_connected = True

    def __init__(self):
        from tests.test_lens_stock_reserve_commit_release import FakeColl
        from tests.test_e3_item_event import CasCollection

        self._fake = {
            "lens_stock_lines": FakeColl(),
            "lens_stock_audit": FakeColl(),
        }
        self._cas = {
            "counters": CasCollection(),
            "item_events": CasCollection(),
        }

    def get_collection(self, name):
        if name in self._fake:
            return self._fake[name]
        if name not in self._cas:
            from tests.test_e3_item_event import CasCollection
            self._cas[name] = CasCollection()
        return self._cas[name]


@pytest.fixture
def lens_env(monkeypatch):
    from api.routers import lens_stock as ls

    db = _HybridLensDB()
    db.get_collection("lens_stock_lines").insert_one(
        {"line_stock_id": "L1", "lens_line_id": "LL-1", "store_id": "S-A",
         "sph": -1.0, "cyl": 0.0, "add": None, "on_hand": 10, "reserved": 0}
    )
    monkeypatch.setattr(ls, "_get_db", lambda: db)
    monkeypatch.setattr(ls, "_stock_coll",
                        lambda: db.get_collection("lens_stock_lines"))
    monkeypatch.setattr(ls, "_audit_coll",
                        lambda: db.get_collection("lens_stock_audit"))
    monkeypatch.setattr(ls, "validate_store_access", lambda store_id, user: store_id)
    return {"db": db, "ls": ls}


def _lens_payload(ls, qty=2):
    return ls.ReserveCommitReleasePayload(
        store_id="S-A", sph=-1.0, cyl=0.0, add=None, qty=qty, source_type="POS")


def test_lens_reserve_unchanged_and_ledger_row(lens_env):
    ls = lens_env["ls"]
    out = _run(ls.reserve_cell("LL-1", _lens_payload(ls, qty=2), _MANAGER))
    # (i) existing behavior: cell reserved += 2.
    assert out["status"] == "success"
    assert out["cell"]["reserved"] == 2
    # (ii) a RESERVE aggregate-cell ledger row (lens_line_id + cell_key, no stock_id).
    rows = [r for r in lens_env["db"].get_collection("item_events").docs
            if r.get("event_type") == "reserve"]
    assert len(rows) == 1
    assert rows[0]["lens_line_id"] == "LL-1"
    assert rows[0]["stock_id"] is None
    assert rows[0]["cell_key"]  # carries the (sph;cyl;add) cell key


def test_lens_release_unchanged_and_ledger_row(lens_env):
    ls = lens_env["ls"]
    _run(ls.reserve_cell("LL-1", _lens_payload(ls, qty=3), _MANAGER))
    out = _run(ls.release_cell("LL-1", _lens_payload(ls, qty=1), _MANAGER))
    # (i) existing behavior: reserved drops by 1 (3 -> 2).
    assert out["cell"]["reserved"] == 2
    # (ii) a RELEASE ledger row recorded.
    rows = [r for r in lens_env["db"].get_collection("item_events").docs
            if r.get("event_type") == "release"]
    assert len(rows) == 1
    assert rows[0]["lens_line_id"] == "LL-1"


def test_lens_reserve_ledger_failure_does_not_break_path(lens_env, monkeypatch):
    from api.services import item_events as ie
    ls = lens_env["ls"]

    def _boom(*a, **k):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(ie, "record_post_write_event", _boom)
    # (iii) reserve still succeeds despite the raising recorder.
    out = _run(ls.reserve_cell("LL-1", _lens_payload(ls, qty=2), _MANAGER))
    assert out["status"] == "success"
    assert out["cell"]["reserved"] == 2


# ===========================================================================
# Return-serial-mismatch guard (E3 acceptance #8, return half)
# ===========================================================================


_ORDER = {
    "order_id": "O-1",
    "order_number": "ORD-1",
    "status": "DELIVERED",
    "store_id": "S-A",
    # GST-inclusive gross the customer paid (1000 net + 5% GST) so the existing
    # refund-cap guard (amount_paid ceiling) does not reject the full refund.
    "amount_paid": 1050.0,
    "items": [
        {"item_id": "IT-1", "product_id": "P-FRAME", "product_name": "Frame",
         "quantity": 1, "unit_price": 1000.0, "gst_rate": 5.0},
    ],
}


@pytest.fixture
def ret_env(monkeypatch):
    from api.routers import returns as ret
    from api import dependencies as dep
    from database.repositories.product_repository import StockRepository

    db = CasDB()
    stock_repo = StockRepository(db.get_collection("stock_units"))

    monkeypatch.setattr(ret, "_get_db", lambda: db)
    monkeypatch.setattr(ret, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(ret, "_resolve_order", lambda body: copy.deepcopy(_ORDER))
    # Neutralise period-lock + the heavier downstream writes the guard precedes.
    monkeypatch.setattr(dep, "get_audit_repository", lambda: None, raising=False)
    # _orders_coll() PREFERS get_order_repository().collection; in CI (real Mongo)
    # that binds to the empty real orders collection -> the atomic returnable-qty
    # claim finds no O-1 -> 409 (locally there is no Mongo so it fell back to the
    # test db). Pin the repo to None so the claim uses OUR monkeypatched _get_db,
    # and seed O-1 there (deepcopy: the $inc on returned_qty must not mutate the
    # shared _ORDER template across tests).
    monkeypatch.setattr(ret, "get_order_repository", lambda: None, raising=False)
    db.get_collection("orders").insert_one(copy.deepcopy(_ORDER))

    def add_sold_unit(stock_id, serial=None, product_id="P-FRAME", store_id="S-A"):
        db.get_collection("stock_units").insert_one(
            {"stock_id": stock_id, "product_id": product_id, "store_id": store_id,
             "status": "SOLD", "order_id": "O-1", "serial": serial, "quantity": 1}
        )

    return {"db": db, "stock_repo": stock_repo, "ret": ret, "add_sold_unit": add_sold_unit}


def _return_body(ret, serial=None, override_token=None):
    return ret.ReturnCreate(
        order_id="O-1",
        store_id="S-A",
        return_type="RETURN",
        items=[ret.ReturnLine(
            order_item_id="IT-1", product_id="P-FRAME", product_name="Frame",
            return_qty=1, unit_price=1000.0, gst_rate=5.0, serial=serial)],
        serial_mismatch_override_token=override_token,
    )


def test_return_serial_mismatch_hard_blocks_409(ret_env):
    from fastapi import HTTPException
    ret = ret_env["ret"]
    ret_env["add_sold_unit"]("S1", serial="SN-001")
    with pytest.raises(HTTPException) as exc:
        _run(ret.create_return(body=_return_body(ret, serial="SN-999"),
                               current_user=_MANAGER, idempotency_key=None))
    assert exc.value.status_code == 409
    assert exc.value.detail.get("reason") == "SERIAL_MISMATCH"


def test_return_serial_match_proceeds(ret_env):
    ret = ret_env["ret"]
    ret_env["add_sold_unit"]("S2", serial="SN-001")
    out = _run(ret.create_return(body=_return_body(ret, serial="SN-001"),
                                 current_user=_MANAGER, idempotency_key=None))
    # Matching serial -> the return completes normally.
    assert out["return_id"]
    assert out.get("_idempotent_replay") is not True


def test_return_no_scanned_serial_is_permissive(ret_env):
    ret = ret_env["ret"]
    ret_env["add_sold_unit"]("S3", serial="SN-001")
    # No till serial scanned -> the check is SKIPPED (legitimate return).
    out = _run(ret.create_return(body=_return_body(ret, serial=None),
                                 current_user=_MANAGER, idempotency_key=None))
    assert out["return_id"]


def test_return_no_recorded_serial_is_permissive(ret_env):
    ret = ret_env["ret"]
    ret_env["add_sold_unit"]("S4", serial=None)  # dirty/legacy unit, no serial
    # The matched unit has no recorded serial -> SKIPPED (never block on dirty data).
    out = _run(ret.create_return(body=_return_body(ret, serial="SN-XYZ"),
                                 current_user=_MANAGER, idempotency_key=None))
    assert out["return_id"]


def test_return_serial_mismatch_override_proceeds(ret_env, monkeypatch):
    ret = ret_env["ret"]
    ret_env["add_sold_unit"]("S5", serial="SN-001")

    # Stub the E4 consume to succeed for the override token (the engine integration
    # is exercised separately; here we prove the override hook lets it through).
    monkeypatch.setattr(ret, "_consume_serial_override",
                        lambda body, user: True)
    out = _run(ret.create_return(
        body=_return_body(ret, serial="SN-999", override_token="TOK-OK"),
        current_user=_MANAGER, idempotency_key=None))
    assert out["return_id"]
    # The override actor is stamped on the persisted return doc.
    persisted = ret_env["db"].get_collection("returns").find_one({"return_id": out["return_id"]})
    assert persisted is not None
    assert persisted.get("serial_override_by") == _MANAGER["user_id"]


def test_return_serial_override_denied_still_blocks(ret_env, monkeypatch):
    from fastapi import HTTPException
    ret = ret_env["ret"]
    ret_env["add_sold_unit"]("S6", serial="SN-001")
    # Override consume FAILS (bad/expired token) -> safe default is still 409.
    monkeypatch.setattr(ret, "_consume_serial_override",
                        lambda body, user: False)
    with pytest.raises(HTTPException) as exc:
        _run(ret.create_return(
            body=_return_body(ret, serial="SN-999", override_token="TOK-BAD"),
            current_user=_MANAGER, idempotency_key=None))
    assert exc.value.status_code == 409
    assert exc.value.detail.get("reason") == "SERIAL_MISMATCH"


def test_return_serial_override_action_type_is_registered(ret_env):
    """P1 regression (adversarial): RETURN_SERIAL_OVERRIDE must be a REGISTERED E4
    action type. If it is not, ApprovalEngine.request() rejects it as
    unknown_action_type -> no token can ever be minted -> the manager override is
    DEAD and every serial mismatch is an un-overridable 409. Prove request() mints
    it (does NOT reject the action type), so the escape valve is operable."""
    from api.services.approvals import ACTION_TYPES, ApprovalEngine

    assert "RETURN_SERIAL_OVERRIDE" in ACTION_TYPES
    engine = ApprovalEngine(db=ret_env["db"])
    res = engine.request(
        action_type="RETURN_SERIAL_OVERRIDE",
        requested_by="cashier1",
        store_id="BV-1",
        reason="serial mismatch on a high-value frame return",
    )
    # The action type is accepted (NOT unknown_action_type) -> a token is mintable.
    assert res.get("error") != "unknown_action_type"
    assert res.get("ok") is True
    assert res.get("required_tier") in ("auto", "admin", "super")
