"""
IMS 2.0 - Feature #27 Refund approval matrix -- intent tests.

The matrix decides, for a refund, WHICH E4 approval tier (if any) is required
before it can be recorded -- keyed on amount band x reason x requesting role,
configurable per store/entity via E2, and DARK by default (flag off) so it can
ship without touching live refunds.

Covered:
  - pure required_tier across role-floor auto-clear, amount bands, reason bump, clamp,
  - int_to_e4_tier mapping,
  - required_tier_for_refund: DARK (flag off) -> None; ENABLED -> resolves tier;
    fail-OPEN on resolver error,
  - the returns gate: dark -> no-op (None); enabled + tier>0 + no token -> 403;
    enabled + valid token -> consumes an E4 approval BOUND to this refund
    (store_id + context.order_id) so a token for refund A can't authorize refund B.

No whole-JSON substring asserts; every DB/E2/E4 touch is monkeypatched. No emoji.
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import refund_approval_matrix as ram  # noqa: E402

M = ram.DEFAULT_MATRIX


# --------------------------------------------------------------------------- #
# Pure required_tier
# --------------------------------------------------------------------------- #
def test_role_floor_auto_clears_below_floor():
    # STORE_MANAGER floor is Rs 5,000 -> a Rs 4,000 refund needs no approval.
    assert ram.required_tier(400000, "DEFECTIVE", "STORE_MANAGER", M) == 0
    # ADMIN floor None -> never needs approval, even huge.
    assert ram.required_tier(9999999, "GOODWILL", "ADMIN", M) == 0


def test_cashier_escalating_bands():
    # SALES_CASHIER floor 0 -> bands apply from rupee one.
    assert ram.required_tier(50000, "DEFECTIVE", "SALES_CASHIER", M) == 0      # <= Rs 1,000
    assert ram.required_tier(300000, "DEFECTIVE", "SALES_CASHIER", M) == 1     # <= Rs 5,000
    assert ram.required_tier(1500000, "DEFECTIVE", "SALES_CASHIER", M) == 2    # <= Rs 20,000
    assert ram.required_tier(5000000, "DEFECTIVE", "SALES_CASHIER", M) == 3    # above


def test_reason_bump_and_clamp():
    # GOODWILL bumps +1: a Rs 5,000 (band tier 1) goodwill -> tier 2.
    assert ram.required_tier(500000, "GOODWILL", "SALES_CASHIER", M) == 2
    # bump cannot exceed max_tier (3): top band + bump stays clamped at 3.
    assert ram.required_tier(9999999, "PRICE_MATCH", "SALES_CASHIER", M) == 3
    # case-insensitive reason
    assert ram.required_tier(500000, "goodwill", "SALES_CASHIER", M) == 2


def test_negative_and_garbage_amounts_safe():
    assert ram.required_tier(-100, "DEFECTIVE", "SALES_CASHIER", M) == 0
    assert ram.required_tier("garbage", "DEFECTIVE", "SALES_CASHIER", M) == 0  # type: ignore[arg-type]


def test_int_to_e4_tier_mapping():
    assert ram.int_to_e4_tier(0) is None         # no approval
    # non-zero tiers map to a non-None E4 tier string
    assert ram.int_to_e4_tier(1) is not None
    assert ram.int_to_e4_tier(3) is not None


# --------------------------------------------------------------------------- #
# required_tier_for_refund (DB-side resolver) -- DARK by default
# --------------------------------------------------------------------------- #
def test_resolver_dark_when_flag_off(monkeypatch):
    monkeypatch.setattr(ram, "matrix_enabled", lambda scope=None: False)
    # even a huge cashier goodwill refund -> None (no gate) while dark
    assert ram.required_tier_for_refund(9999999, "GOODWILL", "SALES_CASHIER",
                                        store_id="BV-01") is None


def test_resolver_enabled_resolves_tier(monkeypatch):
    monkeypatch.setattr(ram, "matrix_enabled", lambda scope=None: True)
    monkeypatch.setattr(ram, "resolve_matrix", lambda scope=None: M)
    out = ram.required_tier_for_refund(300000, "DEFECTIVE", "SALES_CASHIER", store_id="BV-01")
    assert out == ram.int_to_e4_tier(1)   # tier 1 string
    # below the role floor (manager, small) -> None even when enabled
    assert ram.required_tier_for_refund(100000, "DEFECTIVE", "STORE_MANAGER",
                                        store_id="BV-01") is None


def test_resolver_fails_open_on_error(monkeypatch):
    monkeypatch.setattr(ram, "matrix_enabled", lambda scope=None: True)
    monkeypatch.setattr(ram, "resolve_matrix", lambda scope=None: M)

    def _boom(*a, **k):
        raise RuntimeError("matrix corrupt")

    monkeypatch.setattr(ram, "required_tier", _boom)
    # a resolver error must FAIL OPEN (None) so a transient fault can't wedge the till
    assert ram.required_tier_for_refund(300000, "DEFECTIVE", "SALES_CASHIER",
                                        store_id="BV-01") is None


def test_matrix_enabled_dark_default(monkeypatch):
    # get_policy returns the passed default for an unset key -> flag default False.
    from api.services import policy_engine as pe
    monkeypatch.setattr(pe, "get_policy", lambda key, scope=None, *, default=None: default)
    assert ram.matrix_enabled({"store_id": "BV-01"}) is False


# --------------------------------------------------------------------------- #
# The returns gate (_gate_refund_approval_matrix)
# --------------------------------------------------------------------------- #
class _Body:
    def __init__(self):
        self.refund_reason = "DEFECTIVE"
        self.items = []
        self.refund_approval_token = None
        self.refund_approval_request_id = None


def _gate_args(body, **over):
    base = dict(
        body=body, net_amount=3000.0, store_id="BV-01", entity_id=None,
        resolved_order_id="ORD-1",
        current_user={"user_id": "u1", "roles": ["SALES_CASHIER"], "activeRole": "SALES_CASHIER"},
    )
    base.update(over)
    return base


def test_gate_dark_is_noop(monkeypatch):
    from api.routers import returns as r
    monkeypatch.setattr(
        "api.services.refund_approval_matrix.required_tier_for_refund",
        lambda *a, **k: None,  # dark / below floor
    )
    out = r._gate_refund_approval_matrix(**_gate_args(_Body()))
    assert out is None  # no approval required, refund proceeds unchanged


def test_gate_blocks_without_token_when_tier_required(monkeypatch):
    from api.routers import returns as r
    from fastapi import HTTPException
    monkeypatch.setattr(
        "api.services.refund_approval_matrix.required_tier_for_refund",
        lambda *a, **k: "admin",  # a tier IS required
    )
    body = _Body()  # no token / no request_id
    with pytest.raises(HTTPException) as ei:
        r._gate_refund_approval_matrix(**_gate_args(body))
    assert ei.value.status_code == 403


def test_gate_consumes_refund_bound_token(monkeypatch):
    """A supplied token is consumed via E4 BOUND to this refund -- the gate must
    pass the store + order context so a token for refund A can't clear refund B."""
    from api.routers import returns as r
    monkeypatch.setattr(
        "api.services.refund_approval_matrix.required_tier_for_refund",
        lambda *a, **k: "admin",
    )
    monkeypatch.setattr(r, "_get_db", lambda: object())

    captured = {}

    class _FakeEngine:
        def __init__(self, db=None):
            pass

        def consume_approval(self, **kwargs):
            captured.update(kwargs)
            return {"ok": True, "request": {"reviewed_by": "mgr-9"}}

    monkeypatch.setattr("api.services.approvals.ApprovalEngine", _FakeEngine)
    monkeypatch.setattr(r, "_resolve_user_name", lambda uid: uid)
    body = _Body()
    body.refund_approval_request_id = "AR-1"
    out = r._gate_refund_approval_matrix(**_gate_args(body))
    # The gate now returns a dict {approval_by, approval_request_id, status}.
    assert out["approval_by"] == "mgr-9"                    # approver stamped
    assert out["approval_status"] == "APPROVED"
    assert captured.get("action_type") == "REFUND_APPROVAL_MATRIX"
    # bound to THIS refund: the store + order ride into consume so a foreign
    # token (different store/order) would be rejected by the E4 engine.
    assert captured.get("expected_store_id") == "BV-01"
    ctx = captured.get("expected_context") or {}
    assert ctx.get("order_id") == "ORD-1" or ctx.get("refund_id") == "ORD-1"
    # F27 P1 fix: the matrix tier MUST ride into consume so a weaker (amount-only)
    # token can't satisfy a reason/role-escalated matrix tier.
    assert captured.get("min_tier") == "admin"


