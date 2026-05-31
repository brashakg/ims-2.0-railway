"""
IMS 2.0 — Store-credit balance must come from the authoritative field
======================================================================
Bug: the displayed/issuable balance was compute_balance(ledger entries) = the
SUM OF LEDGER DELTAS, which assumes the ledger started at zero. A customer with
a legacy `customer.store_credit` set BEFORE their first ledger entry would have
that opening balance silently dropped from the delta-sum, while REDEEM still
enforced against the full `store_credit` (try_debit_store_credit) -- a display-
vs-redeem divergence on real customer money.

Fix: `_current_credit_balance` trusts `customer.store_credit` (kept current by
both issue-sync and atomic redeem); the ledger is the audit trail only.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-store-credit-balance")


def test_balance_short_circuits_to_authoritative_field(monkeypatch):
    """With a customer doc present, the balance is the authoritative store_credit
    and the ledger is NEVER consulted (so a legacy opening balance can't be
    dropped by a delta-sum)."""
    import api.routers.customers as customers

    def _boom():
        raise AssertionError("_ledger_coll must not be consulted when the doc has store_credit")

    monkeypatch.setattr(customers, "_ledger_coll", _boom)
    # Legacy opening balance of 500; even if the ledger only had +100 of deltas,
    # the function must report 500 (and here proves it by never touching the ledger).
    assert customers._current_credit_balance("c1", {"store_credit": 500.0}) == 500.0
    assert customers._current_credit_balance("c2", {"store_credit": 0}) == 0.0


def test_falls_back_to_ledger_only_without_a_doc(monkeypatch):
    import api.routers.customers as customers

    # No customer doc -> the field can't be read, so fall back to the ledger
    # (here unavailable) -> 0.0. Never raises.
    monkeypatch.setattr(customers, "_ledger_coll", lambda: None)
    assert customers._current_credit_balance("c1", None) == 0.0


def test_compute_balance_helper_still_sums_deltas():
    """The audit-trail helper is unchanged: it sums signed deltas (used for the
    ledger view + tests), it's just no longer the source of truth for balance."""
    from api.services import store_credit_ledger as scl

    entries = [{"delta": 100.0}, {"delta": -30.0}, {"delta": 5.0}]
    assert scl.compute_balance(entries) == 75.0
