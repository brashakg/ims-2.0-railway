"""
Collection Insights (Collections Phase 1, Track 2) -- service + endpoint tests.

Locks the contract of api/services/collection_insights.py +
api/routers/collections_insights.py:

  * member_ids reads the materialised collection_products view, prefers the
    row's product_id, falls back to a sku -> products join CHUNKED at 1000,
    and reports capped (5000-row cap / membership_capped flag).
  * stock value is THE stock value rule (S4): pieces x average cost, never
    the offer price; pieces with no cost are counted in uncosted_units
    (value_basis 'cost', None when nothing is in stock).
  * movement windows (d7/d30/d90) bucket by IST calendar day (orders are UTC;
    an Indian early morning / late evening must not split across UTC days).
  * sell-through / days-of-cover edge cases (zero sales, zero stock).
  * margin_30d is None unless 100% of sold units carried cost_at_sale.
  * the summary endpoint path is BATCHED (one membership read, one stock agg,
    one movement agg -- no per-collection N+1).
  * STORE SCOPING: anyone but an admin reads their own shop (the shared rule,
    403 for another shop named); SALES_STAFF
    is 403'd (policy rows: ADMIN/AREA_MANAGER/STORE_MANAGER/CATALOG_MANAGER).
  * preview evaluates unsaved rules and returns
    {match_count, units_on_hand, sample<=12}.

CI-robust: service tests run on in-memory fakes (MockCollection + a tiny
orders-aggregation emulator that interprets exactly the pipeline the service
emits, including $dateTrunc timezone Asia/Kolkata); endpoint tests use the
shared conftest TestClient and assert only order-independent outcomes.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_collection_insights.py -q
"""

import os
import sys
from datetime import datetime, time, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from database.connection import MockCollection  # noqa: E402
from api.services import collection_insights as ci  # noqa: E402
from api.services import rbac_policy as rbac  # noqa: E402


_IST = timedelta(hours=5, minutes=30)


# ---------------------------------------------------------------------------
# fakes (mirror the MockDatabase/MockCollection style of the materializer tests)
# ---------------------------------------------------------------------------


class _FakeDB:
    """dict-of-collections DB double; db[name] mirrors MockDatabase."""

    def __init__(self, colls=None):
        self._c = dict(colls or {})

    def __getitem__(self, name):
        if name not in self._c:
            self._c[name] = MockCollection(name)
        return self._c[name]


class _CannedAggColl(MockCollection):
    """MockCollection whose aggregate() returns canned group rows and records
    every pipeline it was asked to run."""

    def __init__(self, name, rows=None):
        super().__init__(name)
        self.agg_rows = list(rows or [])
        self.pipelines = []

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        return list(self.agg_rows)


class _RecordingColl(MockCollection):
    """MockCollection that records find() filters (to prove batching)."""

    def __init__(self, name):
        super().__init__(name)
        self.find_filters = []

    def find(self, filter=None, projection=None):
        self.find_filters.append(filter)
        return super().find(filter, projection)


class _SynthesizingProducts:
    """products stand-in: records find() filters and synthesizes a spine row
    {sku, product_id: 'P-'+sku} for every requested sku -- lets the chunking
    test resolve 2500 SKUs without storing 2500 docs."""

    def __init__(self):
        self.filters = []

    def find(self, filter=None, projection=None):
        self.filters.append(filter)
        skus = ((filter or {}).get("sku") or {}).get("$in") or []
        return [{"sku": s, "product_id": "P-" + s} for s in skus]


