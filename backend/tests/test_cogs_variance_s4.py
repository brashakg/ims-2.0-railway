"""S4 owner ruling 2026-11-01 (a): pieces sold before their supplier bill.

When a bill is accepted, only the billed pieces still HELD are averaged into
the product's one average cost at the bill price. For the pieces already
sold, (bill price - the cost their sale line froze) x pieces is a
COST-OF-GOODS VARIANCE: one auditable row per (bill, product) -- the bill,
the product, the pieces, the bill's IST day / period -- that every P&L /
margin report adds to its COGS. Exact to the paisa; re-accepting or
re-processing the bill never doubles it.
"""

import asyncio
import copy
from datetime import timedelta

import mongomock
import pytest

from api.routers import purchase_invoices as pi
from api.services import product_cost
from api.utils.event_time import utc_now
from api.utils.ist import ist_today
from tests.test_gst_basis_margins import SU, _Db, _orders, _period, _Repo, _static_gst  # noqa: F401 (autouse)
from tests.test_one_average_cost_s4 import (  # noqa: F401 (autouse fixtures)
    _accept,
    _avg,
    _bill,
    _db,
    _no_finish,
    _pieces,
    _receipt,
    _restore_router,
)


def _sold(db, receipt, costs, start=0):
    """Pieces of `receipt` sold on their own orders, each line freezing
    `cost` (None: a line with no frozen cost)."""
    for i, cost in enumerate(costs, start):
        sid, oid = f"S{receipt}{i}", f"O{receipt}{i}"
        db.stock_units.insert_one({"stock_id": sid, "product_id": "P", "store_id": "S1", "status": "SOLD",
                                   "quantity": 1, "grn_id": receipt, "order_id": oid,
                                   "sold_at": f"2026-10-2{i}T10:00:00"})
        db.orders.insert_one({"order_id": oid, "items": [{"product_id": "P", "quantity": 1, "cost_at_sale": cost}]})


def test_only_held_pieces_are_averaged_and_sold_ones_give_one_variance_row():
    """10 old pieces at 100; receipt G brought 5 -- 3 still on the shelf, 2
    sold at the frozen cost 100. Its bill, 5 at 130: the average takes the 3
    held pieces only, (10 x 100 + 3 x 130) / 13 = 106.92 (not the 115 of
    counting all 5); the 2 sold pieces are a variance of 2 x (130 - 100) = 60,
    recorded once against the bill, the product, the pieces and the bill's
    IST period."""
    db = _db(100.0, category="FRAME")
    _pieces(db, 10)
    _receipt(db, "G", 5)
    _pieces(db, 3, "G")
    _sold(db, "G", [100.0, 100.0])
    bill = _bill("B", 5, 130, grn_id="G", bill_date="2026-10-31", store_id="S1",
                 created_at=(utc_now() - timedelta(minutes=10)).isoformat())
    _accept(db, bill)
    assert _avg(db) == 106.92
    (row,) = db.products.find_one({})["cost_history"]
    assert (row["qty"], row["held"], row["basis"]) == (5.0, 3.0, 10.0)
    assert (row["variance"]["pieces"], row["variance"]["amount"]) == (2, 60.0)
    (var,) = db.cogs_variances.find({}, {"_id": 0})
    assert (var["variance_id"], var["bill_id"], var["product_id"], var["store_id"], var["category"]) == (
        "B:P", "B", "P", "S1", "FRAME")
    assert (var["pieces"], var["bill_price"], var["sold_at_cost"], var["amount"]) == (2, 130.0, 200.0, 60.0)
    assert sorted(var["stock_ids"]) == ["SG0", "SG1"] and var["receipt_ids"] == ["G"]
    assert (var["day"], var["period"]) == ("2026-10-31", "2026-10")
    # Re-accepting / re-processing never doubles it.
    pi._apply_accepted_bill(db, bill, actor="acc")
    db.vendor_bills.update_one({"bill_id": "B"}, {"$set": {"cost_state": product_cost.PENDING}})
    pi.finish_accepted_bills(db)
    assert db.cogs_variances.count_documents({}) == 1
    assert len(db.products.find_one({})["cost_history"]) == 1 and _avg(db) == 106.92
    assert product_cost.variance_by(db) == {None: 60.0}
    assert product_cost.variance_by(db, "store_id", from_day="2026-10-01", to_day="2026-10-31") == {"S1": 60.0}
    assert product_cost.variance_by(db, from_day="2026-11-01") == {}
    assert product_cost.variance_by(db, store_ids=["S2"]) == {}


