"""
IMS 2.0 - GST reconciliation cross-check (accountant month-end sign-off)
=======================================================================
Pure comparison + aggregation helpers for the accountant's GST cross-check
screen. Given a chosen (month, entity) these functions take the ALREADY
COMPUTED per-store GST-return dicts (produced by reports.py::_compute_gstr1 /
_compute_gstr3b) plus the finance-side "books" figures (orders / payments /
Tally-JV totals the finance router aggregates), and lay them SIDE BY SIDE so
the accountant can confirm that IMS's GSTR-1 / GSTR-3B numbers agree with the
books before filing.

DESIGN CONTRACT (important):
  * This module does NO tax math and NO database access. It NEVER recomputes
    CGST/SGST/IGST, taxable value, ITC, or any GST figure. It only MERGES
    per-store dicts and COMPARES numbers that were computed upstream. The
    single source of truth for the tax math stays reports.py / finance.py.
  * Everything fails soft: missing / malformed input yields zeros and a
    structurally valid result rather than raising, so a partial month or an
    empty entity still renders.

A "cross-check row" compares one metric (e.g. total output GST) across every
source that reports it (GSTR-1, GSTR-3B, Books, Tally). The row's variance is
max(values) - min(values); a row is MATCH when that spread is within a rupee
tolerance, else MISMATCH. The screen is a review aid -- it changes no figure
and locks no period; the accountant sign-off is an audit marker only.
"""

from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional

# Default variance tolerance in rupees. Sub-rupee spreads are paise-rounding
# noise from the independent computation paths (per-line derivation in GSTR-1
# vs order-total aggregation in the books), never a real reconciliation break.
DEFAULT_TOLERANCE = 1.0


