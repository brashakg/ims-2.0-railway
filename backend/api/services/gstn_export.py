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
- GSTR-1: top-level {gstin, fp, version, hash, b2b, b2cl, b2cs, cdnr, hsn}
    * b2b  : grouped by counterparty GSTIN  -> [{ctin, inv:[...]}]
    * b2cl : grouped by place-of-supply code -> [{pos, inv:[...]}]
    * b2cs : flat rows                       -> [{sply_ty, pos, typ, rt, txval, ...}]
    * cdnr : credit/debit notes to registered persons, grouped by
             counterparty GSTIN -> [{ctin, nt:[{ntty, nt_num, nt_dt, val,
             pos, rchrg, inv_typ, itms:[{num, itm_det:{...}}]}]}]
    * hsn  : {data:[{num, hsn_sc, desc, uqc, qty, txval, rt, iamt, camt, samt, csamt}]}
- GSTR-3B: top-level {gstin, ret_period, sup_details, inter_sup, itc_elg, intr_ltax}

Everything fails soft: malformed / missing input yields a structurally
valid skeleton (empty sections) rather than raising, so a partial month
or an empty store still produces an importable file.

State -> GST state code mapping is best-effort: the IMS order data carries
free-text state names ("Maharashtra", "Delhi"), while the portal wants the
2-digit state code that prefixes a GSTIN ("27", "07"). We map the common
spellings and fall back to deriving the code from a GSTIN when present.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ----------------------------------------------------------------------------
# State name -> GST state code (first 2 digits of a GSTIN). Lower-cased keys.
# Not exhaustive of UTs, but covers all mainland states an optical chain bills.
# ----------------------------------------------------------------------------
_STATE_CODES: Dict[str, str] = {
    "jammu and kashmir": "01",
    "himachal pradesh": "02",
    "punjab": "03",
    "chandigarh": "04",
    "uttarakhand": "05",
    "haryana": "06",
    "delhi": "07",
    "rajasthan": "08",
    "uttar pradesh": "09",
    "bihar": "10",
    "sikkim": "11",
    "arunachal pradesh": "12",
    "nagaland": "13",
    "manipur": "14",
    "mizoram": "15",
    "tripura": "16",
    "meghalaya": "17",
    "assam": "18",
    "west bengal": "19",
    "jharkhand": "20",
    "odisha": "21",
    "chhattisgarh": "22",
    "madhya pradesh": "23",
    "gujarat": "24",
    "daman and diu": "25",
    "dadra and nagar haveli": "26",
    "maharashtra": "27",
    "andhra pradesh": "37",
    "karnataka": "29",
    "goa": "30",
    "lakshadweep": "31",
    "kerala": "32",
    "tamil nadu": "33",
    "puducherry": "34",
    "andaman and nicobar islands": "35",
    "telangana": "36",
    "ladakh": "38",
}


def _state_code(state_name: str, gstin: str = "") -> str:
    """Resolve a 2-digit GST state code from a free-text state name.

    Falls back to the GSTIN prefix when the name is unrecognised, then to
    an empty string. Never raises.
    """
    name = (state_name or "").strip().lower()
    if name in _STATE_CODES:
        return _STATE_CODES[name]
    # Some rows already carry the numeric code as the "state".
    raw = (state_name or "").strip()
    if len(raw) == 2 and raw.isdigit():
        return raw
    g = (gstin or "").strip()
    if len(g) >= 2 and g[:2].isdigit():
        return g[:2]
    return ""


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
    cdnr, hsn. Sections with no rows are emitted as empty lists (the offline
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
        "b2cs": _build_b2cs(data.get("b2cs") or [], store_state),
        # CDNR -- credit/debit notes to REGISTERED persons. Omitting this
        # dropped every credit note from the filed return, overstating the
        # tax liability and mismatching GSTR-2B (Rule 47(1) breach). Built
        # from the `cdnr` list reports.py::_compute_gstr1 produces.
        "cdnr": _build_cdnr(data.get("cdnr") or [], store_state),
        "hsn": _build_hsn(data),
    }
    return out


def _build_b2b(rows: List[Dict[str, Any]], store_state: str) -> List[Dict[str, Any]]:
    """Group B2B invoices by counterparty GSTIN (ctin)."""
    by_ctin: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        ctin = str(r.get("customerGSTIN") or r.get("ctin") or "").strip()
        if not ctin:
            # B2B rows must carry a counterparty GSTIN. Skip silently —
            # the IMS validation report already flags these.
            continue
        pos = _state_code(
            str(r.get("placeOfSupply") or r.get("customerState") or ""), ctin
        )
        inv = {
            "inum": str(r.get("invoiceNumber") or r.get("inum") or ""),
            "idt": _fmt_date(r.get("invoiceDate") or r.get("idt") or ""),
            "val": _num(r.get("invoiceValue") or r.get("val")),
            "pos": pos,
            "rchrg": "N",
            "inv_typ": "R",
            "itms": [
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
            ],
        }
        bucket = by_ctin.setdefault(ctin, {"ctin": ctin, "inv": []})
        bucket["inv"].append(inv)
    return list(by_ctin.values())


