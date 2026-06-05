"""
IMS 2.0 - Print legal helpers (statutory primitives for print templates)
========================================================================

Python port of `docs/design/print/legal_helpers.jsx`. Pure, DB-free helpers
that produce the data shapes the 6 in-use print templates need to render in
the statutory aesthetic (bordered, ALL-CAPS, sans-only):

  * LegalHeader(...)     -> customer/vendor-facing full-statutory header dict
                            (Rule 46 21-field identity block + meta strip)
  * StaffHeader(...)     -> internal/staff-facing minimal header dict
                            (logo + branch + doc number, no GSTIN/CIN)
  * amount_in_words(...) -> Indian-numbering helper (Crore / Lakh / Thousand)
  * copy_marker_block(...) -> Rule 48(1) triplicate copy markers
                              (invoice: RECIPIENT/TRANSPORTER/SUPPLIER;
                               Rule 55 challan: CONSIGNEE/TRANSPORTER/CONSIGNOR)
  * statutory_footer(...) -> per-doc canonical rule reference + retention text
  * hsn_tax_summary(...) -> HSN-wise consolidated tax-summary rows
                            (CGST+SGST intra-state vs IGST inter-state, mirrors
                             the routing in services/itc_reconcile.py)
  * declarations(...)    -> standard CGST declaration per doc type
  * format_date(...)     -> DD-Mon-YYYY (e.g. 19-Apr-2026) shared formatter

Rendering is the frontend's job; this module returns dicts the renderer
consumes. Every helper is fail-soft (no raises on junk input) and ASCII-only
in source so it can never trip Windows cp1252.

Per-entity OVERRIDES: every header helper accepts an optional `overrides` dict
sourced from the `print_template_overrides` Mongo collection (see routers/
print_overrides.py). When a field is overridden the override wins; otherwise
sensible CGST/NCAHP-compliant defaults flow from the entity + store dicts the
caller passed in. Defaults are NEVER hard-coded mock identities.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Public template-key catalog -- single source of truth for which template
# keys are addressable by the editor system. Routers and tests import this.
# ---------------------------------------------------------------------------

TEMPLATE_KEYS: Tuple[str, ...] = (
    "tax_invoice",
    "thermal_receipt",
    "rx_card",
    "job_card",
    "grn",
    "z_report",
)

# Which copy markers each doc uses. Tax-invoice and GRN follow Rule 48
# (Original for Recipient / Duplicate for Transporter / Triplicate for
# Supplier). Delivery-challan-style docs swap to Rule 55 labels. Thermal
# receipts and internal staff docs do not carry copy markers.
COPY_MARKER_MODES: Dict[str, str] = {
    "tax_invoice": "rule_48",
    "thermal_receipt": "none",
    "rx_card": "none",
    "job_card": "internal",
    "grn": "rule_48",
    "z_report": "internal",
}


# ---------------------------------------------------------------------------
# Indian-numbering amount-in-words
# ---------------------------------------------------------------------------

_ONES: Tuple[str, ...] = (
    "",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
)

_TENS: Tuple[str, ...] = (
    "",
    "",
    "Twenty",
    "Thirty",
    "Forty",
    "Fifty",
    "Sixty",
    "Seventy",
    "Eighty",
    "Ninety",
)


def _two(n: int) -> str:
    """0..99 -> English words. Empty for 0 (caller composes the higher-order word)."""
    if n <= 0:
        return ""
    if n < 20:
        return _ONES[n]
    t, u = divmod(n, 10)
    if u == 0:
        return _TENS[t]
    return _TENS[t] + "-" + _ONES[u]


def _three(n: int) -> str:
    """0..999 -> English words."""
    if n <= 0:
        return ""
    if n < 100:
        return _two(n)
    h, rest = divmod(n, 100)
    out = _ONES[h] + " Hundred"
    if rest:
        out += " " + _two(rest)
    return out


def amount_in_words(rupees, paise: int = 0) -> str:
    """Render a rupee amount in Indian-numbering English words.

    Examples:
        amount_in_words(0)             -> "Indian Rupees Zero Only"
        amount_in_words(28110)         -> "Indian Rupees Twenty-Eight Thousand One Hundred Ten Only"
        amount_in_words(28110, 25)     -> "Indian Rupees Twenty-Eight Thousand ... Ten and Twenty-Five Paise Only"
        amount_in_words(10000000)      -> "Indian Rupees One Crore Only"
        amount_in_words(100000)        -> "Indian Rupees One Lakh Only"

    Negative amounts (refund context) are prefixed with "Less:" so the words
    stand alone in a credit-note declaration block.

    Accepts ints or floats. Float fractional part contributes to paise when
    `paise` is left at 0 (so amount_in_words(28110.25) and
    amount_in_words(28110, 25) are equivalent). Fail-soft: junk input -> Zero.
    """
    # Float / Decimal path -> split into rupees + paise rounded to nearest paisa.
    try:
        if isinstance(rupees, str):
            rupees = float(rupees)
    except (TypeError, ValueError):
        return "Indian Rupees Zero Only"

    sign = ""
    if isinstance(rupees, (int, float)) and rupees < 0:
        sign = "Less: "
        rupees = -rupees

    try:
        paise = int(paise or 0)
    except (TypeError, ValueError):
        paise = 0

    if isinstance(rupees, float):
        whole = int(rupees)
        frac_paise = round((rupees - whole) * 100)
        # Carry any rounding back into rupees (e.g. 99.999 -> 100.00).
        if frac_paise >= 100:
            whole += 1
            frac_paise = 0
        if paise == 0:
            paise = frac_paise
        rupees = whole
    else:
        try:
            rupees = int(rupees)
        except (TypeError, ValueError):
            rupees = 0

    # Paise overflow safety: 100p == 1Rs.
    if paise >= 100:
        rupees += paise // 100
        paise = paise % 100
    if paise < 0:
        paise = 0

    if rupees < 0:
        # Shouldn't happen after the sign-flip above, but be defensive.
        rupees = 0

    # Indian numbering: Crore | Lakh | Thousand | (rest 0..999)
    crore, rest = divmod(rupees, 10000000)
    lakh, rest = divmod(rest, 100000)
    thousand, rest = divmod(rest, 1000)

    parts: List[str] = []
    if crore:
        parts.append(_two(crore) + " Crore")
    if lakh:
        parts.append(_two(lakh) + " Lakh")
    if thousand:
        parts.append(_two(thousand) + " Thousand")
    if rest:
        parts.append(_three(rest))

    if not parts:
        rupees_words = "Zero"
    else:
        rupees_words = " ".join(parts)

    out = "Indian Rupees " + rupees_words
    if paise:
        out += " and " + _two(paise) + " Paise"
    out += " Only"
    return sign + out


# ---------------------------------------------------------------------------
# Copy markers (Rule 48 for invoice / GRN; Rule 55 for delivery challan)
# ---------------------------------------------------------------------------

_RULE_48_LABELS = (
    "ORIGINAL FOR RECIPIENT",
    "DUPLICATE FOR TRANSPORTER",
    "TRIPLICATE FOR SUPPLIER",
)

_RULE_55_LABELS = (
    "ORIGINAL FOR CONSIGNEE",
    "DUPLICATE FOR TRANSPORTER",
    "TRIPLICATE FOR CONSIGNOR",
)

_COPY_TIER_BY_KEY = {
    "ORIGINAL": 0,
    "DUPLICATE": 1,
    "TRIPLICATE": 2,
}


def copy_marker_block(
    copy_type: str = "ORIGINAL", mode: str = "rule_48"
) -> Dict[str, Any]:
    """Return the 3-strip copy-marker shape used by Rule 48 invoices and
    Rule 55 delivery challans.

    Args:
        copy_type: "ORIGINAL" | "DUPLICATE" | "TRIPLICATE"
        mode:      "rule_48" (invoice/GRN) | "rule_55" (delivery challan)

    Returns:
        {
          "mode": "rule_48",
          "active": "ORIGINAL FOR RECIPIENT",
          "active_index": 0,
          "labels": ["ORIGINAL FOR RECIPIENT", "DUPLICATE FOR ...", "..."],
          "marks":  ["X", " ", " "],
          "rendered": "ORIGINAL FOR RECIPIENT (X) | DUPLICATE FOR TRANSPORTER ( ) | TRIPLICATE FOR SUPPLIER ( )"
        }

    Unknown copy_type silently falls back to "ORIGINAL" (defensive default).
    """
    key = str(copy_type or "ORIGINAL").strip().upper()
    idx = _COPY_TIER_BY_KEY.get(key, 0)
    mode_str = str(mode or "").strip().lower()

    if mode_str == "internal":
        # Internal docs (job card, Z-report, count sheets) carry a single
        # "INTERNAL USE ONLY" pseudo-strip in place of the 3 copy markers.
        return {
            "mode": "internal",
            "active": "INTERNAL USE ONLY",
            "active_index": 0,
            "labels": ["INTERNAL USE ONLY"],
            "marks": ["X"],
            "rendered": "INTERNAL USE ONLY",
        }
    if mode_str == "none":
        return {
            "mode": "none",
            "active": "",
            "active_index": 0,
            "labels": [],
            "marks": [],
            "rendered": "",
        }
    if mode_str == "rule_55":
        labels = list(_RULE_55_LABELS)
        used_mode = "rule_55"
    else:
        labels = list(_RULE_48_LABELS)
        used_mode = "rule_48"

    marks = [" "] * 3
    marks[idx] = "X"
    rendered = " | ".join("{0} ({1})".format(labels[i], marks[i]) for i in range(3))
    return {
        "mode": used_mode,
        "active": labels[idx],
        "active_index": idx,
        "labels": labels,
        "marks": marks,
        "rendered": rendered,
    }


# ---------------------------------------------------------------------------
# Statutory footer text (per-doc rule reference + retention)
# ---------------------------------------------------------------------------

_FOOTER_TEMPLATES: Dict[str, str] = {
    "tax_invoice": (
        "Issued under Sec. 31 CGST Act 2017 r/w Rule 46. "
        "Retain for {retain} years per CGST Rule 56."
    ),
    "thermal_receipt": (
        "Issued under Sec. 31 CGST Act 2017 r/w Rule 46. "
        "Retain for {retain} years per CGST Rule 56."
    ),
    "credit_note": (
        "Issued under Sec. 34(1) CGST Act 2017 r/w Rule 53. "
        "Retain for {retain} years per CGST Rule 56."
    ),
    "debit_note": (
        "Issued under Sec. 34(3) CGST Act 2017 r/w Rule 53. "
        "Retain for {retain} years per CGST Rule 56."
    ),
    "delivery_challan": (
        "Issued under Rule 55 CGST Rules 2017. "
        "Retain for {retain} years per CGST Rule 56."
    ),
    "grn": (
        "Goods Receipt Note - internal control document. "
        "Retain for {retain} years per CGST Rule 56."
    ),
    "z_report": (
        "Day-end cash reconciliation (SOP-FIN-02). "
        "Retain for {retain} years per CGST Rule 56."
    ),
    "rx_card": (
        "Issued under NCAHP Act 2021 by a registered allied healthcare "
        "professional. Valid for use with any registered optician."
    ),
    "job_card": "Internal lens workshop record. Not a statutory tax document.",
}

_DEFAULT_FOOTER = (
    "System-generated document. Retain for {retain} years per CGST Rule 56."
)


def statutory_footer(doc_type: str, retain_years: int = 7) -> str:
    """Canonical footer line for the given doc type.

    `doc_type` is one of TEMPLATE_KEYS or the broader rule-referencing keys
    used by other (out-of-scope-for-this-PR) templates: "credit_note",
    "debit_note", "delivery_challan". Unknown -> generic retention line.

    Examples:
        statutory_footer("tax_invoice")
          -> "Issued under Sec. 31 CGST Act 2017 r/w Rule 46. Retain for 7 ..."
        statutory_footer("z_report", retain_years=5)
          -> "Day-end cash reconciliation (SOP-FIN-02). Retain for 5 years ..."
    """
    key = str(doc_type or "").strip().lower()
    template = _FOOTER_TEMPLATES.get(key, _DEFAULT_FOOTER)
    try:
        retain = int(retain_years)
    except (TypeError, ValueError):
        retain = 7
    if retain <= 0:
        retain = 7
    return template.format(retain=retain)


# ---------------------------------------------------------------------------
# Standard CGST declarations
# ---------------------------------------------------------------------------

_DECLARATIONS: Dict[str, str] = {
    "tax_invoice": (
        "We declare that this invoice shows the actual price of the goods "
        "described and that all particulars are true and correct."
    ),
    "thermal_receipt": (
        "Thank you for your purchase. Goods once sold are governed by our "
        "return policy displayed in-store."
    ),
    "credit_note": (
        "Credit note issued in accordance with Section 34(1) of the CGST Act "
        "2017. Output tax shall be adjusted in the GSTR-3B for the period."
    ),
    "debit_note": (
        "Debit note issued in accordance with Section 34(3) of the CGST Act "
        "2017. Output tax shall be reported in the GSTR-3B for the period."
    ),
    "grn": (
        "Goods inspected and received in good condition unless variance / "
        "remarks recorded against any line."
    ),
    "rx_card": (
        "This prescription is valid for use with any registered optician. "
        "The practitioner has not received any consideration for prescribing "
        "a particular brand of frame, lens, or contact lens."
    ),
    "job_card": (
        "Lens specification verified against prescription on file. Issued for "
        "internal workshop use; not a customer-facing document."
    ),
    "z_report": (
        "Day-end totals are verified against physical cash and tender splits. "
        "Variances over the policy threshold require manager sign-off."
    ),
}


def declarations(doc_type: str) -> str:
    """Standard CGST-compliant declaration text for the given doc type.

    Returns an empty string when no declaration is conventionally required
    for that document (defensive default).
    """
    return _DECLARATIONS.get(str(doc_type or "").strip().lower(), "")


# ---------------------------------------------------------------------------
# Date / currency formatters
# ---------------------------------------------------------------------------


def format_date(value: Any) -> str:
    """Render any date-ish value as DD-Mon-YYYY (e.g. "19-Apr-2026").

    Accepts: datetime, ISO string, "YYYY-MM-DD", None. Junk -> empty string.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return ""
        # Strip a trailing Z (UTC marker) which fromisoformat rejected on
        # older Python releases; tolerate either way for forward-compat.
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            # Try plain date.
            try:
                dt = datetime.strptime(s[:10], "%Y-%m-%d")
            except ValueError:
                return ""
    return dt.strftime("%d-%b-%Y")


