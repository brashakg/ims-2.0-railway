"""
IMS 2.0 - Day-End drawer nets money refunds off the EXPLICIT refund tender
==========================================================================
Money-panel FIX-FIRST (PR #964 round 2). The first cut GUESSED the refund
tender from the original sale (returns.refund_method -> payments[0]), which the
panel proved regresses split-tender days by execution:

  * UPI-first split (UPI 2000 + CASH 3000) with a CASH refund -> the refund was
    keyed to UPI, so the drawer STILL showed a false shortage;
  * CASH-first split (CASH 2000 + CARD 6000) with a full refund -> the WHOLE
    refund was cut from CASH -> a NEGATIVE expected drawer / false overage.

The redesign: the Returns screen CAPTURES the tender(s) actually used
(returns.refund_tenders for a RETURN; returns.collect_method for a
COLLECT-direction EXCHANGE cash-in), and BOTH readers net off THAT only. A
return with no explicit breakdown is UNKNOWN and netted NOWHERE (never
fabricated). reconcile_window keeps its payments-only contract by default
(include_returns=False); only eod_tally.compute_expected opts in.

Every assertion below is a real rupee/paisa number, driven through the shipped
readers against a faithful in-memory fake Mongo (query matcher mirrors
test_e5_tender_recon incl. Mongo type-bracketing). MULTI-payment order fixtures
are used for the split-tender cases the panel flagged.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import returns as returns_router  # noqa: E402
from api.routers.finance import _cash_sales_for_window  # noqa: E402
from api.services import bank_reconciliation as bankrec  # noqa: E402
from api.services import eod_tally  # noqa: E402
from api.services import tender_reconciliation as trec  # noqa: E402
from api.services.cash_register import compute_expected_cash  # noqa: E402


# ============================================================================
# Faithful in-memory fake Mongo (query matching mirrors test_e5_tender_recon)
# ============================================================================


def _cmp_op(actual: Any, op: str, expected: Any) -> bool:
    if actual is None and op in ("$gt", "$gte", "$lt", "$lte"):
        return False
    try:
        if op == "$gt":
            return actual > expected
        if op == "$gte":
            return actual >= expected
        if op == "$lt":
            return actual < expected
        if op == "$lte":
            return actual <= expected
        if op == "$ne":
            return actual != expected
        if op == "$in":
            return actual in expected
    except TypeError:
        # str-vs-datetime comparison: real Mongo type-brackets (no match).
        return False
    return False


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in query.items():
        if k == "$or":
            if not any(_matches(doc, sub) for sub in v):
                return False
            continue
        actual = doc.get(k)
        if isinstance(v, dict) and any(str(kk).startswith("$") for kk in v):
            for op, expected in v.items():
                if not _cmp_op(actual, op, expected):
                    return False
            continue
        if actual != v:
            return False
    return True


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def insert(self, doc: Dict[str, Any]) -> None:
        self.docs.append(dict(doc))

    def find(self, query=None, projection=None):
        return iter([dict(d) for d in self.docs if _matches(d, query or {})])

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if _matches(d, query or {}):
                return dict(d)
        return None


class FakeDB:
    def __init__(self):
        self._collections: Dict[str, FakeCollection] = {}

    def get_collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]


@pytest.fixture()
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture(autouse=True)
def _pin_entity_resolver(monkeypatch):
    """reconcile_window resolves the tender map via the store->entity resolver;
    pin it so no test touches the real (absent) DB through E2's cache."""
    monkeypatch.setattr(
        "api.services.policy_engine._resolve_entity_id", lambda store_id: None
    )


# Yesterday / today windows (the key wrong-day scenario).
Y_START, Y_END = "2026-08-08T00:00:00", "2026-08-08T23:59:59"
T_START, T_END = "2026-08-09T00:00:00", "2026-08-09T23:59:59"


def _order(store: str, created: Any, payments: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"store_id": store, "created_at": created, "payments": payments}


def _pay(method: str, amount: float) -> Dict[str, Any]:
    return {"method": method, "amount": amount}