# --------------------------------------------------------------------------- #
# consume_approval min_tier guard (the P1 fix) -- a weaker token is rejected
# --------------------------------------------------------------------------- #
class _ApprovalColl:
    """Minimal fake supporting consume_approval's two find_one_and_update calls
    (consume flip + rollback) and eq / $gt matching."""

    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]

    def _ok(self, d, q):
        for k, v in q.items():
            if isinstance(v, dict) and "$gt" in v:
                if not (d.get(k) is not None and d.get(k) > v["$gt"]):
                    return False
            elif d.get(k) != v:
                return False
        return True

    def find_one_and_update(self, q, u, return_document=None, **k):
        for d in self.docs:
            if self._ok(d, q):
                for kk, vv in (u.get("$set") or {}).items():
                    d[kk] = vv
                return dict(d)
        return None

    def find_one(self, q):
        for d in self.docs:
            if self._ok(d, q):
                return dict(d)
        return None


def _engine_with(doc):
    from api.services.approvals import ApprovalEngine
    eng = ApprovalEngine(db=None)
    coll = _ApprovalColl([doc])
    eng._coll = lambda: coll                      # type: ignore[assignment]
    eng._consume_failure = lambda ident: {"ok": False, "error": "not_consumable"}
    eng._write_audit = lambda *a, **k: "aud-1"    # type: ignore[assignment]
    eng._set = lambda *a, **k: None               # type: ignore[assignment]
    return eng, coll