def format_datetime_ist(value: Any) -> str:
    """DD-Mon-YYYY HH:MM IST (assumes the caller normalised TZ; we just label)."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return ""
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            try:
                dt = datetime.strptime(s[:10], "%Y-%m-%d")
            except ValueError:
                return ""
    return dt.strftime("%d-%b-%Y %H:%M IST")


# ---------------------------------------------------------------------------
# HSN-wise tax summary (consolidated table after the line items)
# ---------------------------------------------------------------------------


def _f(v: Any) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _state_code_of(value: Any) -> str:
    """Pull a two-digit state code from a place_of_supply / state string /
    GSTIN. Mirrors the logic in itc_reconcile._state_code so the IGST
    routing decision is consistent across modules. ASCII-only.
    """
    if value is None:
        return ""
    s = str(value).strip().upper()
    if not s:
        return ""
    # Leading 2-digit numeric (e.g. "27" or "27-Maharashtra").
    if len(s) >= 2 and s[0].isdigit() and s[1].isdigit():
        return s[:2]
    # GSTIN: first 2 chars are the state code.
    if len(s) >= 15 and s[0].isdigit() and s[1].isdigit():
        return s[:2]
    # Parenthetical numeric: "Maharashtra (27)".
    p_open = s.find("(")
    p_close = s.find(")")
    if 0 <= p_open < p_close:
        inside = s[p_open + 1 : p_close].strip()
        if len(inside) >= 2 and inside[:2].isdigit():
            return inside[:2]
    return ""


def _is_interstate_for(place_of_supply: Any, supplier_state: Any) -> bool:
    """True when the supply is inter-state -> IGST. Missing data defaults to
    False so existing intra-state behaviour never regresses."""
    pos = _state_code_of(place_of_supply)
    sup = _state_code_of(supplier_state)
    if not pos or not sup:
        return False
    return pos != sup


def hsn_tax_summary(
    items: Iterable[Dict[str, Any]],
    place_of_supply: Any = None,
    supplier_state: Any = None,
) -> Dict[str, Any]:
    """Consolidate line items into the HSN-wise tax summary required by
    GST Rule 46 / GSTR-1 staging.

    Each item dict can carry:
      hsn_code / hsn   - HSN/SAC code (string; falls back to "")
      taxable_value / taxable / amount - taxable rupees for the line
      gst_rate / rate  - GST percent (e.g. 5.0, 18.0)
      qty / quantity   - optional, surfaced in the summary
      description / name - optional

    Returns:
      {
        "interstate": bool,
        "rows": [
          {"hsn": "900311", "description": "Frames",
           "qty": 2, "taxable": 12000.00, "rate": 5.0,
           "cgst": 300.00, "sgst": 300.00, "igst": 0.0,
           "total_tax": 600.00, "line_count": 2},
          ...
        ],
        "totals": {"taxable": 12000.00, "cgst": 300.00, "sgst": 300.00,
                   "igst": 0.0, "total_tax": 600.00},
      }
    """
    interstate = _is_interstate_for(place_of_supply, supplier_state)

    rows: Dict[str, Dict[str, Any]] = {}
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        hsn = str(raw.get("hsn_code") or raw.get("hsn") or "").strip() or "-"
        taxable = _f(
            raw.get("taxable_value")
            if raw.get("taxable_value") is not None
            else (
                raw.get("taxable")
                if raw.get("taxable") is not None
                else raw.get("amount")
            )
        )
        try:
            rate = float(raw.get("gst_rate") or raw.get("rate") or 0)
        except (TypeError, ValueError):
            rate = 0.0
        try:
            qty = float(raw.get("qty") or raw.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        description = str(raw.get("description") or raw.get("name") or "").strip()

        # Group by (hsn, rate) so a single HSN with two rates (e.g. tax
        # holiday on a line) doesn't aggregate incorrectly.
        key = "{0}|{1:.2f}".format(hsn, rate)
        row = rows.setdefault(
            key,
            {
                "hsn": hsn,
                "description": description,
                "qty": 0.0,
                "taxable": 0.0,
                "rate": round(rate, 2),
                "cgst": 0.0,
                "sgst": 0.0,
                "igst": 0.0,
                "total_tax": 0.0,
                "line_count": 0,
            },
        )
        # Keep a non-empty description if any line provided one.
        if description and not row["description"]:
            row["description"] = description
        row["qty"] += qty
        row["taxable"] = round(row["taxable"] + taxable, 2)
        tax = round(taxable * rate / 100.0, 2)
        if interstate:
            row["igst"] = round(row["igst"] + tax, 2)
        else:
            half = round(tax / 2.0, 2)
            row["cgst"] = round(row["cgst"] + half, 2)
            # Use the residual on SGST so half-rupees don't accumulate
            # one-sided drift.
            row["sgst"] = round(row["sgst"] + (tax - half), 2)
        row["total_tax"] = round(row["cgst"] + row["sgst"] + row["igst"], 2)
        row["line_count"] += 1

    out_rows = sorted(rows.values(), key=lambda r: (r["hsn"], r["rate"]))
    totals = {
        "taxable": round(sum(r["taxable"] for r in out_rows), 2),
        "cgst": round(sum(r["cgst"] for r in out_rows), 2),
        "sgst": round(sum(r["sgst"] for r in out_rows), 2),
        "igst": round(sum(r["igst"] for r in out_rows), 2),
        "total_tax": round(sum(r["total_tax"] for r in out_rows), 2),
    }
    return {
        "interstate": interstate,
        "rows": out_rows,
        "totals": totals,
    }


# ---------------------------------------------------------------------------
# LegalHeader / StaffHeader builders
# ---------------------------------------------------------------------------


def _pick(entity: Optional[Dict[str, Any]], *keys: str) -> str:
    """Read the first non-empty string under `keys` from `entity`. Safe on None."""
    if not isinstance(entity, dict):
        return ""
    for k in keys:
        v = entity.get(k)
        if v in (None, ""):
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _gstin_for_state(
    entity: Optional[Dict[str, Any]], state_code: str
) -> Tuple[str, str]:
    """Return (gstin, state_name) for the entity's registration that matches
    `state_code`. Falls back to the primary registration; finally to ("", "")."""
    if not isinstance(entity, dict):
        return "", ""
    gstins = entity.get("gstins") or []
    if not isinstance(gstins, list) or not gstins:
        return "", ""

    target = str(state_code or "").strip()
    primary: Optional[Dict[str, Any]] = None
    for g in gstins:
        if not isinstance(g, dict):
            continue
        if target and str(g.get("state_code", "")).strip() == target:
            return str(g.get("gstin", "")), str(g.get("state_name") or "")
        if primary is None or g.get("is_primary"):
            primary = g
    if primary is None:
        return "", ""
    return str(primary.get("gstin", "")), str(primary.get("state_name") or "")


def _apply_overrides(
    fields: Dict[str, Any], overrides: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Return a copy of fields with any non-None override applied."""
    if not isinstance(overrides, dict):
        return dict(fields)
    out = dict(fields)
    for k, v in overrides.items():
        if v is None:
            continue
        # Empty strings are treated as "do not override" so the editor doesn't
        # accidentally blank a required statutory field. The owner deletes the
        # row via the Revert button instead.
        if isinstance(v, str) and not v.strip():
            continue
        out[k] = v
    return out


