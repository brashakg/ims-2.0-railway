"""GST batch 2 (2026-11-03): credit and debit notes in GSTR-1 / GSTR-3B.

  (a) Owner Option B (2026-08-09): a note files in CDNR only when its buyer
      has a GSTIN; an unregistered buyer's note on a B2CL sale (inter-state,
      above Rs 1 lakh) in CDNUR; every other consumer note nets into its B2CS
      row (state, rate) -- the export used to DROP every consumer note.
  (d) Customer DEBIT notes (a SUPERADMIN invoice change that raised the bill)
      were filed nowhere: now GSTR-1 (note type D, same sections) and GSTR-3B
      Table 3.1(a), per GSTIN.
(b) split-tender returns and (c) the manual credit were already right on
main (#1264: tests/test_pos_money_doors_r10.py) -- nothing here.
"""

from datetime import datetime

from api.routers.reports import gstr1 as r1
from api.routers.reports import gstr3b as r3b
from api.services.gstn_export import to_gstr1_json
from tests.test_gst_batch3_per_gstin import MH_STORE, SEPT_10, _sale, gstins  # noqa: F401
from tests.test_receipt_without_po import MONTH, STORE, world  # noqa: F401


def _cash_return(db, rid, order_id, customer, taxable, tax, store=STORE):
    db.returns.insert_one({
        "return_id": rid, "store_id": store, "order_id": order_id, "customer_id": customer,
        "status": "COMPLETED", "created_at": SEPT_10,
        "gst_breakup": {"gross": taxable + tax, "taxable": taxable, "tax": tax, "gst_rate": 5},
    })


def _debit_note(db, number, order_id, customer, taxable, tax, store=STORE):
    """As admin_edit writes it (order_superadmin_edit.build_credit_note_doc)."""
    db.debit_note_ledger.insert_one({
        "note_number": number, "note_type": "DEBIT_NOTE", "order_id": order_id,
        "original_invoice_number": order_id, "customer_id": customer, "store_id": store,
        "amount": round(taxable + tax, 2), "tax_amount": tax, "taxable_amount": taxable,
        "status": "ISSUED", "created_at": SEPT_10.isoformat(),
    })


def _export(rep):
    return to_gstr1_json(rep)


# ---------------------------------------------------------------------------
# (a) Option B: CDNR / CDNUR / B2CS netting
# ---------------------------------------------------------------------------


def test_a_walk_in_credit_note_nets_into_its_b2cs_row(gstins):
    """REVERT CHECK: without the netting the export's B2CS stays 2000 taxable
    and the note is filed nowhere (CDNR skipped it for having no GSTIN)."""
    _sale(gstins, STORE, "O-WALK", "C-JH", 2100.0)
    _cash_return(gstins, "RET-B2-1", "O-WALK", "C-JH", 200.0, 10.0)
    rep = r1._compute_gstr1(MONTH, STORE)
    assert [c["noteSection"] for c in rep["cdnr"]] == ["B2CS"]
    out = _export(rep)
    assert out["cdnr"] == [] and out["cdnur"] == []
    [row] = out["b2cs"]
    assert (row["sply_ty"], row["pos"], row["rt"]) == ("INTRA", "20", 5.0)
    assert (row["txval"], row["camt"], row["samt"]) == (1800.0, 45.0, 45.0)


def test_a_b2cl_sale_credit_note_files_in_cdnur(gstins):
    """An unregistered Maharashtra buyer at the Jharkhand shop, above the
    limit (B2CL_INVOICE_THRESHOLD): the sale is B2CL, its note CDNUR (typ B2CL, pos 27), never B2CS.
    REVERT CHECK: dropping _note_section's CDNUR branch nets it into a
    B2CS row instead."""
    _sale(gstins, STORE, "O-BIG", "C-MH", 315000.0)
    _cash_return(gstins, "RET-B2-2", "O-BIG", "C-MH", 20000.0, 1000.0)
    rep = r1._compute_gstr1(MONTH, STORE)
    assert len(rep["b2cl"]) == 1
    assert [c["noteSection"] for c in rep["cdnr"]] == ["CDNUR"]
    out = _export(rep)
    assert out["b2cs"] == [] and out["cdnr"] == []
    [nt] = out["cdnur"]
    assert (nt["typ"], nt["ntty"], nt["pos"], nt["nt_num"]) == ("B2CL", "C", "27", "RET-B2-2")
    assert nt["itms"][0]["itm_det"] == {
        "rt": 5.0, "txval": 20000.0, "iamt": 1000.0, "camt": 0.0, "samt": 0.0, "csamt": 0.0}