class _EmulatedOrders:
    """Interprets EXACTLY the movement pipeline collection_insights emits
    ($match cutoff/status/$in [+store] -> $unwind items -> re-$match ->
    $group by product_id + $dateTrunc(day, tz Asia/Kolkata) [+store]) against
    plain order docs, with Mongo's documented semantics for those stages --
    so the service's Python-side IST window bucketing is exercised
    end-to-end from known created_at datetimes."""

    def __init__(self, docs):
        self.docs = list(docs)
        self.pipelines = []

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        match = pipeline[0]["$match"]
        cutoff = match["created_at"]["$gte"]
        nin = set(match["status"]["$nin"])
        pid_in = set(match["items.product_id"]["$in"])
        store = match.get("store_id")
        gid_spec = pipeline[3]["$group"]["_id"]
        by_store = "store_id" in gid_spec
        groups = {}
        for o in self.docs:
            created = o.get("created_at")
            if not isinstance(created, datetime) or created < cutoff:
                continue
            if o.get("status") in nin:
                continue
            if store and o.get("store_id") != store:
                continue
            for it in o.get("items") or []:
                pid = it.get("product_id")
                if pid not in pid_in:
                    continue
                qty = it.get("qty")
                if qty is None:
                    qty = it.get("quantity")
                if qty is None:
                    qty = 1
                # $dateTrunc unit day, timezone Asia/Kolkata: the UTC instant
                # of the IST midnight the sale falls into.
                ist_day = datetime.combine((created + _IST).date(), time.min) - _IST
                key = (pid, ist_day, o.get("store_id") if by_store else None)
                g = groups.setdefault(
                    key,
                    {"units": 0, "revenue": 0.0, "revenue_ex_gst": 0.0,
                     "unrated_gross": 0.0, "unrated_hsn": None,
                     "unrated_category": None,
                     "cogs": 0.0, "cogs_units": 0, "last_sold": None},
                )
                g["units"] += qty
                g["revenue"] += it.get("item_total") or 0
                # margin_basis.LINE_REVENUE_EX_GST_GROUP: taxable_value, else
                # item_total / (1 + gst_rate/100); a line with neither comes
                # back as unrated gross + its hints (test_gst_basis_margins
                # runs the real accumulators on mongomock).
                tv, rate = it.get("taxable_value"), it.get("gst_rate")
                gross = it.get("item_total") or 0
                if tv is not None:
                    g["revenue_ex_gst"] += tv
                elif rate is not None:
                    g["revenue_ex_gst"] += gross / (1 + rate / 100)
                else:
                    g["unrated_gross"] += gross
                    g["unrated_hsn"] = it.get("hsn_code") or g["unrated_hsn"]
                    g["unrated_category"] = (
                        it.get("item_type") or it.get("category") or g["unrated_category"]
                    )
                cost = it.get("cost_at_sale")
                if cost is not None:
                    g["cogs"] += cost * qty
                    g["cogs_units"] += qty
                if g["last_sold"] is None or created > g["last_sold"]:
                    g["last_sold"] = created
        rows = []
        for (pid, day, sid), g in groups.items():
            _id = {"product_id": pid, "day": day}
            if by_store:
                _id["store_id"] = sid
            rows.append({"_id": _id, **g})
        return rows