def _approved_token(tier):
    from datetime import datetime, timedelta, timezone
    return {
        "request_id": "AR-9", "approval_token": "tok-9",
        "status": "APPROVED", "consumed": False,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "action_type": "REFUND_APPROVAL_MATRIX", "amount": 100000.0,
        "required_tier": tier, "store_id": "BV-01", "context": {"order_id": "ORD-1"},
    }


def test_consume_rejects_token_below_required_tier():
    eng, coll = _engine_with(_approved_token("auto"))
    res = eng.consume_approval(
        consumed_by="mgr", action_type="REFUND_APPROVAL_MATRIX", request_id="AR-9",
        amount=1500.0, expected_store_id="BV-01",
        expected_context={"order_id": "ORD-1"}, min_tier="admin",  # matrix demands admin
    )
    assert res["ok"] is False and res["error"] == "tier_too_low"
    # rolled back -> not silently burned
    assert coll.docs[0]["status"] == "APPROVED" and coll.docs[0]["consumed"] is False


def test_consume_accepts_token_meeting_required_tier():
    eng, _ = _engine_with(_approved_token("admin"))
    res = eng.consume_approval(
        consumed_by="mgr", action_type="REFUND_APPROVAL_MATRIX", request_id="AR-9",
        amount=1500.0, expected_store_id="BV-01",
        expected_context={"order_id": "ORD-1"}, min_tier="admin",
    )
    assert res["ok"] is True


def test_consume_min_tier_none_is_backcompat():
    eng, _ = _engine_with(_approved_token("auto"))
    res = eng.consume_approval(
        consumed_by="mgr", action_type="REFUND_APPROVAL_MATRIX", request_id="AR-9",
        amount=1500.0,  # no min_tier -> historical consumers unaffected
    )
    assert res["ok"] is True