def _return_doc(
    store: str,
    created_iso: str,
    *,
    rtype: str = "RETURN",
    refund_tenders: Optional[List[Dict[str, Any]]] = None,
    collect_method: Optional[str] = None,
    collect_amount: Optional[float] = None,
    credit_amount: Optional[float] = None,
    settlement: Optional[Dict[str, Any]] = None,
    status: str = "COMPLETED",
    historical: Optional[bool] = None,
) -> Dict[str, Any]:
    """Mirrors the doc create_return persists (created_at = ISO STRING)."""
    doc: Dict[str, Any] = {
        "return_id": f"RET-{created_iso}",
        "store_id": store,
        "return_type": rtype,
        "refund_tenders": refund_tenders,
        "collect_method": collect_method,
        "collect_amount": collect_amount,
        "credit_amount": credit_amount,
        "settlement": settlement,
        "status": status,
        "created_at": created_iso,
    }
    if historical is not None:
        doc["historical"] = historical
    return doc


# ============================================================================
# Panel scenario 1: single-tender cash refund nets CASH (and the right day).
# ============================================================================


def test_single_tender_cash_refund_nets_cash(db):
    db.get_collection("orders").insert(
        _order("S1", datetime(2026, 8, 8, 11, 0, 0), [_pay("CASH", 5000.0)])
    )
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T14:30:00.123456",
            refund_tenders=[{"method": "CASH", "amount": 1200.0}],
        )
    )
    # The refund lands in TODAY's window (its own day), not the sale day.
    t_sales, t_refunds = _cash_sales_for_window(db, "S1", T_START, T_END)
    assert (t_sales, t_refunds) == (0.0, 1200.0)
    y_sales, y_refunds = _cash_sales_for_window(db, "S1", Y_START, Y_END)
    assert (y_sales, y_refunds) == (5000.0, 0.0)
    # Same-day drawer math sanity: opening + sales - refund (no false SHORT).
    assert compute_expected_cash(1000.0, 5000.0, 1200.0, 0.0, 0.0) == 4800.0


# ============================================================================
# Panel scenario 2: UPI-first split + CASH refund nets CASH (REGRESSION fixed).
#   Order [UPI 2000, CASH 3000]; cashier hands back Rs 1000 CASH.
# ============================================================================


def test_upi_first_split_cash_refund_nets_cash_not_upi(db):
    db.get_collection("orders").insert(
        _order(
            "S1", datetime(2026, 8, 9, 10, 0, 0),
            [_pay("UPI", 2000.0), _pay("CASH", 3000.0)],
        )
    )
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T15:00:00",
            refund_tenders=[{"method": "CASH", "amount": 1000.0}],
        )
    )
    # Drawer sees the CASH refund (the false shortage the PR exists to kill).
    sales, refunds = _cash_sales_for_window(db, "S1", T_START, T_END)
    assert sales == 3000.0
    assert refunds == 1000.0
    # And the UPI gateway leg is UNTOUCHED (no phantom -1000 into bank recon).
    recon = trec.reconcile_window(db, "S1", T_START, T_END, include_returns=True,
                                  tender_map={})
    assert recon["by_mode"]["UPI"]["net"] == 2000.0
    assert recon["by_mode"]["UPI"]["refunded"] == 0.0
    assert recon["by_mode"]["CASH"]["net"] == 2000.0   # 3000 collected - 1000 refund
    assert recon["by_mode"]["CASH"]["refunded"] == 1000.0


# ============================================================================
# Panel scenario 3: CASH-first split + partial CARD-reversed refund nets ONLY
#   the cash portion; the drawer never goes negative.
#   Order [CASH 2000, CARD 6000]; full Rs 8000 refund = Rs 2000 cash out +
#   Rs 6000 reversed on the card.
# ============================================================================


