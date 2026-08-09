"""
IMS 2.0 -- F8: GRN acceptance is claimed ATOMICALLY (no double-mint)
====================================================================
POST /purchase/grn/{id}/accept used to be a textbook check-then-act:

    read the GRN -> test status == PENDING -> mint serialized stock_units
    -> (only at the very END) flip the status to ACCEPTED

Two concurrent POSTs -- an impatient double-click on "Accept", a retry, two
terminals -- both passed the status test while the doc was still PENDING and
BOTH ran the minting loop. The per-line `stock_repo.count` idempotency guard
could not save it either: both requests read `already = 0` before either had
written a single unit. Real received inventory doubled.

The fix claims the receipt with ONE guarded single-document update (status-keyed
+ a lock field) before any stock is minted, so exactly one racing caller
proceeds and the loser gets a 409.

These tests reproduce the RACE, not just a sequential re-POST: the fake stock
repo fires the "second click" from inside the first call's very first
`create()` -- i.e. the winner has claimed and is mid-mint, and the GRN doc is
still PENDING when the second request reads it. That is exactly the interleaving
the status check cannot see.

Isolated fake-collection harness (no Mongo), same style as
test_transfer_cancel_stock_guard.py / test_grn_accept_store_guard.py.

Run: JWT_SECRET_KEY=test python -m pytest \
        backend/tests/test_grn_accept_atomic_claim.py -q
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Callable, Optional

os.environ.setdefault("JWT_SECRET_KEY", "test-key-grn-atomic-claim")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from api.routers import vendors as vd  # noqa: E402


_ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"]}


def _run(coro):
    """Drive a coroutine that never awaits, WITHOUT an event loop.

    Needed because the race test re-enters accept_grn from inside the first
    call's mint loop; asyncio refuses to start a second loop while one is
    already running. accept_grn -> _accept_grn_impl -> _accept_grn_claimed
    contains no awaits, so a single send(None) runs it to completion."""
    try:
        coro.send(None)
    except StopIteration as stop:
        # dict(...) -- every handler under test answers a JSON object, and it
        # keeps static analysis from inferring StopIteration.value as None.
        return dict(stop.value or {})
    raise AssertionError("accept_grn awaited -- harness needs a real event loop")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _FakeGrnColl:
    """Stand-in for the `grns` pymongo collection with REAL find_one_and_update
    semantics for the operators the F8 claim filter uses ($in / $exists / $lt /
    $or / $ne). Single doc is enough -- the claim is single-document by design."""

    def __init__(self, doc):
        self.doc = doc
        self.calls = 0

    def _matches(self, flt):
        for key, val in (flt or {}).items():
            if key == "$or":
                if not any(self._matches(cond) for cond in val):
                    return False
                continue
            if isinstance(val, dict):
                for op, operand in val.items():
                    cur = self.doc.get(key)
                    if op == "$in":
                        if cur not in operand:
                            return False
                    elif op == "$nin":
                        if cur in operand:
                            return False
                    elif op == "$exists":
                        if (key in self.doc) != operand:
                            return False
                    elif op == "$ne":
                        if cur == operand:
                            return False
                    elif op == "$lt":
                        if cur is None or not cur < operand:
                            return False
                    else:  # pragma: no cover - guard against silent test drift
                        raise AssertionError(f"unsupported operator {op}")
                continue
            if self.doc.get(key) != val:
                return False
        return True

    def find_one_and_update(self, flt, update):
        self.calls += 1
        if not self._matches(flt):
            return None
        before = dict(self.doc)
        self.doc.update(update.get("$set", {}))
        return before


class _GrnRepo:
    """GRNRepository stand-in: exposes `.collection` exactly like the real one
    (BaseRepository wraps the pymongo collection there)."""

    def __init__(self, doc):
        self.collection = _FakeGrnColl(doc)

    @property
    def doc(self):
        return self.collection.doc

    def find_by_id(self, gid):
        return dict(self.doc) if gid == self.doc["grn_id"] else None

    def update(self, gid, patch):
        self.doc.update(patch)
        return True

    def find_many(self, *a, **k):
        return [dict(self.doc)]

    def find(self, *a, **k):
        return [dict(self.doc)]


class _MinimalGrnRepo:
    """A mock repo with NO atomic primitive at all (no .collection, no
    find_one_and_update, no update_one) -- the fail-open path."""

    def __init__(self, doc):
        self.doc = doc

    def find_by_id(self, gid):
        return dict(self.doc) if gid == self.doc["grn_id"] else None

    def update(self, gid, patch):
        self.doc.update(patch)
        return True


class _BoomColl(_FakeGrnColl):
    """A collection whose atomic primitive RAISES -- a replica-set stepdown /
    AutoReconnect. Distinct from 'this repo has no primitive'."""

    def find_one_and_update(self, flt, update):
        self.calls += 1
        raise RuntimeError("replica set stepdown")


class _LostReplyColl(_FakeGrnColl):
    """The nastiest driver failure: findAndModify APPLIED server-side, then the
    reply was lost and the driver raised. Also exposes update_one, so it proves
    we do NOT fall through to it with a filter our own write just invalidated."""

    def __init__(self, doc):
        super().__init__(doc)
        self.update_one_calls = 0

    def find_one_and_update(self, flt, update):
        self.calls += 1
        if self._matches(flt):
            self.doc.update(update.get("$set", {}))
        raise RuntimeError("connection reset after write")

    def update_one(self, flt, update):  # pragma: no cover - must never be called
        self.update_one_calls += 1
        return type("R", (), {"matched_count": 0, "modified_count": 0})()


class _StockRepo:
    """Serialized stock_units stand-in.

    `on_create` fires once BEFORE the very first unit is appended (the tightest
    race window: claim taken, nothing minted yet). `after_hook` fires once right
    AFTER row number `after_n` is appended -- used to interleave a takeover from
    the MIDDLE of a multi-line mint."""

    def __init__(self):
        self.rows = []
        self.on_create: Optional[Callable[[], None]] = None
        self.after_hook: Optional[Callable[[], None]] = None
        self.after_n = 0

    def create(self, doc):
        hook = self.on_create
        if hook is not None:
            self.on_create = None
            hook()
        row = dict(doc)
        row["stock_id"] = "ST-%d" % (len(self.rows) + 1)
        self.rows.append(row)
        after = self.after_hook
        if after is not None:
            if len(self.rows) == self.after_n:
                self.after_hook = None
                after()
        return row

    def count(self, flt):
        return sum(
            1 for r in self.rows if all(r.get(k) == v for k, v in (flt or {}).items())
        )

    def find_many(self, flt, *a, **k):
        return [
            dict(r)
            for r in self.rows
            if all(r.get(k) == v for k, v in (flt or {}).items())
        ]


def _grn(status="PENDING", qty=5, **extra):
    doc = {
        "grn_id": "GRN-1",
        "grn_number": "GRN-2601-001",
        "store_id": "BV-TEST-01",
        "po_id": None,
        "status": status,
        "items": [{"product_id": "P1", "accepted_qty": qty, "location_code": "A1"}],
    }
    doc.update(extra)
    return doc


def _grn_lines(lines, status="PENDING", **extra):
    """Multi-line GRN, mirroring the panel's reproduction (P1 x4, P2 x6, P3 x6)."""
    doc = _grn(status=status, **extra)
    doc["items"] = [
        {"product_id": pid, "accepted_qty": qty, "location_code": "A1"}
        for pid, qty in lines
    ]
    return doc


