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
    cdnr = r1._compute_gstr1(MONTH, STORE)["cdnr"]
    assert [(c["taxableValue"], c["taxValue"]) for c in cdnr] == [(200.0, 10.0)]


# ---------------------------------------------------------------------------
# (c) GSTR-3B Table 3.2 -- the same per-GSTIN rows, by place of supply
# ---------------------------------------------------------------------------


def test_table_3_2_is_the_gstins_unregistered_interstate_rows(gstins):
    _sale(gstins, STORE, "O-INTRA", "C-JH")  # intra-state: not 3.2
    _sale(gstins, SISTER, "O-MH-1", "C-MH", 2100.0)  # inter-state, unregistered
    _sale(gstins, STORE, "O-MH-2", "C-MH")  # inter-state, unregistered
    _sale(gstins, STORE, "O-MH-B2B", "C-MH-B2B")  # registered buyer: B2B, not 3.2
    _sale(gstins, MH_STORE, "O-PUNE", "C-JH", 525.0)  # another GSTIN's sale
    g3 = r3b._compute_gstr3b(MONTH, SISTER)
    assert g3["interStateUnregistered"] == [
        {"placeOfSupplyCode": "27", "taxableValue": 3000.0, "integratedTax": 150.0}
    ]
    g1 = r1._compute_gstr1(MONTH, SISTER)
    inter = [r for r in g1["b2cl"] + g1["b2cs"] if r["igst"] > 0]
    assert round(sum(r["taxableValue"] for r in inter), 2) == 3000.0
    assert round(sum(r["igst"] for r in inter), 2) == 150.0
    assert {r["placeOfSupplyCode"] for r in inter} == {"27"}
    assert to_gstr3b_json(g3)["inter_sup"]["unreg_details"] == [
        {"pos": "27", "txval": 3000.0, "iamt": 150.0}
    ]
