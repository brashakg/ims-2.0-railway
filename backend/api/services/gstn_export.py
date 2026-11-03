"""
IMS 2.0 - GSTN portal export
============================
Pure shape-mapping helpers that turn the IMS internal GST-return dicts
(produced by reports.py::gstr1_report / gstr3b_report) into the JSON shape
the GST portal's *offline upload tool* expects.

These functions do NOT touch the database, NOR do they push to the GSTN
network (a direct GSTN API push needs a licensed GSP, which is out of
scope). They produce the offline-utility JSON the accountant uploads on
gst.gov.in -> Returns -> Offline Tool -> Import.

Schema references (public GSTN offline-tool format):
- GSTR-1: top-level {gstin, fp, version, hash, b2b, b2cl, b2cs, cdnr, cdnur, hsn, doc_issue}
    * b2b  : grouped by counterparty GSTIN  -> [{ctin, inv:[...]}]
    * b2cl : grouped by place-of-supply code -> [{pos, inv:[...]}]
    * b2cs : flat rows                       -> [{sply_ty, pos, typ, rt, txval, ...}]
    * cdnr : credit/debit notes to registered persons, grouped by
             counterparty GSTIN -> [{ctin, nt:[{ntty, nt_num, nt_dt, val,
             pos, rchrg, inv_typ, itms:[{num, itm_det:{...}}]}]}]
    * cdnur: an unregistered buyer's note on a B2CL invoice ->
             [{typ:"B2CL", ntty, nt_num, nt_dt, val, pos, itms:[...]}]
      Every other consumer note nets into its B2CS row (owner Option B,
      2026-08-09): the report tags each note's ``noteSection``.
    * hsn  : {hsn_b2b:[...], hsn_b2c:[...]}, each row {num, hsn_sc, desc, uqc,
             qty, rt, txval, iamt, camt, samt, csamt} (Table 12 in two parts)
    * doc_issue : {doc_det:[{doc_num, doc_typ, docs:[{num, from, to, totnum,
             cancel, net_issue}]}]} (Table 13)
- GSTR-3B: top-level {gstin, ret_period, sup_details, inter_sup, itc_elg, intr_ltax}

Missing input yields a structurally valid skeleton (empty sections), so an
empty month or store still produces an importable file. The GSTR-1 door
never hands a failed report to these mappers and never swallows their
errors (reports.gstr1._gstr1_return answers 503 "please retry") -- a
partial upload file is worse than none.

A row's place of supply is its own `placeOfSupplyCode`; a row without one
is resolved by THE state-code rule (org_validation.resolve_state_code).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# THE state-code rule (org_validation.resolve_state_code: a GSTIN, a 2-digit
# code, the portal's '27-Maharashtra', an abbreviation or a name) -- the one
# the tax invoice and the returns' place of supply use; no table of its own.
from .org_validation import resolve_state_code  # noqa: E402


def _num(val: Any) -> float:
    """Coerce to float, rounded to 2dp. Non-numeric -> 0.0. Never raises."""
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return 0.0


def _to_fp(period: str) -> str:
    """Convert an IMS period string to the portal's `fp` / `ret_period`
    format "MMYYYY".

    Accepts "YYYY-MM" (IMS canonical), "MMYYYY" (already correct), or
    "YYYY-MM-DD". Returns "" when it can't parse, so the caller still
    emits a structurally valid file the accountant can fix.
    """
    if not period or not isinstance(period, str):
        return ""
    p = period.strip()
    # Already MMYYYY
    if len(p) == 6 and p.isdigit():
        return p
    # YYYY-MM or YYYY-MM-DD
    if len(p) >= 7 and p[4] == "-":
        year = p[:4]
        mon = p[5:7]
        if year.isdigit() and mon.isdigit():
            return f"{mon}{year}"
    return ""


def _itm(
    rate: Any, taxable: Any, igst: Any, cgst: Any, sgst: Any, cess: Any = 0.0
) -> Dict[str, Any]:
    """Build one `itm_det` tax block in portal shape."""
    return {
        "rt": _num(rate),
        "txval": _num(taxable),
        "iamt": _num(igst),
        "camt": _num(cgst),
        "samt": _num(sgst),
        "csamt": _num(cess),
    }


# ============================================================================
# GSTR-1
# ============================================================================


def to_gstr1_json(
    data: Optional[Dict[str, Any]],
    gstin: str = "",
    period: str = "",
) -> Dict[str, Any]:
    """Map the IMS GSTR-1 report dict to the GSTN offline-tool JSON schema.

    `data` is the dict returned by reports.py::gstr1_report. `gstin` and
    `period` override the header; when blank they fall back to the values
    inside `data` ("gstin" / "period").

    Returns a dict with keys: gstin, fp, version, hash, b2b, b2cl, b2cs,
    cdnr, cdnur, hsn, doc_issue. Sections with no rows are emitted as empty lists (the offline
    tool accepts empty arrays and simply imports nothing for that section).
    """
    data = data or {}

    eff_gstin = (gstin or data.get("gstin") or "").strip()
    eff_period = period or data.get("period") or ""
    fp = _to_fp(eff_period)

    store_state = str(data.get("storeState") or data.get("store_state") or "")

    out: Dict[str, Any] = {
        "gstin": eff_gstin,
        "fp": fp,
        "version": "GST3.2",
        "hash": "hash",
        "b2b": _build_b2b(data.get("b2b") or [], store_state),
        "b2cl": _build_b2cl(data.get("b2cl") or [], store_state),
        "b2cs": _build_b2cs(data.get("b2cs") or [], store_state, data.get("cdnr") or []),
        # CDNR -- credit/debit notes to REGISTERED persons. Omitting this
        # dropped every credit note from the filed return, overstating the
        # tax liability and mismatching GSTR-2B (Rule 47(1) breach). Built
        # from the `cdnr` list reports.py::_compute_gstr1 produces.
        "cdnr": _build_cdnr(data.get("cdnr") or [], store_state),
        # CDNUR -- an unregistered buyer's note on a B2CL invoice.
        "cdnur": _build_cdnur(data.get("cdnr") or []),
        "hsn": _build_hsn(data),
        "doc_issue": _build_doc_issue(data),
    }
    return out


def _invoice_itms(r: Dict[str, Any]) -> List[Dict[str, Any]]:
    """An invoice's `itms`: ONE itm_det block PER GST RATE, for B2B and B2CL
    alike. Rows carrying per-rate detail (`rateLines` -- every sale row
    reports._compute_gstr1 builds, and transfer deemed-supply rows) emit one
    block per rate, each self-consistent (txval * rt == tax); a single blended
    block (rt=5 with the tax of a 5% + 18% invoice) misfiles the 18% part and
    fails the offline tool's item validation. A row without `rateLines` (a
    legacy header-only transfer bill) keeps one block from its own heads. The
    heads are always the row's own: an intra-state row is never refiled as
    IGST."""
    rate_lines = r.get("rateLines")
    itms: List[Dict[str, Any]] = []
    if isinstance(rate_lines, list):
        itms = [
            {
                "num": i,
                "itm_det": _itm(
                    rl.get("rate"),
                    rl.get("taxable"),
                    rl.get("igst"),
                    rl.get("cgst"),
                    rl.get("sgst"),
                ),
            }
            for i, rl in enumerate(
                (rl for rl in rate_lines if isinstance(rl, dict)), start=1
            )
        ]
    if not itms:
        itms = [
            {
                "num": 1,
                "itm_det": _itm(
                    r.get("gstRate"),
                    r.get("taxableValue"),
                    r.get("igst"),
                    r.get("cgst"),
                    r.get("sgst"),
                ),
            }
        ]
    return itms


def _build_b2b(rows: List[Dict[str, Any]], store_state: str) -> List[Dict[str, Any]]:
    """Group B2B invoices by counterparty GSTIN (ctin); each invoice's items
    one per GST rate (_invoice_itms)."""
    by_ctin: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        ctin = str(r.get("customerGSTIN") or r.get("ctin") or "").strip()
        if not ctin:
            # B2B rows must carry a counterparty GSTIN. Skip silently —
            # the IMS validation report already flags these.
            continue
        pos = r.get("placeOfSupplyCode") or resolve_state_code(
            str(r.get("placeOfSupply") or r.get("customerState") or ""), ctin
        )
        inv = {
            "inum": str(r.get("invoiceNumber") or r.get("inum") or ""),
            "idt": _fmt_date(r.get("invoiceDate") or r.get("idt") or ""),
            "val": _num(r.get("invoiceValue") or r.get("val")),
            "pos": pos,
            "rchrg": "N",
            "inv_typ": "R",
            "itms": _invoice_itms(r),
        }
        bucket = by_ctin.setdefault(ctin, {"ctin": ctin, "inv": []})
        bucket["inv"].append(inv)
    return list(by_ctin.values())


def _build_b2cl(rows: List[Dict[str, Any]], store_state: str) -> List[Dict[str, Any]]:
    """Group B2CL invoices (inter-state, to a consumer, above the B2CL limit --
    reports.gst_base.B2CL_INVOICE_THRESHOLD decides which rows these are) by
    place-of-supply; each invoice's items one per GST rate (_invoice_itms)."""
    by_pos: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        pos = r.get("placeOfSupplyCode") or resolve_state_code(
            str(r.get("placeOfSupply") or r.get("customerState") or "")
        )
        inv = {
            "inum": str(r.get("invoiceNumber") or r.get("inum") or ""),
            "idt": _fmt_date(r.get("invoiceDate") or r.get("idt") or ""),
            "val": _num(r.get("invoiceValue") or r.get("val")),
            "itms": _invoice_itms(r),
        }
        bucket = by_pos.setdefault(pos, {"pos": pos, "inv": []})
        bucket["inv"].append(inv)
    return list(by_pos.values())


