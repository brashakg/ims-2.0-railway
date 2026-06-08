"""E3 -- Item-event ledger (intent-level acceptance).

These tests assert the BUSINESS INTENT of the E3 packet, not the plumbing:

  * the ledger is append-only and each material stock/serial event records ONE
    row with a strictly-increasing `event_seq` (the monotonic counter), plus one
    AuditRepository.create row for the material event (CORRECTIONS P0-2: NO
    unit-level hash-chain on the event head -- a monotonic seq + an audit row);
  * an illegal transition (quarantine a SOLD unit) is refused with no ledger row
    written and no status change;
  * two racing SELL events resolve to exactly one winner via the single-document
    CAS (no double-sell);
  * quarantining a unit drops it out of the on-hand / sellable rollup
    immediately, and the round-trip (release/RESTOCK) restores it while
    release/DAMAGE | RTV does not;
  * serial-bind reconciles with the EXISTING `serial_numbers` collection (sets
    stock_units.serial AND writes/links a serial_numbers row) -- it does NOT fork
    a parallel store;
  * Base-Bank replenishment math (required = base_bank - in_hand) + the E2
    STORE > GLOBAL scope hierarchy;
  * legacy colour-flag / lowercase status canonicalises onto the StockState enum.

They drive the real route-handler coroutines + the pure service against a
CAS-capable in-memory FakeDB (the same monkeypatch pattern as
test_quarantine_f21), so NO live Mongo is required. A hollow shell FAILS these.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from tests.test_walkouts import FakeCollection, _doc_matches  # noqa: E402


# ---------------------------------------------------------------------------
# CAS-capable FakeDB (the walkout FakeCollection has no find_one_and_update)
# ---------------------------------------------------------------------------


class CasCollection(FakeCollection):
    """FakeCollection + find_one_and_update + $nin/$or matching so the E3 CAS
    (record_event) and the replenishment scan run against the fake."""

    def find_one_and_update(self, filter, update, return_document=None,
                            upsert=False, **kw):
        for d in self.docs:
            if _cas_matches(d, filter):
                set_block = (update or {}).get("$set", {}) or {}
                inc_block = (update or {}).get("$inc", {}) or {}
                d.update(set_block)
                for k, v in inc_block.items():
                    d[k] = (d.get(k) or 0) + v
                return dict(d)
        if upsert:
            # Seed a new doc from the filter equality terms + apply $set/$inc
            # (this is exactly how barcode.allocate_sequence claims seq #1).
            doc = {k: v for k, v in (filter or {}).items() if not isinstance(v, dict)}
            for k, v in ((update or {}).get("$set", {}) or {}).items():
                doc[k] = v
            for k, v in ((update or {}).get("$inc", {}) or {}).items():
                doc[k] = v
            self.docs.append(doc)
            return dict(doc)
        return None

    def find(self, filter=None, projection=None):
        return _CasCursor(d for d in self.docs if _cas_matches(d, filter))

    def find_one(self, filter=None, projection=None):
        if not filter:
            return self.docs[0] if self.docs else None
        for d in self.docs:
            if _cas_matches(d, filter):
                return d
        return None

    def count_documents(self, filter=None):
        return sum(1 for d in self.docs if _cas_matches(d, filter))


class _CasCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *a, **k):
        key = a[0] if a else None
        if isinstance(key, str):
            field, direction = key, (a[1] if len(a) > 1 else 1)
            self._docs.sort(key=lambda d: (d.get(field) is None, d.get(field)),
                            reverse=(direction == -1))
        return self

    def limit(self, n):
        if n:
            self._docs = self._docs[: int(n)]
        return self

    def skip(self, n):
        self._docs = self._docs[int(n or 0):]
        return self

    def __iter__(self):
        return iter(self._docs)


def _cas_matches(doc, filter):
    """_doc_matches + $in / $nin / $or / $nor support for the E3 fakes."""
    if not filter:
        return True
    for k, expected in filter.items():
        if k == "$or":
            if not any(_cas_matches(doc, sub) for sub in expected):
                return False
            continue
        if k == "$nor":
            if any(_cas_matches(doc, sub) for sub in expected):
                return False
            continue
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$in" and actual not in op_val:
                    return False
                if op == "$nin" and actual in op_val:
                    return False
                if op == "$gte" and not (actual is not None and actual >= op_val):
                    return False
                if op == "$lte" and not (actual is not None and actual <= op_val):
                    return False
                if op == "$ne" and actual == op_val:
                    return False
                if op == "$exists":
                    present = k in doc
                    if present != op_val:
                        return False
        else:
            if actual != expected:
                return False
    return True


class CasDB:
    is_connected = True

    def __init__(self):
        self._collections = {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = CasCollection()
        return self._collections[name]

    def __getattr__(self, name):
        return self.get_collection(name)


_MANAGER = {
    "user_id": "mgr-1",
    "name": "Store Manager",
    "roles": ["STORE_MANAGER"],
    "store_ids": ["S-A"],
    "active_store_id": "S-A",
}
_ADMIN = {
    "user_id": "adm-1",
    "name": "Admin",
    "roles": ["ADMIN"],
    "store_ids": [],
    "active_store_id": None,
}


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def env(monkeypatch):
    from api.routers import item_events as router_mod
    from api import dependencies as dep
    from database.repositories.product_repository import StockRepository
    from database.repositories.audit_repository import AuditRepository

    db = CasDB()
    stock_repo = StockRepository(db.get_collection("stock_units"))
    audit_repo = AuditRepository(db.get_collection("audit_logs"))

    monkeypatch.setattr(router_mod, "_get_db", lambda: db)
    monkeypatch.setattr(router_mod, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(dep, "get_audit_repository", lambda: audit_repo)

    def add_unit(stock_id, product_id="P-FRAME", status="AVAILABLE", store_id="S-A", **extra):
        db.get_collection("stock_units").insert_one(
            {"stock_id": stock_id, "product_id": product_id, "store_id": store_id,
             "status": status, "barcode": f"BC-{stock_id}", "quantity": 1, **extra}
        )

    return {"db": db, "stock_repo": stock_repo, "audit_repo": audit_repo,
            "add_unit": add_unit}


def _events(env, stock_id=None):
    rows = env["db"].get_collection("item_events").docs
    if stock_id is None:
        return rows
    return [r for r in rows if r.get("stock_id") == stock_id]


def _audit(env, action_prefix="ITEM_EVENT_"):
    return [r for r in env["db"].get_collection("audit_logs").docs
            if str(r.get("action", "")).startswith(action_prefix)]


# ===========================================================================
# 1. Append-only ledger with a monotonic event_seq + one audit row per event
# ===========================================================================


def test_pure_state_machine_legal_and_illegal_edges():
    from api.services import item_events as ie

    assert ie.is_legal_transition(ie.StockState.AVAILABLE, ie.StockState.QUARANTINED)
    assert ie.is_legal_transition(ie.StockState.QUARANTINED, ie.StockState.AVAILABLE)
    assert ie.is_legal_transition(ie.StockState.AVAILABLE, ie.StockState.SOLD)
    # Terminal: a SOLD unit has no outgoing edge.
    assert not ie.is_legal_transition(ie.StockState.SOLD, ie.StockState.QUARANTINED)
    assert not ie.is_legal_transition(ie.StockState.SOLD, ie.StockState.AVAILABLE)
    # Garbage source -> fail-closed.
    assert not ie.is_legal_transition("NONSENSE", ie.StockState.AVAILABLE)


def test_ledger_records_monotonic_seq_and_one_audit_row(env):
    from api.routers.item_events import (
        quarantine_in, quarantine_release,
        QuarantineInRequest, QuarantineReleaseRequest,
    )

    env["add_unit"]("U1")
    # Two material events on one unit: quarantine.in then release/RESTOCK.
    _run(quarantine_in("U1", QuarantineInRequest(reason="DEFECTIVE"), _MANAGER))
    _run(quarantine_release("U1", QuarantineReleaseRequest(disposition="RESTOCK"), _MANAGER))

    rows = _events(env, "U1")
    assert len(rows) == 2
    seqs = [r["event_seq"] for r in rows]
    # Strictly increasing, no gaps within the unit's history.
    assert seqs == sorted(seqs)
    assert seqs[1] == seqs[0] + 1
    assert rows[0]["event_type"] == "quarantine.in"
    assert rows[1]["event_type"] == "quarantine.out"
    # Exactly one audit row per material event (P0-2: seq + audit row, NO chain).
    audit = _audit(env)
    assert len(audit) == 2
    assert {a["action"] for a in audit} == {"ITEM_EVENT_QUARANTINE_IN",
                                            "ITEM_EVENT_QUARANTINE_OUT"}
    # No hash-chain field is asserted/required on the item_events head.
    assert "prev_hash" not in rows[0] and "entry_hash" not in rows[0]


def test_ledger_read_returns_events_in_seq_order(env):
    from api.routers.item_events import (
        quarantine_in, quarantine_release, get_unit_events,
        QuarantineInRequest, QuarantineReleaseRequest,
    )

    env["add_unit"]("U2")
    _run(quarantine_in("U2", QuarantineInRequest(reason="DEFECTIVE"), _MANAGER))
    _run(quarantine_release("U2", QuarantineReleaseRequest(disposition="RESTOCK"), _MANAGER))
    out = _run(get_unit_events("U2", _MANAGER))
    assert out["total"] == 2
    assert [e["event_type"] for e in out["items"]] == ["quarantine.in", "quarantine.out"]
    assert out["items"][0]["event_seq"] < out["items"][1]["event_seq"]


def test_ledger_router_exposes_no_mutation_route():
    """Append-only by policy: the router defines NO PUT/DELETE on a ledger row."""
    from api.routers.item_events import router
    methods = set()
    for r in router.routes:
        methods |= set(getattr(r, "methods", set()) or set())
    assert "DELETE" not in methods
    assert "PUT" not in methods


# ===========================================================================
# 2. Illegal transition refused (no row, no status change)
# ===========================================================================


def test_quarantine_sold_unit_refused_409_no_row(env):
    from fastapi import HTTPException
    from api.routers.item_events import quarantine_in, QuarantineInRequest

    env["add_unit"]("U3", status="SOLD")
    with pytest.raises(HTTPException) as exc:
        _run(quarantine_in("U3", QuarantineInRequest(reason="DEFECTIVE"), _MANAGER))
    assert exc.value.status_code == 409
    # No ledger row, status unchanged.
    assert _events(env, "U3") == []
    assert env["stock_repo"].find_by_id("U3")["status"] == "SOLD"


def test_release_unquarantined_unit_refused_409(env):
    from fastapi import HTTPException
    from api.routers.item_events import quarantine_release, QuarantineReleaseRequest

    env["add_unit"]("U4")  # AVAILABLE, never quarantined
    with pytest.raises(HTTPException) as exc:
        _run(quarantine_release("U4", QuarantineReleaseRequest(disposition="RESTOCK"), _MANAGER))
    assert exc.value.status_code == 409
    assert _events(env, "U4") == []


# ===========================================================================
# 3. Concurrency-safe sell -- exactly one of two racing writers wins the CAS
# ===========================================================================


def test_concurrent_sell_single_winner(env):
    from api.services import item_events as ie

    env["add_unit"]("U5")
    db = env["db"]
    r1 = ie.record_event_atomic(
        db, event_type=ie.ItemEventType.SELL, actor_id="x", stock_id="U5",
        from_state=ie.StockState.AVAILABLE, to_state=ie.StockState.SOLD, store_id="S-A")
    r2 = ie.record_event_atomic(
        db, event_type=ie.ItemEventType.SELL, actor_id="y", stock_id="U5",
        from_state=ie.StockState.AVAILABLE, to_state=ie.StockState.SOLD, store_id="S-A")
    oks = [r1[0], r2[0]]
    assert oks.count(True) == 1 and oks.count(False) == 1
    # Exactly one SELL row; unit is SOLD; sellable on-hand is 0.
    sells = [r for r in _events(env, "U5") if r["event_type"] == "sell"]
    assert len(sells) == 1
    assert env["stock_repo"].find_by_id("U5")["status"] == "SOLD"
    assert env["stock_repo"].find_available("P-FRAME", "S-A") == 0


# ===========================================================================
# 4. Quarantine drops on-hand immediately
# ===========================================================================


def test_quarantine_drops_on_hand_immediately(env):
    from api.routers.item_events import quarantine_in, QuarantineInRequest

    env["add_unit"]("U6a")
    env["add_unit"]("U6b")
    assert env["stock_repo"].find_available("P-FRAME", "S-A") == 2
    _run(quarantine_in("U6a", QuarantineInRequest(reason="DEFECTIVE"), _MANAGER))
    # On-hand drops to 1, the quarantined unit is excluded from find_available.
    assert env["stock_repo"].find_available("P-FRAME", "S-A") == 1
    assert env["stock_repo"].find_by_id("U6a")["status"] == "QUARANTINED"


# ===========================================================================
# 5. Quarantine round-trip: RESTOCK restores, DAMAGE / RTV do not
# ===========================================================================


@pytest.mark.parametrize("disposition,final_status,restored", [
    ("RESTOCK", "AVAILABLE", True),
    ("DAMAGE", "DAMAGED", False),
    ("RTV", "RTV", False),
])
def test_quarantine_release_dispositions(env, disposition, final_status, restored):
    from api.routers.item_events import (
        quarantine_in, quarantine_release,
        QuarantineInRequest, QuarantineReleaseRequest,
    )

    sid = f"U7-{disposition}"
    env["add_unit"](sid)
    _run(quarantine_in(sid, QuarantineInRequest(reason="DEFECTIVE"), _MANAGER))
    assert env["stock_repo"].find_available("P-FRAME", "S-A") == 0
    _run(quarantine_release(sid, QuarantineReleaseRequest(disposition=disposition), _MANAGER))
    assert env["stock_repo"].find_by_id(sid)["status"] == final_status
    expected = 1 if restored else 0
    assert env["stock_repo"].find_available("P-FRAME", "S-A") == expected
    # Exactly two ledger rows: quarantine.in + quarantine.out.
    rows = _events(env, sid)
    assert len(rows) == 2
    assert {r["event_type"] for r in rows} == {"quarantine.in", "quarantine.out"}


# ===========================================================================
# 6. Serial-bind reconciles with the EXISTING serial_numbers collection
# ===========================================================================


def test_serial_bind_reconciles_with_serial_numbers(env):
    from api.routers.item_events import serial_bind, SerialBindRequest

    env["add_unit"]("U8")
    out = _run(serial_bind("U8", SerialBindRequest(serial="SN-001"), _MANAGER))
    assert out["serial"] == "SN-001"
    # 1) projected onto stock_units.serial (not a parallel store)
    assert env["stock_repo"].find_by_id("U8")["serial"] == "SN-001"
    # 2) bridged into the EXISTING serial_numbers collection
    sn = env["db"].get_collection("serial_numbers").find_one({"serial_number": "SN-001"})
    assert sn is not None
    assert sn["stock_id"] == "U8"
    assert sn["status"] == "IN_STOCK"
    # 3) a serial.bind ledger event was recorded
    binds = [r for r in _events(env, "U8") if r["event_type"] == "serial.bind"]
    assert len(binds) == 1
    assert binds[0]["serial"] == "SN-001"


def test_serial_bind_duplicate_in_store_409(env):
    from fastapi import HTTPException
    from api.routers.item_events import serial_bind, SerialBindRequest

    env["add_unit"]("U9a")
    env["add_unit"]("U9b")
    _run(serial_bind("U9a", SerialBindRequest(serial="SN-DUP"), _MANAGER))
    with pytest.raises(HTTPException) as exc:
        _run(serial_bind("U9b", SerialBindRequest(serial="SN-DUP"), _MANAGER))
    assert exc.value.status_code == 409


# ===========================================================================
# 7. Base-Bank replenishment math + E2 STORE > GLOBAL hierarchy
# ===========================================================================


def test_replenishment_math_and_power_snap(env):
    from api.routers.item_events import upsert_base_bank, replenishment, BaseBankUpsert

    # base_bank 10 at READERS cell "+2.00" for S-A.
    _run(upsert_base_bank(
        BaseBankUpsert(store_id="S-A", grid="READERS", cell_key="2", base_bank=10), _MANAGER))
    # Stock 6 sellable readers at +2.00 (power snaps via format_power).
    for i in range(6):
        env["add_unit"](f"R{i}", product_id="P-READER", power=2.0)
    # A quarantined reader at the same cell must NOT count.
    env["add_unit"]("Rq", product_id="P-READER", power=2.0, status="QUARANTINED")

    out = _run(replenishment(store_id="S-A", grid="READERS", current_user=_MANAGER))
    cells = {c["cell_key"]: c for c in out["items"]}
    assert "+2.00" in cells  # snapped to the normalised form
    c = cells["+2.00"]
    assert c["base_bank"] == 10
    assert c["in_hand"] == 6
    assert c["required"] == 4


def test_base_bank_pure_helper_signed_required():
    from api.services.item_events import build_replenishment, required_qty

    assert required_qty(10, 6) == 4
    assert required_qty(5, 8) == -3  # overstock (signed)
    rows = build_replenishment([{"cell_key": "+1.00", "base_bank": 4, "in_hand": 4}])
    assert rows[0]["required"] == 0


def test_e2_scope_hierarchy_store_beats_global(env):
    """STORE override beats GLOBAL; a store with no STORE target falls back to
    GLOBAL (E2 STORE > ENTITY > GLOBAL)."""
    db = env["db"]
    coll = db.get_collection("base_bank_targets")
    # GLOBAL target for "+3.00" = 8; STORE-S2 override for the same cell = 5.
    coll.insert_one({"target_id": "g1", "scope": "GLOBAL", "grid": "READERS",
                     "cell_key": "+3.00", "base_bank": 8})
    coll.insert_one({"target_id": "s2", "scope": "STORE", "store_id": "S2",
                     "grid": "READERS", "cell_key": "+3.00", "base_bank": 5})

    from api.routers.item_events import replenishment
    _ADMIN_S2 = {**_ADMIN, "active_store_id": "S2"}
    out2 = _run(replenishment(store_id="S2", grid="READERS", current_user=_ADMIN_S2))
    c2 = {c["cell_key"]: c for c in out2["items"]}["+3.00"]
    assert c2["base_bank"] == 5  # STORE wins

    out3 = _run(replenishment(store_id="S3", grid="READERS", current_user=_ADMIN))
    c3 = {c["cell_key"]: c for c in out3["items"]}["+3.00"]
    assert c3["base_bank"] == 8  # GLOBAL fallback


# ===========================================================================
# 8. Legacy colour-flag / lowercase status canonicalises onto the enum
# ===========================================================================


def test_lowercase_legacy_status_canonicalises_and_quarantines(env):
    from api.routers.item_events import quarantine_in, QuarantineInRequest
    from api.services import item_events as ie

    assert ie.canonical_state("available") == ie.StockState.AVAILABLE
    assert ie.canonical_state("in_stock") == ie.StockState.AVAILABLE
    assert ie.canonical_state("QUARANTINED") == ie.StockState.QUARANTINED

    # A unit minted with the legacy lowercase "available" still quarantines: the
    # CAS accepts the lowercase variant, and -- the intent -- NO item_events row
    # carries a raw colour/lowercase string. The router canonicalises the source
    # state before recording, so both from_state and to_state are canonical enum
    # values (never "available").
    env["add_unit"]("U10", status="available")
    _run(quarantine_in("U10", QuarantineInRequest(reason="DEFECTIVE"), _MANAGER))
    assert env["stock_repo"].find_by_id("U10")["status"] == "QUARANTINED"
    row = _events(env, "U10")[0]
    assert row["to_state"] == "QUARANTINED"
    assert row["from_state"] == "AVAILABLE"  # canonicalised; no raw lowercase in the ledger
    assert row["from_state"] not in ("available", "in_stock", "IN_STOCK")


# ===========================================================================
# 9. On-hand rollup excludes every non-sellable status
# ===========================================================================


def test_on_hand_rollup_excludes_all_non_sellable(env):
    from api.routers.inventory import _on_hand_by_product

    for i, st in enumerate(["QUARANTINED", "UNDER_AUDIT", "BLIND_COUNT",
                            "TRANSFERRED", "SOLD", "VOID", "DAMAGED", "RTV"]):
        env["add_unit"](f"X{i}", product_id="P-EXC", status=st)
    rollup = _on_hand_by_product(env["db"], ["P-EXC"], store_id="S-A")
    assert rollup.get("P-EXC", 0) == 0
    # find_available also returns 0 for the product.
    assert env["stock_repo"].find_available("P-EXC", "S-A") == 0