def test_cash_first_split_partial_card_refund_nets_only_cash(db):
    db.get_collection("orders").insert(
        _order(
            "S1", datetime(2026, 8, 9, 10, 0, 0),
            [_pay("CASH", 2000.0), _pay("CARD", 6000.0)],
        )
    )
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T16:00:00",
            refund_tenders=[
                {"method": "CASH", "amount": 2000.0},
                {"method": "CARD", "amount": 6000.0},
            ],
        )
    )
    sales, refunds = _cash_sales_for_window(db, "S1", T_START, T_END)
    assert sales == 2000.0
    assert refunds == 2000.0  # only the CASH leg, NOT the whole Rs 8000
    # Drawer expected stays >= 0 (opening 2000 + 2000 sales - 2000 refund = 2000);
    # the pre-fix code produced NEGATIVE Rs 4000 here.
    expected = compute_expected_cash(2000.0, sales, refunds, 0.0, 0.0)
    assert expected == 2000.0
    assert expected >= 0
    # The CARD leg is reversed on its own gateway/leg, not the drawer.
    recon = trec.reconcile_window(db, "S1", T_START, T_END, include_returns=True,
                                  tender_map={})
    assert recon["by_mode"]["CARD"]["refunded"] == 6000.0
    assert recon["by_mode"]["CARD"]["net"] == 0.0   # 6000 collected - 6000 refund


# ============================================================================
# Panel scenario 4: exchange CASH collect adds cash-IN, nothing phantom on card.
#   Original CARD 5000 sale; customer hands Rs 1000 CASH for the upgrade.
# ============================================================================


def test_exchange_cash_collect_adds_cash_in_nothing_on_card(db):
    db.get_collection("orders").insert(
        _order("S1", datetime(2026, 8, 9, 9, 0, 0), [_pay("CARD", 5000.0)])
    )
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T13:00:00", rtype="EXCHANGE",
            collect_method="CASH", collect_amount=1000.0,
            settlement={"direction": "COLLECT", "difference": 1000.0},
        )
    )
    sales, refunds = _cash_sales_for_window(db, "S1", T_START, T_END)
    assert sales == 1000.0   # the Rs 1000 in the drawer is now visible
    assert refunds == 0.0
    recon = trec.reconcile_window(db, "S1", T_START, T_END, include_returns=True,
                                  tender_map={})
    assert recon["by_mode"]["CARD"]["net"] == 5000.0        # untouched
    assert recon["by_mode"]["CASH"]["collected"] == 1000.0  # new cash-in
    assert recon["by_mode"]["CASH"]["net"] == 1000.0


# ============================================================================
# Rewrite of the old test_exchange_upi_collect (which enshrined the defect):
# a UPI-collected exchange difference hits UPI, NOT the drawer.
# ============================================================================


def test_exchange_upi_collect_hits_upi_not_the_drawer(db):
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T13:00:00", rtype="EXCHANGE",
            collect_method="UPI", collect_amount=650.0,
            settlement={"direction": "COLLECT", "difference": 650.0},
        )
    )
    sales, refunds = _cash_sales_for_window(db, "S1", T_START, T_END)
    assert (sales, refunds) == (0.0, 0.0)  # drawer untouched
    recon = trec.reconcile_window(db, "S1", T_START, T_END, include_returns=True,
                                  tender_map={})
    assert recon["by_mode"]["UPI"]["collected"] == 650.0
    assert recon["by_mode"]["UPI"]["net"] == 650.0


def test_exchange_collect_without_method_nets_nowhere(db):
    # collect_method absent -> UNKNOWN -> no drawer, no gateway fiction.
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T13:00:00", rtype="EXCHANGE",
            collect_method=None, collect_amount=900.0,
            settlement={"direction": "COLLECT", "difference": 900.0},
        )
    )
    assert _cash_sales_for_window(db, "S1", T_START, T_END) == (0.0, 0.0)
    recon = trec.reconcile_window(db, "S1", T_START, T_END, include_returns=True,
                                  tender_map={})
    assert recon["by_mode"] == {}


# ============================================================================
# Panel scenario 5: a RETURN with NO refund_tenders is UNKNOWN -> nets nothing.
# ============================================================================