def _aged_lock(extra_seconds=60):
    return (
        datetime.now()
        - timedelta(seconds=vd._GRN_ACCEPT_LOCK_STALE_SECONDS + extra_seconds)
    ).isoformat()


@pytest.fixture()
def wired(monkeypatch):
    """Wire vendors to fake repos; returns (install) -> (grn_repo, stock_repo)."""

    def install(grn_doc, repo_cls=_GrnRepo, coll_cls=None):
        grn_repo = repo_cls(grn_doc)
        if coll_cls is not None:
            grn_repo.collection = coll_cls(grn_doc)
        stock_repo = _StockRepo()
        monkeypatch.setattr(vd, "get_grn_repository", lambda: grn_repo)
        monkeypatch.setattr(vd, "get_stock_repository", lambda: stock_repo)
        # No PO repo -> the PO receipt math is skipped (covered elsewhere).
        monkeypatch.setattr(vd, "get_purchase_order_repository", lambda: None)
        # No product repo -> the Hub-Phase-2 catalog gate fail-softs to "mint",
        # keeping this test focused on the race.
        monkeypatch.setattr(vd, "get_product_repository", lambda: None)
        # Hermetic: no DB for the fail-soft ledger emit.
        monkeypatch.setattr(vd, "_get_db", lambda: None)
        return grn_repo, stock_repo

    return install