def test_a_small_inter_state_consumer_note_nets_into_inter_b2cs(gstins):
    """Same buyer, below the B2CL limit: B2CS INTER row for state 27, net."""
    _sale(gstins, STORE, "O-SMALL", "C-MH", 2100.0)
    _cash_return(gstins, "RET-B2-3", "O-SMALL", "C-MH", 200.0, 10.0)
    out = _export(r1._compute_gstr1(MONTH, STORE))
    assert out["cdnur"] == []
    [row] = out["b2cs"]
    assert (row["sply_ty"], row["pos"], row["txval"], row["iamt"]) == ("INTER", "27", 1800.0, 90.0)


def test_a_registered_buyers_note_stays_in_cdnr(gstins):
    _sale(gstins, STORE, "O-B2B", "C-MH-B2B", 2100.0)
    _cash_return(gstins, "RET-B2-4", "O-B2B", "C-MH-B2B", 200.0, 10.0)
    out = _export(r1._compute_gstr1(MONTH, STORE))
    assert out["cdnur"] == [] and out["b2cs"] == []
    [grp] = out["cdnr"]
    assert grp["ctin"] == "27AAACR5055K1Z5" and grp["nt"][0]["ntty"] == "C"


def test_a_note_bigger_than_the_months_b2cs_files_zero_and_says_so(gstins):
    """A refund of an earlier month's sale with no sale this month on the row:
    the portal takes no negative B2CS row -- filed at 0, flagged for the CA.
    REVERT CHECK: removing the report's excess check leaves no error."""
    gstins.orders.insert_one({
        "order_id": "O-AUG", "invoice_number": "O-AUG", "store_id": STORE, "status": "DELIVERED",
        "created_at": datetime(2026, 8, 10, 6, 0), "customer_id": "C-JH",
        "grand_total": 2100.0, "tax_amount": 100.0, "tax_rate": 5})
    _cash_return(gstins, "RET-B2-5", "O-AUG", "C-JH", 200.0, 10.0)
    rep = r1._compute_gstr1(MONTH, STORE)
    assert any(i["invoice"].startswith("B2CS 20 @ 5%") for i in rep["validation"]["issues"])
    [row] = _export(rep)["b2cs"]
    assert (row["txval"], row["camt"], row["samt"]) == (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# (d) customer debit notes: GSTR-1 (type D) + GSTR-3B, per GSTIN
# ---------------------------------------------------------------------------


def test_a_registered_buyers_debit_note_files_in_cdnr_as_d(gstins):
    """REVERT CHECK: without the debit-note pass GSTR-1 has no note at all."""
    _sale(gstins, STORE, "O-B2B", "C-MH-B2B", 2100.0)
    _debit_note(gstins, "BOK/D2627/0001", "O-B2B", "C-MH-B2B", 100.0, 5.0)
    rep = r1._compute_gstr1(MONTH, STORE)
    [dn] = rep["cdnr"]
    assert (dn["noteType"], dn["noteSection"], dn["taxValue"], dn["igst"]) == ("D", "CDNR", 5.0, 5.0)
    nt = _export(rep)["cdnr"][0]["nt"][0]
    assert (nt["ntty"], nt["nt_num"], nt["pos"]) == ("D", "BOK/D2627/0001", "27")
    # Table 12 B2B part: the debit note ADDS to the sale's HSN row.
    assert sum(h["taxableValue"] for h in rep["hsnB2B"]) == 2100.0
    # Table 13: a D number is not reported as an unregistered credit note.
    assert not any("credit note(s) filed in CDNR" in i["issue"] for i in rep["validation"]["issues"])


def test_a_walk_in_debit_note_adds_to_its_b2cs_row(gstins):
    _sale(gstins, STORE, "O-WALK", "C-JH", 2100.0)
    _debit_note(gstins, "BOK/D2627/0002", "O-WALK", "C-JH", 100.0, 5.0)
    [row] = _export(r1._compute_gstr1(MONTH, STORE))["b2cs"]
    assert (row["txval"], row["camt"], row["samt"]) == (2100.0, 52.5, 52.5)


def test_gstr3b_adds_the_gstins_debit_notes_and_no_other(gstins):
    """REVERT CHECK: without _debit_note_totals Table 3.1(a) stays at the sale
    (CGST 50 + SGST 50); Pune's debit note never lands on Jharkhand's return."""
    _sale(gstins, STORE, "O-WALK", "C-JH", 2100.0)
    _debit_note(gstins, "BOK/D2627/0003", "O-WALK", "C-JH", 100.0, 5.0)
    _sale(gstins, MH_STORE, "O-PUNE", "C-MH", 2100.0)
    _debit_note(gstins, "PUN/D2627/0001", "O-PUNE", "C-MH", 1000.0, 50.0, store=MH_STORE)
    g3 = r3b._compute_gstr3b(MONTH, STORE)
    t = g3["outwardTaxableSupplies"]
    assert (t["centralTax"], t["stateTax"], t["integratedTax"]) == (52.5, 52.5, 0.0)
    assert g3["outwardTaxableValue"] == 2100.0
    assert g3["debitNotes"]["taxableValue"] == 100.0
    assert [c["refReference"] for c in r1._compute_gstr1(MONTH, STORE)["cdnr"]] == ["BOK/D2627/0003"]


# ---------------------------------------------------------------------------
# Review round 1: per-rate notes, Table 3.2 net, one B2CL rule, the D sign
# ---------------------------------------------------------------------------


def _line(rate, taxable, tax, hsn):
    return {"gst_rate": rate, "taxable_value": taxable, "tax_amount": tax, "hsn_code": hsn,
            "item_total": taxable + tax, "quantity": 1}


def _mixed_sale(db, order_id, customer, k=1.0):
    """A 5% frame (Rs 1000 + 50) and an 18% sunglass (Rs 1000 + 180), x k."""
    items = [_line(5, 1000.0 * k, 50.0 * k, "9004"), _line(18, 1000.0 * k, 180.0 * k, "9004")]
    db.orders.insert_one({
        "order_id": order_id, "invoice_number": order_id, "store_id": STORE, "status": "DELIVERED",
        "created_at": SEPT_10, "customer_id": customer, "items": items,
        "grand_total": 2230.0 * k, "tax_amount": 230.0 * k})


def _mixed_return(db, rid, order_id, customer, k=1.0):
    db.returns.insert_one({
        "return_id": rid, "store_id": STORE, "order_id": order_id, "customer_id": customer,
        "status": "COMPLETED", "created_at": SEPT_10,
        "gst_breakup": {"gross": 2230.0 * k, "taxable": 2000.0 * k, "tax": 230.0 * k,
                        "gst_rate": 18, "by_rate": {"5": {"taxable": 1000.0 * k, "tax": 50.0 * k},
                                                    "18": {"taxable": 1000.0 * k, "tax": 180.0 * k}}},
    })


def _store_credit_note(db, rid, customer, taxable, tax):
    """The ledger row the return door writes: stamped at the DOMINANT rate."""
    db.credit_note_ledger.insert_one({
        "store_id": STORE, "type": "ISSUED", "ref": rid, "customer_id": customer,
        "created_at": SEPT_10.isoformat(), "taxable": taxable, "tax": tax, "gst_rate": 18,
        "gross_refund": taxable + tax, "net_refund": taxable + tax})


def _self_consistent(itms):
    for it in itms:
        d = it["itm_det"]
        assert abs(d["txval"] * d["rt"] / 100 - (d["iamt"] + d["camt"] + d["samt"])) < 0.02, d


def test_a_mixed_rate_store_credit_return_nets_each_rate(gstins):
    """REVERT CHECK: with the ledger pass's one bucket at gst_rate 18 the whole
    Rs 2000 / 230 nets into 18% -- 5% stays at 1000 / 25 + 25 and 18% at 0
    is flagged 'exceed'."""
    _mixed_sale(gstins, "O-MIX", "C-JH")
    _mixed_return(gstins, "RET-MIX-1", "O-MIX", "C-JH")
    _store_credit_note(gstins, "RET-MIX-1", "C-JH", 2000.0, 230.0)
    rep = r1._compute_gstr1(MONTH, STORE)
    [cn] = rep["cdnr"]
    assert [(rl["rate"], rl["taxable"]) for rl in cn["rateLines"]] == [(5, 1000.0), (18, 1000.0)]
    assert [i for i in rep["validation"]["issues"] if "exceed" in i["issue"]] == []
    rows = {r["rt"]: r for r in _export(rep)["b2cs"]}
    assert {rt: (r["txval"], r["camt"], r["samt"]) for rt, r in rows.items()} == {
        5.0: (0.0, 0.0, 0.0), 18.0: (0.0, 0.0, 0.0)}
    assert all(r["taxableValue"] == 0 for r in rep["b2csFiled"])


def test_a_partial_mixed_rate_store_credit_note_is_shared_pro_rata(gstins):
    _mixed_sale(gstins, "O-MIX", "C-JH")
    _mixed_return(gstins, "RET-MIX-2", "O-MIX", "C-JH")
    _store_credit_note(gstins, "RET-MIX-2", "C-JH", 1000.0, 115.0)  # half the return
    [cn] = r1._compute_gstr1(MONTH, STORE)["cdnr"]
    assert [(rl["rate"], rl["taxable"]) for rl in cn["rateLines"]] == [(5, 500.0), (18, 500.0)]


def test_a_mixed_rate_registered_note_files_one_self_consistent_item_per_rate(gstins):
    """REVERT CHECK: _note_nt / b2cs_net_rows ignoring rateLines file one
    blended block (rt 18 with the 5% tax in it)."""
    _mixed_sale(gstins, "O-MIX", "C-MH-B2B")
    _mixed_return(gstins, "RET-MIX-3", "O-MIX", "C-MH-B2B")
    itms = _export(r1._compute_gstr1(MONTH, STORE))["cdnr"][0]["nt"][0]["itms"]
    assert [it["itm_det"]["rt"] for it in itms] == [5.0, 18.0]
    _self_consistent(itms)


def test_a_mixed_rate_b2cl_note_files_in_cdnur_per_rate(gstins):
    _mixed_sale(gstins, "O-MIXBIG", "C-MH", k=150.0)  # Rs 3.3 lakh, inter-state
    _mixed_return(gstins, "RET-MIX-4", "O-MIXBIG", "C-MH", k=10.0)
    [nt] = _export(r1._compute_gstr1(MONTH, STORE))["cdnur"]
    assert [it["itm_det"]["rt"] for it in nt["itms"]] == [5.0, 18.0]
    _self_consistent(nt["itms"])


def test_a_mixed_rate_debit_note_files_per_rate_never_at_14(gstins):
    """A SUPERADMIN raise of 10% on a 5% and an 18% line: taxable 200, tax
    28 -- 14% blended. REVERT CHECK: without the by_rate stamp the note files
    one rt 14 row."""
    from api.services.order_superadmin_edit import note_by_rate

    _mixed_sale(gstins, "O-MIX", "C-JH")
    before = gstins.orders.find_one({"order_id": "O-MIX"})
    after = {**before, "items": [_line(5, 1100.0, 55.0, "9004"), _line(18, 1100.0, 198.0, "9004")],
             "grand_total": 2453.0, "tax_amount": 253.0}
    by_rate = note_by_rate(before, after)
    assert by_rate == {"5": {"taxable": 100.0, "tax": 5.0}, "18": {"taxable": 100.0, "tax": 18.0}}
    gstins.debit_note_ledger.insert_one({
        "note_number": "BOK/D2627/0009", "note_type": "DEBIT_NOTE", "order_id": "O-MIX",
        "customer_id": "C-JH", "store_id": STORE, "amount": 223.0, "tax_amount": 23.0,
        "taxable_amount": 200.0, "status": "ISSUED", "created_at": SEPT_10.isoformat(),
        "by_rate": by_rate})
    rep = r1._compute_gstr1(MONTH, STORE)
    [dn] = rep["cdnr"]
    assert [(rl["rate"], rl["taxable"]) for rl in dn["rateLines"]] == [(5, 100.0), (18, 100.0)]
    assert sorted(r["rt"] for r in _export(rep)["b2cs"]) == [5.0, 18.0]
    assert not any("no GST slab" in i["issue"] for i in rep["validation"]["issues"])


def test_a_debit_note_at_a_non_slab_rate_is_flagged(gstins):
    """A note with no per-rate stamp, 200 / 28: 14% is no GST slab."""
    _mixed_sale(gstins, "O-MIX", "C-JH")
    _debit_note(gstins, "BOK/D2627/0010", "O-MIX", "C-JH", 200.0, 28.0)
    issues = r1._compute_gstr1(MONTH, STORE)["validation"]["issues"]
    assert any(i["invoice"] == "BOK/D2627/0010" and "no GST slab" in i["issue"] for i in issues)


def _table_3_2():
    return {u["placeOfSupplyCode"]: (u["taxableValue"], u["integratedTax"])
            for u in r3b._compute_gstr3b(MONTH, STORE)["interStateUnregistered"]}


def test_table_3_2_is_net_of_the_inter_state_consumer_notes_as_gstr1(gstins):
    """REVERT CHECK: 3.2 stayed gross (2000 / 100) while GSTR-1's INTER B2CS
    row for 27 files net of the return (-200 / 10) and the D note (+100 / 5)."""
    _sale(gstins, STORE, "O-SMALL", "C-MH", 2100.0)
    _cash_return(gstins, "RET-B2-32", "O-SMALL", "C-MH", 200.0, 10.0)
    _debit_note(gstins, "BOK/D2627/0011", "O-SMALL", "C-MH", 100.0, 5.0)
    [row] = _export(r1._compute_gstr1(MONTH, STORE))["b2cs"]
    assert _table_3_2() == {"27": (row["txval"], row["iamt"])} == {"27": (1900.0, 95.0)}


def test_table_3_2_full_return_files_zero_never_negative(gstins):
    _sale(gstins, STORE, "O-SMALL", "C-MH", 2100.0)
    _cash_return(gstins, "RET-B2-33", "O-SMALL", "C-MH", 2000.0, 100.0)
    assert _table_3_2() == {"27": (0.0, 0.0)}
    assert r3b._compute_gstr3b(MONTH, STORE)["interStateUnregisteredCarryForward"] == []
    # More notes than the month's sales on the row: 0 here, the excess said.
    _cash_return(gstins, "RET-B2-34", "O-SMALL", "C-MH", 200.0, 10.0)
    assert r3b._compute_gstr3b(MONTH, STORE)["interStateUnregisteredCarryForward"] == [
        {"placeOfSupplyCode": "27", "taxableValue": 200.0, "integratedTax": 10.0}]


def test_one_b2cl_rule_for_the_sale_and_its_note(gstins, monkeypatch):
    """The sale's table and its note's section ask ONE predicate
    (gst_base.is_b2cl). REVERT CHECK: an inline copy at either site ignores
    the patched rule, and the sale lands B2CL while its note nets in B2CS (or
    the other way round)."""
    _sale(gstins, STORE, "O-SMALL", "C-MH", 2100.0)
    _cash_return(gstins, "RET-B2-35", "O-SMALL", "C-MH", 200.0, 10.0)
    rep = r1._compute_gstr1(MONTH, STORE)
    assert (len(rep["b2cl"]), rep["cdnr"][0]["noteSection"]) == (0, "B2CS")
    monkeypatch.setattr(r1, "is_b2cl", lambda gstin, inter, value: not gstin and bool(inter))
    rep = r1._compute_gstr1(MONTH, STORE)
    assert (len(rep["b2cl"]), rep["cdnr"][0]["noteSection"]) == (1, "CDNUR")


def test_crosscheck_nets_a_debit_note_against_the_credit_notes():
    """REVERT CHECK: without the D sign aggregate_gstr1 adds the debit note to
    the reduction (300 / 15 instead of 100 / 5)."""
    from api.services.gst_crosscheck import aggregate_gstr1

    agg = aggregate_gstr1([{"cdnr": [
        {"noteType": "C", "taxableValue": 200.0, "taxValue": 10.0},
        {"noteType": "D", "taxableValue": 100.0, "taxValue": 5.0},
    ]}])
    assert (agg["cdnr_totals"]["taxableValue"], agg["cdnr_totals"]["tax"]) == (100.0, 5.0)