def test_return_without_refund_tenders_nets_nothing(db):
    db.get_collection("orders").insert(
        _order("S1", datetime(2026, 8, 9, 10, 0, 0), [_pay("CASH", 1000.0)])
    )
    db.get_collection("returns").insert(
        _return_doc("S1", "2026-08-09T15:00:00", refund_tenders=None)
    )
    sales, refunds = _cash_sales_for_window(db, "S1", T_START, T_END)
    assert (sales, refunds) == (1000.0, 0.0)   # refund NOT fabricated onto CASH
    recon = trec.reconcile_window(db, "S1", T_START, T_END, include_returns=True,
                                  tender_map={})
    assert recon["by_mode"]["CASH"]["net"] == 1000.0
    assert recon["by_mode"]["CASH"]["refunded"] == 0.0


# ============================================================================
# Panel scenario 6: reconcile_window's DEFAULT is payments-only (no returns).
# ============================================================================


def test_reconcile_window_default_is_payments_only(db):
    db.get_collection("orders").insert(
        _order("S1", datetime(2026, 8, 9, 10, 0, 0), [_pay("CASH", 1000.0)])
    )
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T15:00:00",
            refund_tenders=[{"method": "CASH", "amount": 300.0}],
        )
    )
    # DEFAULT (bank recon / snapshot path): refund is NOT netted.
    default = trec.reconcile_window(db, "S1", T_START, T_END, tender_map={})
    assert default["by_mode"]["CASH"]["net"] == 1000.0
    assert default["by_mode"]["CASH"]["refunded"] == 0.0
    # OPT-IN (eod_tally drawer path): refund IS netted.
    opted = trec.reconcile_window(db, "S1", T_START, T_END, include_returns=True,
                                  tender_map={})
    assert opted["by_mode"]["CASH"]["net"] == 700.0
    assert opted["by_mode"]["CASH"]["refunded"] == 300.0


# ============================================================================
# Panel scenario 7: bank recon never emits a negative expected / negative MDR.
# ============================================================================


def test_bank_recon_ignores_returns_and_never_goes_negative(db):
    # A pure UPI refund day (recorded on the returns doc) must NOT drop the
    # expected gateway settlement (bank recon is payments-only): there are no
    # UPI sales, so there is simply no UPI expectation.
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T12:00:00",
            refund_tenders=[{"method": "UPI", "amount": 2000.0}],
        )
    )
    eng = bankrec.BankReconciliationEngine(db)
    items = eng.build_pos_digital_expected("S1", T_START, T_END)
    assert items == []   # no negative UPI line, no phantom refund settlement


def test_bank_recon_skips_nonpositive_payments_net(db):
    # Even a payments-only reversal-dominant day (UPI 2000 then a -3000 reversal
    # captured in payments) must not emit a negative expected settlement.
    db.get_collection("orders").insert(
        _order("S1", datetime(2026, 8, 9, 10, 0, 0),
               [_pay("UPI", 2000.0), _pay("UPI", -3000.0)])
    )
    eng = bankrec.BankReconciliationEngine(db)
    items = eng.build_pos_digital_expected("S1", T_START, T_END)
    assert all(it["expected_paise"] > 0 for it in items)
    assert not any(it["tender"] == "UPI" for it in items)  # net -1000 -> skipped


def test_mdr_fee_never_negative():
    assert bankrec._mdr_fee_paise(-100000, 200) == 0
    assert bankrec._mdr_fee_paise(0, 200) == 0
    assert bankrec._mdr_fee_paise(100000, 200) == 2000  # sanity: 2% of Rs 1000


def test_bank_recon_keeps_a_POSITIVE_unknown_tender_visible(db):
    """WALLET / NETBANKING / a blank method all canonicalize to UNKNOWN. That is
    REAL digital money -- it must stay on the expected side as a suspense row
    flagged for reclassification, never be silently dropped."""
    db.get_collection("orders").insert(
        _order("S1", datetime(2026, 8, 9, 10, 0, 0), [_pay("WALLET", 1500.0)])
    )
    eng = bankrec.BankReconciliationEngine(db)
    items = eng.build_pos_digital_expected("S1", T_START, T_END)
    unknown = [it for it in items if it["tender"] == "UNKNOWN"]
    assert len(unknown) == 1
    assert unknown[0]["expected_paise"] == 150000
    assert unknown[0]["needs_reclassification"] is True


