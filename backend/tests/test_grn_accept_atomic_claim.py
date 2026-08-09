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

import logging
import os
import sys
from contextlib import contextmanager
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


@contextmanager
def _captured_errors():
    """Collect ERROR records from the vendors logger WITHOUT depending on the
    pytest logging plugin (this suite is sometimes run with -p no:logging)."""
    records = []

    class _Sink(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                records.append(record.getMessage())

    sink = _Sink()
    log = logging.getLogger("api.routers.vendors")
    previous = log.level
    log.addHandler(sink)
    log.setLevel(logging.ERROR)
    try:
        yield records
    finally:
        log.removeHandler(sink)
        log.setLevel(previous)


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


class _HeartbeatBoomColl(_FakeGrnColl):
    """The claim, the release and every other guarded write succeed; only the
    HEARTBEAT patch raises.

    This is the correlated real-world shape: the replica-set stepdown that
    wedges a worker's socket for minutes is the same event that makes its next
    token-guarded write raise."""

    def __init__(self, doc):
        super().__init__(doc)
        self.heartbeat_attempts = 0

    def find_one_and_update(self, flt, update):
        if set((update or {}).get("$set", {})) == {"accept_lock_at"}:
            self.heartbeat_attempts += 1
            raise RuntimeError("not primary; election in progress")
        return super().find_one_and_update(flt, update)


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


class DuplicateKeyError(Exception):
    """Stand-in for pymongo.errors.DuplicateKeyError.

    The router matches it BY CLASS NAME (no hard pymongo import), exactly as
    BaseRepository.create does, so this local class exercises the real path."""


class _StockRepo:
    """Serialized stock_units stand-in.

    Emulates the UNIQUE PARTIAL index on
    (source_id, grn_line_index, line_unit_seq): a row carrying all three whose
    key is already present is rejected the way Mongo would reject it. Set
    `enforce_unique = False` to model a world without the index (sentinels).

    Hooks, all one-shot:
      * `on_create`  -- before the very first unit is appended (tightest race
        window: claim taken, nothing minted yet);
      * `after_hook` at `after_n` -- right AFTER row N is appended;
      * `before_hook` at `before_n` -- right BEFORE row N is attempted, i.e.
        while that insert is IN FLIGHT. This is the one that reproduces a
        wedged worker whose insert commits after a takeover.
    """

    def __init__(self, enforce_unique=True):
        self.rows = []
        self.enforce_unique = enforce_unique
        self._keys = set()
        self.rejected = 0
        self.on_create: Optional[Callable[[], None]] = None
        self.after_hook: Optional[Callable[[], None]] = None
        self.after_n = 0
        self.before_hook: Optional[Callable[[], None]] = None
        self.before_n = 0

    def create(self, doc, *, raise_on_duplicate=False):
        hook = self.on_create
        if hook is not None:
            self.on_create = None
            hook()
        before = self.before_hook
        if before is not None:
            if len(self.rows) + 1 == self.before_n:
                self.before_hook = None
                before()
        key = (
            doc.get("source_id"),
            doc.get("grn_line_index"),
            doc.get("line_unit_seq"),
        )
        indexed = self.enforce_unique and all(part is not None for part in key)
        if indexed and key in self._keys:
            self.rejected += 1
            if raise_on_duplicate:
                # Mirror Mongo's real shape: the errmsg names the index that
                # fired, which is what the router attributes the skip to.
                exc = DuplicateKeyError(
                    "E11000 duplicate key error collection: ims_2_0.stock_units "
                    "index: uniq_grn_line_unit_seq dup key: %s" % (key,)
                )
                exc.details = {
                    "errmsg": (
                        "E11000 duplicate key error collection: "
                        "ims_2_0.stock_units index: uniq_grn_line_unit_seq"
                    ),
                    "keyPattern": {
                        "source_id": 1,
                        "grn_line_index": 1,
                        "line_unit_seq": 1,
                    },
                }
                raise exc
            return None
        if indexed:
            self._keys.add(key)
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

    def install(grn_doc, repo_cls=_GrnRepo, coll_cls=None, enforce_unique=True):
        grn_repo = repo_cls(grn_doc)
        if coll_cls is not None:
            grn_repo.collection = coll_cls(grn_doc)
        stock_repo = _StockRepo(enforce_unique=enforce_unique)
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


def test_race_without_the_claim_is_still_capped_by_the_unique_key(wired, monkeypatch):
    """Defence in depth: neutralise the CLAIM and the unique
    (source_id, grn_line_index, line_unit_seq) key alone still holds the line
    to its accepted quantity -- the second racer's ordinals are rejected."""
    grn_repo, stock_repo = wired(_grn(qty=5))
    # Claim always "wins" == the pre-fix world (no claim at all).
    monkeypatch.setattr(vd, "_claim_grn_for_accept", lambda *a, **k: "NO-LOCK")

    def _second_click():
        _run(vd.accept_grn("GRN-1", _ADMIN))

    stock_repo.on_create = _second_click
    _run(vd.accept_grn("GRN-1", _ADMIN))

    assert len(stock_repo.rows) == 5
    assert stock_repo.rejected == 5, "every duplicate ordinal was refused"


def test_race_with_neither_the_claim_nor_the_unique_key_double_mints(
    wired, monkeypatch
):
    """Sentinel: with BOTH layers removed the same interleaving doubles stock.

    Proves the tests above measure the guards and not some accident of the
    harness -- and documents exactly what F8 was."""
    grn_repo, stock_repo = wired(_grn(qty=5), enforce_unique=False)
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
    `to_mint` was frozen before the takeover. Through THIS interleaving round-1
    code mints 20 for this 16-unit receipt, status ACCEPTED, no error surfaced.
    (The panel's own write-up cited 21 from a different steal point; 20 is the
    number this harness actually reproduces -- see the sentinel below.)

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


def test_midloop_takeover_without_the_heartbeat_is_still_capped_by_the_unique_key(
    wired, monkeypatch
):
    """Defence in depth: disable the heartbeat and the unique
    (source_id, grn_line_index, line_unit_seq) index alone still caps the
    receipt at its accepted quantity -- the woken worker's units collide with
    the ordinals the takeover already minted and are rejected."""
    grn_repo, stock_repo = wired(_grn_lines([("P1", 4), ("P2", 6), ("P3", 6)]))
    monkeypatch.setattr(vd, "_grn_accept_heartbeat_tick", lambda *a, **k: None)

    def _steal():
        grn_repo.doc["accept_lock_at"] = _aged_lock()
        _run(vd.accept_grn("GRN-1", _ADMIN))

    stock_repo.after_hook = _steal
    stock_repo.after_n = 6
    _run(vd.accept_grn("GRN-1", _ADMIN))

    assert len(stock_repo.rows) == 16
    assert stock_repo.rejected == 4, "the duplicate ordinals were refused"


def test_midloop_takeover_with_neither_guard_would_double_mint(wired, monkeypatch):
    """Sentinel: with BOTH the heartbeat and the unique key removed, the same
    interleaving over-mints -- which is exactly what round 1 did."""
    grn_repo, stock_repo = wired(
        _grn_lines([("P1", 4), ("P2", 6), ("P3", 6)]), enforce_unique=False
    )
    monkeypatch.setattr(vd, "_grn_accept_heartbeat_tick", lambda *a, **k: None)

    def _steal():
        grn_repo.doc["accept_lock_at"] = _aged_lock()
        _run(vd.accept_grn("GRN-1", _ADMIN))

    stock_repo.after_hook = _steal
    stock_repo.after_n = 6
    _run(vd.accept_grn("GRN-1", _ADMIN))

    assert len(stock_repo.rows) == 20, "unguarded takeover over-mints"


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


def test_stale_window_is_wide_enough_to_be_safe_and_short_enough_to_wait_out():
    """Both directions matter and they pull against each other.

    Too NARROW and a live accept gets its claim stolen; too WIDE and a hard
    kill freezes a real delivery for that long with no admin unlock anywhere in
    the app. Round 2 shipped 1800s; round 4 drops it to 300s because the
    heartbeat now fails CLOSED (a worker gives up at half the window, i.e.
    BEFORE a takeover is even permitted), so the extra margin bought nothing but
    shop-floor time."""
    # >= 20x the heartbeat cadence: a live accept can never look stale.
    assert vd._GRN_ACCEPT_LOCK_STALE_SECONDS >= vd._GRN_ACCEPT_HEARTBEAT_SECONDS * 20
    # ...and capped, because this is how long staff wait after a hard kill.
    assert vd._GRN_ACCEPT_LOCK_STALE_SECONDS <= 600
    # Real bounds live in test_abort_bounds_are_strictly_inside_the_takeover_window.


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


# ===========================================================================
# ROUND 3 -- the last hole: the in-flight insert of a taken-over worker.
# Closed by a per-unit ordinal (line_unit_seq) plus the UNIQUE PARTIAL index on
# stock_units {source_id, grn_line_index, line_unit_seq} (connection.py).
# ===========================================================================
def test_inflight_insert_after_a_takeover_is_rejected_by_the_unique_key(
    wired, monkeypatch
):
    """The exact residual round 2 disclosed, now closed.

    16 accepted units (P1 x4, P2 x6, P3 x6). The winner is WEDGED with the
    insert of P2 unit #2 IN FLIGHT: the takeover reads the line count before
    that insert lands, mints the whole remainder, and only then does the
    wedged insert commit. Without a unique key it becomes a 17th unit on the
    shelf. With one it is a DuplicateKeyError -- expected, logged, skipped."""
    grn_repo, stock_repo = wired(_grn_lines([("P1", 4), ("P2", 6), ("P3", 6)]))
    monkeypatch.setattr(vd, "_GRN_ACCEPT_HEARTBEAT_SECONDS", 0)

    thief = {}

    def _steal_while_the_insert_is_in_flight():
        grn_repo.doc["accept_lock_at"] = _aged_lock()
        thief["out"] = _run(vd.accept_grn("GRN-1", _ADMIN))

    stock_repo.before_hook = _steal_while_the_insert_is_in_flight
    stock_repo.before_n = 7  # P2 unit #2 -- attempted, then the takeover runs

    with pytest.raises(HTTPException) as err:
        _run(vd.accept_grn("GRN-1", _ADMIN))

    assert err.value.status_code == 409
    assert len(stock_repo.rows) == 16, "16 accepted units -> exactly 16 rows"
    assert stock_repo.rejected == 1, "the in-flight duplicate was refused"
    assert thief["out"]["grn_status"] == "ACCEPTED"


def test_inflight_insert_without_the_unique_key_becomes_a_phantom_unit(
    wired, monkeypatch
):
    """Sentinel proving the index is what closes it: same interleaving, no
    unique key -> 17 units for a 16-unit delivery."""
    grn_repo, stock_repo = wired(
        _grn_lines([("P1", 4), ("P2", 6), ("P3", 6)]), enforce_unique=False
    )
    monkeypatch.setattr(vd, "_GRN_ACCEPT_HEARTBEAT_SECONDS", 0)

    def _steal_while_the_insert_is_in_flight():
        grn_repo.doc["accept_lock_at"] = _aged_lock()
        _run(vd.accept_grn("GRN-1", _ADMIN))

    stock_repo.before_hook = _steal_while_the_insert_is_in_flight
    stock_repo.before_n = 7

    with pytest.raises(HTTPException):
        _run(vd.accept_grn("GRN-1", _ADMIN))

    assert len(stock_repo.rows) == 17, "one phantom unit without the unique key"


def test_ordinals_are_contiguous_per_line_and_never_collide_across_lines(wired):
    """Each line gets 0..qty-1, keyed per LINE (two lines of the same product
    must not share a key -- that is what grn_line_index is in the index for)."""
    _grn_repo, stock_repo = wired(_grn_lines([("P1", 3), ("P1", 2)]))
    _run(vd.accept_grn("GRN-1", _ADMIN))

    by_line = {}
    for row in stock_repo.rows:
        by_line.setdefault(row["grn_line_index"], []).append(row["line_unit_seq"])
    assert by_line == {0: [0, 1, 2], 1: [0, 1]}
    assert len(stock_repo.rows) == 5
    assert stock_repo.rejected == 0


def test_a_retry_continues_the_ordinal_sequence_instead_of_restarting(wired):
    """The ordinal MUST be derived from the already-minted count.

    If a retry restarted at 0 it would collide with the rows already on the
    shelf and the unique key would reject every unit -- receiving 1 unit
    instead of N. Here the first attempt dies after 2 of 5 units; the retry
    must mint ordinals 2,3,4 and end with exactly 5 units, none rejected."""
    grn_repo, stock_repo = wired(_grn(qty=5))
    real_create = stock_repo.create
    state = {"n": 0}

    def _die_after_two(doc, **kw):
        state["n"] += 1
        if state["n"] > 2:
            raise RuntimeError("worker died")
        return real_create(doc, **kw)

    stock_repo.create = _die_after_two
    with pytest.raises(RuntimeError):
        _run(vd.accept_grn("GRN-1", _ADMIN))
    assert [r["line_unit_seq"] for r in stock_repo.rows] == [0, 1]

    stock_repo.create = real_create
    out = _run(vd.accept_grn("GRN-1", _ADMIN))

    assert out["units_added"] == 3
    assert [r["line_unit_seq"] for r in stock_repo.rows] == [0, 1, 2, 3, 4]
    assert stock_repo.rejected == 0, "a retry must not collide with its own rows"


def test_duplicate_rejection_is_not_surfaced_as_an_error(wired):
    """A DuplicateKeyError from the unique key is the EXPECTED outcome of the
    race, so the mint helper reports it as _GRN_MINT_DUPLICATE and the caller
    skips the unit -- it never propagates out as a 500. (Round 5 made this a
    DISTINCT answer from a falsy 'the insert did not land', which must abort.)"""
    _repo, stock_repo = wired(_grn(qty=1))
    doc = {
        "source_type": "GRN",
        "source_id": "GRN-1",
        "product_id": "P1",
        "grn_line_index": 0,
        "line_unit_seq": 0,
    }
    assert vd._grn_mint_unit(stock_repo, dict(doc), True) is not None
    # A rival worker already minted this exact ordinal.
    assert vd._grn_mint_unit(stock_repo, dict(doc), True) is vd._GRN_MINT_DUPLICATE
    assert stock_repo.rejected == 1
    assert len(stock_repo.rows) == 1


def test_repo_probe_uses_the_explicit_raise_on_duplicate_path(wired):
    """BaseRepository.create supports raise_on_duplicate; a minimal mock does
    not. The probe must pick the explicit path when it is available."""
    _repo, stock_repo = wired(_grn(qty=1))
    assert vd._stock_create_raises_on_duplicate(stock_repo) is True

    class _MinimalStock:
        def create(self, doc):
            return doc

    assert vd._stock_create_raises_on_duplicate(_MinimalStock()) is False


def test_ensure_indexes_registers_the_unique_grn_line_unit_index():
    """The ordinal is only half the fix -- the DB-level unique key must actually
    be requested, with all three fields and a partial filter that exempts legacy
    rows (they carry no line_unit_seq, so the build cannot fail on prod data).

    An index on (source_id, grn_line_index) ALONE would be an outage: the
    2nd..Nth unit of every multi-quantity line would collide and receiving would
    silently mint 1 unit instead of N. This test pins the third field."""
    from database.connection import DatabaseConnection

    requested = []

    class _StockColl:
        def create_index(self, keys, **kw):
            requested.append((keys, kw))
            return "idx"

    class _OtherColl:
        def create_index(self, _keys, **_kw):
            return "idx"

    class _DB:
        def __init__(self):
            self.stock = _StockColl()
            self.other = _OtherColl()

        def __getitem__(self, name):
            return self.stock if name == "stock_units" else self.other

    conn = DatabaseConnection()
    saved_db, saved_connected = conn._db, conn._connected
    try:
        conn._connected = True
        conn._db = _DB()
        conn.ensure_indexes()
    finally:
        conn._db, conn._connected = saved_db, saved_connected

    match = [
        (keys, kw)
        for keys, kw in requested
        if kw.get("name") == "uniq_grn_line_unit_seq"
    ]
    assert len(match) == 1, "the GRN per-unit unique index must be registered"
    keys, kw = match[0]
    assert keys == [("source_id", 1), ("grn_line_index", 1), ("line_unit_seq", 1)]
    assert kw["unique"] is True
    assert kw["partialFilterExpression"] == {
        "source_id": {"$exists": True},
        "grn_line_index": {"$exists": True},
        "line_unit_seq": {"$exists": True},
    }


def test_non_duplicate_insert_errors_still_propagate(wired):
    """Only DuplicateKeyError is swallowed -- a real insert failure must still
    abort the accept (and hand the claim back)."""
    grn_repo, stock_repo = wired(_grn(qty=2))

    def _boom(_doc, **_kw):
        raise RuntimeError("stock store down")

    stock_repo.create = _boom
    with pytest.raises(RuntimeError):
        _run(vd.accept_grn("GRN-1", _ADMIN))
    assert grn_repo.doc["accept_lock_token"] is None


# ===========================================================================
# ROUND 4 -- the re-verify panel's findings.
#   MF1 the heartbeat still failed OPEN on an errored write
#   MF2 void_grn accepted a crashed-mid-accept receipt that HAD minted stock
#   MF3 the 1800s window had no unstick path  -> dropped to 300s
#   MF4 the backstop index was silently optional
#   MF5 a stolen worker still clobbered the holder's accept metadata
# ===========================================================================


@pytest.fixture(autouse=True)
def _reset_index_probe():
    """The backstop-index check is cached process-wide; keep tests independent."""
    vd._GRN_UNIT_INDEX_STATE.update({"checked": False, "present": None})
    yield
    vd._GRN_UNIT_INDEX_STATE.update({"checked": False, "present": None})


# --- MF1: an unverifiable heartbeat must never read as "still ours" --------
def test_errored_heartbeat_after_a_takeover_does_not_keep_minting(wired, monkeypatch):
    """The panel's reproduction (34 units for a 20-unit receipt, HTTP 200).

    Worker A claims and mints 6 of 20, then wedges. The lock ages, worker B
    takes over and finishes. A wakes -- and its heartbeat write RAISES, because
    the same stepdown is still in progress. Round 3 read that as "keep minting".
    It must now stop."""
    grn_repo, stock_repo = wired(_grn(qty=20))
    monkeypatch.setattr(vd, "_GRN_ACCEPT_HEARTBEAT_SECONDS", 0)

    thief = {}

    def _steal():
        grn_repo.doc["accept_lock_at"] = _aged_lock()
        thief["out"] = _run(vd.accept_grn("GRN-1", _ADMIN))
        # From here on, A's heartbeat writes raise (the election is ongoing).
        grn_repo.collection.__class__ = _HeartbeatBoomColl
        grn_repo.collection.heartbeat_attempts = 0

    stock_repo.after_hook = _steal
    stock_repo.after_n = 6

    with pytest.raises(HTTPException) as err:
        _run(vd.accept_grn("GRN-1", _ADMIN))

    assert err.value.status_code == 503
    assert "still reserved" in err.value.detail
    assert len(stock_repo.rows) == 20, "20 accepted units -> exactly 20 rows"
    assert thief["out"]["units_added"] == 14


def test_errored_heartbeat_without_the_failclosed_guard_would_keep_minting(
    wired, monkeypatch
):
    """Sentinel: restore the round-3 fail-open (never give up on errors) and the
    same interleaving over-mints. Proves the abort is what stops it."""
    grn_repo, stock_repo = wired(_grn(qty=20), enforce_unique=False)
    monkeypatch.setattr(vd, "_GRN_ACCEPT_HEARTBEAT_SECONDS", 0)
    monkeypatch.setattr(vd, "_GRN_ACCEPT_HEARTBEAT_MAX_ERRORS", 10**9)

    def _steal():
        grn_repo.doc["accept_lock_at"] = _aged_lock()
        _run(vd.accept_grn("GRN-1", _ADMIN))
        grn_repo.collection.__class__ = _HeartbeatBoomColl
        grn_repo.collection.heartbeat_attempts = 0

    stock_repo.after_hook = _steal
    stock_repo.after_n = 6
    _run(vd.accept_grn("GRN-1", _ADMIN))

    assert len(stock_repo.rows) > 20, "fail-open heartbeat over-mints (round-3 bug)"


def test_persistent_heartbeat_errors_abort_even_with_no_takeover(wired, monkeypatch):
    """No takeover at all: if we simply cannot prove we still hold the claim, we
    stop rather than mint against a claim we cannot verify."""
    grn_repo, stock_repo = wired(_grn(qty=30), coll_cls=_HeartbeatBoomColl)
    monkeypatch.setattr(vd, "_GRN_ACCEPT_HEARTBEAT_SECONDS", 0)

    with pytest.raises(HTTPException) as err:
        _run(vd.accept_grn("GRN-1", _ADMIN))

    assert err.value.status_code == 503
    # Stopped after MAX_ERRORS consecutive failures, not after the whole line.
    assert grn_repo.collection.heartbeat_attempts == vd._GRN_ACCEPT_HEARTBEAT_MAX_ERRORS
    assert len(stock_repo.rows) == vd._GRN_ACCEPT_HEARTBEAT_MAX_ERRORS - 1
    # The claim was handed back so the operator can retry immediately.
    assert grn_repo.doc["accept_lock_token"] is None


def test_a_single_transient_heartbeat_error_is_tolerated_and_re_probes_at_once(
    wired, monkeypatch
):
    """One blip must not abort a healthy accept -- but the fence must re-arm on
    the very NEXT unit, not 25 units later, so the cadence counters are not
    reset by a failed write."""
    grn_repo, stock_repo = wired(_grn(qty=5))
    monkeypatch.setattr(vd, "_GRN_ACCEPT_HEARTBEAT_SECONDS", 0)

    real = grn_repo.collection.find_one_and_update
    calls = {"heartbeat": 0}

    def _flaky(flt, update):
        if set((update or {}).get("$set", {})) == {"accept_lock_at"}:
            calls["heartbeat"] += 1
            if calls["heartbeat"] == 1:
                raise RuntimeError("transient")
        return real(flt, update)

    grn_repo.collection.find_one_and_update = _flaky
    out = _run(vd.accept_grn("GRN-1", _ADMIN))

    assert out["units_added"] == 5
    assert len(stock_repo.rows) == 5
    # One heartbeat per unit: the failed one did NOT buy 25 units of silence.
    assert calls["heartbeat"] >= 5


# --- MF3: the 300s window, and the freeze paths that made 1800s indefensible
def test_stale_boundary_just_inside_the_window_is_refused(wired):
    held = (
        datetime.now() - timedelta(seconds=vd._GRN_ACCEPT_LOCK_STALE_SECONDS - 5)
    ).isoformat()
    grn_repo, _stock = wired(_grn(accept_lock_at=held, accept_lock_token="LIVE"))
    assert vd._claim_grn_for_accept(grn_repo, "GRN-1", "u1") is None
    assert grn_repo.doc["accept_lock_token"] == "LIVE"


def test_stale_boundary_just_past_the_window_is_taken_over(wired):
    aged = (
        datetime.now() - timedelta(seconds=vd._GRN_ACCEPT_LOCK_STALE_SECONDS + 5)
    ).isoformat()
    grn_repo, _stock = wired(_grn(accept_lock_at=aged, accept_lock_token="DEAD"))
    token = vd._claim_grn_for_accept(grn_repo, "GRN-1", "u1")
    assert token and token != "DEAD"


def test_hard_kill_midaccept_then_wait_the_window_completes_the_receipt(wired):
    """End-to-end: SIGKILL leaves 12 of 40 minted, a held lock and status
    PENDING. After the window the retry mints EXACTLY the missing 28."""
    grn_repo, stock_repo = wired(_grn(qty=40))
    dead_token = vd._claim_grn_for_accept(grn_repo, "GRN-1", "u-dead")
    assert dead_token
    for seq in range(12):
        stock_repo.create(
            {
                "source_type": "GRN",
                "source_id": "GRN-1",
                "product_id": "P1",
                "grn_line_index": 0,
                "line_unit_seq": seq,
            }
        )
    # The worker is killed: no release, no status flip.
    assert grn_repo.doc["status"] == "PENDING"
    grn_repo.doc["accept_lock_at"] = _aged_lock()

    out = _run(vd.accept_grn("GRN-1", _ADMIN))

    assert out["units_added"] == 28
    assert len(stock_repo.rows) == 40
    assert stock_repo.rejected == 0
    assert grn_repo.doc["status"] == "ACCEPTED"


def test_a_lost_reply_on_the_claim_leaves_no_orphan_lock(wired):
    """MF1's 503 must genuinely strand nothing: a claim write that APPLIED and
    then lost its reply used to leave our own token on the doc with nobody
    heartbeating it -- a freeze for the whole window on a receipt nobody is
    accepting."""
    grn_repo, stock_repo = wired(_grn(qty=3), coll_cls=_LostReplyColl)
    with pytest.raises(HTTPException) as err:
        _run(vd.accept_grn("GRN-1", _ADMIN))
    assert err.value.status_code == 503
    assert stock_repo.rows == []
    assert grn_repo.doc.get("accept_lock_token") is None
    assert grn_repo.doc.get("accept_lock_at") is None


def test_a_failed_lock_release_is_retried_and_logged(wired):
    """A silent failed release parks a PARTIALLY_ACCEPTED receipt behind the
    window with no crash involved -- it must at least be retried and LOUD."""
    grn_repo, _stock = wired(_grn())
    token = vd._claim_grn_for_accept(grn_repo, "GRN-1", "u1")
    attempts = {"n": 0}

    def _always_raise(_flt, _update):
        attempts["n"] += 1
        raise RuntimeError("write unavailable")

    grn_repo.collection.find_one_and_update = _always_raise
    with _captured_errors() as errors:
        vd._release_grn_accept_claim(grn_repo, "GRN-1", token)
    assert attempts["n"] == 2, "retried once"
    assert any("could NOT be written" in m for m in errors)


# --- MF4: the backstop must not be silently absent -------------------------
def test_missing_backstop_index_is_loud_but_does_not_block_receiving(wired):
    grn_repo, stock_repo = wired(_grn(qty=2))

    class _Coll:
        def index_information(self):
            return {"_id_": {}, "uniq_stock_unit_serial": {}}

    stock_repo.collection = _Coll()
    with _captured_errors() as errors:
        out = _run(vd.accept_grn("GRN-1", _ADMIN))
    assert vd._GRN_UNIT_INDEX_STATE["present"] is False
    assert any("STOCK BACKSTOP MISSING" in m for m in errors)
    # Loud, NOT blocking: a real delivery still gets received.
    assert out["units_added"] == 2


def test_present_backstop_index_is_detected_and_probed_once(wired):
    grn_repo, stock_repo = wired(_grn(qty=1))
    calls = {"n": 0}

    class _Coll:
        def index_information(self):
            calls["n"] += 1
            return {"_id_": {}, vd._GRN_UNIT_INDEX_NAME: {"unique": True}}

    stock_repo.collection = _Coll()
    _run(vd.accept_grn("GRN-1", _ADMIN))
    assert vd._GRN_UNIT_INDEX_STATE["present"] is True
    grn_repo.doc["status"] = "PARTIALLY_ACCEPTED"
    _run(vd.accept_grn("GRN-1", _ADMIN))
    assert calls["n"] == 1, "probed once per process, not per accept"


def test_duplicate_from_a_different_index_is_not_treated_as_a_skip():
    """Skipping any DuplicateKeyError would silently LOSE a real received unit
    the day another unique index lands on stock_units (there is already one on
    `serial`)."""
    ours = DuplicateKeyError("E11000 ... index: uniq_grn_line_unit_seq dup key")
    assert vd._is_grn_unit_duplicate(ours) is True

    foreign = DuplicateKeyError("E11000 ... index: uniq_stock_unit_serial dup key")
    foreign.details = {
        "errmsg": "E11000 ... index: uniq_stock_unit_serial",
        "keyPattern": {"serial": 1},
    }
    assert vd._is_grn_unit_duplicate(foreign) is False

    class _Repo:
        def create(self, _doc, raise_on_duplicate=False):
            raise foreign

    with pytest.raises(DuplicateKeyError):
        vd._grn_mint_unit(_Repo(), {"source_id": "GRN-1"}, True)


# --- MF5: a stolen worker must not clobber the holder's accept metadata ----
def test_stolen_worker_does_not_overwrite_the_holders_units_added(wired):
    grn_repo, stock_repo = wired(_grn(qty=2))
    grn_repo.doc.update(
        {
            "accept_lock_at": datetime.now().isoformat(),
            "accept_lock_token": "THIEF",
            "accepted_by": "u-thief",
            "units_added": 99,
            "unresolved_lines": [{"product_id": "PX"}],
        }
    )

    vd._accept_grn_claimed(
        "GRN-1", dict(grn_repo.doc), _ADMIN, grn_repo, stock_repo, None, "STOLEN"
    )

    # Status still advanced (stock is never stranded behind PENDING)...
    assert grn_repo.doc["status"] == "ACCEPTED"
    # ...but the real holder's numbers and lock are untouched.
    assert grn_repo.doc["units_added"] == 99
    assert grn_repo.doc["accepted_by"] == "u-thief"
    assert grn_repo.doc["unresolved_lines"] == [{"product_id": "PX"}]
    assert grn_repo.doc["accept_lock_token"] == "THIEF"


def test_stolen_worker_cannot_demote_an_accepted_receipt(wired):
    """PARTIALLY_ACCEPTED is a CLAIMABLE status, so demoting an ACCEPTED receipt
    would invite a third accept. The advance-only filter blocks it."""
    grn_repo, _stock = wired(_grn(status="ACCEPTED"))
    assert vd._advance_grn_terminal_status(grn_repo, "GRN-1", "PARTIALLY_ACCEPTED")
    assert grn_repo.doc["status"] == "ACCEPTED"


def test_status_flip_failure_is_reported_honestly(wired, monkeypatch):
    """Units on the shelf but the receipt did not advance -> say so, do not show
    a green 'accepted'."""
    grn_repo, stock_repo = wired(_grn(qty=3))
    monkeypatch.setattr(vd, "_advance_grn_terminal_status", lambda *a, **k: False)
    out = _run(vd.accept_grn("GRN-1", _ADMIN))
    assert out["status_flip_failed"] is True
    assert out["grn_status"] == "PENDING"
    assert out["units_added"] == 3
    assert "accept it again" in out["message"]


# --- MF2: void must refuse a receipt that already put stock on the shelf ----
def _void_env(monkeypatch, grn_repo, stock_repo):
    monkeypatch.setattr(vd, "get_grn_repository", lambda: grn_repo)
    monkeypatch.setattr(vd, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(vd, "get_audit_repository", lambda: None)


def test_void_refuses_a_pending_receipt_that_already_minted_units(wired, monkeypatch):
    """The sharpest finding: a crash mid-accept leaves PENDING + real units, and
    Void was the ONE button staff had. It orphaned those units and licensed a
    full re-mint under a NEW grn_id that the unique index cannot catch."""
    grn_repo, stock_repo = wired(_grn(qty=10))
    for seq in range(4):
        stock_repo.create(
            {
                "source_type": "GRN",
                "source_id": "GRN-1",
                "product_id": "P1",
                "grn_line_index": 0,
                "line_unit_seq": seq,
            }
        )
    _void_env(monkeypatch, grn_repo, stock_repo)

    with pytest.raises(HTTPException) as err:
        _run(vd.void_grn("GRN-1", _ADMIN))

    assert err.value.status_code == 409
    assert "4 unit(s)" in err.value.detail
    assert "Accept it again" in err.value.detail
    assert grn_repo.doc["status"] == "PENDING", "the receipt must not be voided"


def test_void_still_works_for_a_receipt_that_minted_nothing(wired, monkeypatch):
    grn_repo, stock_repo = wired(_grn(qty=10))
    _void_env(monkeypatch, grn_repo, stock_repo)
    out = _run(vd.void_grn("GRN-1", _ADMIN))
    assert grn_repo.doc["status"] == "VOID"
    assert out is not None


def test_void_fails_closed_when_the_stock_check_errors(wired, monkeypatch):
    grn_repo, stock_repo = wired(_grn(qty=10))

    def _boom(_flt):
        raise RuntimeError("count unavailable")

    monkeypatch.setattr(stock_repo, "count", _boom)
    _void_env(monkeypatch, grn_repo, stock_repo)

    with pytest.raises(HTTPException) as err:
        _run(vd.void_grn("GRN-1", _ADMIN))
    assert err.value.status_code == 503
    assert grn_repo.doc["status"] == "PENDING"


# ===========================================================================
# ROUND 5 -- the mirror image of the original bug, found by inverting the
# ordering, plus the ordinal hole and the retry amplifier.
#   MF1 void raced the mint and orphaned units on a VOID receipt
#   MF2 the flip reported SUCCESS over a VOID doc
#   MF3 express 500 was auto-retried into a second stock-bearing GRN
#   MF4 a real insert failure punched a permanent ordinal hole
#   MF5 the 503 copy never reached the operator
# ===========================================================================


# --- MF1 + MF2: void racing an in-flight accept ---------------------------
def test_void_racing_an_in_flight_accept_is_refused(wired, monkeypatch):
    """THE inverted ordering. The accept claims, then stalls before its first
    mint -- a window that in production spans the PO fetch, the product lookup,
    the cost backfill write and the already-minted count. A void landing there
    passes the status gate AND the stock gate (nothing is minted yet), and the
    accept then puts the whole delivery onto a VOID receipt.

    Void now takes the same guarded claim, so it simply loses."""
    grn_repo, stock_repo = wired(_grn(qty=20))
    _void_env(monkeypatch, grn_repo, stock_repo)

    voided = {}

    def _void_mid_claim():
        try:
            _run(vd.void_grn("GRN-1", _ADMIN))
        except HTTPException as exc:
            voided["exc"] = exc

    # Fires BEFORE the first unit is appended: claim taken, nothing minted.
    stock_repo.on_create = _void_mid_claim
    out = _run(vd.accept_grn("GRN-1", _ADMIN))

    assert voided["exc"].status_code == 409
    assert "being accepted right now" in voided["exc"].detail
    assert grn_repo.doc["status"] == "ACCEPTED", "the receipt must not be VOID"
    assert len(stock_repo.rows) == 20
    assert out["units_added"] == 20
    assert out.get("status_flip_failed") is None


def test_flip_over_a_void_doc_is_reported_as_a_failure_not_success(wired):
    """MF2 in isolation: even if a receipt reaches VOID by some other route,
    the terminal flip must NOT answer a green 'GRN accepted, stock added'.

    `_guarded_grn_write` returns plain False on a no-match, which is neither
    None nor _GRN_WRITE_ERROR -- round 4 fell through to `return True`."""
    grn_repo, _stock = wired(_grn(status="VOID"))
    assert vd._advance_grn_terminal_status(grn_repo, "GRN-1", "ACCEPTED") is False
    assert grn_repo.doc["status"] == "VOID"

    # ...but a receipt somebody else already advanced is still a success.
    ok_repo, _s2 = wired(_grn(status="ACCEPTED"))
    assert vd._advance_grn_terminal_status(ok_repo, "GRN-1", "ACCEPTED") is True


def test_units_never_end_up_behind_a_void_receipt(wired, monkeypatch):
    """End-to-end guarantee, asserted from the stock side: whichever of the two
    wins, the units and the receipt status can never disagree."""
    grn_repo, stock_repo = wired(_grn(qty=6))
    _void_env(monkeypatch, grn_repo, stock_repo)

    def _void_mid_claim():
        try:
            _run(vd.void_grn("GRN-1", _ADMIN))
        except HTTPException:
            pass

    stock_repo.on_create = _void_mid_claim
    _run(vd.accept_grn("GRN-1", _ADMIN))

    if stock_repo.rows:
        assert grn_repo.doc["status"] != "VOID"
    else:
        assert grn_repo.doc["status"] == "VOID"


def test_void_before_any_accept_still_wins_cleanly(wired, monkeypatch):
    """The claim must not make a legitimate void harder: with no accept in
    flight, void takes the claim, voids, and hands it straight back."""
    grn_repo, stock_repo = wired(_grn(qty=5))
    _void_env(monkeypatch, grn_repo, stock_repo)
    out = _run(vd.void_grn("GRN-1", _ADMIN))
    assert out["grn_status"] == "VOID"
    assert grn_repo.doc["status"] == "VOID"
    assert grn_repo.doc["accept_lock_token"] is None, "claim handed back"


def test_accept_after_a_void_is_refused(wired, monkeypatch):
    grn_repo, stock_repo = wired(_grn(qty=5))
    _void_env(monkeypatch, grn_repo, stock_repo)
    _run(vd.void_grn("GRN-1", _ADMIN))
    with pytest.raises(HTTPException) as err:
        _run(vd.accept_grn("GRN-1", _ADMIN))
    assert err.value.status_code == 400
    assert stock_repo.rows == []


# --- MF4: a real insert failure must not punch an ordinal hole -------------
def test_a_dropped_insert_does_not_punch_a_permanent_ordinal_hole(wired):
    """BaseRepository.create swallows every non-duplicate error and returns
    None. Round 4 advanced `seq` anyway, leaving ordinals [0,1,2,4..9]; the
    re-accept then computed already=9, tried seq=9, was rejected as a duplicate,
    and the 10th physical unit could NEVER be received -- silent, permanent, and
    a regression versus main, which healed on retry."""
    grn_repo, stock_repo = wired(_grn(qty=10))
    real_create = stock_repo.create

    def _drop_unit_three(doc, **kw):
        if doc.get("line_unit_seq") == 3:
            return None  # exactly what BaseRepository.create does on an error
        return real_create(doc, **kw)

    stock_repo.create = _drop_unit_three
    with pytest.raises(HTTPException) as err:
        _run(vd.accept_grn("GRN-1", _ADMIN))
    assert err.value.status_code == 503
    assert "will not be counted twice" in err.value.detail

    # The ordinals are still a CONTIGUOUS PREFIX -- no hole was punched.
    assert [r["line_unit_seq"] for r in stock_repo.rows] == [0, 1, 2]

    # ...so the retry heals completely.
    stock_repo.create = real_create
    out = _run(vd.accept_grn("GRN-1", _ADMIN))
    assert out["units_added"] == 7
    assert [r["line_unit_seq"] for r in stock_repo.rows] == list(range(10))
    assert stock_repo.rejected == 0


def test_a_duplicate_is_still_skipped_not_treated_as_a_failure(wired):
    """The other half of the three-way answer: an index-rejected ordinal must
    NOT abort the accept."""
    grn_repo, stock_repo = wired(_grn(qty=3))
    doc = {
        "source_type": "GRN",
        "source_id": "GRN-1",
        "product_id": "P1",
        "grn_line_index": 0,
        "line_unit_seq": 0,
    }
    assert vd._grn_mint_unit(stock_repo, dict(doc), True) is not None
    assert vd._grn_mint_unit(stock_repo, dict(doc), True) is vd._GRN_MINT_DUPLICATE
    # A genuine failure is a DIFFERENT, falsy answer.

    class _Dead:
        def create(self, _doc, raise_on_duplicate=False):
            return None

    assert not vd._grn_mint_unit(_Dead(), dict(doc), True)


# --- MF3: express must not be auto-retried into a second receipt -----------
def test_express_partial_is_a_conflict_not_a_server_error():
    """The frontend api client auto-retries every 5xx POST three times, and
    _create_grn_impl has no duplicate guard for STANDARD receipts -- so each
    retry created a NEW grn_id and minted the whole delivery again, which the
    per-(grn, line, unit) index cannot catch because it keys on source_id.

    Asserted at the source so nobody can quietly restore the 500."""
    import inspect

    src = inspect.getsource(vd.express_receive_grn)
    assert "status_code=500" not in src, "express must not raise a retryable 5xx"
    assert src.count("status_code=409") >= 3


# --- Ship-with-note hardening (all inside files already being edited) ------
def test_half_window_arm_aborts_even_before_max_errors(wired):
    """The `unconfirmed_for >= STALE/2` arm is the exact mechanism that
    justifies dropping the window to 300s -- 'a worker gives up at half the
    window, BEFORE a takeover is even permitted'. It had no behavioural test."""
    grn_repo, _stock = wired(_grn())
    token = vd._claim_grn_for_accept(grn_repo, "GRN-1", "u1")
    grn_repo.collection.__class__ = _HeartbeatBoomColl
    grn_repo.collection.heartbeat_attempts = 0

    stale_confirm = datetime.now() - timedelta(
        seconds=vd._GRN_ACCEPT_LOCK_STALE_SECONDS / 2 + 5
    )
    state = {
        "units": vd._GRN_ACCEPT_HEARTBEAT_UNITS,
        "at": datetime.now(),
        "errors": 1,  # well under MAX_ERRORS: the TIME arm must fire
        "confirmed_at": stale_confirm,
    }
    with pytest.raises(HTTPException) as err:
        vd._grn_accept_heartbeat_tick(grn_repo, "GRN-1", token, state)
    assert err.value.status_code == 503
    assert state["errors"] == 2, "one attempt, and it did not reset the run"


def test_abort_bounds_are_strictly_inside_the_takeover_window():
    """Replaces a tautology (x/2 < x) that would have passed with the half-
    window arm deleted and MAX_ERRORS set to 500."""
    worst_case_errors_arm = (
        vd._GRN_ACCEPT_HEARTBEAT_MAX_ERRORS * vd._GRN_ACCEPT_HEARTBEAT_SECONDS
    )
    assert worst_case_errors_arm < vd._GRN_ACCEPT_LOCK_STALE_SECONDS
    assert vd._GRN_ACCEPT_LOCK_STALE_SECONDS / 2 < vd._GRN_ACCEPT_LOCK_STALE_SECONDS
    # MAX_ERRORS is what bounds units minted against an unprovable claim.
    assert vd._GRN_ACCEPT_HEARTBEAT_MAX_ERRORS <= 5


def test_unverified_mint_tolerance_is_exactly_max_errors_minus_one(wired, monkeypatch):
    """MAX_ERRORS tolerates MAX_ERRORS-1 units minted while the claim cannot be
    proved. Pin the exact number so the tolerance cannot drift unnoticed."""
    grn_repo, stock_repo = wired(_grn(qty=30), coll_cls=_HeartbeatBoomColl)
    monkeypatch.setattr(vd, "_GRN_ACCEPT_HEARTBEAT_SECONDS", 0)
    with pytest.raises(HTTPException):
        _run(vd.accept_grn("GRN-1", _ADMIN))
    assert len(stock_repo.rows) == vd._GRN_ACCEPT_HEARTBEAT_MAX_ERRORS - 1


def test_headline_interleave_with_the_index_absent_is_pinned(wired, monkeypatch):
    """MF4 explicitly tolerates a MISSING index (ensure_indexes is fail-soft and
    receiving must never be blocked). In that state the fail-closed heartbeat
    alone still bounds the damage -- pin the exact count so the tolerance is
    visible rather than assumed."""
    grn_repo, stock_repo = wired(_grn(qty=20), enforce_unique=False)
    monkeypatch.setattr(vd, "_GRN_ACCEPT_HEARTBEAT_SECONDS", 0)

    def _steal():
        grn_repo.doc["accept_lock_at"] = _aged_lock()
        _run(vd.accept_grn("GRN-1", _ADMIN))
        grn_repo.collection.__class__ = _HeartbeatBoomColl
        grn_repo.collection.heartbeat_attempts = 0

    stock_repo.after_hook = _steal
    stock_repo.after_n = 6
    with pytest.raises(HTTPException):
        _run(vd.accept_grn("GRN-1", _ADMIN))

    # 20 real units + (MAX_ERRORS - 1) minted before the abort could fire.
    assert len(stock_repo.rows) == 20 + (vd._GRN_ACCEPT_HEARTBEAT_MAX_ERRORS - 1)


def test_void_fail_closed_on_the_production_count_branch(wired, monkeypatch):
    """The round-4 test monkeypatched repo.count, but production takes the
    collection.count_documents branch -- prove the 503 on the branch prod runs."""
    grn_repo, stock_repo = wired(_grn(qty=10))

    class _Coll:
        def count_documents(self, _flt):
            raise RuntimeError("mongo unavailable")

    stock_repo.collection = _Coll()
    _void_env(monkeypatch, grn_repo, stock_repo)

    with pytest.raises(HTTPException) as err:
        _run(vd.void_grn("GRN-1", _ADMIN))
    assert err.value.status_code == 503
    assert grn_repo.doc["status"] == "PENDING"
    assert grn_repo.doc["accept_lock_token"] is None, "claim handed back"
