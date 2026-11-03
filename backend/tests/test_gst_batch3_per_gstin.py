"""GST batch 3 (2026-11-03): GSTR-1 is filed PER GSTIN, and GSTR-3B Table 3.2
is built from the same per-GSTIN rows.

  (a) BV-PUN-01 + BV-ONLINE-01 share one GSTIN: the GSTIN's GSTR-1 carries
      every shop on it -- asked through either shop -- and never a shop of
      another GSTIN. A refund booked at one shop of the GSTIN for a sale of
      the other is filed once.
  (c) Table 3.2: inter-state sales to UNREGISTERED buyers, per place of
      supply -- the GSTR-1 B2CL / B2CS inter-state rows of the same GSTIN.
"""

from datetime import datetime

import pytest

from api.routers.reports import gstr1 as r1
from api.routers.reports import gstr3b as r3b
from api.services.gstn_export import to_gstr3b_json
from tests.test_receipt_without_po import (  # noqa: F401 -- `world` is the fixture
    ENTITY,
    MONTH,
    SHOP_GSTIN,
    STORE,
    world,
)

SISTER = "BV-B3-ONLINE"  # a second shop on the Jharkhand GSTIN
MH_STORE = "BV-B3-PUNE"  # the same company's Maharashtra GSTIN
MH_GSTIN = "27AABCB1234C1ZQ"
SEPT_10 = datetime(2026, 9, 10, 6, 0, 0)  # 11:30 IST, a naive-UTC instant


@pytest.fixture
def gstins(world, monkeypatch):
    db = world["db"]
    monkeypatch.setattr(r1, "_get_raw_db", lambda: db)
    db.entities.update_one({"entity_id": ENTITY},
                           {"$push": {"gstins": {"state_code": "27", "gstin": MH_GSTIN}}})
    db.stores.insert_many([
        # The online shop: no state or GSTIN of its own -- its registration's.
        {"store_id": SISTER, "store_name": "BV Online", "state": "Jharkhand", "state_code": "20",
         "entity_id": ENTITY, "gstin": SHOP_GSTIN, "is_active": True},
        {"store_id": MH_STORE, "store_name": "BV Pune", "state": "Maharashtra", "state_code": "27",
         "entity_id": ENTITY, "gstin": MH_GSTIN, "is_active": True},
    ])
    db.customers.insert_many([
        {"customer_id": "C-JH", "state": "Jharkhand"},
        {"customer_id": "C-MH", "state": "Maharashtra"},
        {"customer_id": "C-MH-B2B", "state": "Maharashtra", "gstin": "27AAACR5055K1Z5"},
        {"customer_id": "C-WB", "state": "West Bengal"},
    ])
    return db


def _sale(db, store, order_id, customer, grand_total=1050.0):
    """A 5% sale (grand total incl. GST) as the till stores it."""
    tax = round(grand_total * 5 / 105, 2)
    db.orders.insert_one({"order_id": order_id, "invoice_number": order_id, "store_id": store,
                          "status": "DELIVERED", "created_at": SEPT_10, "customer_id": customer,
                          "grand_total": grand_total, "tax_amount": tax, "tax_rate": 5})


def _b2cs_taxable(rep):
    return round(sum(r["taxableValue"] for r in rep["b2cs"]), 2)


# ---------------------------------------------------------------------------
# (a) GSTR-1 per GSTIN
# ---------------------------------------------------------------------------


def test_gstr1_files_every_shop_on_the_gstin_and_no_other(gstins):
    _sale(gstins, STORE, "O-JH", "C-JH")
    _sale(gstins, SISTER, "O-ONLINE", "C-JH", 2100.0)
    _sale(gstins, MH_STORE, "O-PUNE", "C-MH", 525.0)
    for shop in (STORE, SISTER):  # asked through either shop: the same return
        rep = r1._compute_gstr1(MONTH, shop)
        assert rep["gstin"] == SHOP_GSTIN
        assert rep["shops"] == sorted([STORE, SISTER])
        assert _b2cs_taxable(rep) == 3000.0  # 1000 + 2000, never Pune's 500
    pune = r1._compute_gstr1(MONTH, MH_STORE)
    assert (pune["gstin"], pune["shops"], _b2cs_taxable(pune)) == (MH_GSTIN, [MH_STORE], 500.0)


def test_gstr1_narrowed_to_the_shops_a_reader_sees(gstins):
    _sale(gstins, STORE, "O-JH", "C-JH")
    _sale(gstins, SISTER, "O-ONLINE", "C-JH", 2100.0)
    assert _b2cs_taxable(r1._compute_gstr1(MONTH, STORE, shops=[STORE])) == 1000.0