def test_bank_recon_drops_only_a_NON_POSITIVE_unknown(db):
    db.get_collection("orders").insert(
        _order("S1", datetime(2026, 8, 9, 10, 0, 0),
               [_pay("WALLET", 1000.0), _pay("WALLET", -1500.0)])
    )
    eng = bankrec.BankReconciliationEngine(db)
    items = eng.build_pos_digital_expected("S1", T_START, T_END)
    assert not any(it["tender"] == "UNKNOWN" for it in items)


# ============================================================================
# NEGATIVE expected drawer: a cash-in is missing (e.g. a refund funded from the
# safe). The over/short VERDICT is withheld rather than crediting the cashier a
# phantom overage -- but the real figure is never clamped or hidden.
# ============================================================================


def test_negative_expected_suppresses_the_variance_verdict_cash_register():
    from api.services import cash_register as cr

    summary = cr.build_close_summary(
        opening_float=0.0, cash_sales=0.0, cash_refunds=5000.0,
        cash_expenses=0.0, bank_deposit=0.0, denominations=[], tolerance=0.0,
    )
    assert summary["expected"] == -5000.0          # the real number is shown
    assert summary["variance_status"] == "NEGATIVE_EXPECTED"  # verdict withheld
    assert summary["negative_expected_advisory"] is True
    assert "cash-in is missing" in summary["negative_expected_message"]


def test_positive_expected_keeps_the_normal_verdict():
    from api.services import cash_register as cr

    summary = cr.build_close_summary(
        opening_float=1000.0, cash_sales=5000.0, cash_refunds=500.0,
        cash_expenses=0.0, bank_deposit=0.0, denominations=[], tolerance=0.0,
    )
    assert summary["expected"] == 5500.0
    assert summary["variance_status"] in ("BALANCED", "OVER", "SHORT")
    assert summary["negative_expected_advisory"] is False


def test_zread_negative_expected_advisory(db):
    db.get_collection("returns").insert(
        _return_doc("S1", "2026-08-09T15:00:00",
                    refund_tenders=[{"method": "CASH", "amount": 4000.0}])
    )
    exp = eod_tally.compute_expected(db, "S1", T_START, T_END, 0, 0)
    assert exp["expected_cash_paisa"] == -400000   # shown, not clamped
    assert exp["negative_expected_advisory"] is True


# ============================================================================
# CREDIT_NOTE / EXCHANGE-credit issue store credit -> never touch cash.
# ============================================================================


def test_credit_note_and_exchange_credit_do_not_touch_cash(db):
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T10:00:00", rtype="CREDIT_NOTE",
            refund_tenders=None, credit_amount=900.0,
        )
    )
    db.get_collection("returns").insert(
        _return_doc(
            "S1", "2026-08-09T11:00:00", rtype="EXCHANGE",
            collect_method=None, credit_amount=400.0,
            settlement={"direction": "REFUND", "difference": 400.0},
        )
    )
    assert _cash_sales_for_window(db, "S1", T_START, T_END) == (0.0, 0.0)
    recon = trec.reconcile_window(db, "S1", T_START, T_END, include_returns=True,
                                  tender_map={})
    assert recon["by_mode"] == {}


# ============================================================================
# historical / non-COMPLETED / store scoping / window boundaries.
# ============================================================================


