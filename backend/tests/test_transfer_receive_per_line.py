"""
IMS 2.0 - Transfer RECEIVE: per-line completeness + identity-based re-homing
===========================================================================
Third sibling of the "compared totals where it should have compared sets"
family (PR #1018). Two independent defects on the receive path, both of which
let a receive close over stock that never actually landed:

  * A - COMPLETENESS DECIDED FROM CROSS-LINE TOTALS.
    receive_transfer summed quantity_shipped / quantity_received across every
    line and compared the two totals. Worse, the BUG-011 over-receive cap and
    that sum disagreed about what "shipped" means: the cap falls back to
    quantity_requested when quantity_shipped is absent, the sum used
    `item.get("quantity_shipped", 0)` with NO fallback. So a legacy line that
    never stamped quantity_shipped contributed 0 to expected and its FULL
    receipt to received -- masking a second line's shortfall, marking the
    transfer RECEIVED, and reporting a 2-unit SHORTAGE as a 2-unit SURPLUS
    (which also downgrades the follow-up task from P2 to P3).
    Requirement: completeness is decided PER LINE (every line received ==
    shipped), short/surplus are summed PER LINE, and the cap and the totals
    read the SAME definition of shipped quantity.

  * B - RE-HOMING PICKED FRAMES BY POSITION, NOT BY WHICH ARE OUTSTANDING.
    _apply_receive_stock_move sliced the shipped pool by a COUNT:
    `movable = pool[already : already + want]`, where `already` is
    received_qty_committed. That assumes the first `already` ids in the pool
    are exactly the ones already re-homed. If an earlier pass's _rehome failed
    on an EARLY unit and succeeded on a later one, the counter advances past
    the failed unit and the slice never returns to it -- one frame stranded at
    the source store forever, while an already-re-homed unit is moved twice.
    Requirement: the outstanding pool is the SET difference against the ids the
    line already recorded (received_stock_ids + damaged_stock_ids), so identity
    -- not position -- decides what still has to move.

No Mongo needed: the endpoint's own no-DB fallback (_get_db() -> None puts the
transfer doc in the in-memory STOCK_TRANSFERS map) plus a small in-memory stock
repository. Every assertion that matters compares the SET of stock unit ids.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import transfers  # noqa: E402


_ADMIN = {
    "user_id": "u-admin",
    "username": "admin",
    "roles": ["SUPERADMIN"],
    "store_ids": ["A", "B"],
    "active_store_id": "A",
}


class FakeStockRepo:
    """In-memory stand-in for StockRepository, faithful on the four methods the
    transfer receive path touches. `fail_updates_for` names unit ids whose
    update must report failure -- the transient write flake that strands a unit
    in defect B."""

    def __init__(self):
        self.units = {}
        self.fail_updates_for = set()

    def create(self, doc):
        sid = doc.get("stock_id") or f"SU-{len(self.units) + 1:03d}"
        doc["stock_id"] = sid
        self.units[sid] = doc
        return doc

    def find_by_id(self, sid):
        return self.units.get(str(sid))

    def find_many(self, filter=None, sort=None, skip=0, limit=100):
        rows = [
            u
            for u in self.units.values()
            if all(u.get(k) == v for k, v in (filter or {}).items())
        ]
        return rows[: limit or None]

    def update(self, sid, patch):
        if str(sid) in self.fail_updates_for:
            return False
        unit = self.units.get(str(sid))
        if unit is None:
            return False
        unit.update(patch)
        return True

    def claim_for_transfer(self, sid, transfer_id, to_store_id):
        unit = self.units.get(str(sid))
        if unit is None or unit.get("status") != transfers.STOCK_STATUS_AVAILABLE:
            return False
        unit.update(
            {
                "status": transfers.STOCK_STATUS_TRANSFERRED,
                "transfer_id": transfer_id,
                "transfer_to_store_id": to_store_id,
            }
        )
        return True


@pytest.fixture
def repo(monkeypatch):
    """No DB -> the endpoint's in-memory transfer map + a fake stock repo."""
    fake = FakeStockRepo()
    monkeypatch.setattr(transfers, "_get_db", lambda: None)
    monkeypatch.setattr(transfers, "get_stock_repository", lambda: fake)
    transfers.STOCK_TRANSFERS.clear()
    try:
        yield fake
    finally:
        transfers.STOCK_TRANSFERS.clear()