def test_a_refund_booked_at_the_sister_shop_is_filed_once(gstins):
    """A legacy store-credit note booked at the CASHIER's shop (STORE) for a
    return of the sister shop's sale: one credit note on the GSTIN's return --
    the per-shop rule skipped it at STORE and the returns pass skipped it as
    already noted, so it vanished from the GSTIN's GSTR-1."""
    _sale(gstins, SISTER, "O-ONLINE", "C-JH", 2100.0)
    gstins.returns.insert_one({
        "return_id": "RET-B3-1", "store_id": SISTER, "order_id": "O-ONLINE", "customer_id": "C-JH",
        "status": "COMPLETED", "created_at": SEPT_10,
        "gst_breakup": {"gross": 210.0, "taxable": 200.0, "tax": 10.0, "gst_rate": 5},
    })
    gstins.credit_note_ledger.insert_one({
        "store_id": STORE, "type": "ISSUED", "ref": "RET-B3-1", "customer_id": "C-JH",
        "created_at": SEPT_10.isoformat(), "taxable": 200.0, "tax": 10.0, "gst_rate": 5,
        "gross_refund": 210.0, "net_refund": 210.0,
    })
    for ask in (STORE, SISTER):  # the GSTIN's return, asked through either shop
        cdnr = r1._compute_gstr1(MONTH, ask)["cdnr"]
        assert [(c["taxableValue"], c["taxValue"]) for c in cdnr] == [(200.0, 10.0)], ask


@pytest.mark.parametrize("return_shop", [STORE, SISTER])
def test_a_return_without_a_ledger_note_is_filed_once_through_either_shop(gstins, return_shop):
    """A cash refund (COMPLETED return, no store-credit ledger row) at one shop
    of the GSTIN: its credit note is on the GSTIN's GSTR-1 CDNR and GSTR-3B
    credit notes once, whichever shop the return is asked through."""
    _sale(gstins, return_shop, "O-CASH", "C-JH", 2100.0)
    gstins.returns.insert_one({
        "return_id": "RET-B3-3", "store_id": return_shop, "order_id": "O-CASH",
        "customer_id": "C-JH", "status": "COMPLETED", "created_at": SEPT_10,
        "gst_breakup": {"gross": 210.0, "taxable": 200.0, "tax": 10.0, "gst_rate": 5},
    })
    for ask in (STORE, SISTER):
        cdnr = r1._compute_gstr1(MONTH, ask)["cdnr"]
        assert [(c["taxableValue"], c["taxValue"]) for c in cdnr] == [(200.0, 10.0)], ask
        cn = r3b._compute_gstr3b(MONTH, ask)["creditNotes"]
        assert (round(cn["taxableValue"], 2),
                round(cn["integratedTax"] + cn["centralTax"] + cn["stateTax"], 2)) == (200.0, 10.0), ask


def test_a_sister_shops_transfer_is_on_the_gstins_gstr1(gstins):
    """SISTER dispatches stock to the Pune GSTIN (a deemed supply, IGST) and a
    piece is short-delivered (the transfer credit note). Asked through STORE,
    the GSTIN's GSTR-1 files the B2B tax invoice, the CDNR note and the net
    HSN line -- the same net outward supply GSTR-3B 3.1(a) files."""
    line = {"hsn": "900311", "gst_rate": 5, "taxable": 1000.0, "cgst": 0, "sgst": 0, "igst": 50.0}
    gstins.vendor_bills.insert_many([
        {"source_transfer_id": "TRF-B3-1", "from_store_id": SISTER, "to_store_id": MH_STORE,
         "invoice_number": "BVO01/T2627/0001", "invoice_date": "2026-09-10",
         "recipient_gstin": MH_GSTIN, "supply_place_recipient": "27",
         "taxable_amount": 1000.0, "igst_total": 50.0, "cgst_total": 0, "sgst_total": 0,
         "lines": [line]},
        {"source_transfer_id": "TRF-B3-1", "from_store_id": SISTER, "to_store_id": MH_STORE,
         "doc_type": "TRANSFER_CREDIT_NOTE",
         "invoice_number": "BVO01/C2627/0001", "invoice_date": "2026-09-12",
         "recipient_gstin": MH_GSTIN, "supply_place_recipient": "27",
         "taxable_amount": -200.0, "igst_total": -10.0, "cgst_total": 0, "sgst_total": 0,
         "lines": [{**line, "taxable": -200.0, "igst": -10.0}]},
    ])
    g1 = r1._compute_gstr1(MONTH, STORE)
    assert [(r["taxableValue"], r["igst"], r.get("deemedSupply")) for r in g1["b2b"]] == [
        (1000.0, 50.0, True)
    ]
    (cn,) = [c for c in g1["cdnr"] if c.get("documentType") == "STOCK_TRANSFER"]
    assert (cn["taxableValue"], cn["refReference"]) == (200.0, "BVO01/C2627/0001")
    (hsn,) = [h for h in g1["hsnSummary"] if h["hsnCode"] == "900311"]
    assert hsn["taxableValue"] == 800.0
    g3 = r3b._compute_gstr3b(MONTH, STORE)
    assert g3["outwardTaxableValue"] == 800.0
    assert g3["outwardTaxableSupplies"]["integratedTax"] == 40.0