# ---------------------------------------------------------------------------
# THE RACE
# ---------------------------------------------------------------------------
def test_double_click_accept_mints_stock_exactly_once(wired):
    """Two interleaved accepts: ONE mints 5 units, the other gets a 409."""
    grn_repo, stock_repo = wired(_grn(qty=5))

    loser = {}

    def _second_click():
        # Fires while call #1 holds the claim and the doc is still PENDING.
        assert grn_repo.doc["status"] == "PENDING", "race window must be pre-flip"
        try:
            loser["result"] = _run(vd.accept_grn("GRN-1", _ADMIN))
        except HTTPException as exc:
            loser["exc"] = exc

    stock_repo.on_create = _second_click
    out = _run(vd.accept_grn("GRN-1", _ADMIN))

    # The loser was refused -- and refused with a conflict, not a 500.
    assert "result" not in loser, "second concurrent accept must NOT succeed"
    assert loser["exc"].status_code == 409
    assert "being accepted" in loser["exc"].detail

    # Stock minted EXACTLY once: 5 units, not 10.
    assert len(stock_repo.rows) == 5
    assert out["units_added"] == 5
    assert stock_repo.count({"source_id": "GRN-1", "product_id": "P1"}) == 5
    assert grn_repo.doc["status"] == "ACCEPTED"
    assert grn_repo.doc["units_added"] == 5


def test_race_without_the_claim_would_double_mint(wired, monkeypatch):
    """Sentinel: neutralise the claim and the SAME interleaving doubles stock.

    Proves the test above is measuring the claim and not some accident of the
    harness -- and documents exactly what F8 was."""
    grn_repo, stock_repo = wired(_grn(qty=5))
    # Claim always "wins" == the pre-fix world (no claim at all).
    monkeypatch.setattr(vd, "_claim_grn_for_accept", lambda *a, **k: "NO-LOCK")

    def _second_click():
        _run(vd.accept_grn("GRN-1", _ADMIN))

    stock_repo.on_create = _second_click
    _run(vd.accept_grn("GRN-1", _ADMIN))

    assert len(stock_repo.rows) == 10, "unguarded race must double-mint (F8)"


def test_loser_touches_no_stock_at_all(wired):
    """The 409'd caller must not create, or partially create, any unit."""
    grn_repo, stock_repo = wired(_grn(qty=3))
    seen = {}

    def _second_click():
        try:
            _run(vd.accept_grn("GRN-1", _ADMIN))
        except HTTPException as exc:
            seen["code"] = exc.status_code
            seen["rows_at_reject"] = len(stock_repo.rows)

    stock_repo.on_create = _second_click
    _run(vd.accept_grn("GRN-1", _ADMIN))

    assert seen["code"] == 409
    # The loser was rejected before the winner had appended its first row.
    assert seen["rows_at_reject"] == 0
    assert len(stock_repo.rows) == 3


# ---------------------------------------------------------------------------
# Sequential re-POST (existing behaviour must be preserved)
# ---------------------------------------------------------------------------
def test_sequential_second_accept_still_400s(wired):
    """After a clean accept the receipt is ACCEPTED -> the friendly 400 stands,
    and no extra unit is minted."""
    grn_repo, stock_repo = wired(_grn(qty=4))
    _run(vd.accept_grn("GRN-1", _ADMIN))
    assert len(stock_repo.rows) == 4

    with pytest.raises(HTTPException) as err:
        _run(vd.accept_grn("GRN-1", _ADMIN))
    assert err.value.status_code == 400
    assert len(stock_repo.rows) == 4