# --------------------------------------------------------------------------- #
# F27 original-tender hard-lock (_gate_original_tender) -- DECISIONS sec 6:
# refunds always go back to the original tender (configurable per scope).
# --------------------------------------------------------------------------- #
class _TenderBody:
    def __init__(self, refund_method=None, return_type="RETURN"):
        self.return_type = return_type
        self.refund_method = refund_method


def _set_tender_policy(monkeypatch, enforce: bool):
    """Force the refund.original_tender_enforce policy read to `enforce`."""
    monkeypatch.setattr(
        "api.services.policy_engine.get_policy",
        lambda key, scope=None, *, default=None: (
            enforce if key == "refund.original_tender_enforce" else default
        ),
    )


def test_normalize_tender_folds_card_synonyms():
    from api.routers import returns as r
    assert r._normalize_tender("CREDIT_CARD") == r._normalize_tender("card")
    assert r._normalize_tender("DEBIT_CARD") == "CARD"
    assert r._normalize_tender("neft") == "BANK_TRANSFER"
    assert r._normalize_tender("UPI") == "UPI"
    assert r._normalize_tender(None) == ""


def test_tender_gate_blocks_mismatch_when_enforced(monkeypatch):
    from api.routers import returns as r
    from fastapi import HTTPException
    _set_tender_policy(monkeypatch, True)
    order = {"payment_method": "CARD"}
    body = _TenderBody(refund_method="CASH")  # cashier reroutes card sale to cash
    with pytest.raises(HTTPException) as ei:
        r._gate_original_tender(body, order=order, store_id="BV-01", entity_id=None)
    assert ei.value.status_code == 422
    assert ei.value.detail.get("reason") == "TENDER_MISMATCH"


def test_tender_gate_allows_matching_tender(monkeypatch):
    from api.routers import returns as r
    _set_tender_policy(monkeypatch, True)
    order = {"payments": [{"method": "UPI"}]}
    body = _TenderBody(refund_method="UPI")  # same tender -> allowed
    assert r._gate_original_tender(body, order=order, store_id="BV-01", entity_id=None) is None


def test_tender_gate_card_synonym_is_not_a_mismatch(monkeypatch):
    from api.routers import returns as r
    _set_tender_policy(monkeypatch, True)
    order = {"payment_method": "CREDIT_CARD"}
    body = _TenderBody(refund_method="DEBIT_CARD")  # both fold to CARD
    assert r._gate_original_tender(body, order=order, store_id="BV-01", entity_id=None) is None


def test_tender_gate_noop_when_policy_off(monkeypatch):
    from api.routers import returns as r
    _set_tender_policy(monkeypatch, False)  # override permitted
    order = {"payment_method": "CARD"}
    body = _TenderBody(refund_method="CASH")
    assert r._gate_original_tender(body, order=order, store_id="BV-01", entity_id=None) is None


def test_tender_gate_permissive_without_chosen_method(monkeypatch):
    from api.routers import returns as r
    _set_tender_policy(monkeypatch, True)
    order = {"payment_method": "CARD"}
    body = _TenderBody(refund_method=None)  # defaults to original downstream
    assert r._gate_original_tender(body, order=order, store_id="BV-01", entity_id=None) is None


def test_tender_gate_permissive_on_unknown_original(monkeypatch):
    from api.routers import returns as r
    _set_tender_policy(monkeypatch, True)
    body = _TenderBody(refund_method="CASH")
    # order=None -> _order_payment_method returns SOURCE -> cannot enforce
    assert r._gate_original_tender(body, order=None, store_id="BV-01", entity_id=None) is None


def test_tender_gate_skips_exchange_and_credit_note(monkeypatch):
    from api.routers import returns as r
    _set_tender_policy(monkeypatch, True)
    order = {"payment_method": "CARD"}
    for rt in ("EXCHANGE", "CREDIT_NOTE"):
        body = _TenderBody(refund_method="CASH", return_type=rt)
        assert r._gate_original_tender(body, order=order, store_id="BV-01", entity_id=None) is None