def test_the_variance_is_exact_to_the_paisa_on_the_bill_total():
    """3 pieces billed for Rs 100.00 in all, all 3 sold at a frozen 30: the
    variance is 100.00 - 90.00 = 10.00 -- never 3 x 3.33 = 9.99 from a
    per-piece rounded price."""
    db = _db(30.0)
    _receipt(db, "G", 3)
    _sold(db, "G", [30.0, 30.0, 30.0])
    bill = _bill("B", 3, 33.33, grn_id="G", bill_date="2026-10-31")
    bill["lines"][0]["taxable"] = 100.0
    _accept(db, bill)
    assert db.cogs_variances.find_one({})["amount"] == 10.0


def test_a_provisional_cost_sold_at_the_po_rate_is_replaced_and_trued_up():
    """A PO-rate cost of 400 (provisional) on 3 received pieces: 1 held, 1
    sold at the frozen 400, 1 sold on a line with no frozen cost (the COGS
    readers price that line at the live cost: no variance). The bill at 500
    REPLACES the provisional cost and trues the frozen sale up by 100."""
    db = _db(400.0, cost_source="PO_RATE")
    _receipt(db, "G", 3)
    _pieces(db, 1, "G")
    _sold(db, "G", [400.0, None])
    _accept(db, _bill("B", 3, 500, grn_id="G", bill_date="2026-10-31"))
    assert _avg(db) == 500.0
    (var,) = db.cogs_variances.find({})
    assert (var["pieces"], var["amount"]) == (1, 100.0)


def test_a_variance_row_a_crash_lost_is_written_by_the_sweeper_once():
    """The history row landed, the process died before the variance row:
    the bill is not APPLIED (product_cost.applied checks the row), so the
    sweeper writes it -- from the history row, once."""
    db = _db(100.0)
    _receipt(db, "G", 2)
    _sold(db, "G", [100.0, 100.0])
    bill = _bill("B", 2, 120, grn_id="G", bill_date="2026-10-31",
                 created_at=(utc_now() - timedelta(minutes=10)).isoformat())
    _accept(db, bill)
    db.cogs_variances.delete_many({})
    db.vendor_bills.update_one({"bill_id": "B"}, {"$set": {"cost_state": product_cost.PENDING}})
    assert not product_cost.applied(db, bill["lines"], "B")
    assert pi.finish_accepted_bills(db) == 1
    assert [v["amount"] for v in db.cogs_variances.find({})] == [40.0]
    assert db.vendor_bills.find_one({})["cost_state"] == product_cost.APPLIED
    assert len(db.products.find_one({})["cost_history"]) == 1


# --------------------------------------------------------------------------
# The P&L / margin readers add it to COGS (the fixture: revenue 2000, cost
# 1200; a variance of 60 on category FR at shop S1, dated today).
# --------------------------------------------------------------------------


def _variances():
    split = [{"key": "FR", "amount": 60.0}]
    return [{"variance_id": "B:P", "amount": 60.0, "store_id": "S1", "category": "FR",
             "by_item_type": split, "by_category": split, "day": ist_today().isoformat()}]