def test_accept_releases_the_lock_so_catalog_now_can_re_accept(wired):
    """A clean accept must leave NO lock behind, otherwise the legitimate
    PARTIALLY_ACCEPTED -> 'Catalog now' -> re-accept flow would 409 for the
    whole stale window."""
    grn_repo, _stock = wired(_grn(qty=2))
    _run(vd.accept_grn("GRN-1", _ADMIN))
    assert grn_repo.doc["accept_lock_at"] is None
    assert grn_repo.doc["accept_lock_token"] is None


def test_partially_accepted_grn_can_be_re_accepted_and_does_not_re_mint(wired):
    """Re-accepting a PARTIALLY_ACCEPTED receipt wins a fresh claim, and the
    per-(grn, line) guard means it mints nothing twice."""
    grn_repo, stock_repo = wired(_grn(qty=3))
    _run(vd.accept_grn("GRN-1", _ADMIN))
    assert len(stock_repo.rows) == 3

    # Simulate the "Catalog now" re-open: back to PARTIALLY_ACCEPTED.
    grn_repo.doc["status"] = "PARTIALLY_ACCEPTED"
    out = _run(vd.accept_grn("GRN-1", _ADMIN))

    assert out["units_added"] == 0
    assert len(stock_repo.rows) == 3, "re-accept must not re-mint minted lines"


# ---------------------------------------------------------------------------
# The claim primitive itself
# ---------------------------------------------------------------------------
def test_claim_filter_rejects_a_non_acceptable_status(wired):
    grn_repo, _stock = wired(_grn(status="ACCEPTED"))
    assert vd._claim_grn_for_accept(grn_repo, "GRN-1", "u1") is None


def test_claim_rejects_while_a_fresh_lock_is_held(wired):
    grn_repo, _stock = wired(
        _grn(accept_lock_at=datetime.now().isoformat(), accept_lock_token="OTHER")
    )
    assert vd._claim_grn_for_accept(grn_repo, "GRN-1", "u1") is None
    # The rival's lock is untouched.
    assert grn_repo.doc["accept_lock_token"] == "OTHER"


def test_stale_lock_is_taken_over(wired):
    """A crashed worker must not freeze a receipt forever."""
    stale = (
        datetime.now() - timedelta(seconds=vd._GRN_ACCEPT_LOCK_STALE_SECONDS + 60)
    ).isoformat()
    grn_repo, _stock = wired(_grn(accept_lock_at=stale, accept_lock_token="DEAD"))
    token = vd._claim_grn_for_accept(grn_repo, "GRN-1", "u1")
    assert token and token != "DEAD"
    assert grn_repo.doc["accept_lock_token"] == token


def test_release_is_token_guarded(wired):
    """Releasing with the wrong token must never free somebody else's lock."""
    grn_repo, _stock = wired(_grn())
    token = vd._claim_grn_for_accept(grn_repo, "GRN-1", "u1")
    vd._release_grn_accept_claim(grn_repo, "GRN-1", "SOMEONE-ELSE")
    assert grn_repo.doc["accept_lock_token"] == token
    vd._release_grn_accept_claim(grn_repo, "GRN-1", token)
    assert grn_repo.doc["accept_lock_token"] is None


def test_failed_accept_hands_the_claim_back(wired, monkeypatch):
    """A blow-up mid-accept releases the lock so the operator can retry now."""
    grn_repo, stock_repo = wired(_grn(qty=2))

    def _boom(_doc):
        raise RuntimeError("stock store down")

    monkeypatch.setattr(stock_repo, "create", _boom)
    with pytest.raises(RuntimeError):
        _run(vd.accept_grn("GRN-1", _ADMIN))

    assert grn_repo.doc["accept_lock_token"] is None
    assert grn_repo.doc["status"] == "PENDING"
    # And the retry is accepted, not 409'd.
    grn_repo2, stock_repo2 = grn_repo, _StockRepo()
    monkeypatch.setattr(vd, "get_stock_repository", lambda: stock_repo2)
    out = _run(vd.accept_grn("GRN-1", _ADMIN))
    assert out["units_added"] == 2
    assert grn_repo2.doc["status"] == "ACCEPTED"


def test_claim_fails_open_on_a_repo_with_no_atomic_primitive(wired):
    """A minimal mock repo (stub mode) must never block receiving -- same
    fail-open convention as is_online_store and the marketing.py claim."""
    grn_repo, stock_repo = wired(_grn(qty=2), repo_cls=_MinimalGrnRepo)
    assert vd._claim_grn_for_accept(grn_repo, "GRN-1", "u1") is not None
    out = _run(vd.accept_grn("GRN-1", _ADMIN))
    assert out["units_added"] == 2
    assert len(stock_repo.rows) == 2