def _seed(repo, product_id, store_id, n, prefix):
    ids = []
    for i in range(n):
        unit = repo.create(
            {
                "stock_id": f"{prefix}-{i}",
                "product_id": product_id,
                "store_id": store_id,
                "barcode": f"BC-{prefix}-{i}",
                "status": transfers.STOCK_STATUS_AVAILABLE,
            }
        )
        ids.append(unit["stock_id"])
    return ids


def _units_at(repo, store_id, status=None):
    """SET of unit ids sitting at a store (optionally in one status)."""
    return {
        sid
        for sid, u in repo.units.items()
        if u.get("store_id") == store_id and (status is None or u.get("status") == status)
    }


def _receive(tid, lines):
    return asyncio.run(
        transfers.receive_transfer(
            tid,
            items_received=[
                transfers.TransferItemReceive(
                    transfer_item_id=lid,
                    quantity_received=got,
                    quantity_damaged=dmg,
                )
                for lid, got, dmg in lines
            ],
            current_user=_ADMIN,
        )
    )


# ===========================================================================
# A - completeness and short/surplus must be decided PER LINE
# ===========================================================================


def _two_line_transfer(repo, legacy_line_shipped_absent: bool):
    """An IN_TRANSIT two-line transfer. Line L1 is a legacy/back-filled doc that
    never stamped quantity_shipped (the exact shape the BUG-011 cap's
    quantity_requested fallback and INV-11's _resolve_item exist for); L2 is a
    normal line that ship stamped."""
    l1_ids = _seed(repo, "PRD-L1", "A", 4, "L1")
    l2_ids = _seed(repo, "PRD-L2", "A", 4, "L2")
    for sid in l1_ids + l2_ids:
        repo.units[sid]["status"] = transfers.STOCK_STATUS_TRANSFERRED

    l1 = {
        "id": "LINE-1",
        "product_id": "PRD-L1",
        "quantity_requested": 4,
        "shipped_stock_ids": l1_ids,
    }
    if not legacy_line_shipped_absent:
        l1["quantity_shipped"] = 4
    transfer = {
        "id": "TRF-PERLINE-1",
        "transfer_number": "TRF-202608-1001",
        "status": transfers.TransferStatus.IN_TRANSIT,
        "from_location_id": "A",
        "from_location_name": "Store A",
        "to_location_id": "B",
        "to_location_name": "Store B",
        "stock_shipped": True,
        "items": [
            l1,
            {
                "id": "LINE-2",
                "product_id": "PRD-L2",
                "quantity_requested": 4,
                "quantity_shipped": 4,
                "shipped_stock_ids": l2_ids,
            },
        ],
    }
    transfers._save_transfer(transfer)
    return transfer


def test_short_line_is_not_masked_by_a_line_missing_quantity_shipped(repo):
    """L1 (legacy, no quantity_shipped) receives its full 4; L2 is 2 SHORT of 4.

    Cross-line totals scored this 6 received vs 4 expected -> RECEIVED, and
    reported the 2-unit shortage as a 2-unit SURPLUS. Per-line, L2's shortfall
    must survive: PARTIALLY_RECEIVED, short 2, surplus 0."""
    _two_line_transfer(repo, legacy_line_shipped_absent=True)

    res = _receive("TRF-PERLINE-1", [("LINE-1", 4, 0), ("LINE-2", 2, 0)])

    assert res["transfer"]["status"] == transfers.TransferStatus.PARTIALLY_RECEIVED
    assert res["summary"]["short"] == 2
    assert res["summary"]["surplus"] == 0
    # The cap and the totals must agree on what L1 was shipped: 4, not 0.
    assert res["summary"]["expected"] == 8
    assert res["summary"]["received"] == 6
    # Only what physically arrived is sellable at B.
    assert _units_at(repo, "B", transfers.STOCK_STATUS_AVAILABLE) == set(
        repo.units[s]["stock_id"] for s in ("L1-0", "L1-1", "L1-2", "L1-3", "L2-0", "L2-1")
    )