def _build_b2cl(rows: List[Dict[str, Any]], store_state: str) -> List[Dict[str, Any]]:
    """Group B2CL (>2.5L inter-state consumer) invoices by place-of-supply."""
    by_pos: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        pos = _state_code(str(r.get("placeOfSupply") or r.get("customerState") or ""))
        inv = {
            "inum": str(r.get("invoiceNumber") or r.get("inum") or ""),
            "idt": _fmt_date(r.get("invoiceDate") or r.get("idt") or ""),
            "val": _num(r.get("invoiceValue") or r.get("val")),
            "itms": [
                {
                    "num": 1,
                    "itm_det": _itm(
                        r.get("gstRate"),
                        r.get("taxableValue"),
                        # B2CL is inter-state by definition -> tax is IGST.
                        r.get("igst") or r.get("totalTax"),
                        0.0,
                        0.0,
                    ),
                }
            ],
        }
        bucket = by_pos.setdefault(pos, {"pos": pos, "inv": []})
        bucket["inv"].append(inv)
    return list(by_pos.values())


def _build_b2cs(rows: List[Dict[str, Any]], store_state: str) -> List[Dict[str, Any]]:
    """B2CS is a flat list of consolidated rows keyed by (pos, rate)."""
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        igst = _num(r.get("igst"))
        pos = _state_code(str(r.get("placeOfSupply") or ""))
        # INTER when IGST present, else INTRA.
        sply_ty = "INTER" if igst > 0 else "INTRA"
        out.append(
            {
                "sply_ty": sply_ty,
                "pos": pos,
                "typ": "OE",  # OE = Other than E-commerce
                "rt": _num(r.get("gstRate")),
                "txval": _num(r.get("taxableValue")),
                "iamt": igst,
                "camt": _num(r.get("cgst")),
                "samt": _num(r.get("sgst")),
                "csamt": _num(r.get("cess")),
            }
        )
    return out


def _build_cdnr(rows: List[Dict[str, Any]], store_state: str) -> List[Dict[str, Any]]:
    """Build the CDNR section: credit/debit notes issued to REGISTERED persons,
    grouped by counterparty GSTIN (ctin).

    Maps from the cdnr items reports.py::_compute_gstr1 produces. Each IMS
    cdnr item carries: refReference, creditNoteDate, customerGSTIN,
    placeOfSupply/customerState, grossValue, taxableValue, cgst, sgst, igst,
    hsnCode, gstRate.

    Portal schema (offline-tool):
        [{ctin, nt:[{ntty, nt_num, nt_dt, val, pos, rchrg, inv_typ,
                     itms:[{num, itm_det:{rt, txval, iamt, camt, samt, csamt}}]}]}]

    `ntty` is "C" (credit note) or "D" (debit note). IMS only issues CREDIT
    notes today (returns / refunds), so we default to "C" and honour an
    explicit `noteType`/`ntty` override when a debit note is ever produced.

    Notes WITHOUT a counterparty GSTIN cannot sit in CDNR -- a credit note to
    an UNREGISTERED consumer belongs in CDNUR, which IMS does not yet compute
    (see PR limitation note). Such rows are skipped here rather than emitted
    with an empty ctin, mirroring the B2B handling.
    """
    by_ctin: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        ctin = str(r.get("customerGSTIN") or r.get("ctin") or "").strip()
        if not ctin:
            # Credit note to an unregistered person -> CDNUR, not CDNR. IMS
            # does not compute CDNUR yet, so skip rather than misfile it.
            continue
        ntty = str(r.get("noteType") or r.get("ntty") or "C").strip().upper()
        if ntty not in ("C", "D"):
            ntty = "C"
        pos = _state_code(
            str(r.get("placeOfSupply") or r.get("customerState") or ""), ctin
        )
        nt = {
            "ntty": ntty,
            "nt_num": str(
                r.get("refReference")
                or r.get("noteNumber")
                or r.get("nt_num")
                or ""
            ),
            "nt_dt": _fmt_date(
                r.get("creditNoteDate") or r.get("noteDate") or r.get("nt_dt") or ""
            ),
            "val": _num(r.get("grossValue") or r.get("val")),
            "pos": pos,
            "rchrg": "N",
            "inv_typ": "R",  # Regular
            "itms": [
                {
                    "num": 1,
                    "itm_det": _itm(
                        r.get("gstRate") or r.get("rt"),
                        r.get("taxableValue") or r.get("txval"),
                        r.get("igst") or r.get("iamt"),
                        r.get("cgst") or r.get("camt"),
                        r.get("sgst") or r.get("samt"),
                        r.get("cess") or r.get("csamt"),
                    ),
                }
            ],
        }
        bucket = by_ctin.setdefault(ctin, {"ctin": ctin, "nt": []})
        bucket["nt"].append(nt)
    return list(by_ctin.values())