def test_online_store_guard_still_fires_before_any_claim(wired):
    """Ordering: the pooled/stockless-store 400 must beat the claim, so a
    rejected receipt never leaves a lock behind."""
    grn_repo, stock_repo = wired(_grn(store_id="BV-ONLINE-01"))
    with pytest.raises(HTTPException) as err:
        _run(vd.accept_grn("GRN-1", _ADMIN))
    assert err.value.status_code == 400
    assert "online store" in err.value.detail
    assert grn_repo.collection.calls == 0, "no claim write on a rejected receipt"
    assert stock_repo.rows == []


# ===========================================================================
# ROUND 2 -- the four defects the adversarial stock panel found in round 1
# ===========================================================================


# --- MUST-FIX 1: the claim must fail CLOSED when the atomic write ERRORS ----
def test_claim_fails_closed_when_the_atomic_write_raises(wired):
    """A Mongo blip must NOT hand out a token.

    Round 1 collapsed 'no atomic primitive' (mock -> fail open) and 'the
    primitive raised' (real driver error) into the same None, so during a
    replica-set stepdown BOTH double-click POSTs were handed a token and both
    minted -- the original F8 double-mint with no stale window involved. The
    stepdown is exactly when the spinner hangs and staff click again."""
    grn_repo, stock_repo = wired(_grn(qty=5), coll_cls=_BoomColl)
    with pytest.raises(HTTPException) as err:
        _run(vd.accept_grn("GRN-1", _ADMIN))
    assert err.value.status_code == 503
    assert "try again" in err.value.detail
    # Nothing minted and NO lock written, so the 503 strands nothing.
    assert stock_repo.rows == []
    assert grn_repo.doc.get("accept_lock_token") is None


def test_both_racers_are_refused_during_a_write_error(wired):
    """The whole point: two racing clicks during a blip mint ZERO, not double."""
    grn_repo, stock_repo = wired(_grn(qty=5), coll_cls=_BoomColl)
    codes = []
    for _ in range(2):
        with pytest.raises(HTTPException) as err:
            _run(vd.accept_grn("GRN-1", _ADMIN))
        codes.append(err.value.status_code)
    assert codes == [503, 503]
    assert stock_repo.rows == []


def test_guarded_write_distinguishes_no_primitive_from_a_write_error(wired):
    grn_repo, _stock = wired(_grn(), coll_cls=_BoomColl)
    assert (
        vd._guarded_grn_write(grn_repo, {"grn_id": "GRN-1"}, {"$set": {"x": 1}})
        is vd._GRN_WRITE_ERROR
    )
    minimal, _s = wired(_grn(), repo_cls=_MinimalGrnRepo)
    assert (
        vd._guarded_grn_write(minimal, {"grn_id": "GRN-1"}, {"$set": {"x": 1}}) is None
    )


def test_no_fallthrough_to_update_one_after_a_lost_reply(wired):
    """A find_one_and_update that APPLIED but lost its reply must not be
    re-tested with update_one: our own write has already invalidated the filter,
    so the fallthrough returned 'lost' and self-409'd the only caller, freezing
    the receipt for the whole stale window."""
    grn_repo, stock_repo = wired(_grn(qty=3), coll_cls=_LostReplyColl)
    with pytest.raises(HTTPException) as err:
        _run(vd.accept_grn("GRN-1", _ADMIN))
    assert err.value.status_code == 503, "unknown state -> 503, never a bogus 409"
    assert grn_repo.collection.update_one_calls == 0
    assert stock_repo.rows == []