def test_every_line_complete_is_the_only_route_to_received(repo):
    """Control: both lines land in full -> RECEIVED, no discrepancy at all."""
    _two_line_transfer(repo, legacy_line_shipped_absent=False)

    res = _receive("TRF-PERLINE-1", [("LINE-1", 4, 0), ("LINE-2", 4, 0)])

    assert res["transfer"]["status"] == transfers.TransferStatus.RECEIVED
    assert res["summary"]["short"] == 0
    assert res["summary"]["surplus"] == 0


def test_line_shipped_zero_is_authoritative_not_a_missing_value(repo):
    """quantity_shipped == 0 means the source held nothing to send, so the line
    expects 0 -- it must NOT fall back to quantity_requested the way an ABSENT
    quantity_shipped does. Receiving 0 against it is a complete line."""
    _seed(repo, "PRD-Z", "A", 0, "Z")
    transfers._save_transfer(
        {
            "id": "TRF-ZERO",
            "status": transfers.TransferStatus.IN_TRANSIT,
            "from_location_id": "A",
            "to_location_id": "B",
            "stock_shipped": True,
            "items": [
                {
                    "id": "LINE-Z",
                    "product_id": "PRD-Z",
                    "quantity_requested": 5,
                    "quantity_shipped": 0,
                    "shipped_stock_ids": [],
                }
            ],
        }
    )

    res = _receive("TRF-ZERO", [("LINE-Z", 0, 0)])

    assert res["summary"]["expected"] == 0
    assert res["summary"]["short"] == 0
    assert res["transfer"]["status"] == transfers.TransferStatus.RECEIVED


# ===========================================================================
# B - the outstanding pool is a SET difference, not a positional slice
# ===========================================================================


def _rehome_transfer(repo, n=3):
    """A shipped, in-transit transfer with one n-unit line, ready for
    _apply_receive_stock_move -- the helper that owns the pool selection."""
    ids = _seed(repo, "PRD-REHOME", "A", n, "R")
    for sid in ids:
        repo.units[sid]["status"] = transfers.STOCK_STATUS_TRANSFERRED
    transfer = {
        "id": "TRF-REHOME",
        "transfer_number": "TRF-202608-2001",
        "status": transfers.TransferStatus.IN_TRANSIT,
        "from_location_id": "A",
        "to_location_id": "B",
        "stock_shipped": True,
        "items": [
            {
                "id": "LINE-R",
                "product_id": "PRD-REHOME",
                "quantity_requested": n,
                "quantity_shipped": n,
                "shipped_stock_ids": list(ids),
            }
        ],
    }
    return transfer, ids


def _pass(transfer, received, damaged=0):
    """One receive pass over the line, as receive_transfer drives it: stamp the
    claimed quantities, then re-home the delta."""
    transfer["items"][0]["quantity_received"] = received
    transfer["items"][0]["quantity_damaged"] = damaged
    return transfers._apply_receive_stock_move(transfer)


def test_a_unit_whose_first_rehome_failed_is_retried_not_skipped(repo):
    """Pass 1: re-homing the FIRST shipped unit flakes, the other two land, so
    received_qty_committed advances to 2. Pass 2 must move the unit that is
    still OUTSTANDING (R-0) -- not pool[2:3], which is R-2, already re-homed.

    Positionally, R-0 is stranded at the source forever and R-2 moves twice."""
    transfer, ids = _rehome_transfer(repo, n=3)
    shipped = set(ids)

    repo.fail_updates_for = {"R-0"}
    _pass(transfer, 3)
    assert _units_at(repo, "B") == {"R-1", "R-2"}

    repo.fail_updates_for = set()  # the flake clears
    transfer = _pass(transfer, 3)

    line = transfer["items"][0]
    # Every shipped unit is accounted for exactly once, by IDENTITY.
    assert set(line["received_stock_ids"]) == shipped
    assert len(line["received_stock_ids"]) == len(shipped)
    # And every unit physically sits at the destination -- none left behind.
    assert _units_at(repo, "B", transfers.STOCK_STATUS_AVAILABLE) == shipped
    assert _units_at(repo, "A") == set()