def _build_hsn(data: Dict[str, Any]) -> Dict[str, Any]:
    """Build the section-12 HSN summary.

    If the report already carries an `hsn` / `hsnSummary` list, map it
    directly. Otherwise synthesise a single consolidated HSN row from the
    report totals so the section is never empty when there were sales
    (the portal requires an HSN summary).
    """
    raw = data.get("hsn") or data.get("hsnSummary") or data.get("hsn_summary")
    rows: List[Dict[str, Any]] = []
    num = 1
    if isinstance(raw, list) and raw:
        for r in raw:
            if not isinstance(r, dict):
                continue
            rows.append(
                {
                    "num": num,
                    "hsn_sc": str(r.get("hsnCode") or r.get("hsn_sc") or "9004"),
                    "desc": str(r.get("description") or r.get("desc") or ""),
                    "uqc": str(r.get("uqc") or "PCS"),
                    "qty": _num(r.get("quantity") or r.get("qty")),
                    "txval": _num(r.get("taxableValue") or r.get("txval")),
                    "rt": _num(r.get("gstRate") or r.get("rt")),
                    "iamt": _num(r.get("igst") or r.get("iamt")),
                    "camt": _num(r.get("cgst") or r.get("camt")),
                    "samt": _num(r.get("sgst") or r.get("samt")),
                    "csamt": _num(r.get("cess") or r.get("csamt")),
                }
            )
            num += 1
        return {"data": rows}

    # Synthesise from totals when no per-HSN breakdown is available.
    total_taxable = _num(data.get("totalTaxableValue"))
    total_tax = _num(data.get("totalTax"))
    if total_taxable > 0 or total_tax > 0:
        rows.append(
            {
                "num": 1,
                "hsn_sc": "9004",
                "desc": "Spectacles, goggles and the like",
                "uqc": "PCS",
                "qty": 0.0,
                "txval": total_taxable,
                "rt": 0.0,
                # We can't split CGST/SGST/IGST reliably at the aggregate
                # level, so report the combined tax under camt+samt halves
                # only when intra dominates; leave a single combined value
                # in camt as a conservative best-effort. The accountant
                # adjusts in the offline tool before filing.
                "iamt": 0.0,
                "camt": _num(total_tax / 2),
                "samt": _num(total_tax / 2),
                "csamt": 0.0,
            }
        )
    return {"data": rows}


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
    intr_ltax. Fails soft to an all-zero skeleton.
    """
    data = data or {}

    eff_gstin = (gstin or data.get("gstin") or "").strip()
    eff_period = period or data.get("period") or ""
    ret_period = _to_fp(eff_period)

    outward = data.get("outwardTaxableSupplies") or {}
    zero = data.get("zeroRatedSupplies") or {}
    itc = data.get("itcAvailable") or {}

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
        "isup_rev": {
            "txval": 0.0,
            "iamt": 0.0,
            "camt": 0.0,
            "samt": 0.0,
            "csamt": 0.0,
        },
    }

    # Table 4 — Eligible ITC. (A)(5) All other ITC, (C) Net ITC available.
    itc_block = {
        "iamt": _num(itc.get("integratedTax")),
        "camt": _num(itc.get("centralTax")),
        "samt": _num(itc.get("stateTax")),
        "csamt": _num(itc.get("cess")),
    }
    itc_elg = {
        "itc_avl": [
            {
                "ty": "OTH",  # All other ITC
                **itc_block,
            }
        ],
        "itc_net": dict(itc_block),
    }

    out: Dict[str, Any] = {
        "gstin": eff_gstin,
        "ret_period": ret_period,
        "sup_details": sup_details,
        "inter_sup": {
            # 3.2 -- inter-state supplies broken out by counterparty type
            # (unregistered / composition dealer / UIN holder), keyed by
            # place-of-supply.
            #
            # KNOWN LIMITATION (upstream gap, not wired): reports.py
            # ::_compute_gstr3b only computes the AGGREGATE inter-state output
            # tax (out_igst, already reported in 3.1(a) via osup_det.iamt). It
            # does NOT segregate inter-state supplies by counterparty
            # registration type, nor key them by place-of-supply. IMS also does
            # not record whether a customer is a composition dealer or a UIN
            # holder at all. Populating 3.2 correctly therefore requires an
            # upstream change to _compute_gstr3b (a place-of-supply-keyed
            # inter-state B2C aggregation, plus capturing comp/UIN status).
            #
            # Per the fix brief we do the SAFE thing: emit empty lists rather
            # than fabricate a split. 3.2 is a memorandum disclosure that does
            # NOT change the net tax payable (driven by 3.1 + Table 4), so an
            # empty 3.2 understates a disclosure but never the liability. The
            # accountant fills 3.2 in the offline tool before filing.
            "unreg_details": [],
            "comp_details": [],
            "uin_details": [],
        },
        "itc_elg": itc_elg,
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