def _f(val: Any) -> float:
    """Coerce to float; non-numeric / None -> 0.0. Never raises."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _r(val: Any) -> float:
    """Coerce + round to 2dp."""
    return round(_f(val), 2)


# ============================================================================
# Per-store -> per-entity aggregation
# ============================================================================


def aggregate_gstr1(store_reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge N per-store GSTR-1 dicts (reports.py::_compute_gstr1) into one
    entity-level dict.

    Concatenates the B2B / B2CL / B2CS / CDNR sections, sums the taxable +
    tax totals, derives the CGST/SGST/IGST split by summing every invoice
    row (gross -- before credit notes, so it lines up with GSTR-3B's gross
    outward tax), merges the HSN summary per GST rate (this one is NET of
    CDNR, as reports.py builds it), and separates the transfer deemed-supply
    rows (b2b rows flagged ``deemedSupply``) so the screen can show the
    Schedule-I inter-GSTIN stock-transfer outward supply on its own.
    Pure."""
    b2b: List[Dict[str, Any]] = []
    b2cl: List[Dict[str, Any]] = []
    b2cs: List[Dict[str, Any]] = []
    cdnr: List[Dict[str, Any]] = []
    validation_issues: List[Dict[str, Any]] = []
    rate_map: Dict[float, Dict[str, Any]] = {}

    total_taxable = 0.0
    total_tax = 0.0

    for rep in store_reports or []:
        if not isinstance(rep, dict):
            continue
        total_taxable += _f(rep.get("totalTaxableValue"))
        total_tax += _f(rep.get("totalTax"))
        b2b.extend(x for x in (rep.get("b2b") or []) if isinstance(x, dict))
        b2cl.extend(x for x in (rep.get("b2cl") or []) if isinstance(x, dict))
        b2cs.extend(x for x in (rep.get("b2cs") or []) if isinstance(x, dict))
        cdnr.extend(x for x in (rep.get("cdnr") or []) if isinstance(x, dict))
        val = rep.get("validation") or {}
        for issue in (val.get("issues") or []):
            if isinstance(issue, dict):
                validation_issues.append(issue)
        for hrow in (rep.get("hsnSummary") or []):
            if not isinstance(hrow, dict):
                continue
            rate = _f(hrow.get("gstRate"))
            bucket = rate_map.setdefault(
                rate,
                {
                    "gstRate": rate,
                    "taxableValue": 0.0,
                    "cgst": 0.0,
                    "sgst": 0.0,
                    "igst": 0.0,
                },
            )
            bucket["taxableValue"] += _f(hrow.get("taxableValue"))
            bucket["cgst"] += _f(hrow.get("cgst"))
            bucket["sgst"] += _f(hrow.get("sgst"))
            bucket["igst"] += _f(hrow.get("igst"))

    # Gross CGST/SGST/IGST across every outward row (matches total_tax).
    cgst = sgst = igst = 0.0
    for row in (*b2b, *b2cl, *b2cs):
        cgst += _f(row.get("cgst"))
        sgst += _f(row.get("sgst"))
        igst += _f(row.get("igst"))

    deemed = [r for r in b2b if r.get("deemedSupply")]
    deemed_taxable = sum(_f(r.get("taxableValue")) for r in deemed)
    deemed_tax = sum(_f(r.get("totalTax")) for r in deemed)
    # Per-head split of the deemed supply so the cross-check can add it to the
    # orders-only Books/Tally sources (which never see inter-GSTIN transfers)
    # and line them up with GSTR-1/3B (which already include it).
    deemed_cgst = sum(_f(r.get("cgst")) for r in deemed)
    deemed_sgst = sum(_f(r.get("sgst")) for r in deemed)
    deemed_igst = sum(_f(r.get("igst")) for r in deemed)

    cdnr_taxable = sum(_f(c.get("taxableValue")) for c in cdnr)
    cdnr_tax = sum(
        _f(c.get("taxValue"))
        or (_f(c.get("cgst")) + _f(c.get("sgst")) + _f(c.get("igst")))
        for c in cdnr
    )

    rate_breakup = [
        {
            "gstRate": int(k) if float(k).is_integer() else k,
            "taxableValue": _r(v["taxableValue"]),
            "cgst": _r(v["cgst"]),
            "sgst": _r(v["sgst"]),
            "igst": _r(v["igst"]),
            "tax": _r(v["cgst"] + v["sgst"] + v["igst"]),
        }
        for k, v in sorted(rate_map.items(), key=lambda kv: kv[0])
    ]

    return {
        "totalTaxableValue": _r(total_taxable),
        "totalTax": _r(total_tax),
        "cgst": _r(cgst),
        "sgst": _r(sgst),
        "igst": _r(igst),
        "b2b": b2b,
        "b2cl": b2cl,
        "b2cs": b2cs,
        "cdnr": cdnr,
        "cdnr_totals": {
            "count": len(cdnr),
            "taxableValue": _r(cdnr_taxable),
            "tax": _r(cdnr_tax),
        },
        "deemed_supply": {
            "count": len(deemed),
            "taxableValue": _r(deemed_taxable),
            "tax": _r(deemed_tax),
            "cgst": _r(deemed_cgst),
            "sgst": _r(deemed_sgst),
            "igst": _r(deemed_igst),
            "rows": deemed,
        },
        "rate_breakup": rate_breakup,
        "validation": {
            "ok": len(validation_issues) == 0,
            "issueCount": len(validation_issues),
            "issues": validation_issues[:100],
        },
    }


def _itc_regular(rep: Dict[str, Any]) -> Dict[str, float]:
    """The COMPANY-wide ITC of a per-store GSTR-3B report (bills that name no
    GSTIN). Prefers the split field ``itcAvailableRegular``; falls back to the
    whole ``itcAvailable`` for legacy report dicts that predate the R1 split
    (the GSTIN slice then 0)."""
    src = rep.get("itcAvailableRegular")
    if src is None and (
        "itcAvailableGstin" not in rep and "itcAvailableRegular" not in rep
    ):
        src = rep.get("itcAvailable")
    src = src or {}
    return {
        "c": _f(src.get("centralTax")),
        "s": _f(src.get("stateTax")),
        "i": _f(src.get("integratedTax")),
    }


def _itc_gstin(rep: Dict[str, Any]) -> Dict[str, float]:
    """The GSTIN-bound ITC slice of a per-store GSTR-3B report
    (``itcAvailableGstin``: every bill received on that GSTIN, plus GSTIN-less
    transfer mirrors at that store); 0 for legacy dicts without the split."""
    src = rep.get("itcAvailableGstin") or {}
    return {
        "c": _f(src.get("centralTax")),
        "s": _f(src.get("stateTax")),
        "i": _f(src.get("integratedTax")),
    }


