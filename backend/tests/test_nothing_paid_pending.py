"""'Nothing was paid' says the hold is PENDING until it is actually released (D59).

Codex round 6 of PR #1393 (14 Nov 2026): the shared "Nothing was paid" answer of
Complete record said "its units and the bill were released" while the age guard
(_give_back: a step another pass claimed less than the reconciler's timeout ago
is "still acting") kept the hold. The lead's decision D59 (option B): the answer
says the release is PENDING until it is done. Stored rows are read, not only the
reply. No emoji (cp1252).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from tests.test_pos_money_doors import _stored, returns_router, till  # noqa: E402,F401
from tests.test_pos_money_doors_r9 import _ago  # noqa: E402
from tests.test_pos_money_doors_r12 import tasks  # noqa: E402,F401
from tests.test_pos_money_doors_r13 import _bill_with_hold, _complete  # noqa: E402


def test_a_kept_hold_is_reported_pending_never_released(till, monkeypatch, tasks):
    released = []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a) or True)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: True)
    # Another pass claimed the line release a minute ago: still acting, so the hold stays.
    _bill_with_hold(till, "RET-YOUNG", claims_released=True, claims_released_at=_ago(1))

    first = _complete("RET-YOUNG", nothing_paid=True)
    assert "RET-YOUNG" in (_stored(till).get("refund_holds") or {}), "the age guard keeps the hold"
    assert "PENDING" in first["message"] and "released," not in first["message"], first["message"]
    assert first["hold_released"] is False
    replay = _complete("RET-YOUNG", nothing_paid=True)
    assert replay["hold_released"] is False and "PENDING" in replay["message"]
    assert "RET-YOUNG" in (_stored(till).get("refund_holds") or {})

    # The claim ages past the timeout: the next press finishes it and says so.
    till["db"].get_collection("orders").update_one(
        {}, {"$set": {"refund_holds.RET-YOUNG.claims_released_at": _ago(30)}})
    done = _complete("RET-YOUNG", nothing_paid=True)
    assert done["hold_released"] is True and "PENDING" not in done["message"]
    assert "RET-YOUNG" not in (_stored(till).get("refund_holds") or {})


def test_a_hold_released_at_once_still_says_released(till, monkeypatch, tasks):
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: True)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: True)
    _bill_with_hold(till, "RET-NOW")
    done = _complete("RET-NOW", nothing_paid=True)
    assert done["hold_released"] is True
    assert "its units and the bill were released" in done["message"]
    assert "RET-NOW" not in (_stored(till).get("refund_holds") or {})