# --- MUST-FIX 2: the per-line mint guard must fail CLOSED ------------------
def test_count_failure_aborts_instead_of_re_minting(wired, monkeypatch):
    """The count is the ONLY defence on the re-run paths (there is no unique
    index behind source_id/grn_line_index), so a swallowed failure re-mints a
    whole receipt. It must abort instead."""
    grn_repo, stock_repo = wired(_grn(qty=5))
    _run(vd.accept_grn("GRN-1", _ADMIN))
    assert len(stock_repo.rows) == 5

    # Re-open the receipt the way "Catalog now" does, then break the count.
    grn_repo.doc["status"] = "PARTIALLY_ACCEPTED"

    def _boom(_flt):
        raise RuntimeError("count unavailable")

    monkeypatch.setattr(stock_repo, "count", _boom)
    with pytest.raises(HTTPException) as err:
        _run(vd.accept_grn("GRN-1", _ADMIN))
    assert err.value.status_code == 503
    assert "already been received" in err.value.detail
    assert len(stock_repo.rows) == 5, "must not re-mint what it cannot verify"
    # The claim was handed back, so a healthy retry works immediately.
    assert grn_repo.doc["accept_lock_token"] is None


def test_already_minted_reads_through_the_raw_collection(wired):
    """BaseRepository.count SWALLOWS driver errors and returns 0, so counting
    via repo.count would silently answer 'nothing minted yet'. The guard must
    read the raw collection so the error actually surfaces."""

    class _Coll:
        def count_documents(self, _flt):
            raise RuntimeError("driver error")

    class _SwallowingRepo:
        collection = _Coll()

        def count(self, _flt):  # what BaseRepository does with the same error
            return 0

    with pytest.raises(RuntimeError):
        vd._grn_already_minted(_SwallowingRepo(), {"source_id": "GRN-1"})

    # Mock repos with no collection still work through repo.count.
    stock = _StockRepo()
    stock.create({"source_id": "GRN-1"})
    assert vd._grn_already_minted(stock, {"source_id": "GRN-1"}) == 1


# --- MUST-FIX 3: a takeover must not fire while the winner is still minting -
def test_midloop_takeover_with_an_aged_lock_does_not_double_mint(wired, monkeypatch):
    """THE panel's reproduction, now asserted the other way round.

    16 real units (P1 x4, P2 x6, P3 x6). The winner is wedged mid-line-2; its
    lock ages past the stale window and a second POST takes it over and mints
    the remainder. The winner then wakes up -- and MUST stop, because its
    `to_mint` was frozen before the takeover. Round 1 minted 21 units for this
    16-unit receipt, status ACCEPTED, no error surfaced.

    HEARTBEAT_SECONDS is forced to 0 to express 'a heartbeat is overdue', which
    is precisely the state of a worker that has just come back from a
    multi-minute block inside one pymongo call."""
    grn_repo, stock_repo = wired(_grn_lines([("P1", 4), ("P2", 6), ("P3", 6)]))
    monkeypatch.setattr(vd, "_GRN_ACCEPT_HEARTBEAT_SECONDS", 0)

    thief = {}

    def _steal():
        grn_repo.doc["accept_lock_at"] = _aged_lock()
        thief["out"] = _run(vd.accept_grn("GRN-1", _ADMIN))

    stock_repo.after_hook = _steal
    stock_repo.after_n = 6  # mid line P2

    with pytest.raises(HTTPException) as err:
        _run(vd.accept_grn("GRN-1", _ADMIN))

    assert err.value.status_code == 409
    assert "taken over" in err.value.detail
    assert len(stock_repo.rows) == 16, "16 accepted units -> exactly 16 stock rows"
    assert stock_repo.count({"product_id": "P2"}) == 6
    assert stock_repo.count({"product_id": "P3"}) == 6
    assert thief["out"]["grn_status"] == "ACCEPTED"


def test_midloop_takeover_without_the_heartbeat_would_double_mint(wired, monkeypatch):
    """Sentinel for MUST-FIX 3: disable the heartbeat and the same interleaving
    over-mints, exactly as round 1 did."""
    grn_repo, stock_repo = wired(_grn_lines([("P1", 4), ("P2", 6), ("P3", 6)]))
    monkeypatch.setattr(vd, "_grn_accept_heartbeat_tick", lambda *a, **k: None)

    def _steal():
        grn_repo.doc["accept_lock_at"] = _aged_lock()
        _run(vd.accept_grn("GRN-1", _ADMIN))

    stock_repo.after_hook = _steal
    stock_repo.after_n = 6
    _run(vd.accept_grn("GRN-1", _ADMIN))

    assert len(stock_repo.rows) == 20, "unfenced takeover over-mints (round-1 bug)"