def _rcm_parts(rep: Dict[str, Any]) -> tuple:
    """(company-wide, GSTIN-bound) Table 3.1(d) of a per-store report, each as
    {c, s, i, t}. Placed like the credit (gst_itc._placement), so it splits
    the same way; a legacy dict without the split is all company-wide."""
    tot = rep.get("inwardSuppliesReverseCharge") or {}
    g = rep.get("inwardSuppliesReverseChargeGstin") or {}
    whole = {
        "c": _f(tot.get("centralTax")),
        "s": _f(tot.get("stateTax")),
        "i": _f(tot.get("integratedTax")),
        "t": _f(rep.get("inwardSuppliesReverseChargeValue")),
    }
    bound = {
        "c": _f(g.get("centralTax")),
        "s": _f(g.get("stateTax")),
        "i": _f(g.get("integratedTax")),
        "t": _f(g.get("taxableValue")),
    }
    return {k: whole[k] - bound[k] for k in whole}, bound


def aggregate_gstr3b(
    store_reports: List[Dict[str, Any]],
    entity_ids: Optional[List[Any]] = None,
    store_gstins: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Merge N per-store GSTR-3B dicts (reports.py::_compute_gstr3b) into one
    entity-level dict: outward taxable + tax, ITC available, net cash payable,
    and reverse-charge (RCM). Pure.

    OUTWARD supply is STORE-scoped (reports.py reads it from that store's own
    orders + sender-side transfer bills), so it is SUMMED across every store.

    ITC is split by scope (R1). The GSTIN-BOUND slice (``itcAvailableGstin``:
    every bill received on the store's GSTIN, and GSTIN-less transfer mirrors
    received at any shop carrying that GSTIN -- gst_itc._itc_match) differs between
    sibling stores of one entity with DIFFERENT GSTINs; it is counted ONCE per
    (company, GSTIN), so a bill is never claimed on two registrations. The company-wide
    remainder (legacy bills naming no GSTIN) filters on recipient_entity_id
    alone, so EVERY store of an entity returns the SAME figure; it is counted
    ONCE per entity. RCM (Table 3.1(d)) is placed by the same rule as the
    credit and split the same way (_rcm_parts). Without this split the entity
    figure depended on which store Mongo listed first (order-dependent ITC and
    net cash) -- the R1 defect this closes. A store whose entity_id is falsy
    contributes ZERO ITC/RCM (its per-store figure is org-wide, not
    entity-scoped, so it must not be trusted).

    Pass ``entity_ids`` (a parallel list, one entity_id per store report) and,
    for the GSTIN split, ``store_gstins`` (one GSTIN per store report). A
    report with no GSTIN falls back to per-store inclusion (its slice is
    empty: gst_itc._placement keeps no transfer mirror on a shop with no
    GSTIN, which files no return).

    Net cash is derived ENTITY-LEVEL after aggregation as per-head
    max(0, out - itc) + rcm and summed across entities -- one entity's ITC
    surplus can never offset another entity's output tax (distinct GSTINs file
    separately), and RCM is always discharged in cash on top.

    ``entity_ids=None`` keeps the legacy per-report summation, for callers that
    already know every report is a distinct entity.

    Also returns ``bucketCount`` (number of distinct entity buckets that
    contributed) so build_crosscheck can mark the combined-view net-cash
    comparator advisory when more than one entity clamps separately."""
    if entity_ids is None:
        reports = [r for r in (store_reports or []) if isinstance(r, dict)]
        keys: List[Any] = list(range(len(reports)))
        gstins: List[Any] = [None] * len(reports)
    else:
        # R3: zip the parallel lists FIRST, then drop non-dict reports, so a bad
        # report never shifts every later report onto the wrong entity/GSTIN.
        triples = [
            (r, k, g)
            for r, k, g in itertools.zip_longest(
                store_reports or [], entity_ids, store_gstins or [], fillvalue=None
            )
            if isinstance(r, dict)
        ]
        reports = [t[0] for t in triples]
        keys = [t[1] for t in triples]
        gstins = [t[2] for t in triples]

    def _blank() -> Dict[str, float]:
        return {
            "out_c": 0.0, "out_s": 0.0, "out_i": 0.0, "out_taxable": 0.0,
            "itc_c": 0.0, "itc_s": 0.0, "itc_i": 0.0,
            "rcm_c": 0.0, "rcm_s": 0.0, "rcm_i": 0.0, "rcm_taxable": 0.0,
        }

    # Accumulate PER ENTITY so ITC/RCM (entity-scoped) is not multiplied and net
    # cash is clamped per entity, not on the cross-entity grand total.
    buckets: Dict[Any, Dict[str, float]] = {}
    regular_taken: set = set()  # company-wide ITC + RCM: once per entity
    gstin_taken: set = set()    # GSTIN-bound ITC: once per (entity, GSTIN)
    for rep, key, gstin in zip(reports, keys, gstins):
        # Storeless stores (falsy entity_id) share one bucket: their outward is
        # real and counted, but they never contribute ITC/RCM.
        bkey = key if (entity_ids is None or key) else "__no_entity__"
        b = buckets.setdefault(bkey, _blank())
        b["out_taxable"] += _f(rep.get("outwardTaxableValue"))
        osup = rep.get("outwardTaxableSupplies") or {}
        b["out_c"] += _f(osup.get("centralTax"))
        b["out_s"] += _f(osup.get("stateTax"))
        b["out_i"] += _f(osup.get("integratedTax"))

        # Storeless (falsy entity_id in the tagged path) never contributes ITC/RCM.
        if not (entity_ids is None or bool(key)):
            continue

        rcm_reg, rcm_bound = _rcm_parts(rep)

        # Company-wide ITC + RCM -> count ONCE per entity.
        if key not in regular_taken:
            regular_taken.add(key)
            reg = _itc_regular(rep)
            b["itc_c"] += reg["c"]
            b["itc_s"] += reg["s"]
            b["itc_i"] += reg["i"]
            b["rcm_c"] += rcm_reg["c"]
            b["rcm_s"] += rcm_reg["s"]
            b["rcm_i"] += rcm_reg["i"]
            b["rcm_taxable"] += rcm_reg["t"]

        # GSTIN-bound ITC + RCM -> count ONCE per company + GSTIN (legacy per-report path
        # or a report with no GSTIN sums it in, since those keys are already
        # distinct per filing / per store). Every store of one GSTIN reports
        # the same slice, so which store comes first cannot matter.
        # Keyed by (entity, GSTIN): each report's slice is scoped to its own
        # company (gst_itc._placement), so a shop of E2 carrying E1's number
        # holds a different slice -- keyed by GSTIN alone, whichever store
        # came first silently dropped the other company's credit.
        if entity_ids is not None and gstin:
            if (key, gstin) in gstin_taken:
                continue
            gstin_taken.add((key, gstin))
        trf = _itc_gstin(rep)
        b["itc_c"] += trf["c"]
        b["itc_s"] += trf["s"]
        b["itc_i"] += trf["i"]
        b["rcm_c"] += rcm_bound["c"]
        b["rcm_s"] += rcm_bound["s"]
        b["rcm_i"] += rcm_bound["i"]
        b["rcm_taxable"] += rcm_bound["t"]

    vals = list(buckets.values())
    out_taxable = sum(b["out_taxable"] for b in vals)
    out_c = sum(b["out_c"] for b in vals)
    out_s = sum(b["out_s"] for b in vals)
    out_i = sum(b["out_i"] for b in vals)
    itc_c = sum(b["itc_c"] for b in vals)
    itc_s = sum(b["itc_s"] for b in vals)
    itc_i = sum(b["itc_i"] for b in vals)
    rcm_c = sum(b["rcm_c"] for b in vals)
    rcm_s = sum(b["rcm_s"] for b in vals)
    rcm_i = sum(b["rcm_i"] for b in vals)
    rcm_taxable = sum(b["rcm_taxable"] for b in vals)
    # Net cash: per entity, per head, output minus ITC clamped at 0, plus RCM;
    # then summed across entities.
    cash_c = sum(max(0.0, b["out_c"] - b["itc_c"]) + b["rcm_c"] for b in vals)
    cash_s = sum(max(0.0, b["out_s"] - b["itc_s"]) + b["rcm_s"] for b in vals)
    cash_i = sum(max(0.0, b["out_i"] - b["itc_i"]) + b["rcm_i"] for b in vals)

    return {
        "outwardTaxableValue": _r(out_taxable),
        "cgst": _r(out_c),
        "sgst": _r(out_s),
        "igst": _r(out_i),
        "outwardTax": _r(out_c + out_s + out_i),
        # Distinct entity buckets that contributed. >1 means the combined view
        # clamps each entity's net cash separately, so build_crosscheck marks the
        # 'Net GST payable (cash)' comparator advisory (R2).
        "bucketCount": len(buckets),
        "itc": {
            "cgst": _r(itc_c),
            "sgst": _r(itc_s),
            "igst": _r(itc_i),
            "total": _r(itc_c + itc_s + itc_i),
        },
        "netCash": {
            "cgst": _r(cash_c),
            "sgst": _r(cash_s),
            "igst": _r(cash_i),
            "total": _r(cash_c + cash_s + cash_i),
        },
        "rcm": {
            "taxableValue": _r(rcm_taxable),
            "cgst": _r(rcm_c),
            "sgst": _r(rcm_s),
            "igst": _r(rcm_i),
            "total": _r(rcm_c + rcm_s + rcm_i),
        },
    }


# ============================================================================
# Side-by-side comparison
# ============================================================================


def _cmp_row(
    metric: str,
    sources: Dict[str, Optional[float]],
    tolerance: float,
    note: str = "",
    advisory: bool = False,
) -> Dict[str, Any]:
    """Build one comparison row. ``sources`` maps a source label -> value (or
    None when that source does not report the metric). Variance is the spread
    across the reported (non-None) values; status is MATCH within tolerance,
    else MISMATCH. A row with fewer than two reported sources is INFO (nothing
    to reconcile against).

    ``advisory=True`` still computes the variance (so the gap is visible) but
    reports status INFO so the row is EXCLUDED from mismatch_count / all_matched
    -- used for rows whose gap is expected by design (e.g. invoiced-vs-collected
    on the credit/advance-payment retail model), so genuine GST breaks are not
    buried under a recurring, benign amber flag."""
    reported = {k: _r(v) for k, v in sources.items() if v is not None}
    values = list(reported.values())
    if len(values) >= 2:
        variance = round(max(values) - min(values), 2)
        if advisory:
            status = "INFO"
        else:
            status = "MATCH" if variance <= tolerance else "MISMATCH"
    else:
        variance = 0.0
        status = "INFO"
    return {
        "metric": metric,
        "sources": reported,
        "variance": variance,
        "status": status,
        "note": note,
    }


def build_crosscheck(
    gstr1: Dict[str, Any],
    gstr3b: Dict[str, Any],
    books: Dict[str, Any],
    tally: Dict[str, Any],
    tolerance: float = DEFAULT_TOLERANCE,
    unplaced: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the full cross-check payload from the aggregated sources.

    ``gstr1``  : aggregate_gstr1() output.
    ``gstr3b`` : aggregate_gstr3b() output.
    ``books``  : finance-side order/payment figures for the (entity, month):
                 {sales_taxable, sales_tax, sales_grand_total,
                  payments_collected, input_credit}. input_credit is the
                  purchase-side ITC finance.gst_reconciliation derives from
                  purchase_orders (an INDEPENDENT source vs GSTR-3B's
                  vendor_bills ITC -- a real cross-check).
    ``tally``  : {taxable, tax, cgst, sgst, igst} the Tally sales-JV export
                 would carry for the same order set.
    ``unplaced``: reports gst_itc._itc_unplaced -- booked input credit NO
                 GSTIN's GSTR-3B counts ({count, tax, bill_numbers}). When
                 given, a row compares it to zero, so credit silently missing
                 from every return can never read as a green screen; a
                 read failure (``failed``) is a MISMATCH row, and
                 ``unregistered`` (credit claimed from suppliers with no
                 GSTIN) gets a row of its own.

    Returns the comparison rows, the GSTR-1 per-rate breakup, the CDNR and
    deemed-supply detail, and a summary (mismatch_count / all_matched). Pure.
    """
    tolerance = _f(tolerance) or DEFAULT_TOLERANCE
    gstr1 = gstr1 or {}
    gstr3b = gstr3b or {}
    books = books or {}
    tally = tally or {}

    g1_taxable = _f(gstr1.get("totalTaxableValue"))
    g1_tax = _f(gstr1.get("totalTax"))
    g3_out_tax = _f(gstr3b.get("outwardTax"))
    itc_total = _f((gstr3b.get("itc") or {}).get("total"))
    net_cash = _f((gstr3b.get("netCash") or {}).get("total"))

    # Inter-GSTIN transfer deemed supply (Schedule I). GSTR-1 / GSTR-3B INCLUDE
    # it; the orders-only Books/Tally sources do NOT (a stock transfer is not an
    # order). Add it to Books/Tally on the outward rows so a routine transfer
    # month does not flag a phantom MISMATCH on the headline totals.
    deemed = gstr1.get("deemed_supply") or {}
    d_taxable = _f(deemed.get("taxableValue"))
    d_tax = _f(deemed.get("tax"))
    d_cgst = _f(deemed.get("cgst"))
    d_sgst = _f(deemed.get("sgst"))
    d_igst = _f(deemed.get("igst"))
    deemed_note = (
        (
            "Books / Tally include Rs %s inter-GSTIN transfer deemed supply "
            "(Rs %s tax) so they line up with GSTR-1 / GSTR-3B, which already "
            "carry it. See the deemed-supply card below."
            % (f"{d_taxable:,.2f}", f"{d_tax:,.2f}")
        )
        if (d_taxable or d_tax)
        else ""
    )
    # A CGST/SGST-vs-IGST swap whose totals still agree is state-format drift
    # (store vs customer state stored as name vs code), not a real break.
    state_note = (
        "If this splits differently across sources but 'Total output GST' still "
        "matches, it is store/customer state-format drift (e.g. 'Jharkhand' vs "
        "'JH'), not a GST break."
    )
    head_note = (deemed_note + " " + state_note).strip() if deemed_note else state_note

    # Per-head net cash the SAME way GSTR-3B derives it (output minus ITC clamped
    # at 0, plus RCM), so this row is like-for-like and only breaks on a real
    # 3B math error. In a single-entity view it equals gstr3b.netCash exactly.
    out_c = _f(gstr3b.get("cgst"))
    out_s = _f(gstr3b.get("sgst"))
    out_i = _f(gstr3b.get("igst"))
    itc = gstr3b.get("itc") or {}
    rcm = gstr3b.get("rcm") or {}
    net_expected = round(
        max(0.0, out_c - _f(itc.get("cgst"))) + _f(rcm.get("cgst"))
        + max(0.0, out_s - _f(itc.get("sgst"))) + _f(rcm.get("sgst"))
        + max(0.0, out_i - _f(itc.get("igst"))) + _f(rcm.get("igst")),
        2,
    )
    # R2: net_expected clamps the CROSS-ENTITY totals once per head (clamp-of-
    # sums), while netCash clamps PER ENTITY then sums (sum-of-clamps). Those
    # agree exactly for a single entity but diverge without bound in the combined
    # all-entities view (one entity's ITC surplus wrongly nets another's output
    # tax in net_expected). So this row is advisory when more than one entity
    # bucket contributed -- netCash is the correct figure; the comparator is the
    # wrong-model side and must not inflate the sign-off mismatch_count.
    bucket_count = int(_f(gstr3b.get("bucketCount")) or 1)
    net_cash_advisory = bucket_count > 1

    b_taxable = _f(books.get("sales_taxable"))
    b_tax = _f(books.get("sales_tax"))
    b_grand = _f(books.get("sales_grand_total"))
    b_collected = _f(books.get("payments_collected"))
    b_input_credit = books.get("input_credit")

    comparisons: List[Dict[str, Any]] = [
        _cmp_row(
            "Taxable value (outward)",
            {
                "GSTR-1": g1_taxable,
                "GSTR-3B": _f(gstr3b.get("outwardTaxableValue")),
                "Books (orders)": b_taxable + d_taxable,
                "Tally Sales JV": _f(tally.get("taxable")) + d_taxable,
            },
            tolerance,
            note=deemed_note,
        ),
        _cmp_row(
            "Total output GST",
            {
                "GSTR-1": g1_tax,
                "GSTR-3B": g3_out_tax,
                "Books (orders)": b_tax + d_tax,
                "Tally Sales JV": _f(tally.get("tax")) + d_tax,
            },
            tolerance,
            note=deemed_note,
        ),
        _cmp_row(
            "CGST (output)",
            {
                "GSTR-1": _f(gstr1.get("cgst")),
                "GSTR-3B": _f(gstr3b.get("cgst")),
                "Tally Sales JV": _f(tally.get("cgst")) + d_cgst,
            },
            tolerance,
            note=head_note,
        ),
        _cmp_row(
            "SGST (output)",
            {
                "GSTR-1": _f(gstr1.get("sgst")),
                "GSTR-3B": _f(gstr3b.get("sgst")),
                "Tally Sales JV": _f(tally.get("sgst")) + d_sgst,
            },
            tolerance,
            note=head_note,
        ),
        _cmp_row(
            "IGST (output)",
            {
                "GSTR-1": _f(gstr1.get("igst")),
                "GSTR-3B": _f(gstr3b.get("igst")),
                "Tally Sales JV": _f(tally.get("igst")) + d_igst,
            },
            tolerance,
            note=head_note,
        ),
        _cmp_row(
            "Input tax credit (ITC)",
            {
                "GSTR-3B (vendor bills)": itc_total,
                "Books (purchase orders)": (
                    _f(b_input_credit) if b_input_credit is not None else None
                ),
            },
            tolerance,
            note=(
                "GSTR-3B ITC is from booked vendor bills; the books figure is "
                "from purchase orders. A gap means POs raised but bills not yet "
                "booked (or vice versa)."
            ),
        ),
        _cmp_row(
            "Net GST payable (cash)",
            {
                "GSTR-3B": net_cash,
                "Output - ITC + RCM": net_expected,
            },
            tolerance,
            note=(
                "Output tax minus ITC (floored at zero per head) plus reverse-"
                "charge tax, which is paid in cash. This row is informational in "
                "the combined all-entities view: each entity clamps its own cash "
                "separately (one entity's ITC surplus cannot offset another's "
                "output tax), so this comparator legitimately differs from the "
                "summed GSTR-3B figure -- GSTR-3B is the correct number. Open "
                "each entity individually to reconcile net cash exactly."
                if net_cash_advisory
                else "Output tax minus ITC (floored at zero per head) plus "
                "reverse-charge tax, which is paid in cash. In a single-entity "
                "view this equals the GSTR-3B net cash exactly."
            ),
            advisory=net_cash_advisory,
        ),
        _cmp_row(
            "Sales: invoiced vs collected",
            {
                "Invoiced (orders)": b_grand,
                "Collected (payments)": b_collected,
            },
            tolerance,
            note=(
                "A gap here is normal when sales are on credit / partly paid -- "
                "it is a collections check, not a GST break, so it does not count "
                "toward the mismatch total."
            ),
            advisory=True,
        ),
    ]

    if unplaced is not None and unplaced.get("failed"):
        # The bills could not be read: never a green row.
        comparisons.append(
            {
                "metric": "Input credit left off GSTR-3B",
                "sources": {},
                "variance": 0.0,
                "status": "MISMATCH",
                "note": "The booked bills could not be read, so credit left off "
                "the return cannot be ruled out. Retry before filing.",
            }
        )
    elif unplaced is not None:
        n = int(_f(unplaced.get("count")))
        comparisons.append(
            _cmp_row(
                "Input credit left off GSTR-3B",
                {
                    "Booked bills on no return": _f(unplaced.get("tax")),
                    "Expected": 0.0,
                },
                # Exact: one computation against zero, no rounding spread to
                # forgive -- Rs 0.90 of credit on no return is a break.
                0.0,
                note=(
                    "%d booked bill(s) carry input credit that IMS left off every "
                    "GSTIN's GSTR-3B: the bill has no company, no tax heads, no "
                    "date written YYYY-MM-DD (listed every month), or our GST "
                    "number on it is no shop's. IMS cannot edit a booked bill. "
                    "The GSTR-3B figures on this screen already exclude this "
                    "credit; on the GST portal, claim it in Table 4 only if "
                    "your accountant confirms it belongs on that month's return: %s"
                    % (n, ", ".join(str(x) for x in (unplaced.get("bill_numbers") or [])[:20]))
                    if n
                    else "Every booked bill's input credit is on a GSTIN's GSTR-3B."
                ),
            )
        )
        unreg = unplaced.get("unregistered")
        if unreg is not None:
            k = int(_f(unreg.get("count")))
            comparisons.append(
                _cmp_row(
                    "Input credit from suppliers with no GSTIN",
                    {"Claimed on GSTR-3B": _f(unreg.get("tax")), "Expected": 0.0},
                    0.0,
                    note=_unregistered_note(unreg) if k else "Every claimed bill names a registered supplier.",
                )
            )

        denied = [d for d in (unplaced.get("denied_transfers") or []) if isinstance(d, dict)]
        if denied:
            comparisons.append(
                _cmp_row(
                    "Transfers with no input credit",
                    {"Credit denied": round(sum(_f(d.get("tax")) for d in denied), 2)},
                    0.0,
                    note=" ".join(
                        "Transfer from %s - sender has no GSTIN: no input credit "
                        "(bill %s, tax %.2f); check whether outward tax applies "
                        "with your CA."
                        % (d.get("from_shop") or "the sending shop", d.get("bill_number"), _f(d.get("tax")))
                        for d in denied[:20]
                    ),
                )
            )

    mismatches =[c for c in comparisons if c["status"] == "MISMATCH"]

    return {
        "tolerance": round(tolerance, 2),
        "comparisons": comparisons,
        "rate_breakup": gstr1.get("rate_breakup") or [],
        "cdnr": {
            **(gstr1.get("cdnr_totals") or {"count": 0, "taxableValue": 0.0, "tax": 0.0}),
            "rows": gstr1.get("cdnr") or [],
        },
        "deemed_supply": gstr1.get("deemed_supply")
        or {"count": 0, "taxableValue": 0.0, "tax": 0.0, "rows": []},
        "validation": gstr1.get("validation")
        or {"ok": True, "issueCount": 0, "issues": []},
        "summary": {
            "mismatch_count": len(mismatches),
            "mismatch_metrics": [c["metric"] for c in mismatches],
            "all_matched": len(mismatches) == 0,
            "gst_payable": net_cash,
        },
    }