def _note_section(r: Dict[str, Any]) -> str:
    """Where a note files (owner Option B, 2026-08-09): the report's own
    ``noteSection``; a row without one, by its buyer -- a GSTIN -> CDNR, else
    B2CS netting."""
    sec = str(r.get("noteSection") or "").upper()
    if sec in ("CDNR", "CDNUR", "B2CS"):
        return sec
    return "CDNR" if str(r.get("customerGSTIN") or r.get("ctin") or "").strip() else "B2CS"


def _note_sign(r: Dict[str, Any]) -> int:
    """+1 for a debit note (adds to the supply), -1 for a credit note."""
    return 1 if str(r.get("noteType") or r.get("ntty") or "C").strip().upper() == "D" else -1


def b2cs_net_rows(rows: List[Dict[str, Any]], notes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """B2CS rows keyed by (supply type, pos, rate), NET of the consumer notes
    that file there (credit notes less, debit notes more), each note per rate
    (its ``rateLines``). UNCLAMPED: a row its notes exceed comes out negative
    -- the export files it at 0 and the report flags the excess (reports
    gstr1). Pure."""
    by: Dict[tuple, Dict[str, Any]] = {}

    def add(pos, rate, taxable, igst, cgst, sgst, cess, sign):
        sply_ty = "INTER" if _num(igst) > 0 else "INTRA"
        key = (sply_ty, pos, _num(rate))
        v = by.setdefault(key, {"sply_ty": sply_ty, "pos": pos, "typ": "OE", "rt": _num(rate),
                                "txval": 0.0, "iamt": 0.0, "camt": 0.0, "samt": 0.0, "csamt": 0.0})
        for k, x in (("txval", taxable), ("iamt", igst), ("camt", cgst), ("samt", sgst), ("csamt", cess)):
            v[k] += sign * _num(x)

    for r in rows:
        if isinstance(r, dict):
            pos = r.get("placeOfSupplyCode") or resolve_state_code(str(r.get("placeOfSupply") or ""))
            add(pos, r.get("gstRate"), r.get("taxableValue"), r.get("igst"), r.get("cgst"),
                r.get("sgst"), r.get("cess"), 1)
    for n in notes:
        if not isinstance(n, dict) or _note_section(n) != "B2CS":
            continue
        pos = n.get("placeOfSupplyCode") or resolve_state_code(
            str(n.get("placeOfSupply") or n.get("customerState") or "")
        )
        lines = [ln for ln in n.get("rateLines") or [] if isinstance(ln, dict)] or [
            {"rate": n.get("gstRate"), "taxable": n.get("taxableValue"), "igst": n.get("igst"),
             "cgst": n.get("cgst"), "sgst": n.get("sgst")}
        ]
        for ln in lines:
            add(pos, ln.get("rate"), ln.get("taxable"), ln.get("igst"), ln.get("cgst"),
                ln.get("sgst"), 0, _note_sign(n))
    return [{**v, **{k: round(v[k], 2) for k in ("txval", "iamt", "camt", "samt", "csamt")}}
            for v in by.values()]


def _build_b2cs(
    rows: List[Dict[str, Any]], store_state: str, notes: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """B2CS: consolidated rows keyed by (supply type, pos, rate), net of the
    consumer notes (b2cs_net_rows). The portal takes no negative row: one its
    credit notes exceed files at 0 (the report says so for the CA)."""
    return [
        {**v, **{k: max(0.0, v[k]) for k in ("txval", "iamt", "camt", "samt", "csamt")}}
        for v in b2cs_net_rows(rows, notes or [])
    ]


def _note_nt(r: Dict[str, Any]) -> Dict[str, Any]:
    """One note's common portal fields: ntty, nt_num, nt_dt, val, itms (one
    per rate, _invoice_itms over its ``rateLines``)."""
    return {
        "ntty": "D" if _note_sign(r) > 0 else "C",
        "nt_num": str(r.get("refReference") or r.get("noteNumber") or r.get("nt_num") or ""),
        "nt_dt": _fmt_date(r.get("creditNoteDate") or r.get("noteDate") or r.get("nt_dt") or ""),
        "val": _num(r.get("grossValue") or r.get("val")),
        "itms": _invoice_itms(r),
    }


def _build_cdnur(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """CDNUR: an unregistered buyer's credit/debit note on a B2CL invoice
    (the report's ``noteSection`` CDNUR), typ B2CL, with its place of supply."""
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict) or _note_section(r) != "CDNUR":
            continue
        pos = r.get("placeOfSupplyCode") or resolve_state_code(
            str(r.get("placeOfSupply") or r.get("customerState") or "")
        )
        nt = _note_nt(r)
        out.append({"typ": "B2CL", "ntty": nt["ntty"], "nt_num": nt["nt_num"],
                    "nt_dt": nt["nt_dt"], "val": nt["val"], "pos": pos, "itms": nt["itms"]})
    return out


def _build_cdnr(rows: List[Dict[str, Any]], store_state: str) -> List[Dict[str, Any]]:
    """CDNR: credit/debit notes to REGISTERED buyers (a GSTIN; the report's
    ``noteSection`` CDNR), document-wise, grouped by counterparty GSTIN:
        [{ctin, nt:[{ntty, nt_num, nt_dt, val, pos, rchrg, inv_typ, itms}]}]
    ``ntty`` C or D (the row's ``noteType``); ``itms`` one per GST rate. A
    consumer's note is CDNUR (B2CL sale) or nets into B2CS -- never here with
    an empty ctin."""
    by_ctin: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if not isinstance(r, dict) or _note_section(r) != "CDNR":
            continue
        ctin = str(r.get("customerGSTIN") or r.get("ctin") or "").strip()
        if not ctin:
            continue
        pos = r.get("placeOfSupplyCode") or resolve_state_code(
            str(r.get("placeOfSupply") or r.get("customerState") or ""), ctin
        )
        nt = _note_nt(r)
        nt = {"ntty": nt["ntty"], "nt_num": nt["nt_num"], "nt_dt": nt["nt_dt"], "val": nt["val"],
              "pos": pos, "rchrg": "N", "inv_typ": "R", "itms": nt["itms"]}
        by_ctin.setdefault(ctin, {"ctin": ctin, "nt": []})["nt"].append(nt)
    return list(by_ctin.values())


def _build_hsn(data: Dict[str, Any]) -> Dict[str, Any]:
    """Table 12, the HSN summary, in its two parts (CA 2026-10-23): ``hsn_b2b``
    (supplies to a GSTIN) and ``hsn_b2c``, each row per HSN per rate with its
    UQC and total quantity, from the report's ``hsnB2B`` / ``hsnB2C``
    (reports gstr1._hsn_rows). Never synthesised from totals: a row the report
    does not carry is not filed."""

    def rows(raw: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for r in raw if isinstance(raw, list) else []:
            if not isinstance(r, dict):
                continue
            out.append(
                {
                    "num": len(out) + 1,
                    "hsn_sc": str(r.get("hsnCode") or "9004"),
                    "desc": str(r.get("description") or ""),
                    "uqc": str(r.get("uqc") or "PCS"),
                    "qty": _num(r.get("quantity")),
                    "rt": _num(r.get("gstRate")),
                    "txval": _num(r.get("taxableValue")),
                    "iamt": _num(r.get("igst")),
                    "camt": _num(r.get("cgst")),
                    "samt": _num(r.get("sgst")),
                    "csamt": _num(r.get("cess")),
                }
            )
        return out

    return {"hsn_b2b": rows(data.get("hsnB2B")), "hsn_b2c": rows(data.get("hsnB2C"))}


def _build_doc_issue(data: Dict[str, Any]) -> Dict[str, Any]:
    """Table 13, documents issued: one ``doc_det`` per document type, each
    series run a ``docs`` row (from, to, total, cancelled, net issued), from
    the report's ``docsIssued`` (reports gstr1._docs_issued)."""
    by_type: Dict[int, Dict[str, Any]] = {}
    for r in data.get("docsIssued") or []:
        if not isinstance(r, dict):
            continue
        det = by_type.setdefault(
            int(r["docNum"]), {"doc_num": int(r["docNum"]), "doc_typ": r["docType"], "docs": []}
        )
        det["docs"].append(
            {
                "num": len(det["docs"]) + 1,
                "from": r["from"],
                "to": r["to"],
                "totnum": int(r["total"]),
                "cancel": int(r["cancelled"]),
                "net_issue": int(r["total"]) - int(r["cancelled"]),
            }
        )
    return {"doc_det": [by_type[k] for k in sorted(by_type)]}


def _fmt_date(d: Any) -> str:
    """Portal wants invoice date as DD-MM-YYYY. IMS stores YYYY-MM-DD.

    Returns the input unchanged when it's not the expected ISO shape, so
    a pre-formatted or empty value passes through harmlessly.
    """
    s = str(d or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        y, m, day = s[:4], s[5:7], s[8:10]
        return f"{day}-{m}-{y}"
    return s


# ============================================================================
# GSTR-3B
# ============================================================================


def to_gstr3b_json(
    data: Optional[Dict[str, Any]],
    gstin: str = "",
    period: str = "",
) -> Dict[str, Any]:
    """Map the IMS GSTR-3B report dict to the GSTN offline-tool JSON schema.

    `data` is the dict returned by reports.py::gstr3b_report. Returns a
    dict with keys: gstin, ret_period, sup_details, inter_sup, itc_elg,
    inward_sup, intr_ltax. Fails soft to an all-zero skeleton.
    """
    data = data or {}

    eff_gstin = (gstin or data.get("gstin") or "").strip()
    eff_period = period or data.get("period") or ""
    ret_period = _to_fp(eff_period)

    outward = data.get("outwardTaxableSupplies") or {}
    zero = data.get("zeroRatedSupplies") or {}
    itc = data.get("itcAvailable") or {}
    inelg = data.get("itcIneligible") or {}
    inward = data.get("inwardExemptNilNonGst") or {}
    itc_rcm = data.get("itcReverseCharge") or {}
    reversed_itc = data.get("itcReversed") or {}
    rcm = data.get("inwardSuppliesReverseCharge") or {}

    # 3.1(a) — Outward taxable supplies (other than zero/nil/exempt)
    osup_det = {
        "txval": _num(data.get("outwardTaxableValue")),
        "iamt": _num(outward.get("integratedTax")),
        "camt": _num(outward.get("centralTax")),
        "samt": _num(outward.get("stateTax")),
        "csamt": _num(outward.get("cess")),
    }

    # 3.1(b) — Outward zero-rated supplies
    osup_zero = {
        "txval": _num(data.get("zeroRatedValue")),
        "iamt": _num(zero.get("integratedTax")),
        "csamt": _num(zero.get("cess")),
    }

    # 3.1(c) — Nil-rated / exempt outward supplies
    osup_nil_exmp = {
        "txval": _num(data.get("exemptSupplies")),
    }

    sup_details = {
        "osup_det": osup_det,
        "osup_zero": osup_zero,
        "osup_nil_exmp": osup_nil_exmp,
        "osup_nongst": {"txval": _num(data.get("nonGstSupplies"))},
        # 3.1(d) — Inward supplies liable to reverse charge: the shop's own
        # GST on its RCM purchases (it was uploaded as zero).
        "isup_rev": {
            "txval": _num(data.get("inwardSuppliesReverseChargeValue")),
            "iamt": _num(rcm.get("integratedTax")),
            "camt": _num(rcm.get("centralTax")),
            "samt": _num(rcm.get("stateTax")),
            "csamt": _num(rcm.get("cess")),
        },
    }

    # Table 4 — Eligible ITC. (A)(3) inward supplies liable to reverse charge
    # (a part of the credit), (A)(5) all other ITC, (C) Net ITC available.
    keys = {"iamt": "integratedTax", "camt": "centralTax", "samt": "stateTax", "csamt": "cess"}
    itc_block = {k: _num(itc.get(src)) for k, src in keys.items()}
    isrc = {k: _num(itc_rcm.get(src)) for k, src in keys.items()}
    reversal = {k: _num(reversed_itc.get(src)) for k, src in keys.items()}
    # Section 17(5) blocked credit: in 4(A)(5) as GSTR-2B shows it, reversed in
    # 4(B)(1) (CBIC Circular 170/02/2022), so 4(C) stays the claimable credit.
    # ponytail: a blocked reverse-charge bill lands in 4(A)(5), not 4(A)(3);
    # same net, split it out if one is ever booked.
    blocked = {k: _num((data.get("itcBlocked") or {}).get(src)) for k, src in keys.items()}
    # Section 17(5)(h): credit claimed earlier on stock written off this month.
    # Already off itcAvailable (the net), so 4(A)(5) adds it back and 4(B)(1)
    # reverses it, like a supplier note's reversal in 4(B)(2). A re-claim
    # (itcWriteOffReclaimed) is already in itcAvailable, so 4(A)(5) carries it
    # and 4(B)(1) holds reversals only -- never negative.
    written_off = {k: _num((data.get("itcWriteOffReversed") or {}).get(src)) for k, src in keys.items()}
    itc_elg = {
        "itc_avl": [
            {"ty": "OTH", **{k: round(itc_block[k] + reversal[k] + blocked[k] + written_off[k] - isrc[k], 2) for k in keys}},
            {"ty": "ISRC", **isrc},
        ],
        "itc_net": dict(itc_block),
        "itc_rev": [
            {"ty": "OTH", **reversal},
            {"ty": "RUL", **{k: round(blocked[k] + written_off[k], 2) for k in keys}},
        ],
        # 4(D) Ineligible ITC: (1) reclaimed credit (none here), (2) others --
        # the GST paid on purchase bills that claim no input credit (G3).
        # Section 17(5) blocked credit is 4(B)(1) above, never here.
        "itc_inelg": [
            {"ty": "RUL", "iamt": 0.0, "camt": 0.0, "samt": 0.0, "csamt": 0.0},
            {
                "ty": "OTH",
                "iamt": _num(inelg.get("integratedTax")),
                "camt": _num(inelg.get("centralTax")),
                "samt": _num(inelg.get("stateTax")),
                "csamt": _num(inelg.get("cess")),
            },
        ],
    }

    out: Dict[str, Any] = {
        "gstin": eff_gstin,
        "ret_period": ret_period,
        "sup_details": sup_details,
        "inter_sup": {
            # 3.2 -- inter-state supplies to unregistered persons by place of
            # supply (_compute_gstr3b `interStateUnregistered`: GSTR-1's B2CL +
            # inter-state B2CS rows). IMS records no composition dealer or UIN
            # holder, so those two lists stay empty.
            "unreg_details": [
                {"pos": str(u.get("placeOfSupplyCode") or ""),
                 "txval": _num(u.get("taxableValue")),
                 "iamt": _num(u.get("integratedTax"))}
                for u in data.get("interStateUnregistered") or []
                if isinstance(u, dict)
            ],
            "comp_details": [],
            "uin_details": [],
        },
        "itc_elg": itc_elg,
        # Table 5 -- exempt, nil-rated, non-GST and composition inward
        # supplies (purchases from unregistered suppliers reported with them,
        # owner 2026-10-13 G3); IMS records no non-GST purchase separately.
        "inward_sup": {
            "isup_details": [
                {
                    "ty": "GST",
                    "inter": _num(inward.get("interState")),
                    "intra": _num(inward.get("intraState")),
                },
                {"ty": "NONGST", "inter": 0.0, "intra": 0.0},
            ]
        },
        "intr_ltax": {
            # 5.1 — Interest & late fee.
            "intr_details": {
                "iamt": _num((data.get("interest") or {}).get("integratedTax")),
                "camt": _num((data.get("interest") or {}).get("centralTax")),
                "samt": _num((data.get("interest") or {}).get("stateTax")),
                "csamt": _num((data.get("interest") or {}).get("cess")),
            },
        },
    }
    return out