# ---------------------------------------------------------------------------
# (c) GSTR-3B Table 3.2 -- the same per-GSTIN rows, by place of supply
# ---------------------------------------------------------------------------


def test_table_3_2_is_the_gstins_unregistered_interstate_rows(gstins):
    _sale(gstins, STORE, "O-INTRA", "C-JH")  # intra-state: not 3.2
    _sale(gstins, SISTER, "O-MH-1", "C-MH", 2100.0)  # inter-state, unregistered
    _sale(gstins, STORE, "O-MH-2", "C-MH")  # inter-state, unregistered
    _sale(gstins, STORE, "O-MH-B2B", "C-MH-B2B")  # registered buyer: B2B, not 3.2
    _sale(gstins, MH_STORE, "O-PUNE", "C-JH", 525.0)  # another GSTIN's sale
    _sale(gstins, SISTER, "O-WB", "C-WB", 525.0)  # inter-state, unregistered, West Bengal
    g3 = r3b._compute_gstr3b(MONTH, SISTER)
    assert g3["interStateUnregistered"] == [
        {"placeOfSupplyCode": "19", "taxableValue": 500.0, "integratedTax": 25.0},
        {"placeOfSupplyCode": "27", "taxableValue": 3000.0, "integratedTax": 150.0},
    ]
    g1 = r1._compute_gstr1(MONTH, SISTER)
    by_pos: dict = {}
    for r in g1["b2cl"] + g1["b2cs"]:
        if r["igst"] > 0:
            t = by_pos.setdefault(r["placeOfSupplyCode"], [0.0, 0.0])
            t[0], t[1] = round(t[0] + r["taxableValue"], 2), round(t[1] + r["igst"], 2)
    assert by_pos == {"19": [500.0, 25.0], "27": [3000.0, 150.0]}
    assert to_gstr3b_json(g3)["inter_sup"]["unreg_details"] == [
        {"pos": "19", "txval": 500.0, "iamt": 25.0},
        {"pos": "27", "txval": 3000.0, "iamt": 150.0},
    ]


def test_a_sister_shop_refund_lands_in_one_month_on_both_returns(gstins):
    """The return dated 30 Sep at SISTER; its legacy store-credit note booked at
    STORE (the cashier's shop) on 1 Oct IST, with a ledger tax that differs from
    the return's gst_breakup. GSTR-1 files the note from the ledger row in
    October; GSTR-3B used to net the return doc in September (per-shop dedup)
    -- one GSTIN, one refund, two periods and two amounts."""
    _sale(gstins, SISTER, "O-ONLINE", "C-JH", 2100.0)
    gstins.returns.insert_one({
        "return_id": "RET-B3-2", "store_id": SISTER, "order_id": "O-ONLINE", "customer_id": "C-JH",
        "status": "COMPLETED", "created_at": datetime(2026, 9, 30, 6, 0, 0),
        "gst_breakup": {"gross": 252.0, "taxable": 240.0, "tax": 12.0, "gst_rate": 5},
    })
    gstins.credit_note_ledger.insert_one({
        "store_id": STORE, "type": "ISSUED", "ref": "RET-B3-2", "customer_id": "C-JH",
        "created_at": datetime(2026, 9, 30, 19, 0, 0).isoformat(),  # 1 Oct 00:30 IST
        "taxable": 200.0, "tax": 10.0, "gst_rate": 5,
        "gross_refund": 210.0, "net_refund": 210.0,
    })
    want = {"2026-09": (0.0, 0.0), "2026-10": (200.0, 10.0)}  # the ledger row, in October
    for ask in (STORE, SISTER):  # the GSTIN's returns, asked through either shop
        for month in ("2026-09", "2026-10"):
            cdnr = r1._compute_gstr1(month, ask)["cdnr"]
            g1 = (round(sum(c["taxableValue"] for c in cdnr), 2),
                  round(sum(c["taxValue"] for c in cdnr), 2))
            cn = r3b._compute_gstr3b(month, ask)["creditNotes"]
            g3 = (round(cn["taxableValue"], 2),
                  round(cn["integratedTax"] + cn["centralTax"] + cn["stateTax"], 2))
            assert g1 == g3 == want[month], (ask, month, g1, g3)