def _unregistered_note(unreg: dict) -> str:
    """The note of the 'Input credit from suppliers with no GSTIN' row. It names
    only what the app can do: nothing edits or cancels a booked bill, and a
    second booking of the same supplier invoice (another vendor record, or the
    same one with 'no input credit') is either refused as a duplicate or claims
    the credit TWICE -- so it never says 'book again'. Stock-transfer mirror
    bills (made by the system, hidden from Purchase Invoices) get their own
    text: their head came from the two shops' GST numbers, not a supplier."""
    mirrors = [str(x) for x in (unreg.get("transfer_bill_numbers") or [])]
    skip = set(mirrors)
    own = [str(x) for x in (unreg.get("bill_numbers") or []) if str(x) not in skip]
    parts = []
    if own:
        parts.append(
            "%d bill(s) were booked before IMS refused input credit without a "
            "valid supplier GSTIN: they name none, and an unregistered "
            "supplier's tax never reaches GSTR-2B, so it cannot be claimed. "
            "IMS counted this credit in the GSTR-3B figure on this screen. "
            "IMS cannot edit or cancel a booked bill, and booking the same "
            "supplier invoice again would claim its credit twice, so do not "
            "book it again. On the GST portal, leave this credit out of "
            "Table 4 of the GSTR-3B you file. Bills booked from now on with "
            "no valid supplier GSTIN are marked no-credit by IMS "
            "automatically: %s" % (len(own), ", ".join(own[:20]))
        )
    if mirrors:
        parts.append(
            "%d stock-transfer bill(s) were made by the system when a transfer "
            "was received, and the sending shop has no GST number of its "
            "company, so IMS set the head from the two shops' GST numbers "
            "with no sender registration. They do not appear in Purchase "
            "Invoices and cannot be booked again. Add the sending shop's "
            "company registration in Organization (left menu) so later transfers "
            "carry one; on the GST portal, leave this credit out of Table 4 "
            "of the GSTR-3B you file: %s" % (len(mirrors), ", ".join(mirrors[:20]))
        )
    return " ".join(parts)