def test_damaged_and_good_units_stay_disjoint_across_a_retried_pass(repo):
    """Same flake, with the tail unit arriving damaged. The good and quarantined
    id sets must stay disjoint and together cover the whole shipment -- a
    positional slice re-moves an already-good unit into quarantine."""
    transfer, ids = _rehome_transfer(repo, n=3)
    shipped = set(ids)

    repo.fail_updates_for = {"R-0"}
    _pass(transfer, 3, damaged=1)

    repo.fail_updates_for = set()
    transfer = _pass(transfer, 3, damaged=1)

    line = transfer["items"][0]
    good = set(line.get("received_stock_ids", []))
    damaged = set(line.get("damaged_stock_ids", []))
    assert good & damaged == set(), f"unit in both sets: {good & damaged}"
    assert good | damaged == shipped
    assert len(damaged) == 1
    assert _units_at(repo, "A") == set()


def test_declared_damage_is_quarantined_when_the_pool_comes_from_the_db_fallback(repo):
    """_transferred_pool falls back to querying units still marked TRANSFERRED
    when a legacy line never recorded shipped_stock_ids -- and that pool SHRINKS
    every pass, because re-homing clears a unit's status and transfer_id.

    So the pool is NOT the full shipment on a repeat pass, and anything that
    indexes into it by a position measured over the full shipment reads low.
    What must hold regardless: exactly `quantity_damaged` units end up
    QUARANTINED. Under-quarantining puts damaged frames on the sellable floor."""
    ids = _seed(repo, "PRD-LEGACY", "A", 4, "U")
    for sid in ids:
        repo.units[sid].update(
            {"status": transfers.STOCK_STATUS_TRANSFERRED, "transfer_id": "TRF-LEGACY"}
        )
    transfer = {
        "id": "TRF-LEGACY",
        "status": transfers.TransferStatus.IN_TRANSIT,
        "from_location_id": "A",
        "to_location_id": "B",
        "stock_shipped": True,
        # Legacy shape: ship never recorded the moved ids, so the pool has to be
        # re-queried each pass.
        "items": [{"id": "LINE-L", "product_id": "PRD-LEGACY", "quantity_requested": 4}],
    }

    _pass(transfer, 2, damaged=0)
    assert _units_at(repo, "B", transfers.STOCK_STATUS_AVAILABLE) == {"U-0", "U-1"}

    # The last two frames arrived cracked.
    transfer = _pass(transfer, 4, damaged=2)

    line = transfer["items"][0]
    quarantined = _units_at(repo, "B", transfers.STOCK_STATUS_QUARANTINED)
    assert len(quarantined) == 2, f"declared 2 damaged, quarantined {quarantined}"
    assert set(line.get("damaged_stock_ids", [])) == quarantined
    # And the good ones are exactly the rest -- no unit counted both ways.
    assert set(line["received_stock_ids"]) == set(ids) - quarantined
    assert transfer["stock_units_quarantined"] == 2


def test_a_unit_already_rehomed_as_good_is_not_requarantined_next_pass(repo):
    """The damaged count is declared per line and grows with `received`, so the
    split must be computed from what the line has ALREADY recorded -- not
    recomputed from the cumulative totals each pass. Otherwise pass 2 quarantines
    a fresh unit on top of the one pass 1 already quarantined, and the line ends
    with more damaged units than the receiver ever declared."""
    transfer, ids = _rehome_transfer(repo, n=3)

    _pass(transfer, 2, damaged=1)  # 2 arrived, 1 of them cracked
    transfer = _pass(transfer, 3, damaged=1)  # the third arrived, still 1 cracked

    line = transfer["items"][0]
    damaged = set(line.get("damaged_stock_ids", []))
    good = set(line["received_stock_ids"])
    assert len(damaged) == 1, f"declared 1 damaged, quarantined {damaged}"
    assert good & damaged == set()
    assert good | damaged == set(ids)
    assert _units_at(repo, "B", transfers.STOCK_STATUS_QUARANTINED) == damaged


def test_partial_then_full_receive_rehomes_each_unit_exactly_once(repo):
    """Control: no flake, an honest 2-then-3 partial receive. Each unit moves
    exactly once and nothing is fabricated."""
    transfer, ids = _rehome_transfer(repo, n=3)

    _pass(transfer, 2)
    transfer = _pass(transfer, 3)

    line = transfer["items"][0]
    assert set(line["received_stock_ids"]) == set(ids)
    assert len(line["received_stock_ids"]) == 3
    assert transfer["stock_units_moved_in"] == 3
