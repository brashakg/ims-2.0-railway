"""
IMS 2.0 - #1397 merged with #1423: this PR's own readers of a refund's claim follow D118.
==========================================================================================
Decision D118 (#1423): a refund's claim is read strictly -- shopify_refund.
_unfinished_claim RAISES on a failed read, and no answer is never "nothing
open". #1397 has two readers of its own that main does not: the Confirm's
unfinished-note (online_store_refund_reviews._unfinished_note) and the
scheduled finisher (finish_unfinished_refunds). Both relied on the old
fail-soft None. After the merge a failed read there would turn the Confirm
into a 500 (its row stamped by no one) and stop the whole money tick.

Now: the Confirm answers 422 with the 'could not tell, do not book it by hand'
note on the row (it stays open), and the tick skips that refund (no task
claiming it unfinished, no crash) and looks again next time. Nothing is
booked by either.

No emoji (Windows cp1252).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_refund_review_unpaid_credit import env  # noqa: E402,F401
from tests.test_shopify_refund_points_paid import (  # noqa: E402,F401
    _confirm,
    _queued,
    _stored,
    points,
)

pytestmark = pytest.mark.usefixtures("gst_numbers")


def test_a_confirm_whose_claim_cannot_be_read_after_a_duplicate_says_do_not_book_it(env, points, monkeypatch):
    """Another run's fresh claim holds the refund: the post answers
    'duplicate'. The returns read right after fails. Before: the unfinished
    note's read raised -- a 500, the row never stamped."""
    from api.services import shopify_refund as S

    reviews, review_id = _queued(env)
    coll = env.get_collection("returns")
    # Another run's FRESH claim (its run may still be alive): a duplicate.
    coll.insert_one({"return_id": "RET-OTHER", "shopify_refund_id": "700001", "status": "PENDING",
                     "credit_note_issued": False, "source": "shopify",
                     "created_at": S.datetime.now(S.timezone.utc).isoformat()})
    real_post, real_find = S.post_from_review, coll.find_one

    def post_then_blind(db, row):
        out = real_post(db, row)

        def blind(*a, **k):
            raise RuntimeError("socket reset on the returns read")

        monkeypatch.setattr(coll, "find_one", blind)
        return out

    monkeypatch.setattr(S, "post_from_review", post_then_blind)
    code, detail = _confirm(review_id)
    monkeypatch.setattr(coll, "find_one", real_find)
    monkeypatch.setattr(S, "post_from_review", real_post)

    assert (code, detail) == (422, S.CLAIM_UNKNOWN_NOTE), (code, detail)
    row = reviews.find_one({"review_id": review_id})
    assert (row["status"], row["resolved"], row["note"]) == ("CREDIT_FAILED", False, S.CLAIM_UNKNOWN_NOTE)
    nothing = _stored(env, points)
    assert nothing["credit_rows"] == [] and nothing["points"] == (50, 100, 50), nothing


def test_the_tick_skips_a_refund_whose_claim_cannot_be_read_and_never_stops(env, monkeypatch):
    """A stale unfinished claim the tick cannot post (no open row holds it),
    and the read that asks whether it is still unfinished fails. Before: the
    raise left the 'never raises' tick (and the money tick around it)."""
    from api.routers.online_store_refund_reviews import finish_unfinished_refunds
    from api.services import shopify_refund as S

    claims = [{"shopify_refund_id": "RF-A", "return_id": "RET-A"},
              {"shopify_refund_id": "RF-B", "return_id": "RET-B"}]
    raised = []

    def unreadable(_db, refund_id):
        if refund_id == "RF-A":
            raise RuntimeError("socket reset on the returns read")
        return {"shopify_refund_id": refund_id, "status": "PENDING"}

    monkeypatch.setattr(S, "stale_unfinished_claims", lambda _db: claims)
    monkeypatch.setattr(S, "_unfinished_claim", unreadable)
    monkeypatch.setattr(S, "raise_unfinished_task", lambda claim: raised.append(claim["shopify_refund_id"]))

    assert finish_unfinished_refunds() == {"finished": 0, "unresolved": 1}
    # RF-A: unread, so no task saying it is unfinished; RF-B still gets its one task.
    assert raised == ["RF-B"]