def test_heartbeat_keeps_a_live_accept_from_being_declared_stale(wired, monkeypatch):
    """The other half: a LONG but live accept must keep its claim, so a second
    click still gets a clean 409 instead of stealing the receipt."""
    grn_repo, stock_repo = wired(_grn(qty=6))
    monkeypatch.setattr(vd, "_GRN_ACCEPT_HEARTBEAT_SECONDS", 0)

    seen = {}

    def _second_click():
        # The lock was stamped at claim time, but the heartbeat has been
        # refreshing it, so an "old" claim time must not make it stealable.
        try:
            _run(vd.accept_grn("GRN-1", _ADMIN))
        except HTTPException as exc:
            seen["code"] = exc.status_code

    stock_repo.after_hook = _second_click
    stock_repo.after_n = 3
    out = _run(vd.accept_grn("GRN-1", _ADMIN))

    assert seen["code"] == 409
    assert len(stock_repo.rows) == 6
    assert out["units_added"] == 6


def test_stale_window_is_far_above_any_plausible_accept():
    """Round 1's 180s could be reached by a merely SLOW accept (prod sets no
    socketTimeoutMS, so a blackholed socket parks a request indefinitely)."""
    assert vd._GRN_ACCEPT_LOCK_STALE_SECONDS >= 900
    # ...and the heartbeat cadence must stay well inside it.
    assert vd._GRN_ACCEPT_HEARTBEAT_SECONDS * 10 < vd._GRN_ACCEPT_LOCK_STALE_SECONDS


def test_conflict_message_is_honest_about_the_wait(wired):
    """After a hard kill the wait really is the stale window, so the 409 must
    say roughly how long rather than 'wait a moment'."""
    grn_repo, _stock = wired(_grn(accept_lock_at=datetime.now().isoformat()))
    exc = vd._grn_accept_conflict(grn_repo, "GRN-1")
    assert exc.status_code == 409
    assert "minute(s)" in exc.detail


# --- MUST-FIX 4: the terminal write must not clear a STOLEN lock -----------
def test_terminal_write_flips_status_but_never_clears_a_stolen_lock(wired):
    """A winner whose claim was stolen used to null the takeover holder's LIVE
    lock on its way out. Because the status it writes when a line is held is
    PARTIALLY_ACCEPTED (an acceptable status), a third POST then saw an
    acceptable status AND a null lock and minted again -- the amplifier that
    turned one takeover into unbounded re-entry."""
    grn_repo, stock_repo = wired(_grn(qty=2))
    grn_repo.doc["accept_lock_at"] = datetime.now().isoformat()
    grn_repo.doc["accept_lock_token"] = "THIEF"
    grn_repo.doc["accept_lock_by"] = "u-thief"

    out = vd._accept_grn_claimed(
        "GRN-1",
        dict(grn_repo.doc),
        _ADMIN,
        grn_repo,
        stock_repo,
        None,
        "MY-STOLEN-TOKEN",
    )

    # (a) the status flip is UNGUARDED -- stock is never stranded as PENDING.
    assert out["grn_status"] == "ACCEPTED"
    assert grn_repo.doc["status"] == "ACCEPTED"
    # (b) the release is TOKEN-GUARDED -- the real holder's lock survives.
    assert grn_repo.doc["accept_lock_token"] == "THIEF"
    assert grn_repo.doc["accept_lock_by"] == "u-thief"


def test_a_stolen_winner_leaves_the_receipt_unclaimable_by_a_third_caller(wired):
    """The end-to-end consequence of MUST-FIX 4: with the thief's lock intact, a
    third POST is refused instead of minting a third time."""
    grn_repo, stock_repo = wired(_grn(qty=2))
    grn_repo.doc["accept_lock_at"] = datetime.now().isoformat()
    grn_repo.doc["accept_lock_token"] = "THIEF"

    vd._accept_grn_claimed(
        "GRN-1", dict(grn_repo.doc), _ADMIN, grn_repo, stock_repo, None, "STOLEN"
    )
    grn_repo.doc["status"] = "PARTIALLY_ACCEPTED"  # the dangerous status

    assert vd._claim_grn_for_accept(grn_repo, "GRN-1", "u3") is None