def LegalHeader(  # noqa: N802 - intentionally mirror the JSX export name
    entity: Optional[Dict[str, Any]],
    store: Optional[Dict[str, Any]],
    doc_type: str,
    doc_number: str = "",
    doc_date: Any = None,
    place_of_supply: Any = None,
    reverse_charge: bool = False,
    copy_marker: str = "ORIGINAL",
    overrides: Optional[Dict[str, Any]] = None,
    extra_meta: Optional[List[Tuple[str, str]]] = None,
) -> Dict[str, Any]:
    """Build the data shape for the customer/vendor-facing statutory header.

    `entity` is the legal entity dict (from the `entities` collection or any
    equivalent shape: name/legal_name/pan/cin/registered_address/website +
    gstins list). `store` is the place-of-supply outlet (from the `stores`
    collection: name/address/city/state/state_code/pincode/phone/email).
    `overrides` is the per-entity-per-template content override dict from
    `print_template_overrides` (see routers/print_overrides.py).

    Returns a dict the frontend renders into the bordered ALL-CAPS header.
    All values are ASCII-safe and may be empty strings (which the renderer
    must hide gracefully).
    """
    # ---- supplier identity ------------------------------------------------
    legal_name = _pick(entity, "legal_name", "name")
    trade_name = _pick(entity, "name")
    if trade_name == legal_name:
        # If both keys are the same, drop the parenthetical "(Trade name: ...)"
        # by emptying trade_name so the renderer skips it.
        trade_name = ""

    pan = _pick(entity, "pan")
    cin = _pick(entity, "cin", "llpin")
    registered_address = _pick(entity, "registered_address")
    registered_phone = _pick(entity, "registered_phone")
    registered_email = _pick(entity, "registered_email")
    website = _pick(entity, "website")

    # ---- store / place of supply -----------------------------------------
    store_name = _pick(store, "name", "store_name", "trade_name")
    store_addr_lines: List[str] = []
    for k in ("address", "street", "address_line_1"):
        v = _pick(store, k)
        if v and v not in store_addr_lines:
            store_addr_lines.append(v)
    city = _pick(store, "city")
    state_name_store = _pick(store, "state", "state_name")
    pincode = _pick(store, "pincode")
    store_phone = _pick(store, "phone")
    store_email = _pick(store, "email")
    store_state_code = _pick(store, "state_code")

    store_addr_full = ", ".join(
        part for part in store_addr_lines + [city, state_name_store, pincode] if part
    )

    # ---- pick the GSTIN for the store state ------------------------------
    gstin, state_name_gst = _gstin_for_state(entity, store_state_code)
    state_name = state_name_gst or state_name_store

    # ---- defaults that can be overridden ----------------------------------
    defaults: Dict[str, Any] = {
        "legal_name": legal_name,
        "trade_name": trade_name,
        "header_subtitle": "",
        "registered_address": registered_address,
        "registered_email": registered_email,
        "registered_phone": registered_phone,
        "website": website,
        "pan": pan,
        "cin": cin,
        "drug_licence_no": "",
        "ncahp_uid": "",
        "dmc_reg": "",
        "signatory_name": "",
        "signatory_designation": "Authorised Signatory",
        "footer_terms": "",
        "logo_url": _pick(entity, "logo_url"),
        "retention_years": 7,
        "reverse_charge_default": False,
    }
    applied = _apply_overrides(defaults, overrides)

    # Reverse-charge: caller-provided value wins; falls back to override
    # default; defaults to False.
    rc_value = bool(reverse_charge) if reverse_charge is not None else False
    if not rc_value and applied.get("reverse_charge_default"):
        rc_value = True

    place_of_supply_str = (
        str(place_of_supply).strip()
        if place_of_supply is not None and str(place_of_supply).strip()
        else (state_name or "")
    )

    cmb = copy_marker_block(
        copy_marker, mode=COPY_MARKER_MODES.get(doc_type, "rule_48")
    )

    meta_rows: List[Tuple[str, str]] = []
    if doc_number:
        meta_rows.append(("Document No.", str(doc_number)))
    if doc_date is not None:
        meta_rows.append(("Date", format_date(doc_date)))
    meta_rows.append(("Place of Supply", place_of_supply_str))
    meta_rows.append(("Reverse Charge", "Yes" if rc_value else "No"))
    if extra_meta:
        for kv in extra_meta:
            try:
                k, v = kv
            except (TypeError, ValueError):
                continue
            if k is None:
                continue
            meta_rows.append((str(k), "" if v is None else str(v)))

    supplier_kv: List[Tuple[str, str]] = [
        ("Registered office", applied["registered_address"]),
        ("Place of supply", store_addr_full),
        (
            "Contact",
            " | ".join(
                p
                for p in [
                    applied["registered_phone"],
                    applied["registered_email"],
                    applied["website"],
                ]
                if p
            ),
        ),
        ("GSTIN / UIN", gstin),
        (
            "State / Code",
            (
                (state_name + (" / " + store_state_code if store_state_code else ""))
                if state_name
                else store_state_code
            ),
        ),
        ("PAN", applied["pan"]),
        ("CIN", applied["cin"]),
    ]
    if applied.get("drug_licence_no"):
        supplier_kv.append(("Drug Licence", str(applied["drug_licence_no"])))
    if doc_type == "rx_card":
        if applied.get("ncahp_uid"):
            supplier_kv.append(("NCAHP UID", str(applied["ncahp_uid"])))
        if applied.get("dmc_reg"):
            supplier_kv.append(("State Council Reg", str(applied["dmc_reg"])))
    # Drop empty rows so the renderer's grid stays tight.
    supplier_kv = [(k, v) for k, v in supplier_kv if v]

    return {
        "doc_type": doc_type,
        "doc_number": doc_number or "",
        "doc_date": format_date(doc_date) if doc_date else "",
        "copy_marker": cmb,
        "legal_name": applied["legal_name"],
        "trade_name": applied["trade_name"],
        "header_subtitle": applied.get("header_subtitle", ""),
        "supplier_kv": supplier_kv,
        "store_name": store_name,
        "store_address": store_addr_full,
        "store_phone": store_phone,
        "store_email": store_email,
        "place_of_supply": place_of_supply_str,
        "state_code": store_state_code,
        "state_name": state_name,
        "gstin": gstin,
        "reverse_charge": rc_value,
        "meta": meta_rows,
        "signatory_name": applied.get("signatory_name", ""),
        "signatory_designation": applied.get(
            "signatory_designation", "Authorised Signatory"
        ),
        "footer_terms": applied.get("footer_terms", ""),
        "logo_url": applied.get("logo_url", ""),
        "retention_years": int(applied.get("retention_years") or 7),
    }


