"""One guard document per payroll month, shared by the payroll run and the
accounting period lock, so that neither can commit across the other.

Why: the run checked "is the month locked?" and then wrote its DRAFT rows; the
lock checked "is there a DRAFT?" and then wrote its lock. Two requests could
each pass their check before the other wrote, leaving a DRAFT inside a locked
month: approve and re-run answer 423 there for good, so those wages never
reach the P&L. A second check on either side only narrows that window.

How (standalone Mongo, no transactions -- every step is ONE atomic
single-document update on ``payroll_period_guards``, ``_id`` = "YYYY-MM"):

* A saving run CLAIMS the month (``runs`` +1) before its first write, RENEWS
  the claim before every row it writes, and RELEASES it after the last. Each
  of those is refused once the guard is ``locked``, and each bumps ``rev``.
* The period lock reads the guard, refuses while a run is in flight, reads
  payroll, and then COMMITS with a filter on the ``rev`` it read. Any run that
  claimed, wrote or finished in between changed ``rev``, so the commit matches
  nothing and the lock is refused; nothing a run does after the commit is
  accepted, so no DRAFT can be saved behind the lock.

A run that died holding its claim stops counting after LEASE_SECONDS without
a renewal (a live run renews before every row). If such a run was only
stalled and comes back after the month was locked, its next renew / release
is refused and the caller removes the rows it inserted in that call. A row
it RE-writes needs no clean-up: that write only matches a row whose status is
still what the run read, and the lock is refused while any row is a DRAFT, so
a late re-write cannot turn an approved row into a DRAFT behind the lock.

The period lock row in ``period_locks`` is still what every other posting
reads; it is written straight after the commit. If the server dies between
the two, the guard is already ``locked``: payroll is refused, and pressing
Lock again finishes the job from the guard (see ``lock_period``).

Nothing in the app unlocks a month. If support ever removes a ``period_locks``
row by hand, this month's guard document must be removed with it.
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

COLLECTION = "payroll_period_guards"
# A live run renews before every row it writes, so this only has to outlast
# one row's compute + write. ponytail: a time lease, the one thing here that
# is not exact -- a single write stalled longer than this AND a crash before
# the caller's clean-up could still strand a draft; a replica-set transaction
# is the upgrade if that ever matters.
LEASE_SECONDS = 120.0

_OPEN = {"locked": {"$ne": True}}


def _key(year: int, month: int) -> str:
    return f"{int(year)}-{int(month):02d}"


def _coll(db):
    return db.get_collection(COLLECTION)


def _refuse_locked(year: int, month: int) -> None:
    raise HTTPException(
        status_code=423,
        detail=f"Accounting period {int(month):02d}/{int(year)} is locked; "
        "cannot post to a closed month.",
    )


def _touch(db, year: int, month: int, runs_delta: int) -> bool:
    """One atomic step of a run on an OPEN guard. False = the guard is locked
    (or gone): the month closed under this run."""
    return (
        _coll(db).find_one_and_update(
            {"_id": _key(year, month), **_OPEN},
            {"$inc": {"rev": 1, "runs": runs_delta}, "$set": {"beat": time.time()}},
        )
        is not None
    )


def claim_run(db, year: int, month: int) -> None:
    """Start a saving payroll run for the month. Raises 423 when the month is
    locked (or being locked). Call before the run's first write."""
    for _ in range(3):
        if _touch(db, year, month, +1):
            return
        guard = read(db, year, month)
        if guard is not None:
            if guard.get("locked"):
                _refuse_locked(year, month)
            continue  # created open by another run in the same instant
        try:
            _coll(db).insert_one(
                {
                    "_id": _key(year, month),
                    "year": int(year),
                    "month": int(month),
                    "rev": 1,
                    "runs": 1,
                    "beat": time.time(),
                    "locked": False,
                }
            )
            return
        except DuplicateKeyError:
            continue  # someone created the guard in the same instant
    raise HTTPException(
        status_code=409,
        detail="Payroll for this month is being changed by someone else right "
        "now. Wait a moment and run it again.",
    )


def renew_run(db, year: int, month: int) -> bool:
    """Before each row a run writes. False = the month was locked under it."""
    return _touch(db, year, month, 0)


def release_run(db, year: int, month: int) -> bool:
    """After a run's last write. False = the month was locked under it."""
    return _touch(db, year, month, -1)


def read(db, year: int, month: int) -> Optional[dict]:
    return _coll(db).find_one({"_id": _key(year, month)})


def run_in_flight(guard: Optional[dict]) -> bool:
    """A saving run holds the month and has renewed within the lease."""
    return bool(
        guard
        and (guard.get("runs") or 0) > 0
        and time.time() - float(guard.get("beat") or 0) < LEASE_SECONDS
    )


def commit_lock(db, year: int, month: int, seen: Optional[dict], fields: dict) -> bool:
    """Lock the guard if NOTHING touched it since ``seen`` was read (``seen``
    is None when no guard existed). False = a payroll run claimed, wrote or
    finished in between, or someone else locked: the caller must not lock."""
    if run_in_flight(seen):
        return False
    locked = {"locked": True, "runs": 0, **fields}
    if seen is None:
        try:
            _coll(db).insert_one(
                {"_id": _key(year, month), "year": int(year), "month": int(month), "rev": 1, **locked}
            )
            return True
        except DuplicateKeyError:
            return False
    return (
        _coll(db).find_one_and_update(
            {"_id": _key(year, month), "rev": seen.get("rev"), **_OPEN},
            {"$set": locked, "$inc": {"rev": 1}},
        )
        is not None
    )