def _ist_midnight_utc():
    """UTC instant of TODAY's IST midnight (naive UTC, matching the service).
    Day-granularity math; only an exactly-at-IST-midnight run could flake."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return datetime.combine((now + _IST).date(), time.min) - _IST


def _order(oid, created_at, items, status="COMPLETED", store_id="BV-01"):
    return {
        "order_id": oid,
        "status": status,
        "store_id": store_id,
        "created_at": created_at,
        "items": items,
    }


# ===========================================================================
# member_ids -- product_id preference, sku fallback, chunking, capped flag
# ===========================================================================


def test_member_ids_prefers_product_id_and_falls_back_to_sku_join():
    db = _FakeDB()
    view = db["collection_products"]
    view.insert_one({"collection_id": "C1", "sku": "SKU-A", "product_id": "PA", "position": 0})
    view.insert_one({"collection_id": "C1", "sku": "SKU-B", "position": 1})  # pid null -> join
    view.insert_one({"collection_id": "C1", "sku": "SKU-C", "position": 2})  # unresolvable
    view.insert_one({"collection_id": "OTHER", "sku": "SKU-Z", "product_id": "PZ"})
    db["products"].insert_one({"sku": "SKU-B", "product_id": "PB"})

    m = ci.member_ids(db, "C1")
    assert m["product_ids"] == ["PA", "PB"]
    assert m["skus"] == ["SKU-A", "SKU-B", "SKU-C"]
    assert m["capped"] is False


def test_member_ids_chunks_sku_fallback_at_1000():
    prods = _SynthesizingProducts()
    db = _FakeDB({"products": prods})
    view = db["collection_products"]
    for i in range(2500):  # no product_id on any row -> all go through the join
        view.insert_one({"collection_id": "C1", "sku": f"S{i:04d}", "position": i})

    m = ci.member_ids(db, "C1")
    assert len(m["product_ids"]) == 2500
    assert len(prods.filters) == 3  # 1000 + 1000 + 500
    assert max(len(f["sku"]["$in"]) for f in prods.filters) <= 1000


def test_member_ids_capped_at_materializer_cap():
    db = _FakeDB()
    view = db["collection_products"]
    for i in range(5000):
        view.insert_one({"collection_id": "C1", "sku": f"S{i}", "product_id": f"P{i}"})
    assert ci.member_ids(db, "C1")["capped"] is True


def test_member_ids_capped_via_flag_on_collection_doc():
    db = _FakeDB()
    db["collection_products"].insert_one(
        {"collection_id": "C1", "sku": "A", "product_id": "PA"}
    )
    # flag passed inline
    m = ci.member_ids(db, "C1", collection={"membership_capped": True})
    assert m["capped"] is True
    # flag read off the stored doc when no doc is passed
    db["ecom_collections"].insert_one(
        {"collection_id": "C1", "membership_capped": True}
    )
    assert ci.member_ids(db, "C1")["capped"] is True


def test_member_ids_failsoft_no_db():
    assert ci.member_ids(None, "C1") == {"product_ids": [], "skus": [], "listings": 0, "capped": False}


# ===========================================================================
# stock value at cost; pieces with no cost counted apart (S4)
# ===========================================================================

_MEMBERS_2 = {"product_ids": ["P1", "P2"], "skus": ["S1", "S2"], "capped": False}
_STOCK_ROWS = [
    {"_id": {"product_id": "P1"}, "on_hand": 2},
    {"_id": {"product_id": "P2"}, "on_hand": 3},
]


def _stock_db(price_docs, rows=_STOCK_ROWS):
    db = _FakeDB({"stock_units": _CannedAggColl("stock_units", rows)})
    for d in price_docs:
        db["products"].insert_one(dict(d))
    return db


def test_stock_value_basis_cost_when_all_costed():
    db = _stock_db([
        {"product_id": "P1", "cost_price": 100, "offer_price": 150, "mrp": 200},
        {"product_id": "P2", "cost_price": 50, "offer_price": 80, "mrp": 100},
    ])
    s = ci.stock_summary(db, _MEMBERS_2)
    assert s["units_on_hand"] == 5
    assert s["stock_value"] == 2 * 100 + 3 * 50
    assert s["value_basis"] == "cost"
    assert s["stock_value_mrp"] == 2 * 200 + 3 * 100


def test_stock_value_never_prices_uncosted_pieces_at_the_offer():
    db = _stock_db([
        {"product_id": "P1", "offer_price": 150, "mrp": 200},
        {"product_id": "P2", "cost_price": None, "offer_price": 80, "mrp": 100},
    ])
    s = ci.stock_summary(db, _MEMBERS_2)
    assert (s["stock_value"], s["uncosted_units"], s["value_basis"]) == (0.0, 5, "cost")
    assert s["stock_value_mrp"] == 2 * 200 + 3 * 100  # the selling value, labelled apart


def test_stock_value_counts_the_pieces_with_no_cost():
    db = _stock_db([
        {"product_id": "P1", "cost_price": 100, "offer_price": 150, "mrp": 200},
        {"product_id": "P2", "offer_price": 80, "mrp": 100},
    ])
    s = ci.stock_summary(db, _MEMBERS_2)
    assert (s["stock_value"], s["uncosted_units"]) == (2 * 100, 3)


def test_stock_value_basis_none_when_no_stock():
    db = _stock_db([{"product_id": "P1", "cost_price": 100}], rows=[])
    s = ci.stock_summary(db, _MEMBERS_2)
    assert s["units_on_hand"] == 0
    assert s["stock_value"] == 0.0
    assert s["value_basis"] is None


def test_stock_rollup_filters_available_and_store():
    coll = _CannedAggColl("stock_units", [])
    db = _FakeDB({"stock_units": coll})
    ci.stock_summary(db, _MEMBERS_2, store_id="BV-01")
    match = coll.pipelines[0][0]["$match"]
    # The status half is THE shared on-hand decision, not a literal typed here:
    # a bare status == "AVAILABLE" made this reader disagree with every other
    # on-hand reader about a legacy lowercase / IN_STOCK / status-absent unit.
    from api.services.item_events import on_hand_match

    assert match["$or"] == on_hand_match()["$or"]
    assert "status" not in match
    assert match["store_id"] == "BV-01"
    assert set(match["product_id"]["$in"]) == {"P1", "P2"}
    # quantity coalesces to 1 when absent
    group = coll.pipelines[0][1]["$group"]
    assert group["on_hand"] == {"$sum": {"$ifNull": ["$quantity", 1]}}


# ===========================================================================
# movement windows -- IST-day bucketing from known created_at datetimes
# ===========================================================================


def test_movement_windows_bucket_by_ist_day():
    mid = _ist_midnight_utc()
    orders = [
        # 01:00 IST today (19:30 UTC *yesterday* -- crosses the UTC day line).
        _order("O1", mid + timedelta(hours=1),
               [{"product_id": "P_TODAY", "qty": 2, "item_total": 1000.0,
                 "cost_at_sale": 300.0}]),
        # 01:00 IST six IST days ago: age 6 -> INSIDE d7. A UTC-day bucketing
        # bug would age it 7 and drop it from d7 -- the discriminating case.
        _order("O2", mid - timedelta(days=6) + timedelta(hours=1),
               [{"product_id": "P_EDGE6", "quantity": 1, "item_total": 400.0,
                 "cost_at_sale": 100.0}]),
        # exactly 7 IST days ago: age 7 -> NOT d7, still d30.
        _order("O3", mid - timedelta(days=7) + timedelta(hours=5),
               [{"product_id": "P_EDGE7", "qty": 1, "item_total": 500.0,
                 "cost_at_sale": 200.0}]),
        # 40 days ago -> d90 only (and: no cost_at_sale -> no cogs).
        _order("O4", mid - timedelta(days=40) + timedelta(hours=2),
               [{"product_id": "P_40D", "qty": 3, "item_total": 900.0}]),
        # 100 days ago -> outside the 90-day window entirely.
        _order("O5", mid - timedelta(days=100),
               [{"product_id": "P_OLD", "qty": 50, "item_total": 9999.0}]),
        # cancelled + draft today -> excluded by status.
        _order("O6", mid + timedelta(hours=2),
               [{"product_id": "P_TODAY", "qty": 10, "item_total": 5000.0}],
               status="CANCELLED"),
        _order("O7", mid + timedelta(hours=2),
               [{"product_id": "P_TODAY", "qty": 10, "item_total": 5000.0}],
               status="DRAFT"),
    ]
    db = _FakeDB({"orders": _EmulatedOrders(orders)})
    members = {"product_ids": ["P_TODAY", "P_EDGE6", "P_EDGE7", "P_40D", "P_OLD"]}
    mv = ci.movement_summary(db, members, days=90)

    per = mv["per_product"]
    assert per["P_TODAY"]["units_d7"] == 2
    assert per["P_EDGE6"]["units_d7"] == 1          # IST bucketing keeps it in d7
    assert "P_EDGE7" in per and per["P_EDGE7"]["units_d7"] == 0
    assert per["P_EDGE7"]["units_d30"] == 1
    assert per["P_40D"]["units_d30"] == 0 and per["P_40D"]["units_d90"] == 3
    assert "P_OLD" not in per                        # outside the window

    t = mv["totals"]
    assert t["units_d7"] == 3                        # O1 + O2
    assert t["units_d30"] == 4                       # + O3
    assert t["units_d90"] == 7                       # + O4
    assert t["revenue_d30"] == 1900.0
    assert t["cogs_d30"] == 2 * 300 + 1 * 100 + 1 * 200
    assert t["cogs_units_d30"] == 4
    assert t["last_sold"] == mid + timedelta(hours=1)


def test_movement_store_filter_reaches_the_match_stage():
    coll = _EmulatedOrders([])
    db = _FakeDB({"orders": coll})
    ci.movement_summary(db, {"product_ids": ["P1"]}, store_id="BV-02")
    match = coll.pipelines[0][0]["$match"]
    assert match["store_id"] == "BV-02"
    # HISTORICAL = pre-IMS imported order (bvi_import) -- never movement/revenue.
    assert match["status"] == {"$nin": ["CANCELLED", "DRAFT", "HISTORICAL"]}
    # the day sub-group is IST-truncated
    gid = coll.pipelines[0][3]["$group"]["_id"]
    assert gid["day"]["$dateTrunc"]["timezone"] == "Asia/Kolkata"


# ===========================================================================
# derived KPI math -- sell-through / days-of-cover / margin edges
# ===========================================================================


def test_sell_through_edges():
    assert ci.sell_through(0, 0) is None            # no signal
    assert ci.sell_through(0, 5) == 0.0             # stock, no sales
    assert ci.sell_through(5, 0) == 1.0             # sold out
    assert ci.sell_through(3, 9) == 0.25


def test_days_of_cover_edges():
    assert ci.days_of_cover(0, 0) is None           # no signal
    assert ci.days_of_cover(10, 0) == 999.0         # stock, zero sales -> cap
    assert ci.days_of_cover(0, 5) == 0.0            # sold out
    assert ci.days_of_cover(30, 30) == 30.0
    assert ci.days_of_cover(100000, 1) == 999.0     # cap


def test_margin_null_unless_full_cogs_coverage():
    assert ci.margin_30d(1000.0, 400.0, 4, 4) == 600.0
    assert ci.margin_30d(1000.0, 400.0, 4, 3) is None   # partial coverage
    assert ci.margin_30d(0.0, 0.0, 0, 0) is None        # nothing sold


# ===========================================================================
# kpis composition (end-to-end over the fakes)
# ===========================================================================


def _kpi_db(order_items_cost=300.0):
    mid = _ist_midnight_utc()
    db = _FakeDB(
        {
            "stock_units": _CannedAggColl(
                "stock_units", [{"_id": {"product_id": "P1"}, "on_hand": 6}]
            ),
            "orders": _EmulatedOrders(
                [
                    _order(
                        "O1",
                        mid + timedelta(hours=1),
                        [
                            {
                                "product_id": "P1",
                                "qty": 2,
                                "item_total": 1000.0,
                                "cost_at_sale": order_items_cost,
                            }
                        ],
                    )
                ]
            ),
        }
    )
    db["collection_products"].insert_one(
        {"collection_id": "C1", "sku": "SKU-1", "product_id": "P1", "position": 0}
    )
    db["products"].insert_one(
        {"product_id": "P1", "sku": "SKU-1", "cost_price": 250.0,
         "offer_price": 400.0, "mrp": 500.0}
    )
    return db


def test_kpis_composition_full_cogs():
    db = _kpi_db()
    coll_doc = {
        "collection_id": "C1",
        "title": "Test",
        "collection_type": "SMART",
        "materialized_at": datetime(2026, 7, 1, 10, 0, 0),
    }
    k = ci.kpis(db, coll_doc)
    assert k["members"] == 1
    assert k["units_on_hand"] == 6
    assert k["stock_value"] == 6 * 250.0 and k["value_basis"] == "cost"
    assert k["stock_value_mrp"] == 6 * 500.0
    assert k["sold"] == {"d7": 2, "d30": 2, "d90": 2}
    assert k["revenue_30d"] == 1000.0
    # The line carries no taxable_value, gst_rate or category, so it is read
    # at the default category rate (5%), as line_revenue_ex_gst reads it:
    # 1000 / 1.05 - 2 * 300 = 952.38 - 600 = 352.38.
    assert k["margin_30d"] == 352.38
    assert k["sell_through_30d"] == round(2 / 8, 4)
    assert k["days_of_cover"] == round(6 / (2 / 30.0), 1)
    assert k["membership_capped"] is False
    assert k["materialized_at"] == "2026-07-01T10:00:00"


def test_kpis_margin_none_when_a_line_has_no_cost():
    db = _kpi_db(order_items_cost=None)
    k = ci.kpis(db, {"collection_id": "C1", "title": "Test"})
    assert k["revenue_30d"] == 1000.0
    assert k["margin_30d"] is None  # cogs coverage < 100% -> labelled honestly


def test_kpis_margin_is_on_revenue_net_of_gst():
    """cost_at_sale is ex-GST, item_total incl-GST: margin_30d is the lines'
    taxable value less cogs. A 5% line (paid 1050) and an 18% line (paid
    1180), each 1000 before GST and costing 600: 2000 - 1200 = 800, not the
    2230 - 1200 = 1030 that counts the GST as margin."""
    db = _kpi_db()
    mid = _ist_midnight_utc()
    db._c["orders"] = _EmulatedOrders([
        _order("G5", mid + timedelta(hours=1), [
            {"product_id": "P1", "qty": 1, "item_total": 1050.0, "gst_rate": 5.0,
             "taxable_value": 1000.0, "cost_at_sale": 600.0}]),
        _order("G18", mid + timedelta(hours=2), [
            {"product_id": "P1", "qty": 1, "item_total": 1180.0, "gst_rate": 18.0,
             "taxable_value": 1000.0, "cost_at_sale": 600.0}]),
    ])
    k = ci.kpis(db, {"collection_id": "C1", "title": "Test"})
    assert k["revenue_30d"] == 2230.0  # sales figure: what was paid
    assert k["margin_30d"] == 800.0
    # and the database sums the shared ex-GST line rule, not its own copy
    group = db._c["orders"].pipelines[0][3]["$group"]
    from api.services.margin_basis import LINE_REVENUE_EX_GST_GROUP
    for key, acc in LINE_REVENUE_EX_GST_GROUP.items():
        assert group[key] == acc


# ===========================================================================
# batched summary -- no N+1
# ===========================================================================


def test_summary_is_batched_and_sorted_by_sold_desc():
    mid = _ist_midnight_utc()
    view = _RecordingColl("collection_products")
    stock = _CannedAggColl(
        "stock_units",
        [
            {"_id": {"product_id": "P1"}, "on_hand": 4},
            {"_id": {"product_id": "P2"}, "on_hand": 1},
        ],
    )
    orders = _EmulatedOrders(
        [
            _order("O1", mid + timedelta(hours=1),
                   [{"product_id": "P2", "qty": 5, "item_total": 2500.0}]),
        ]
    )
    db = _FakeDB({"collection_products": view, "stock_units": stock, "orders": orders})
    view.insert_one({"collection_id": "CA", "sku": "S1", "product_id": "P1"})
    view.insert_one({"collection_id": "CB", "sku": "S2", "product_id": "P2"})
    db["products"].insert_one({"product_id": "P1", "sku": "S1", "cost_price": 100.0, "mrp": 150.0})
    db["products"].insert_one({"product_id": "P2", "sku": "S2", "offer_price": 300.0, "mrp": 400.0})

    docs = [
        {"collection_id": "CA", "title": "A", "collection_type": "CUSTOM", "published": True},
        {"collection_id": "CB", "title": "B", "collection_type": "SMART", "published": False},
    ]
    rows = ci.summary(db, docs)

    # BATCHED: one membership read over the view, one stock agg, one movement agg.
    assert len(view.find_filters) == 1
    assert view.find_filters[0] == {"collection_id": {"$in": ["CA", "CB"]}}
    assert len(stock.pipelines) == 1
    assert len(orders.pipelines) == 1

    # sold_30d desc -> CB (5 sold) first.
    assert [r["collection_id"] for r in rows] == ["CB", "CA"]
    cb, ca = rows[0], rows[1]
    assert cb["sold_30d"] == 5 and cb["on_hand"] == 1
    assert (cb["stock_value"], cb["uncosted_units"]) == (0.0, 1)
    assert cb["published"] is False and cb["collection_type"] == "SMART"
    assert ca["sold_30d"] == 0 and ca["on_hand"] == 4
    assert ca["stock_value"] == 400.0 and ca["value_basis"] == "cost"
    assert ca["members"] == 1


def test_summary_failsoft_no_db_or_empty():
    assert ci.summary(None, [{"collection_id": "X"}]) == []
    assert ci.summary(_FakeDB(), []) == []


# ===========================================================================
# per-store breakdown
# ===========================================================================


def test_store_breakdown_rows_names_and_metrics():
    mid = _ist_midnight_utc()
    stock = _CannedAggColl(
        "stock_units",
        [
            {"_id": {"product_id": "P1", "store_id": "BV-01"}, "on_hand": 4},
            {"_id": {"product_id": "P2", "store_id": "BV-02"}, "on_hand": 2},
        ],
    )
    orders = _EmulatedOrders(
        [
            _order("O1", mid + timedelta(hours=1),
                   [{"product_id": "P1", "qty": 4, "item_total": 2000.0}],
                   store_id="BV-01"),
        ]
    )
    db = _FakeDB({"stock_units": stock, "orders": orders})
    db["collection_products"].insert_one({"collection_id": "C1", "sku": "S1", "product_id": "P1"})
    db["collection_products"].insert_one({"collection_id": "C1", "sku": "S2", "product_id": "P2"})
    db["products"].insert_one({"product_id": "P1", "sku": "S1", "cost_price": 100.0, "mrp": 150.0})
    db["products"].insert_one({"product_id": "P2", "sku": "S2", "offer_price": 300.0, "mrp": 400.0})
    db["stores"].insert_one({"store_id": "BV-01", "store_name": "Better Vision Main"})
    # BV-02 has no stores row -> name falls back to the id.

    rows = ci.store_breakdown(db, {"collection_id": "C1", "title": "T"})
    by_id = {r["store_id"]: r for r in rows}
    assert set(by_id) == {"BV-01", "BV-02"}

    s1 = by_id["BV-01"]
    assert s1["store_name"] == "Better Vision Main"
    assert s1["on_hand"] == 4 and s1["stock_value"] == 400.0
    assert s1["value_basis"] == "cost"
    assert s1["sold_30d"] == 4
    assert s1["sell_through"] == 0.5            # 4 sold / (4 + 4 on hand)
    assert s1["days_of_cover"] == round(4 / (4 / 30.0), 1)

    s2 = by_id["BV-02"]
    assert s2["store_name"] == "BV-02"          # fail-soft to the id
    assert s2["on_hand"] == 2 and (s2["stock_value"], s2["uncosted_units"]) == (0.0, 2)
    assert s2["sold_30d"] == 0
    assert s2["sell_through"] == 0.0
    assert s2["days_of_cover"] == 999.0


# ===========================================================================
# preview -- unsaved rules over a faked product scan
# ===========================================================================


_LIVE = {"shopify_product_id": "gid://shopify/Product/1"}


def _preview_db():
    """Listings ON the website (catalog_products with a Shopify id): THE
    website membership rule reads the rules over what the push sends."""
    db = _FakeDB(
        {
            "stock_units": _CannedAggColl(
                "stock_units", [{"_id": {"product_id": "RB1"}, "on_hand": 4}]
            )
        }
    )
    db["catalog_products"].insert_one(
        {
            "id": "RB1",
            "product_id": "RB1",
            "sku": "RB-0001",
            "brand": "Ray-Ban",
            "model": "RB2140",
            "mrp": 8000.0,
            "images": ["https://img/rb1.jpg", "https://img/rb1b.jpg"],
            "category": "SUNGLASS",
            "ecom": dict(_LIVE),
        }
    )
    db["catalog_products"].insert_one(
        {
            "id": "GZ1",
            "product_id": "GZ1",
            "sku": "GZ-0001",
            "brand": "Gucci",
            "model": "GG0001",
            "mrp": 20000.0,
            "category": "FRAME",
            "ecom": dict(_LIVE),
        }
    )
    # catalogue row with no product_id, no images and the model / MRP only in
    # attributes / pricing (the sample row's fallbacks). Its vendor is its own
    # brand field -- what the push sends (product_input: vendor = brand).
    db["catalog_products"].insert_one(
        {
            "id": "RBCAT",
            "sku": "RB-CAT-1",
            "brand": "Ray-Ban",
            "category": "SUNGLASS",
            "attributes": {"brand_name": "Ray-Ban", "model_no": "RBX"},
            "pricing": {"mrp": 6000.0},
            "ecom": dict(_LIVE),
        }
    )
    # the same brand, NOT on the website: never counted.
    db["catalog_products"].insert_one(
        {"id": "RBOFF", "sku": "RB-OFF", "brand": "Ray-Ban", "category": "SUNGLASS", "mrp": 5000.0, "ecom": {}}
    )
    return db


def test_preview_matches_samples_and_counts_stock():
    db = _preview_db()
    res = ci.preview(
        db, [{"field": "brand", "relation": "EQUALS", "value": "ray-ban"}],
        disjunctive=False,
    )
    assert res["match_count"] == 2                 # RB1 + the catalog-only row (not RB-OFF)
    assert res["units_on_hand"] == 4               # canned stock for RB1
    assert len(res["sample"]) == 2
    by_sku = {r["sku"]: r for r in res["sample"]}
    assert set(by_sku) == {"RB-0001", "RB-CAT-1"}
    rb = by_sku["RB-0001"]
    assert rb["brand"] == "Ray-Ban" and rb["model"] == "RB2140"
    assert rb["mrp"] == 8000.0
    assert rb["image"] == "https://img/rb1.jpg"    # first image
    cat = by_sku["RB-CAT-1"]
    assert cat["image"] is None
    assert cat["mrp"] == 6000.0                    # pricing.mrp fallback
    assert cat["model"] == "RBX"                   # attributes.model_no fallback


def test_preview_accepts_shopify_shape_rules():
    db = _preview_db()
    res = ci.preview(
        db, [{"column": "VENDOR", "relation": "EQUALS", "condition": "Ray-Ban"}],
    )
    assert res["match_count"] == 2


def test_preview_sample_capped_at_12():
    db = _FakeDB({"stock_units": _CannedAggColl("stock_units", [])})
    for i in range(20):
        db["catalog_products"].insert_one(
            {"id": f"P{i}", "product_id": f"P{i}", "sku": f"SKU-{i}", "brand": "Lenskart", "mrp": 100,
             "category": "SUNGLASS", "ecom": dict(_LIVE)}
        )
    res = ci.preview(
        db, [{"field": "brand", "relation": "EQUALS", "value": "lenskart"}]
    )
    assert res["match_count"] == 20
    assert len(res["sample"]) == 12


def test_preview_failsoft_no_db_and_no_rules():
    assert ci.preview(None, [{"field": "brand", "relation": "EQUALS", "value": "x"}]) == {
        "match_count": 0, "units_on_hand": 0, "sample": [], "scanned": 0,
        "scan_capped": False,
    }
    # no valid rules -> matches nothing (never an accidental match-all)
    db = _preview_db()
    assert ci.preview(db, [])["match_count"] == 0


# ===========================================================================
# RBAC policy rows
# ===========================================================================

_INSIGHT_PATHS = [
    ("GET", "/api/v1/collections/insights/summary"),
    ("POST", "/api/v1/collections/preview"),
    ("GET", "/api/v1/collections/COLL-1/insights"),
    ("GET", "/api/v1/collections/COLL-1/insights/stores"),
]


def test_insights_routes_catalogued_with_manager_roles():
    for method, path in _INSIGHT_PATHS:
        assert rbac.policy_for(method, path) is not None, f"{method} {path}"
        for role in ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER",
                     "SUPERADMIN"):
            assert rbac.check_access(method, path, [role]) is True, (method, path, role)
        for role in ("SALES_STAFF", "SALES_CASHIER", "CASHIER", "OPTOMETRIST",
                     "WORKSHOP_STAFF", "ACCOUNTANT"):
            assert rbac.check_access(method, path, [role]) is False, (method, path, role)


def test_insights_literal_paths_do_not_shadow_browse_rows():
    # the browse wildcards must still resolve to their own rows
    assert rbac.policy_for("GET", "/api/v1/collections/summer/products")["path"] == (
        "/api/v1/collections/{handle}/products"
    )
    assert rbac.policy_for("GET", "/api/v1/collections/insights/summary")["path"] == (
        "/api/v1/collections/insights/summary"
    )


# ===========================================================================
# endpoint-level: store scoping forced, role 403s, preview shape
# ===========================================================================


def _headers_for(roles, store="BV-TEST-01"):
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": "ci-test-user",
            "username": "citest",
            "roles": roles,
            "store_ids": [store],
            "active_store_id": store,
        }
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("role", ["STORE_MANAGER", "AREA_MANAGER", "CATALOG_MANAGER"])
def test_summary_held_to_own_shop_for_non_admins(client, role):
    # THE shop rule (owner 2026-10-18): none named -> their own shop; another
    # shop named -> 403 (was: silently overridden; area and catalogue managers
    # read every shop).
    r = client.get(
        "/api/v1/collections/insights/summary", headers=_headers_for([role])
    )
    assert r.status_code == 200
    body = r.json()
    assert body["store_id"] == "BV-TEST-01"
    assert "collections" in body and isinstance(body["collections"], list)
    r = client.get(
        "/api/v1/collections/insights/summary?store_id=BV-OTHER-99",
        headers=_headers_for([role]),
    )
    assert r.status_code == 403


def test_summary_admin_keeps_requested_store(client):
    r = client.get(
        "/api/v1/collections/insights/summary?store_id=BV-OTHER-99",
        headers=_headers_for(["ADMIN"]),
    )
    assert r.status_code == 200
    assert r.json()["store_id"] == "BV-OTHER-99"


def test_insights_endpoints_403_for_sales_staff(client, staff_headers):
    assert client.get(
        "/api/v1/collections/insights/summary", headers=staff_headers
    ).status_code == 403
    assert client.get(
        "/api/v1/collections/some-id/insights", headers=staff_headers
    ).status_code == 403
    assert client.get(
        "/api/v1/collections/some-id/insights/stores", headers=staff_headers
    ).status_code == 403
    assert client.post(
        "/api/v1/collections/preview", json={"rules": []}, headers=staff_headers
    ).status_code == 403


def test_insights_401_without_token(client):
    assert client.get("/api/v1/collections/insights/summary").status_code == 401


def test_insights_404_for_unknown_collection(client, auth_headers):
    r = client.get(
        "/api/v1/collections/no-such-collection-xyz/insights", headers=auth_headers
    )
    assert r.status_code == 404


def test_preview_endpoint_shape(client, auth_headers):
    r = client.post(
        "/api/v1/collections/preview",
        json={
            "rules": [{"field": "brand", "relation": "EQUALS", "value": "zz-nope"}],
            "disjunctive": False,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert {"match_count", "units_on_hand", "sample"} <= set(body)
    assert body["match_count"] == 0
    assert body["sample"] == []
    assert body["units_on_hand"] == 0


# ---------------------------------------------------------------------------
# SmartRule payload normalization (integration fixup: the CRUD model must
# accept the merch builder's IN arrays + numeric price values)
# ---------------------------------------------------------------------------


class TestSmartRulePayload:
    def _rule(self, **kw):
        from api.routers.online_store_collections import SmartRule

        return SmartRule(**kw)

    def test_in_accepts_list_and_cleans(self):
        r = self._rule(field="lens_colour", relation="IN", value=["Black", " ", "Grey "])
        assert r.value == ["Black", "Grey"]

    def test_list_rejected_for_non_in(self):
        import pytest as _pt
        from pydantic import ValidationError

        with _pt.raises(ValidationError):
            self._rule(field="brand", relation="EQUALS", value=["Ray-Ban"])

    def test_numeric_price_value_coerced_to_str(self):
        r = self._rule(field="price", relation="GREATER_THAN", value=5000)
        assert r.value == "5000"

    def test_in_scalar_wrapped(self):
        r = self._rule(field="gender", relation="IN", value="Women")
        assert r.value == ["Women"]

    def test_plain_string_untouched(self):
        r = self._rule(field="brand", relation="EQUALS", value="Ray-Ban")
        assert r.value == "Ray-Ban"


@pytest.mark.usefixtures("brand_is_for_the_website")
def test_preview_and_view_count_every_colour_and_size_of_a_model(monkeypatch):
    """A model listing (lead + a colour + a size) is ONE website product,
    but its stock is every SKU it carries: the preview's units and the
    collection_products view count the colour and the size too."""
    from api.services import collection_materializer as mat
    from tests import test_shopify_model_listings as ml

    db = ml._world()
    pids = ml._family(db, [("Gold", "001", "58", {"units": (1, 0)}),
                           ("Black", "002/58", "58", {"units": (2, 0)}),
                           ("Gold", "001", "62", {"units": (3, 0)})])
    lead_sku = db["products"].find_one({"product_id": pids[0]})["sku"]
    db["catalog_products"].update_one({"sku": lead_sku}, {"$set": {
        "ecom.shopify_product_id": "gid://shopify/Product/5", "ecom.status": "ACTIVE"}})
    for i, p in enumerate(pids[1:], 2):  # the colour and the size are on Shopify (#1275's rule)
        sku = db["products"].find_one({"product_id": p})["sku"]
        db["catalog_variants"].update_one({"sku": sku}, {"$set": {"shopify_variant_id": f"gid://shopify/ProductVariant/{i}"}})
    rules = [{"column": "TYPE", "relation": "EQUALS", "condition": "Sunglass"}]
    units = {"sp-1": 1, "sp-2": 2, "sp-3": 3}  # StrictDB has no composite $group: the rollup is the seam
    monkeypatch.setattr(ci, "_stock_rollup", lambda db, pids, store_id=None: {p: units.get(p, 0) for p in pids})
    res = ci.preview(db, rules)
    assert res["match_count"] == 1          # one listing on the website
    assert res["units_on_hand"] == 6        # 1 + 2 + 3: every colour and size
    db.seed("ecom_collections", [{"collection_id": "sun", "handle": "sunglasses", "collection_type": "SMART",
                                  "rules": rules}])
    mat.materialize_all(db)
    view = sorted(db["collection_products"].find({"collection_id": "sun"}), key=lambda r: r["position"])
    every = [db["products"].find_one({"product_id": p})["sku"] for p in pids]
    assert [r["sku"] for r in view][0] == lead_sku and sorted(r["sku"] for r in view) == sorted(every)