def StaffHeader(  # noqa: N802 - intentionally mirror the JSX export name
    entity: Optional[Dict[str, Any]],
    store: Optional[Dict[str, Any]],
    doc_type: str,
    doc_number: str = "",
    doc_date: Any = None,
    overrides: Optional[Dict[str, Any]] = None,
    extra_meta: Optional[List[Tuple[str, str]]] = None,
) -> Dict[str, Any]:
    """Build the minimal internal-doc header (job card, Z-report, count sheets,
    labels). Logo + branch name + doc number + a single meta strip. NO GSTIN,
    CIN, or registered office -- those statutory fields belong to customer /
    vendor-facing documents only.
    """
    defaults: Dict[str, Any] = {
        "trade_name": _pick(entity, "name"),
        "header_subtitle": "",
        "logo_url": _pick(entity, "logo_url"),
        "signatory_name": "",
        "signatory_designation": "",
        "footer_terms": "",
    }
    applied = _apply_overrides(defaults, overrides)

    store_name = _pick(store, "name", "store_name", "trade_name")
    store_code = _pick(store, "code", "store_code")
    branch_label = ", ".join(p for p in [store_name, store_code] if p)
    if not branch_label:
        branch_label = _pick(store, "city")

    meta_rows: List[Tuple[str, str]] = []
    if doc_number:
        meta_rows.append(("Document No.", str(doc_number)))
    if doc_date is not None:
        meta_rows.append(("Date", format_date(doc_date)))
    if extra_meta:
        for kv in extra_meta:
            try:
                k, v = kv
            except (TypeError, ValueError):
                continue
            if k is None:
                continue
            meta_rows.append((str(k), "" if v is None else str(v)))

    return {
        "doc_type": doc_type,
        "doc_number": doc_number or "",
        "doc_date": format_date(doc_date) if doc_date else "",
        "trade_name": applied["trade_name"],
        "header_subtitle": applied.get("header_subtitle", ""),
        "branch_label": branch_label,
        "logo_url": applied.get("logo_url", ""),
        "meta": meta_rows,
        "signatory_name": applied.get("signatory_name", ""),
        "signatory_designation": applied.get("signatory_designation", ""),
        "footer_terms": applied.get("footer_terms", ""),
        "copy_marker": copy_marker_block("ORIGINAL", mode="internal"),
        # Internal docs do not carry copy markers; the field is kept for
        # render symmetry. The "internal" mode emits a single
        # "INTERNAL USE ONLY" pseudo-strip the renderer may choose to hide.
    }


