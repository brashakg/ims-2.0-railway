"""
IMS 2.0 - GST cross-check ROUTER behaviors
==========================================
Endpoint-level coverage for the hardened behaviors of the accountant GST
cross-check / sign-off (api.routers.finance), which the pure-service suite
(test_gst_crosscheck.py) does not exercise:

  * _run_gst_cross_check: 503 (db None), 404 (named entity with no stores),
    404 (all-entities with no stores, HR-2), partial flag on a per-store compute
    failure, and the itc_leg_failed flag when the purchase-side ITC leg dies
    (HR-1).
  * gst_cross_check_signoff: 409 when partial, 409 when itc_leg_failed (HR-1),
    server-recomputed figures recorded (client claim quarantined under client_*),
    prior sign-off pushed to history with client_gst_payable (HR-5), and 503
    when the DB is down.
  * GstCrossCheckSignoff model rejects month=13 (the 422 the endpoint returns).

Uses a fake DB + monkeypatched _compute_gstr1/_compute_gstr3b so no MongoDB or
tax math runs -- these tests assert the ROUTER's guards, not GST figures.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from api.routers import finance, reports  # noqa: E402


# ---------------------------------------------------------------------------
# Fake DB
# ---------------------------------------------------------------------------


class FakeCollection:
    def __init__(self, docs=None, *, raises=False, prior=None, sink=None):
        self._docs = docs or []
        self._raises = raises
        self._prior = prior
        self._sink = sink  # a dict to capture update_one calls

    def find(self, *args, **kwargs):
        if self._raises:
            raise RuntimeError("collection unavailable")
        return iter(list(self._docs))

    def find_one(self, *args, **kwargs):
        return self._prior

    def aggregate(self, *args, **kwargs):
        return iter([])

    def update_one(self, key, update, upsert=False):
        if self._sink is not None:
            self._sink["key"] = key
            self._sink["update"] = update
            self._sink["upsert"] = upsert

        class _R:
            upserted_id = "x"
            modified_count = 1

        return _R()


class FakeDB:
    def __init__(self, collections):
        self._c = collections

    def get_collection(self, name):
        return self._c.get(name, FakeCollection())

    def __getitem__(self, name):
        return self.get_collection(name)


def _stores(*, empty=False):
    if empty:
        return []
    return [
        {"store_id": "S1", "entity_id": "E1", "gstin": "G1", "state": "Jharkhand"},
        {"store_id": "S2", "entity_id": "E1", "gstin": "G2", "state": "Maharashtra"},
    ]


def _base_collections(*, empty_stores=False, po_raises=False):
    return {
        "stores": FakeCollection(_stores(empty=empty_stores)),
        "entities": FakeCollection([{"entity_id": "E1", "name": "Entity One"}]),
        "customers": FakeCollection([]),
        "orders": FakeCollection([]),
        "purchase_orders": FakeCollection([], raises=po_raises),
    }


def _fake_gstr3b(period, sid):
    return {
        "outwardTaxableValue": 10000.0,
        "outwardTaxableSupplies": {
            "integratedTax": 0.0, "centralTax": 900.0, "stateTax": 900.0, "cess": 0.0,
        },
        "itcAvailable": {"integratedTax": 0.0, "centralTax": 0.0, "stateTax": 0.0, "cess": 0.0},
        "itcAvailableRegular": {"integratedTax": 0.0, "centralTax": 0.0, "stateTax": 0.0, "cess": 0.0},
        "itcAvailableTransfer": {"integratedTax": 0.0, "centralTax": 0.0, "stateTax": 0.0, "cess": 0.0},
        "inwardSuppliesReverseChargeValue": 0.0,
        "inwardSuppliesReverseCharge": {
            "integratedTax": 0.0, "centralTax": 0.0, "stateTax": 0.0, "cess": 0.0,
        },
    }


@pytest.fixture
def patch_compute(monkeypatch):
    """Monkeypatch the per-store GST-return computes to fakes (no DB/tax math)."""
    monkeypatch.setattr(reports, "_compute_gstr1", lambda period, sid: {"period": period})
    monkeypatch.setattr(reports, "_compute_gstr3b", _fake_gstr3b)


# ---------------------------------------------------------------------------
# _run_gst_cross_check guards
# ---------------------------------------------------------------------------


def test_run_cross_check_db_none_503():
    with pytest.raises(HTTPException) as ei:
        finance._run_gst_cross_check(None, 4, 2026, None)
    assert ei.value.status_code == 503


def test_run_cross_check_named_entity_no_stores_404(patch_compute):
    db = FakeDB(_base_collections())
    with pytest.raises(HTTPException) as ei:
        finance._run_gst_cross_check(db, 4, 2026, "E_MISSING")
    assert ei.value.status_code == 404


def test_run_cross_check_all_entities_no_stores_404(patch_compute):
    # HR-2: all-entities view with an empty store map must 404, not sign off an
    # all-zero green.
    db = FakeDB(_base_collections(empty_stores=True))
    with pytest.raises(HTTPException) as ei:
        finance._run_gst_cross_check(db, 4, 2026, None)
    assert ei.value.status_code == 404


def test_run_cross_check_partial_on_store_compute_failure(monkeypatch):
    monkeypatch.setattr(reports, "_compute_gstr1", lambda period, sid: {"period": period})

    def _g3(period, sid):
        if sid == "S2":
            raise RuntimeError("bad store")
        return _fake_gstr3b(period, sid)

    monkeypatch.setattr(reports, "_compute_gstr3b", _g3)
    db = FakeDB(_base_collections())
    res = finance._run_gst_cross_check(db, 4, 2026, "E1")
    assert res["partial"] is True
    assert "S2" in res["failed_store_ids"]


def test_run_cross_check_itc_leg_failed_flag(patch_compute):
    # HR-1: purchase-side ITC leg throws -> itc_leg_failed True, input_credit None.
    db = FakeDB(_base_collections(po_raises=True))
    res = finance._run_gst_cross_check(db, 4, 2026, "E1")
    assert res["itc_leg_failed"] is True
    assert res["books"]["input_credit"] is None
    assert res["partial"] is False


# ---------------------------------------------------------------------------
# gst_cross_check_signoff gate + record
# ---------------------------------------------------------------------------


_USER = {"roles": ["ACCOUNTANT"], "user_id": "u1", "name": "Acc"}


def _signoff(monkeypatch, server_result, *, prior=None, db_none=False):
    """Drive the sign-off with a controlled _run result and capture the write."""
    sink: dict = {}
    if db_none:
        db = None
    else:
        db = FakeDB({
            finance._GST_CROSSCHECK_SIGNOFFS: FakeCollection(prior=prior, sink=sink),
        })
    monkeypatch.setattr(finance, "_get_db", lambda: db)
    monkeypatch.setattr(finance, "_require_finance_admin", lambda u: None)
    if server_result is not None:
        monkeypatch.setattr(
            finance, "_run_gst_cross_check", lambda *a, **k: server_result
        )
    import api.dependencies as _dep
    monkeypatch.setattr(_dep, "get_audit_repository", lambda: None, raising=False)

    body = finance.GstCrossCheckSignoff(
        month=4, year=2026, entity_id="E1", note="ok",
        mismatch_count=0, gst_payable=0.01,
    )
    return asyncio.run(finance.gst_cross_check_signoff(body, _USER)), sink


def _clean_server():
    return {
        "partial": False,
        "itc_leg_failed": False,
        "failed_store_ids": [],
        "summary": {
            "mismatch_count": 2,
            "gst_payable": 500.0,
            "mismatch_metrics": ["Total output GST", "Input tax credit (ITC)"],
        },
    }


def test_signoff_blocks_on_partial_409(monkeypatch):
    server = _clean_server()
    server.update({"partial": True, "failed_store_ids": ["S2"]})
    with pytest.raises(HTTPException) as ei:
        _signoff(monkeypatch, server)
    assert ei.value.status_code == 409
    assert "store" in ei.value.detail.lower()


def test_signoff_blocks_on_itc_leg_failed_409(monkeypatch):
    # HR-1: a dead ITC leg must block sign-off even when nothing else is partial.
    server = _clean_server()
    server["itc_leg_failed"] = True
    with pytest.raises(HTTPException) as ei:
        _signoff(monkeypatch, server)
    assert ei.value.status_code == 409
    assert "itc" in ei.value.detail.lower()


def test_signoff_records_server_figures_not_client(monkeypatch):
    resp, sink = _signoff(monkeypatch, _clean_server())
    rec = resp["signoff"]
    # Server-recomputed authoritative snapshot.
    assert rec["mismatch_count"] == 2
    assert rec["gst_payable"] == 500.0
    # Client claim quarantined, never trusted.
    assert rec["client_mismatch_count"] == 0
    assert rec["client_gst_payable"] == 0.01
    assert rec["itc_leg_failed"] is False
    # The persisted $set carries the same server figures.
    assert sink["update"]["$set"]["mismatch_count"] == 2


def test_signoff_pushes_prior_to_history_with_client_payable(monkeypatch):
    # HR-5: overwriting a prior sign-off preserves BOTH gst_payable forensics and
    # caps history growth.
    prior = {
        "checked_by": "old", "checked_by_name": "Old", "checked_at": "t0",
        "note": "prev", "mismatch_count": 1, "gst_payable": 100.0,
        "client_mismatch_count": 1, "client_gst_payable": 99.0,
    }
    _resp, sink = _signoff(monkeypatch, _clean_server(), prior=prior)
    push = sink["update"]["$push"]["history"]
    assert push["$slice"] == -50
    snap = push["$each"][0]
    assert snap["client_gst_payable"] == 99.0
    assert snap["gst_payable"] == 100.0


def test_signoff_db_none_503(monkeypatch):
    with pytest.raises(HTTPException) as ei:
        _signoff(monkeypatch, _clean_server(), db_none=True)
    assert ei.value.status_code == 503


# ---------------------------------------------------------------------------
# Model validation (the 422 the endpoint returns)
# ---------------------------------------------------------------------------


def test_signoff_model_rejects_month_13():
    with pytest.raises(ValidationError):
        finance.GstCrossCheckSignoff(month=13, year=2026)


def test_signoff_model_rejects_year_out_of_range():
    with pytest.raises(ValidationError):
        finance.GstCrossCheckSignoff(month=4, year=1999)