def test_historical_and_non_completed_excluded(db):
    db.get_collection("returns").insert(
        _return_doc("S1", "2026-08-09T12:00:00",
                    refund_tenders=[{"method": "CASH", "amount": 999.0}],
                    historical=True)
    )
    db.get_collection("returns").insert(
        _return_doc("S1", "2026-08-09T12:30:00",
                    refund_tenders=[{"method": "CASH", "amount": 999.0}],
                    status="PENDING")
    )
    assert _cash_sales_for_window(db, "S1", T_START, T_END) == (0.0, 0.0)
    recon = trec.reconcile_window(db, "S1", T_START, T_END, include_returns=True,
                                  tender_map={})
    assert recon["by_mode"] == {}


def test_store_scoping(db):
    db.get_collection("returns").insert(
        _return_doc("S2", "2026-08-09T12:00:00",
                    refund_tenders=[{"method": "CASH", "amount": 700.0}])
    )
    assert _cash_sales_for_window(db, "S1", T_START, T_END) == (0.0, 0.0)
    s2 = _cash_sales_for_window(db, "S2", T_START, T_END)
    assert s2 == (0.0, 700.0)


def test_string_created_at_window_bounds(db):
    for iso in ("2026-08-09T00:00:01", "2026-08-08T23:59:58", "2026-08-10T00:00:01"):
        db.get_collection("returns").insert(
            _return_doc("S1", iso, refund_tenders=[{"method": "CASH", "amount": 100.0}])
        )
    _, refunds = _cash_sales_for_window(db, "S1", T_START, T_END)
    assert refunds == 100.0  # only the one inside the day


def test_datetime_window_bounds_still_match_string_created_at(db):
    # PANEL HARDENING: production IST-day helpers hand out naive-UTC DATETIME
    # bounds; returns.created_at is string-only. The reader must coerce so a
    # datetime bound never type-brackets a cash refund out of existence.
    db.get_collection("returns").insert(
        _return_doc("S1", "2026-08-09T15:30:00.500000",
                    refund_tenders=[{"method": "CASH", "amount": 450.0}])
    )
    sales, refunds = _cash_sales_for_window(
        db, "S1", datetime(2026, 8, 9, 0, 0, 0), datetime(2026, 8, 9, 23, 59, 59)
    )
    assert refunds == 450.0


# ============================================================================
# Fail-soft is non-destructive; count semantics unchanged.
# ============================================================================


def test_returns_scan_failure_never_zeroes_sales(db):
    db.get_collection("orders").insert(
        _order("S1", datetime(2026, 8, 9, 10, 0, 0), [_pay("CASH", 2000.0)])
    )

    class _Boom:
        def find(self, *a, **k):
            raise RuntimeError("returns collection unavailable")

    db._collections["returns"] = _Boom()  # type: ignore[assignment]
    sales, refunds = _cash_sales_for_window(db, "S1", T_START, T_END)
    assert sales == 2000.0
    assert refunds == 0.0


def test_count_stays_payment_row_count(db):
    db.get_collection("orders").insert(
        _order("S1", datetime(2026, 8, 9, 10, 0, 0), [_pay("CASH", 1000.0)])
    )
    db.get_collection("returns").insert(
        _return_doc("S1", "2026-08-09T15:00:00",
                    refund_tenders=[{"method": "CASH", "amount": 100.0}])
    )
    recon = trec.reconcile_window(db, "S1", T_START, T_END, include_returns=True,
                                  tender_map={})
    assert recon["by_mode"]["CASH"]["count"] == 1  # one payments[] row only
    assert recon["by_mode"]["CASH"]["net"] == 900.0


def test_reconcile_skips_returns_when_orders_read_fails(db):
    # Asymmetric fail-soft guard: if the orders read blows up, the refund
    # netting is skipped too (never a refunds-only envelope frozen into a
    # hash-chained snapshot).
    db.get_collection("returns").insert(
        _return_doc("S1", "2026-08-09T15:00:00",
                    refund_tenders=[{"method": "CASH", "amount": 500.0}])
    )

    class _BoomOrders(FakeCollection):
        def find(self, *a, **k):
            raise RuntimeError("orders read failed")

    db._collections["orders"] = _BoomOrders()
    recon = trec.reconcile_window(db, "S1", T_START, T_END, include_returns=True,
                                  tender_map={})
    assert recon["by_mode"] == {}