def test_an_invoice_cancelled_behind_its_credit_note_stays_filed_per_gstin(gstins):
    """PR #1264's GST-document view carried into the per-GSTIN queries: the
    sister shop's invoice the refund-advance door cancelled behind a
    whole-invoice credit note (booked at STORE) is still filed on the GSTIN's
    GSTR-1, and its note reverses it once -- the same on GSTR-3B."""
    _sale(gstins, SISTER, "O-ADV", "C-JH", 2100.0)
    gstins.orders.update_one({"order_id": "O-ADV"}, {"$set": {
        "status": "CANCELLED", "advance_refund_status": "COMPLETED",
        "advance_refund_credit_note": "BOK01/C2627/0001"}})
    gstins.credit_note_ledger.insert_one({
        "store_id": STORE, "type": "ISSUED", "ref": "BOK01/C2627/0001", "customer_id": "C-JH",
        "created_at": SEPT_10.isoformat(), "taxable": 2000.0, "tax": 100.0, "gst_rate": 5,
        "gross_refund": 2100.0, "net_refund": 2100.0,
    })
    g1 = r1._compute_gstr1(MONTH, STORE)
    assert _b2cs_taxable(g1) == 2000.0  # the invoice is still filed
    assert [(c["taxableValue"], c["taxValue"]) for c in g1["cdnr"]] == [(2000.0, 100.0)]
    g3 = r3b._compute_gstr3b(MONTH, SISTER)
    assert g3["outwardTaxableValue"] == pytest.approx(0.0, abs=0.01)  # invoice met its note
    assert round(g3["creditNotes"]["taxableValue"], 2) == 2000.0


@pytest.mark.parametrize(
    "note_at, want",
    [
        # booked the same day as the return: one note, in September
        (SEPT_10, {"2026-09": (200.0, 10.0), "2026-10": (0.0, 0.0)}),
        # booked 1 Oct 00:30 IST for a 30 Sep return: one note, in October
        (datetime(2026, 9, 30, 19, 0, 0), {"2026-09": (0.0, 0.0), "2026-10": (200.0, 10.0)}),
    ],
    ids=["same-month", "next-month"],
)
def test_a_note_naming_its_return_only_in_the_reason_is_filed_once(gstins, note_at, want):
    """The return at SISTER; its store-credit note booked at STORE with its own
    number as ref ("CN-0001") and the return named only in the reason text.
    Through either shop of the GSTIN, the CDNR carries the note exactly once,
    in the note's month, and equals GSTR-3B's credit notes."""
    ret_at = SEPT_10 if note_at == SEPT_10 else datetime(2026, 9, 30, 6, 0, 0)
    _sale(gstins, SISTER, "O-ONLINE", "C-JH", 2100.0)
    gstins.returns.insert_one({
        "return_id": "RET-P1", "store_id": SISTER, "order_id": "O-ONLINE", "customer_id": "C-JH",
        "status": "COMPLETED", "created_at": ret_at,
        "gst_breakup": {"gross": 252.0, "taxable": 240.0, "tax": 12.0, "gst_rate": 5},
    })
    gstins.credit_note_ledger.insert_one({
        "store_id": STORE, "type": "ISSUED", "ref": "CN-0001",
        "reason": "Return RET-P1, frame damaged", "customer_id": "C-JH",
        "created_at": note_at.isoformat(), "taxable": 200.0, "tax": 10.0, "gst_rate": 5,
        "gross_refund": 210.0, "net_refund": 210.0,
    })
    for ask in (STORE, SISTER):
        for month in ("2026-09", "2026-10"):
            cdnr = r1._compute_gstr1(month, ask)["cdnr"]
            assert len(cdnr) == (1 if want[month][0] else 0), (ask, month, cdnr)
            g1 = (round(sum(c["taxableValue"] for c in cdnr), 2),
                  round(sum(c["taxValue"] for c in cdnr), 2))
            cn = r3b._compute_gstr3b(month, ask)["creditNotes"]
            g3 = (round(cn["taxableValue"], 2),
                  round(cn["integratedTax"] + cn["centralTax"] + cn["stateTax"], 2))
            assert g1 == g3 == want[month], (ask, month, g1, g3)
