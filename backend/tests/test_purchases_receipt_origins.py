"""Monthly receiving keeps the mint's origin across transfers and old numbers."""

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from api.routers import finance
from api.routers.vendors import grn_accept_lock as receiving
from api.routers.vendors import purchases_report as report
from strict_fakes import StrictDB


def receipt(gid, *, number=None, shop="S1", accepted="2026-09-20", price=100):
    return {
        "grn_id": gid, "grn_number": number or gid, "vendor_id": "V" + gid,
        "store_id": shop, "status": "ACCEPTED", "accepted_at": accepted,
        "items": [{"product_id": "P1", "accepted_qty": 2, "unit_price": price, "gst_rate": 0}],
    }


def unit(sid, *, gid=None, number=None, source_type="TRANSFER", source_id="T1", created="2026-09-10"):
    return {
        "stock_id": sid, "grn_id": gid, "grn_number": number,
        "source_type": source_type, "source_id": source_id, "product_id": "P1",
        "grn_line_index": 0, "created_at": created,
    }


def shown(monkeypatch, db, *, shop=None, month="2026-09"):
    monkeypatch.setattr(report, "_get_db", lambda: db)
    monkeypatch.setattr(finance, "_ap_rows", lambda *_: ([], [], []))
    return asyncio.run(report.purchases_this_month(
        month=month, store_id=shop, current_user={"roles": ["ADMIN"]},
    ))


def test_unpriced_po_receipt_is_reported_without_crashing(monkeypatch):
    db = StrictDB()
    grn = receipt("G1", price=None)
    grn["po_id"] = "PO1"
    db.seed("grns", [grn])
    db.seed("purchase_orders", [{"po_id": "PO1", "items": [{"product_id": "P1", "unit_price": None}]}])
    db.seed("stock_units", [unit("U1", gid="G1")])
    body = shown(monkeypatch, db)
    assert body["totals"]["received"] == 0
    assert body["unpriced_receipt_lines"] == 1


def test_moved_units_keep_immutable_origin_even_when_numbers_collide(monkeypatch):
    db = StrictDB()
    db.seed("grns", [receipt("G1", number="OLD"), receipt("G2", number="OLD", shop="S2", price=900)])
    db.seed("stock_units", [unit("U1", gid="G1", number="OLD", source_type="GRN", source_id="G2")])
    body = shown(monkeypatch, db)
    assert body["totals"]["received"] == 100
    assert [(v["vendor_id"], v["received"]) for v in body["vendors"]] == [("VG1", 100)]
    assert body["uncertain_receipt_origins"] == 1


def test_number_uniqueness_is_global_and_ambiguous_units_do_not_use_accept_date(monkeypatch):
    db = StrictDB()
    db.seed("grns", [
        receipt("G1", number="OLD", accepted="2026-10-05"),
        receipt("G2", number="OLD", shop="S2", accepted="2026-08-05"),
    ])
    db.seed("stock_units", [unit("U1", number="OLD")])
    for month in ("2026-09", "2026-10"):
        body = shown(monkeypatch, db, shop="S1", month=month)
        assert body["totals"]["received"] == 0
        assert body["uncertain_receipt_origins"] == 1


@pytest.mark.parametrize("legacy", [False, True])
def test_unique_transferred_units_stay_in_their_mint_month(monkeypatch, legacy):
    db = StrictDB()
    db.seed("grns", [receipt("G1", accepted="2026-10-05")])
    db.seed("stock_units", [unit("U1", gid=None if legacy else "G1", number="G1")])
    september = shown(monkeypatch, db)
    october = shown(monkeypatch, db, month="2026-10")
    assert september["totals"]["received"] == 100
    assert october["totals"]["received"] == 0
    assert september["uncertain_receipt_origins"] == october["uncertain_receipt_origins"] == 0


def test_legacy_source_still_proves_origin_with_a_duplicate_number(monkeypatch):
    db = StrictDB()
    grn = receipt("G1", number="OLD")
    db.seed("grns", [grn, receipt("G2", number="OLD", shop="S2")])
    db.seed("stock_units", [
        unit("U1", number="OLD", source_type="GRN", source_id="G1"),
        unit("U2", gid="G2", number="OLD", source_type="GRN", source_id="G1"),
    ])
    monkeypatch.setattr(receiving, "_number_is_this_receipts_alone", lambda _: False)
    stock = type("Stock", (), {"collection": db.stock_units})()
    assert receiving._receipt_units(stock, grn, product_id="P1") == 1
    body = shown(monkeypatch, db, shop="S1")
    assert body["totals"]["received"] == 100
    assert body["uncertain_receipt_origins"] == 0


def test_old_receipt_with_no_unit_history_keeps_existing_fallback(monkeypatch):
    db = StrictDB()
    db.seed("grns", [receipt("G1")])
    body = shown(monkeypatch, db)
    assert body["totals"]["received"] == 200
    assert body["uncertain_receipt_origins"] == 0


def test_later_catalogue_release_uses_mint_date_after_first_accept(monkeypatch):
    db = StrictDB()
    db.seed("grns", [receipt("G1", accepted="2026-09-10")])
    db.seed("stock_units", [
        unit("U1", gid="G1", created="2026-09-10"),
        unit("U2", gid="G1", created="2026-10-02"),
    ])
    assert shown(monkeypatch, db)["totals"]["received"] == 100
    assert shown(monkeypatch, db, month="2026-10")["totals"]["received"] == 100