# ============================================================================
# F23 blind Z-Read: compute_expected nets refunds + surfaces gross/refunds.
# ============================================================================


def test_zread_compute_expected_gross_and_refunds(db):
    db.get_collection("orders").insert(
        _order("S1", datetime(2026, 8, 9, 10, 0, 0), [_pay("CASH", 1000.0)])
    )
    db.get_collection("returns").insert(
        _return_doc("S1", "2026-08-09T15:00:00",
                    refund_tenders=[{"method": "CASH", "amount": 300.0}])
    )
    exp = eod_tally.compute_expected(db, "S1", T_START, T_END, 10000, 0)
    assert exp["cash_sales_paisa"] == 100000    # GROSS collected
    assert exp["cash_refunds_paisa"] == 30000   # recorded refund, distinct line
    assert exp["expected_cash_paisa"] == 10000 + 100000 - 30000  # opening + gross - refund


def test_zread_double_entry_advisory_when_payout_and_refund_coincide(db):
    db.get_collection("orders").insert(
        _order("S1", datetime(2026, 8, 9, 10, 0, 0), [_pay("CASH", 1000.0)])
    )
    db.get_collection("returns").insert(
        _return_doc("S1", "2026-08-09T15:00:00",
                    refund_tenders=[{"method": "CASH", "amount": 300.0}])
    )
    # A manual payout in the same window -> advisory raised (possible double entry).
    with_payout = eod_tally.compute_expected(db, "S1", T_START, T_END, 10000, 5000)
    assert with_payout["refund_double_entry_advisory"] is True
    # No manual payout -> no advisory.
    no_payout = eod_tally.compute_expected(db, "S1", T_START, T_END, 10000, 0)
    assert no_payout["refund_double_entry_advisory"] is False


# ============================================================================
# Writer-side validation (returns._normalize_refund_tenders / _collect_method).
# ============================================================================


def _tl(method, amount):
    return returns_router.RefundTenderLine(method=method, amount=amount)


def test_refund_tenders_absent_is_unknown_not_netted():
    rows, auto = returns_router._normalize_refund_tenders(None, 1000.0)
    assert rows is None and auto is False


def test_refund_tenders_balanced_split_is_accepted_and_flagged():
    rows, auto = returns_router._normalize_refund_tenders(
        [_tl("CASH", 2000.0), _tl("CARD", 6000.0)], 8000.0
    )
    assert auto is True
    # The MONEY contract, unchanged. (A CASH leg additionally carries the
    # notes-and-coins record; the caller here gave no breakdown, so it is an
    # honest absence and NEVER a zero count -- asserted separately so this
    # test keeps failing if a money field ever moves.)
    assert [{"method": r["method"], "amount": r["amount"]} for r in rows] == [
        {"method": "CASH", "amount": 2000.0},
        {"method": "CARD", "amount": 6000.0},
    ]
    assert rows[0]["cash_count"]["state"] == "NOT_CAPTURED"
    assert rows[0]["cash_count"]["matches_amount"] is None
    assert "cash_count" not in rows[1]  # a CARD refund has no notes


def test_refund_tenders_unbalanced_split_is_rejected():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        returns_router._normalize_refund_tenders([_tl("CASH", 1000.0)], 8000.0)
    assert exc.value.status_code == 400


def test_refund_tender_unknown_method_is_rejected():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        returns_router._normalize_refund_tenders([_tl("BITCOIN", 1000.0)], 1000.0)
    assert exc.value.status_code == 400


def test_bank_code_normalizes_to_bank_transfer():
    rows, auto = returns_router._normalize_refund_tenders([_tl("BANK", 500.0)], 500.0)
    assert auto is True
    assert rows == [{"method": "BANK_TRANSFER", "amount": 500.0}]


def test_collect_method_normalization():
    assert returns_router._normalize_collect_method(None) is None
    assert returns_router._normalize_collect_method("cash") == "CASH"
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        returns_router._normalize_collect_method("crypto")