def now_ist_label() -> str:
    """Render the current UTC moment as DD-Mon-YYYY HH:MM IST (labelled UTC
    -- the label is informational; callers that need true IST should pass an
    already-localised datetime to format_datetime_ist)."""
    return format_datetime_ist(datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# GST e-invoice QR block (FIN-1) -- appended to the tax-invoice print context
# ---------------------------------------------------------------------------


def einvoice_qr_block(order: Dict[str, Any]) -> Dict[str, Any]:
    """Return the e-invoice data block for the GST tax invoice template.

    When an order carries a signed QR (einvoice_signed_qr) this helper:
      1. Attempts to render it as a base64-encoded PNG via render_signed_qr_png
         (uses `qrcode` if installed; fail-soft to None when absent).
      2. Returns the IRN, AckNo, AckDate, and either the rendered QR PNG as a
         data URI or the raw signed_qr string + a TODO note for the renderer.

    The print template checks `einvoice.present` before rendering the block.
    When an order has no IRN (not yet generated or DARK) `present` is False
    and the template omits the block entirely -- zero impact on existing invoices.

    Usage (inside a tax_invoice template context builder):
        ctx["einvoice"] = einvoice_qr_block(order_doc)
    """
    irn = str(order.get("irn") or order.get("einvoice_irn") or "").strip()
    if not irn:
        return {
            "present": False,
            "irn": "",
            "ack_no": "",
            "ack_date": "",
            "qr_data_uri": None,
            "signed_qr_raw": None,
            "render_note": "",
        }

    signed_qr = str(order.get("einvoice_signed_qr") or "").strip()
    ack_no = str(order.get("ack_no") or "").strip()
    ack_date = str(order.get("ack_date") or "").strip()

    qr_data_uri: Optional[str] = None
    render_note = ""

    if signed_qr:
        # Lazy import: render_signed_qr_png is import-guarded inside einvoice.py
        try:
            from api.services.einvoice import render_signed_qr_png

            png_bytes = render_signed_qr_png(signed_qr)
            if png_bytes:
                import base64

                b64 = base64.b64encode(png_bytes).decode("ascii")
                qr_data_uri = f"data:image/png;base64,{b64}"
            else:
                render_note = (
                    "TODO: install 'qrcode' + 'Pillow' to auto-render the QR image. "
                    "Use signed_qr_raw to generate it client-side."
                )
        except Exception:  # noqa: BLE001 -- never crash the invoice render
            render_note = (
                "TODO: install 'qrcode' + 'Pillow' to auto-render the QR image. "
                "Use signed_qr_raw to generate it client-side."
            )

    return {
        "present": True,
        "irn": irn,
        "ack_no": ack_no,
        "ack_date": ack_date,
        "qr_data_uri": qr_data_uri,  # base64 PNG data URI when qrcode installed
        "signed_qr_raw": signed_qr or None,  # raw string for client-side render
        "render_note": render_note,
    }