def test_finance_pnl_readers_include_the_variance(monkeypatch):
    from api.routers.finance import pnl as pnl_mod
    from api.routers.finance import pnl_breakdown as pb
    from api.routers.finance._shared import pnl_by_category

    db = _Db(orders=_orders(), cogs_variances=_variances())
    monkeypatch.setattr(pnl_mod, "_get_db", lambda: db)
    monkeypatch.setattr(pnl_mod, "_cost_by_product", lambda _db: {})
    monkeypatch.setattr(pnl_mod, "_payroll_cost", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(pnl_mod.je_service, "pnl_adjustments", lambda *_a, **_k: {})
    pnl = asyncio.run(pnl_mod.get_pnl(store_id=None, from_date=None, to_date=None, current_user=SU))
    assert (pnl["cogs"], pnl["cogs_variance"], pnl["gross_profit"]) == (1260.0, 60.0, 740.0)
    mgr = {"user_id": "m", "roles": ["STORE_MANAGER"], "active_store_id": "S1", "store_ids": ["S1"]}
    assert "cogs_variance" not in asyncio.run(pnl_mod.get_pnl(store_id="S1", from_date=None, to_date=None,
                                                              current_user=mgr))

    monkeypatch.setattr(pb, "_get_db", lambda: db)
    monkeypatch.setattr(pb, "_store_maps", lambda _db: ({"S1": "E1"}, {}))
    monkeypatch.setattr(pb, "_cost_by_product", lambda _db: {})
    monkeypatch.setattr(pb, "_payroll_by_store", lambda *_a, **_k: {})
    monkeypatch.setattr(pb, "_store_name_map", lambda _db: {"S1": "Shop"})
    (row,) = asyncio.run(pb.get_pnl_by_store(from_date=None, to_date=None, entity_id=None, current_user=SU))["stores"]
    assert (row["cogs"], row["net_profit"]) == (1260.0, 740.0)

    cats = {c["category"]: c for c in pnl_by_category(_orders(), {}, {"FR": 60.0})}
    assert (cats["FR"]["cogs"], cats["FR"]["gross_profit"], cats["SG"]["cogs"]) == (660.0, 340.0, 600.0)
    by_cat = asyncio.run(pb.get_pnl_by_category(from_date=None, to_date=None, store_id=None, entity_id=None,
                                                current_user=SU))
    assert by_cat["total_gross_profit"] == 740.0


def test_reports_margin_readers_include_the_variance(monkeypatch):
    from api.routers.reports import customers as rc
    from api.routers.reports import growth

    db = mongomock.MongoClient().r
    db.cogs_variances.insert_many(copy.deepcopy(_variances()))
    for mod in (rc, growth):
        monkeypatch.setattr(mod, "get_order_repository", lambda: _Repo(_orders()))
        monkeypatch.setattr(mod, "get_db", lambda: db)
    f, t = _period()
    body = asyncio.run(rc.expense_vs_revenue(store_id=None, from_date=f, to_date=t, current_user=SU))
    assert (body["cost"], body["profit"]) == (1260.0, 740.0)
    cats = asyncio.run(growth.profit_by_category(store_id=None, from_date=f, to_date=t, current_user=SU))
    assert {r["category"]: r["cost"] for r in cats["data"]} == {"FR": 660.0, "SG": 600.0}
    assert cats["total_profit"] == 740.0
    stores = asyncio.run(growth.profit_by_store(from_date=f, to_date=t, current_user=SU))
    assert (stores["data"][0]["cost"], stores["total_profit"]) == (1260.0, 740.0)
    kpis = _enterprise_kpis(monkeypatch, db, _orders())
    assert (kpis["gross_profit"], kpis["gross_margin_percent"]) == (740.0, 37.0)


def _enterprise_kpis(monkeypatch, db, orders):
    from api.routers import analytics as an

    monkeypatch.setattr(an, "get_order_repository", lambda: _Repo(orders))
    monkeypatch.setattr(an, "get_stock_repository", lambda: _Repo([]))
    monkeypatch.setattr(an, "get_customer_repository", lambda: _Repo([]))
    monkeypatch.setattr(an, "get_db", lambda: db)
    return asyncio.run(an.get_enterprise_kpis(current_user=SU, period="month", store_id="S1"))["margins"]


def test_the_collection_margin_includes_its_products_variance():
    """A collection's 30-day margin (352.38 on its own lines) less its
    product's variance at this shop in the 30 days (60): 292.38. Last
    month's, another shop's and a non-member product's are left out."""
    from tests.test_collection_insights import _kpi_db
    from api.services import collection_insights as ci

    db = _kpi_db()
    db._c["cogs_variances"] = mongomock.MongoClient().ci.cogs_variances
    db.get_collection = db.__getitem__
    today = ist_today()
    for vid, pid, amount, store, day in (("IN", "P1", 60.0, "BV-01", today),
                                         ("OLD", "P1", 7.0, "BV-01", today - timedelta(days=40)),
                                         ("S2", "P1", 11.0, "BV-02", today),
                                         ("NM", "P9", 13.0, "BV-01", today)):
        db["cogs_variances"].insert_one({"variance_id": vid, "product_id": pid, "amount": amount,
                                         "store_id": store, "day": day.isoformat()})
    assert ci.kpis(db, {"collection_id": "C1", "title": "Test"}, store_id="BV-01")["margin_30d"] == 292.38


# --------------------------------------------------------------------------
# Fix round 2 (S4).
# --------------------------------------------------------------------------


@pytest.mark.parametrize("first", ([100.0, 100.0], [None, None]))
def test_a_receipt_billed_in_two_parts_trues_up_its_own_pieces(first):
    """Receipt G: 4 pieces, all sold, the lines froze `first` then 90, 90
    (oldest first). B1 prices 2 at 100 (no variance: 0, or no frozen cost);
    B2 prices 2 at 120. B1 still CLAIMS its two pieces, so B2 trues up its
    own: 2 x (120 - 90) = 60 -- never the oldest two again."""
    db = _db(100.0)
    _receipt(db, "G", 4)
    _sold(db, "G", first + [90.0, 90.0])
    _accept(db, _bill("B1", 2, 100, grn_id="G", bill_date="2026-10-31"))
    _accept(db, _bill("B2", 2, 120, grn_id="G", bill_date="2026-10-31"))
    (var,) = db.cogs_variances.find({})
    assert (var["bill_id"], var["amount"], sorted(var["stock_ids"])) == ("B2", 60.0, ["SG2", "SG3"])


@pytest.mark.parametrize("status", ("QUARANTINED", "UNDER_AUDIT"))
def test_a_billed_piece_still_ours_but_off_the_shelf_averages_in(status):
    """10 old pieces at 100; receipt G brought 5: 3 on the shelf, 1 `status`
    (still ours, not sold), 1 sold at a frozen 100. Bill 5 x 130 = 650, all
    of it booked: 4 x 130 averaged in ((1000 + 520) / 14 = 108.57), the sold
    piece 100 + its variance 30."""
    db = _db(100.0)
    _pieces(db, 10)
    _receipt(db, "G", 5)
    _pieces(db, 3, "G")
    _pieces(db, 1, "G", status=status, prefix="Q")
    _sold(db, "G", [100.0])
    _accept(db, _bill("B", 5, 130, grn_id="G", bill_date="2026-10-31"))
    assert _avg(db) == 108.57
    (row,) = db.products.find_one({})["cost_history"]
    assert (row["held"], row["variance"]["amount"]) == (4.0, 30.0)


@pytest.mark.parametrize("status", ("VOID", "DAMAGED"))
def test_a_billed_piece_written_off_before_its_bill_is_trued_up(status):
    """As above, but the odd piece was WRITTEN OFF (a stock count's VOID; or
    quarantine released as DAMAGED, dated by its DAMAGED event) at the then
    average 100. Bill 650 = 3 x 130 averaged in + the sold piece (100 + 30)
    + the written-off piece (100 + 30): the variance is 60, its write-off
    part named."""
    db = _db(100.0)
    _pieces(db, 10)
    _receipt(db, "G", 5)
    _pieces(db, 3, "G")
    lost = {"voided_at": "2026-10-25T10:00:00+00:00"} if status == "VOID" else {}
    db.stock_units.insert_one({"stock_id": "W1", "product_id": "P", "store_id": "S1", "status": status,
                               "quantity": 1, "grn_id": "G", **lost})
    if status == "DAMAGED":
        db.item_events.insert_one({"stock_id": "W1", "to_state": "DAMAGED", "at": "2026-10-25T10:00:00+00:00"})
    _sold(db, "G", [100.0])
    _accept(db, _bill("B", 5, 130, grn_id="G", bill_date="2026-10-31"))
    assert _avg(db) == 106.92
    (var,) = db.cogs_variances.find({})
    assert (var["amount"], var["pieces"], var["written_off"], sorted(var["stock_ids"])) == (60.0, 2, 1, ["SG0", "W1"])
    assert 3 * 130 + (100 + 100) + var["amount"] == 650


def test_the_variance_lands_in_the_category_each_reader_groups_by():
    """A till line carries item_type LENS (billQuote mapCategory) and
    category OPTICAL_LENS: /finance/pnl/by-category groups by item_type,
    growth.profit_by_category by category -- the variance follows each."""
    from api.routers.finance._shared import pnl_by_category

    db = _db(500.0, category="OPTICAL_LENS")
    _receipt(db, "G", 1)
    db.stock_units.insert_one({"stock_id": "L1", "product_id": "P", "store_id": "S1", "status": "SOLD",
                               "quantity": 1, "grn_id": "G", "order_id": "OL", "sold_at": "2026-10-20"})
    db.orders.insert_one({"order_id": "OL", "items": [{"product_id": "P", "quantity": 1, "cost_at_sale": 500.0,
                                                       "item_type": "LENS", "category": "OPTICAL_LENS"}]})
    _accept(db, _bill("B", 1, 560, grn_id="G", bill_date="2026-10-31"))
    assert product_cost.variance_by(db, "item_type") == {"LENS": 60.0}
    assert product_cost.variance_by(db, "category") == {"OPTICAL_LENS": 60.0}
    line = {"product_id": "P", "item_type": "LENS", "category": "OPTICAL_LENS", "quantity": 1,
            "taxable_value": 1000.0, "cost_at_sale": 500.0}
    (cat,) = pnl_by_category([{"items": [line]}], {}, product_cost.variance_by(db, "item_type"))
    assert (cat["category"], cat["cogs"]) == ("LENS", 560.0)


# The readers keep to their own shop and dates: one variance in scope (S1,
# today), one last month, one at another shop. The frame line is till-shaped
# (item_type FRAME, category FR): each reader takes the variance on its key.


def _till_orders():
    orders = _orders()
    orders[0]["items"][0]["item_type"] = "FRAME"
    return orders


def _scoped_variances():
    today = ist_today()
    rows = []
    for vid, amount, store, day in (("IN", 60.0, "S1", today), ("OLD", 7.0, "S1", today - timedelta(days=40)),
                                    ("S2", 11.0, "S2", today)):
        rows.append({"variance_id": vid, "amount": amount, "store_id": store, "category": "FR",
                     "by_item_type": [{"key": "FRAME", "amount": amount}],
                     "by_category": [{"key": "FR", "amount": amount}], "day": day.isoformat()})
    return rows


class _VarDb(_Db):
    """_Db, with cogs_variances in mongomock (it honours the reader's filter)."""

    def __init__(self, **colls):
        super().__init__(**colls)
        self.var = mongomock.MongoClient().v.cogs_variances
        self.var.insert_many(_scoped_variances())

    def get_collection(self, name):
        return self.var if name == "cogs_variances" else super().get_collection(name)


def test_every_reader_keeps_the_variance_to_its_shop_and_dates(monkeypatch):
    from api.routers.finance import pnl as pnl_mod
    from api.routers.finance import pnl_breakdown as pb
    from api.routers.reports import customers as rc
    from api.routers.reports import growth

    f, t = (d.isoformat() for d in _period())
    db = _VarDb(orders=_till_orders())
    for mod in (pnl_mod, pb):
        monkeypatch.setattr(mod, "_get_db", lambda: db)
        monkeypatch.setattr(mod, "_cost_by_product", lambda _db: {})
    monkeypatch.setattr(pnl_mod, "_payroll_cost", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(pnl_mod.je_service, "pnl_adjustments", lambda *_a, **_k: {})
    pnl = asyncio.run(pnl_mod.get_pnl(store_id="S1", from_date=f, to_date=t, current_user=SU))
    assert pnl["cogs_variance"] == 60.0

    monkeypatch.setattr(pb, "_store_maps", lambda _db: ({"S1": "E1", "S2": "E2"}, {}))
    monkeypatch.setattr(pb, "_payroll_by_store", lambda *_a, **_k: {})
    monkeypatch.setattr(pb, "_store_name_map", lambda _db: {"S1": "Shop", "S2": "Two"})
    stores = {r["store_id"]: r for r in asyncio.run(pb.get_pnl_by_store(
        from_date=f, to_date=t, entity_id="E1", current_user=SU))["stores"]}
    assert stores["S1"]["cogs"] == 1260.0 and "S2" not in stores
    cats = {c["category"]: c for c in asyncio.run(pb.get_pnl_by_category(
        from_date=f, to_date=t, store_id="S1", entity_id=None, current_user=SU))["categories"]}
    assert cats["FRAME"]["cogs"] == 660.0 and "FR" not in cats

    mdb = mongomock.MongoClient().r2
    mdb.cogs_variances.insert_many(_scoped_variances())
    for mod in (rc, growth):
        monkeypatch.setattr(mod, "get_order_repository", lambda: _Repo(_till_orders()))
        monkeypatch.setattr(mod, "get_db", lambda: mdb)
    f, t = _period()
    assert asyncio.run(rc.expense_vs_revenue(store_id="S1", from_date=f, to_date=t, current_user=SU))["cost"] == 1260.0
    by_cat = asyncio.run(growth.profit_by_category(store_id="S1", from_date=f, to_date=t, current_user=SU))
    assert {r["category"]: r["cost"] for r in by_cat["data"]} == {"FR": 660.0, "SG": 600.0}
    acc = {"user_id": "a", "roles": ["ACCOUNTANT"], "active_store_id": "S1", "store_ids": ["S1"]}
    by_store = asyncio.run(growth.profit_by_store(from_date=f, to_date=t, current_user=acc))
    assert {r["store_id"]: r["cost"] for r in by_store["data"]} == {"S1": 1260.0}
    assert _enterprise_kpis(monkeypatch, mdb, _till_orders())["gross_profit"] == 740.0



# --------------------------------------------------------------------------
# Fix round 3 (S4).
# --------------------------------------------------------------------------


def _written_off_g(db):
    """10 old pieces at 100; receipt G brought 4: 3 on the shelf, W1 written
    off on 2026-10-01 (its loss booked at the then average, 100)."""
    _pieces(db, 10)
    _receipt(db, "G", 4)
    _pieces(db, 3, "G", prefix="G")
    db.stock_units.insert_one({"stock_id": "W1", "product_id": "P", "store_id": "S1", "status": "VOID",
                               "quantity": 1, "grn_id": "G", "voided_at": "2026-10-01T10:00:00+00:00"})


def test_a_write_off_is_trued_up_at_the_average_then_not_after_a_later_bill():
    """W1 written off at 100; bill BH on receipt H (10 at 160) then moves the
    average to 130; G's bill, 4 at 130: W1's variance is 130 - 100 = 30 --
    never 130 - 130 = 0 at the live average."""
    db = _db(100.0)
    _written_off_g(db)
    _receipt(db, "H", 10)
    _pieces(db, 10, "H", prefix="H")
    _accept(db, _bill("BH", 10, 160, grn_id="H"))
    assert _avg(db) == 130.0
    _accept(db, _bill("B", 4, 130, grn_id="G", bill_date="2026-11-01"))
    var = db.cogs_variances.find_one({})
    assert (var["amount"], var["written_off"], var["sold_at_cost"]) == (30.0, 1, 100.0)


def test_a_write_off_older_than_the_carried_over_average_is_trued_up_at_it():
    """W1 written off on 10-01 at the pre-S4 average 100; the backfill
    carries 100 over (a history row dated the run); bill B1 on G1 (5 at 160)
    moves it to 120; G's bill at 130: the variance is 130 - 100 = 30, never
    130 - 120 = 10 at the live average."""
    from scripts import backfill_average_cost as bf

    db = _db(100.0, cost_source="PURCHASE_INVOICE")
    _written_off_g(db)
    assert bf.carry_over(db, db.products.find_one({}), "backfill:RUN1")
    _receipt(db, "G1", 5)
    _pieces(db, 5, "G1", prefix="K")
    _accept(db, _bill("B1", 5, 160, grn_id="G1"))
    assert _avg(db) == 120.0
    _accept(db, _bill("B", 4, 130, grn_id="G", bill_date="2026-11-01"))
    assert db.cogs_variances.find_one({})["amount"] == 30.0


def test_the_variance_split_adds_up_to_the_variance_to_the_paisa():
    """Rs 100.00 for 3 pieces, all sold at a frozen 30 on lines of three
    item_types: each share rounds to 3.33, the remainder goes to one so the
    split adds up to the variance, 10.00 -- never 9.99."""
    db = _db(30.0)
    _receipt(db, "G", 3)
    _sold(db, "G", [30.0, 30.0, 30.0])
    for i, kind in enumerate(("FRAME", "LENS", "SUNGLASS")):
        db.orders.update_one({"order_id": f"OG{i}"}, {"$set": {"items.0.item_type": kind}})
    bill = _bill("B", 3, 33.33, grn_id="G", bill_date="2026-10-31")
    bill["lines"][0]["taxable"] = 100.0
    _accept(db, bill)
    split = product_cost.variance_by(db, "item_type")
    assert set(split) == {"FRAME", "LENS", "SUNGLASS"}
    assert round(sum(split.values()), 2) == product_cost.variance_by(db)[None] == 10.0


# --------------------------------------------------------------------------
# Fix round 14 (S4): a stuck count finished after the average moved.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("moved_by", ("correction", "bill"))
def test_a_stuck_count_finished_after_the_average_moved_books_the_loss_at_the_write_off(
    monkeypatch, moved_by
):
    """A count VOIDed W0, W1 (receipt G, unbilled) on 10-25 at the average
    then (100) and lost its shrinkage write. The average then moves (a
    correction to 200, or bill BH blending it to 200); the admin finishes
    the count: the loss is 2 x 100 -- the average at the write-off, the figure
    G's bill (2 at 300) trues them up from. Loss + true-up = the 600 billed
    (never 400 + 400 = 800)."""
    from api.routers.inventory import stock_count_reconcile as scr

    db = _db(None)
    product_cost.seed(db, "P", 100.0, key="open", kind="OPENING_STOCK", actor="acc")
    hist = db.products.find_one({})["cost_history"]
    hist[0]["at"] = "2026-10-20T10:00:00+00:00"
    db.products.update_one({}, {"$set": {"cost_history": hist}})
    _pieces(db, 10)
    _receipt(db, "G", 2)
    _pieces(db, 2, "G", status="VOID", prefix="W")
    db.stock_units.update_many({"status": "VOID"}, {"$set": {
        "void_reason": "cycle-count-reconcile:C1", "voided_at": "2026-10-25T10:00:00+00:00"}})
    db.stock_counts.insert_one({"count_id": "C1", "status": "reconciling", "store_id": "S1", "variances": [
        {"product_id": "P", "product_name": "Frame", "sku": "F", "system_quantity": 12, "physical_quantity": 10}]})
    if moved_by == "correction":
        product_cost.correct(db, "P", 200.0, "price fix", "acc", expected=100.0)
    else:
        _receipt(db, "H", 10)
        _pieces(db, 10, "H", prefix="H")
        _accept(db, _bill("BH", 10, 300, grn_id="H"))
    assert _avg(db) == 200.0

    monkeypatch.setattr(scr, "_get_db", lambda: db)
    admin = {"user_id": "a", "roles": ["ADMIN"], "store_ids": ["S1"], "active_store_id": "S1"}
    out = asyncio.run(scr.finish_stuck_stock_count_reconcile("C1", None, admin))
    assert out["shrinkage_value_written_off"] == 200.0
    _accept(db, _bill("B", 2, 300, grn_id="G", bill_date="2026-11-01"))

    (shrink,) = db.stock_shrinkage.find({"count_id": "C1"})
    assert (shrink["unit_cost"], shrink["shrinkage_value"]) == (100.0, 200.0)
    row = db.products.find_one({})["cost_history"][-1]
    assert sorted(row["claimed"]) == ["W10", "W11"] and row["variance"]["amount"] == 400.0
    assert round(shrink["shrinkage_value"] + row["variance"]["amount"], 2) == 600.0


class _SkuRepo:
    def __init__(self, db):
        self.db = db

    def find_by_sku(self, sku):
        return self.db.products.find_one({"sku": sku}, {"_id": 0})


def test_an_online_piece_sold_before_its_bill_is_trued_up_and_books_balance():
    """9 old pieces at 100; receipt G brought 2 -- 1 held, 1 sold ONLINE
    before its bill. The Shopify line (shopify_ingest._map_line_items) names
    the Shopify product id and keeps the IMS one in ims_product_id; it
    freezes the average at sale, 100. Bill B, 2 at 160: the held piece
    averages in, (900 + 160) / 10 = 106.00; the online piece is trued up by
    160 - 100 = 60. Stock + the line's COGS + the variance is exactly what
    was paid -- the bill's 160 for the online piece never drops out."""
    from api.services import margin_basis, shopify_ingest, stock_value

    db = _db(100.0, category="FRAME", sku="SKU-P")
    _pieces(db, 9)
    _receipt(db, "G", 2)
    _pieces(db, 1, "G")
    (line,) = shopify_ingest._map_line_items(
        {"line_items": [{"product_id": 8812345, "sku": "SKU-P", "price": "500.00", "quantity": 1}]},
        _SkuRepo(db))
    assert (line["product_id"], line["ims_product_id"], line["cost_at_sale"]) == ("8812345", "P", 100.0)
    db.stock_units.insert_one({"stock_id": "SG9", "product_id": "P", "store_id": "S1", "status": "SOLD",
                               "quantity": 1, "grn_id": "G", "order_id": "OWEB",
                               "sold_at": "2026-10-20T10:00:00"})
    db.orders.insert_one({"order_id": "OWEB", "items": [line]})
    _accept(db, _bill("B", 2, 160, grn_id="G", bill_date="2026-10-31", store_id="S1"))
    assert _avg(db) == 106.0
    (var,) = db.cogs_variances.find({})
    assert (var["stock_ids"], var["sold_at_cost"], var["amount"]) == (["SG9"], 100.0, 60.0)
    assert product_cost.variance_by(db) == {None: 60.0}
    stock = stock_value.value(10, db.products.find_one({}))
    cogs = margin_basis.line_unit_cost(line, margin_basis.cost_by_product(db))
    assert round(stock + cogs + product_cost.variance_by(db)[None], 2) == 9 * 100 + 2 * 160


def test_an_online_line_freezes_no_cost_for_a_product_without_one():
    """A product with no cost yet (or only a cataloguing price) freezes None,
    never 0: the readers then estimate, they never book Rs 0 COGS."""
    from api.services import shopify_ingest

    for extra in ({"cost_price": None}, {"cost_price": 250.0, "cost_price_is_catalogue_only": True}):
        db = mongomock.MongoClient().s4
        db.products.insert_one({"product_id": "P", "sku": "SKU-P", **extra})
        (line,) = shopify_ingest._map_line_items(
            {"line_items": [{"product_id": 1, "sku": "SKU-P", "price": "500.00", "quantity": 1}]}, _SkuRepo(db))
        assert line["ims_product_id"] == "P" and line["cost_at_sale"] is None
