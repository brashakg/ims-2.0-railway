"""
IMS 2.0 - Accounts-Payable (AP) engine
======================================
Pure, side-effect-free money + date math for the vendor payment cycle:

  * due-date from a bill date + the vendor's credit terms
  * AP aging buckets (current / 1-30 / 31-60 / 61-90 / 90+ days past due)
  * a vendor ledger (chronological bills / payments / debit-notes with a
    running payable balance)
  * TDS (tax deducted at source) on a vendor payment, for the common Indian
    sections (194C / 194J / 194Q)

Everything here takes plain dicts/values and returns plain dicts so it is
trivially unit-testable and never imports the DB. The router (vendors.py)
fetches the rows from Mongo and calls these helpers.

Money convention (payable view)
-------------------------------
A BILL increases what we owe a vendor (a payable). A PAYMENT or a DEBIT-NOTE
reduces it. So:

    vendor balance = sum(bills) - sum(payments incl. TDS) - sum(debit notes)

A payment discharges the bill by its GROSS value = cash paid + TDS withheld
(the TDS is remitted to the government on the vendor's behalf, so from the
vendor's ledger it still settles that much of the bill).

All amounts are floats rounded to 2 dp. Functions are defensive: missing or
garbage fields coerce to 0 / are skipped so a malformed row never raises.
"""

from datetime import datetime, timedelta, date
from typing import List, Optional, Dict

# IST (TZ-P3): the as_of default must be the IST business day, not the UTC box
# clock (00:00-05:30 IST would otherwise age bills against YESTERDAY).
from api.utils.ist import now_ist_naive

# --- TDS sections (rate %) -------------------------------------------------
# Common sections an optical retailer hits when paying vendors / contractors.
# Rates are the post-Budget-2024 "normal" rates (no surcharge/cess, payee has
# a valid PAN; 20% applies without PAN but that is a data-entry override, not a
# default here).
TDS_SECTIONS = {
    "NONE": 0.0,
    "194C_IND": 1.0,  # payment to contractor - individual / HUF
    "194C_OTHER": 2.0,  # payment to contractor - company / firm / others
    "194J": 10.0,  # professional services (default 194J rate)
    "194J_TECH": 2.0,  # 194J technical services / call-centre (2% since FY2020-21)
    "194Q": 0.1,  # purchase of goods (aggregate > Rs 50 lakh / payee)
    "194H": 2.0,  # commission / brokerage (cut 5% -> 2% by Budget 2024, eff. 1 Oct 2024)
    "194I_PLANT": 2.0,  # rent - plant & machinery
    "194I_LAND": 10.0,  # rent - land / building / furniture
}

# FIN-11: Per-section annual monetary thresholds (Rs) below which TDS must NOT
# be deducted.  These are the standard thresholds under the Income Tax Act, AY
# 2024-25.  Sections with no threshold (or 0) have TDS from the first rupee.
# Source: CBDT.  Confirm with your CA before going live -- thresholds may be
# updated in the Union Budget.
TDS_THRESHOLDS: Dict[str, float] = {
    "194C_IND": 100000.0,    # Rs 1 lakh aggregate per FY (single payment > 30k also triggers)
    "194C_OTHER": 100000.0,
    "194J": 30000.0,         # Rs 30k per payee per FY
    "194J_TECH": 30000.0,
    "194Q": 5000000.0,       # Rs 50 lakh aggregate per FY per buyer-seller pair
    "194H": 15000.0,         # Rs 15k per FY
    "194I_PLANT": 240000.0,  # Rs 2.4 lakh per FY
    "194I_LAND": 240000.0,
}

# FIN-11: 206C(1H) TCS - Tax Collected at Source on sale of goods.
# A seller whose turnover > Rs 10 crore in the prior FY must collect 0.1% TCS
# from any buyer to whom aggregate receipts > Rs 50 lakh in the current FY.
TCS_206C1H_RATE: float = 0.1          # 0.1% of the amount received above threshold
TCS_206C1H_THRESHOLD: float = 5000000.0  # Rs 50 lakh per buyer per FY

# AP aging bucket keys, in display order. "current" = not yet past its due
# date; the rest are days PAST the due date.
AGING_BUCKETS = ["current", "1_30", "31_60", "61_90", "90_plus"]


def _f(v) -> float:
    """Coerce anything to a float, defaulting to 0.0."""
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def parse_date(s) -> Optional[datetime]:
    """Tolerant ISO parse for 'YYYY-MM-DD' or full ISO datetimes. None on junk."""
    if isinstance(s, datetime):
        return s
    if not s or not isinstance(s, str):
        return None
    txt = s.strip()
    if not txt:
        return None
    # Try full ISO first, then date-only.
    try:
        return datetime.fromisoformat(txt.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(txt[:10])
    except ValueError:
        return None


def compute_due_date(bill_date_iso: str, credit_days: int) -> Optional[str]:
    """Due date = bill date + credit_days. ISO date string, or None if the
    bill date is unparseable."""
    d = parse_date(bill_date_iso)
    if d is None:
        return None
    try:
        cd = int(credit_days or 0)
    except (TypeError, ValueError):
        cd = 0
    return (d + timedelta(days=cd)).date().isoformat()


def aging_bucket(days_past_due: int) -> str:
    """Map days-past-due to an AP aging bucket key.

    days_past_due <= 0  -> 'current' (not yet due)
    1..30               -> '1_30'
    31..60              -> '31_60'
    61..90              -> '61_90'
    > 90                -> '90_plus'
    """
    try:
        d = int(days_past_due)
    except (TypeError, ValueError):
        d = 0
    if d <= 0:
        return "current"
    if d <= 30:
        return "1_30"
    if d <= 60:
        return "31_60"
    if d <= 90:
        return "61_90"
    return "90_plus"


# --- TDS -------------------------------------------------------------------


def resolve_tds_rate(section: str, overrides: Optional[dict] = None) -> float:
    """The effective TDS rate (%) for a section: an admin-edited DB override wins,
    otherwise the code default in TDS_SECTIONS (0.0 for an unknown section).

    `overrides` is the SUPERADMIN-editable {section: rate} map persisted in
    settings (read by the router); passing it keeps this function pure + tested.
    """
    sec = (section or "NONE").strip().upper()
    if overrides and sec in overrides:
        try:
            return float(overrides[sec])
        except (TypeError, ValueError):
            pass
    return TDS_SECTIONS.get(sec, 0.0)


def compute_tds(base_amount, section: str, overrides: Optional[dict] = None) -> dict:
    """TDS on a payment base for a given section.

    Returns {section, rate, tds_amount, net_payable}. net_payable = the cash
    that actually leaves the bank (base - tds). Unknown section -> 0% (NONE).
    `overrides` (optional) is the admin-edited rate map; an override for the
    section wins over the code default.
    """
    base = _f(base_amount)
    sec = (section or "NONE").strip().upper()
    rate = resolve_tds_rate(sec, overrides)
    tds = round(base * rate / 100.0, 2)
    return {
        "section": sec if sec in TDS_SECTIONS else "NONE",
        "rate": rate,
        "tds_amount": tds,
        "net_payable": round(base - tds, 2),
    }


# FIN-11 --------------------------------------------------------------------


def tds_threshold_status(
    section: str,
    cumulative_paid_fy: float,
    current_payment: float,
    overrides: Optional[dict] = None,
) -> dict:
    """FIN-11: Determine whether TDS should be deducted on a payment and, if
    the threshold is crossed mid-payment, how much of the payment is the
    taxable base.

    Args:
        section:            TDS section code (e.g. "194C_OTHER").
        cumulative_paid_fy: Total amount already paid to this vendor in the
                            current financial year BEFORE this payment.
        current_payment:    The gross amount of the current payment (before TDS).
        overrides:          Optional admin-edited rate map (passed through to
                            compute_tds).

    Returns a dict:
        tds_applies      bool - True when TDS must be deducted.
        taxable_base     float - Portion of current_payment subject to TDS.
                         0 if threshold not crossed; > 0 when deductible.
        threshold        float - The section's annual threshold.
        cumulative_after float - cumulative_paid_fy + current_payment.
        tds_result       dict  - output of compute_tds(taxable_base, section)
                         when tds_applies else None.
    """
    sec = (section or "NONE").strip().upper()
    threshold = TDS_THRESHOLDS.get(sec, 0.0)
    cum = _f(cumulative_paid_fy)
    pmt = _f(current_payment)
    cum_after = round(cum + pmt, 2)

    # If section has no threshold (0.0), TDS is from the first rupee.
    if threshold == 0.0:
        tds_applies = (sec in TDS_SECTIONS and sec != "NONE") and pmt > 0
        taxable_base = pmt if tds_applies else 0.0
    else:
        # Threshold has not yet been crossed -- no TDS yet.
        if cum >= threshold:
            # Already past threshold; entire payment is taxable.
            tds_applies = True
            taxable_base = pmt
        elif cum_after > threshold:
            # This payment crosses the threshold.  Only the amount above
            # the threshold is taxable (conservative interpretation matching
            # CBDT guidance -- entire aggregate is technically taxable from
            # the first payment once the threshold is crossed, but to remain
            # implementable without retroactive adjustments we tax the
            # incremental over-threshold portion here).
            tds_applies = True
            taxable_base = round(cum_after - threshold, 2)
        else:
            tds_applies = False
            taxable_base = 0.0

    tds_result = compute_tds(taxable_base, sec, overrides) if tds_applies else None

    return {
        "tds_applies": tds_applies,
        "taxable_base": taxable_base,
        "threshold": threshold,
        "cumulative_before": cum,
        "cumulative_after": cum_after,
        "tds_result": tds_result,
    }


def compute_tcs_206c1h(
    cumulative_received_fy: float,
    current_receipt: float,
) -> dict:
    """FIN-11: 206C(1H) - TCS on sale of goods.

    Applicable when the seller's prior-FY turnover > Rs 10 crore and the
    aggregate receipts from a single buyer exceed Rs 50 lakh in the current FY.
    Rate: 0.1% of the amount received above Rs 50 lakh.

    Args:
        cumulative_received_fy: Total already received from this buyer this FY
                                BEFORE this receipt.
        current_receipt:        The receipt amount (before TCS).

    Returns:
        tcs_applies  bool
        taxable_base float  - portion above Rs 50 lakh threshold.
        tcs_amount   float  - TCS to collect (0.1% of taxable_base).
        rate         float  - 0.1
        threshold    float  - TCS_206C1H_THRESHOLD (50 lakh).
    """
    cum = _f(cumulative_received_fy)
    rcpt = _f(current_receipt)
    cum_after = round(cum + rcpt, 2)

    if cum >= TCS_206C1H_THRESHOLD:
        taxable_base = rcpt
    elif cum_after > TCS_206C1H_THRESHOLD:
        taxable_base = round(cum_after - TCS_206C1H_THRESHOLD, 2)
    else:
        taxable_base = 0.0

    tcs_applies = taxable_base > 0
    tcs_amount = round(taxable_base * TCS_206C1H_RATE / 100.0, 2)

    return {
        "tcs_applies": tcs_applies,
        "taxable_base": taxable_base,
        "tcs_amount": tcs_amount,
        "rate": TCS_206C1H_RATE,
        "threshold": TCS_206C1H_THRESHOLD,
        "cumulative_before": cum,
        "cumulative_after": cum_after,
    }


def _fy_quarter(dt: datetime) -> tuple:
    """Return the Indian financial year (e.g. 2026) and quarter (1-4) for a
    datetime.  FY starts 1-Apr; Q1=Apr-Jun, Q2=Jul-Sep, Q3=Oct-Dec, Q4=Jan-Mar.
    """
    m = dt.month
    y = dt.year
    if m >= 4:
        fy = y
        q = (m - 4) // 3 + 1
    else:
        fy = y - 1
        q = 4 if m <= 3 else 3
    return fy, q


def build_26q_export(
    payments: List[dict],
) -> dict:
    """FIN-11: Build the data for a quarterly 26Q (TDS on non-salary payments)
    or 27EQ (TCS under 206C) return.

    26Q covers TDS deducted under sections 194C, 194J, 194Q, 194H, 194I etc.
    27EQ covers TCS collected under 206C.

    This function is deliberately PURE (no DB) -- the router fetches all
    vendor_payments for the relevant quarter and passes them here.

    Args:
        payments: List of vendor_payment dicts.  Each is expected to carry:
                  - payment_date (ISO string)
                  - vendor_id
                  - vendor_name
                  - vendor_pan  (optional; blank = 'PANNOTAVBL')
                  - tds_section
                  - tds_amount
                  - amount (cash paid, excluding TDS)

    Returns:
        {
          "form_26q": { <fy>: { <quarter>: [rows] } },
          "form_27eq": { <fy>: { <quarter>: [rows] } },
          "summary": { total_tds_26q, total_tcs_27eq, deductee_count }
        }
    """
    form_26q: dict = {}   # TDS
    form_27eq: dict = {}  # TCS 206C(1H) -- only if tds_section == "206C_1H"
    tds_total = 0.0
    tcs_total = 0.0
    deductees: set = set()

    for pmt in payments or []:
        if not isinstance(pmt, dict):
            continue
        pdate = parse_date(pmt.get("payment_date") or pmt.get("created_at"))
        if pdate is None:
            continue

        fy, q = _fy_quarter(pdate)
        section = (pmt.get("tds_section") or "NONE").strip().upper()
        tds_amt = _f(pmt.get("tds_amount"))
        cash_paid = _f(pmt.get("amount"))
        gross = round(cash_paid + tds_amt, 2)
        vendor_pan = (pmt.get("vendor_pan") or "PANNOTAVBL").strip().upper() or "PANNOTAVBL"
        vendor_name = pmt.get("vendor_name") or pmt.get("vendor_id") or ""
        vendor_id = pmt.get("vendor_id") or ""

        if section == "206C_1H":
            # TCS return (27EQ)
            target = form_27eq
            tcs_total += tds_amt
        else:
            # TDS return (26Q) -- only rows with actual TDS deducted
            if tds_amt <= 0 or section == "NONE":
                continue
            target = form_26q
            tds_total += tds_amt

        deductees.add(vendor_id)

        fy_key = f"{fy}-{(fy + 1) % 100:02d}"
        q_key = f"Q{q}"
        target.setdefault(fy_key, {}).setdefault(q_key, []).append(
            {
                "payment_date": pdate.date().isoformat(),
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "pan": vendor_pan,
                "section": section,
                "gross_payment": gross,
                "tds_tcs_amount": tds_amt,
                "net_paid": cash_paid,
                "remark": "",
            }
        )

    return {
        "form_26q": form_26q,
        "form_27eq": form_27eq,
        "summary": {
            "total_tds_26q": round(tds_total, 2),
            "total_tcs_27eq": round(tcs_total, 2),
            "deductee_count": len(deductees),
        },
    }


# --- per-bill outstanding --------------------------------------------------


def _payment_gross(p: dict) -> float:
    """Gross value a payment discharges off a bill = cash + TDS withheld."""
    return round(_f(p.get("amount")) + _f(p.get("tds_amount")), 2)


def bill_outstanding(
    bill: dict, payments: List[dict], debit_notes: List[dict]
) -> float:
    """Outstanding on a single bill = total - allocated payments - allocated
    debit-notes. Only rows whose bill_id matches this bill count. Never < 0."""
    if not isinstance(bill, dict):
        return 0.0
    bid = bill.get("bill_id")
    total = _f(bill.get("total_amount"))
    paid = sum(
        _payment_gross(p)
        for p in (payments or [])
        if isinstance(p, dict) and p.get("bill_id") == bid
    )
    dn = sum(
        _f(d.get("amount"))
        for d in (debit_notes or [])
        if isinstance(d, dict) and d.get("bill_id") == bid
    )
    return round(max(total - paid - dn, 0.0), 2)


# --- aging -----------------------------------------------------------------


def build_aging(
    bills: List[dict],
    payments: List[dict],
    debit_notes: List[dict],
    as_of_iso: Optional[str] = None,
) -> dict:
    """AP aging for one vendor (or any flat list of bills).

    Buckets each bill's OUTSTANDING amount by how far past its due date it is
    as of `as_of_iso` (default: today). On-account credits (payments / debit
    notes with no bill_id) cannot be aged against a bill, so they are summed
    into `unallocated_credits` and netted off at the end.
    """
    as_of = parse_date(as_of_iso) or now_ist_naive()
    buckets = {k: 0.0 for k in AGING_BUCKETS}
    items: List[dict] = []
    total_out = 0.0

    bill_ids = {b.get("bill_id") for b in (bills or []) if isinstance(b, dict)}

    for b in bills or []:
        if not isinstance(b, dict):
            continue
        out = bill_outstanding(b, payments, debit_notes)
        if out <= 0:
            continue
        due_iso = b.get("due_date") or compute_due_date(
            b.get("bill_date"), b.get("credit_days", 0)
        )
        due = parse_date(due_iso)
        # Bug fix: when both due_date and bill_date are absent/unparseable
        # the original code silently set days_past=0 and bucketed the bill as
        # "current". A bill with no usable date could be years overdue, so
        # putting it in "current" produces a falsely clean AP report. Instead
        # bucket it under "90_plus" (most conservative, prompts investigation)
        # and expose a sentinel days_past_due=-1 so callers can distinguish
        # "genuinely current" from "undatable".
        if due is None:
            days_past = -1
            bucket = "90_plus"
        else:
            days_past = (as_of - due).days
            bucket = aging_bucket(days_past)
        buckets[bucket] = round(buckets[bucket] + out, 2)
        total_out = round(total_out + out, 2)
        items.append(
            {
                "bill_id": b.get("bill_id"),
                "bill_number": b.get("bill_number"),
                "vendor_id": b.get("vendor_id"),
                "vendor_name": b.get("vendor_name"),
                "bill_date": b.get("bill_date"),
                "due_date": due_iso,
                "total_amount": _f(b.get("total_amount")),
                "outstanding": out,
                # -1 signals "undatable" to the caller; 0 means current
                "days_past_due": max(days_past, 0) if days_past >= 0 else -1,
                "bucket": bucket,
                "undatable": due is None,
            }
        )

    # Credits that are not tied to any bill present in this set (advances /
    # on-account payments / unallocated debit notes).
    unallocated = 0.0
    for p in payments or []:
        if isinstance(p, dict) and p.get("bill_id") not in bill_ids:
            unallocated += _payment_gross(p)
    for d in debit_notes or []:
        if isinstance(d, dict) and d.get("bill_id") not in bill_ids:
            unallocated += _f(d.get("amount"))
    unallocated = round(unallocated, 2)

    return {
        "as_of": as_of.date().isoformat(),
        "buckets": buckets,
        "total_outstanding": total_out,
        "unallocated_credits": unallocated,
        "net_payable": round(max(total_out - unallocated, 0.0), 2),
        # Sort: undatable bills (-1) sort first (they are the most uncertain and
        # need attention), then by days_past_due descending (most overdue first).
        "items": sorted(
            items,
            key=lambda x: (x["days_past_due"] >= 0, -x["days_past_due"]),
        ),
    }


def build_aging_by_vendor(
    bills: List[dict],
    payments: List[dict],
    debit_notes: List[dict],
    as_of_iso: Optional[str] = None,
) -> dict:
    """Org-wide AP aging grouped by vendor, plus a grand-total summary.

    Returns {as_of, totals:{buckets,total_outstanding,...}, vendors:[...]}.
    Each vendor row carries its own bucket split + outstanding.
    """
    by_vendor: dict = {}
    for b in bills or []:
        if isinstance(b, dict):
            by_vendor.setdefault(b.get("vendor_id"), {"bills": []})["bills"].append(b)

    vendor_rows: List[dict] = []
    totals = {k: 0.0 for k in AGING_BUCKETS}
    grand_out = 0.0
    grand_unalloc = 0.0

    for vendor_id, grp in by_vendor.items():
        v_payments = [
            p
            for p in (payments or [])
            if isinstance(p, dict) and p.get("vendor_id") == vendor_id
        ]
        v_dn = [
            d
            for d in (debit_notes or [])
            if isinstance(d, dict) and d.get("vendor_id") == vendor_id
        ]
        ag = build_aging(grp["bills"], v_payments, v_dn, as_of_iso)
        name = next(
            (b.get("vendor_name") for b in grp["bills"] if b.get("vendor_name")),
            vendor_id,
        )
        vendor_rows.append(
            {
                "vendor_id": vendor_id,
                "vendor_name": name,
                "buckets": ag["buckets"],
                "total_outstanding": ag["total_outstanding"],
                "unallocated_credits": ag["unallocated_credits"],
                "net_payable": ag["net_payable"],
            }
        )
        for k in AGING_BUCKETS:
            totals[k] = round(totals[k] + ag["buckets"][k], 2)
        grand_out = round(grand_out + ag["total_outstanding"], 2)
        grand_unalloc = round(grand_unalloc + ag["unallocated_credits"], 2)

    return {
        "as_of": (parse_date(as_of_iso) or now_ist_naive()).date().isoformat(),
        "totals": {
            "buckets": totals,
            "total_outstanding": grand_out,
            "unallocated_credits": grand_unalloc,
            "net_payable": round(max(grand_out - grand_unalloc, 0.0), 2),
        },
        "vendors": sorted(vendor_rows, key=lambda x: -x["net_payable"]),
    }


# --- ledger ----------------------------------------------------------------


def build_ledger(
    bills: List[dict],
    payments: List[dict],
    debit_notes: List[dict],
) -> dict:
    """Chronological vendor ledger with a running payable balance.

    Credit increases the payable (we owe more); debit reduces it. So a BILL is
    a credit, a PAYMENT (cash + TDS) and a DEBIT-NOTE are debits. Entries are
    sorted by date; the running `balance` is what we owe the vendor after each
    line.
    """
    rows: List[dict] = []

    for b in bills or []:
        if not isinstance(b, dict):
            continue
        rows.append(
            {
                "date": b.get("bill_date") or b.get("created_at"),
                "type": "BILL",
                "ref": b.get("bill_number") or b.get("bill_id"),
                "description": b.get("notes") or "Vendor bill",
                "debit": 0.0,
                "credit": _f(b.get("total_amount")),
            }
        )

    for p in payments or []:
        if not isinstance(p, dict):
            continue
        gross = _payment_gross(p)
        tds = _f(p.get("tds_amount"))
        mode = p.get("mode") or "PAYMENT"
        desc = f"Payment ({mode})"
        if tds > 0:
            desc += f" incl TDS Rs {tds:.2f}"
        rows.append(
            {
                "date": p.get("payment_date") or p.get("created_at"),
                "type": "PAYMENT",
                "ref": p.get("reference") or p.get("payment_id"),
                "description": desc,
                "debit": gross,
                "credit": 0.0,
            }
        )

    for d in debit_notes or []:
        if not isinstance(d, dict):
            continue
        rows.append(
            {
                "date": d.get("date") or d.get("created_at"),
                "type": "DEBIT_NOTE",
                "ref": d.get("debit_note_number") or d.get("debit_note_id"),
                "description": d.get("reason") or "Debit note",
                "debit": _f(d.get("amount")),
                "credit": 0.0,
            }
        )

    # Stable chronological sort; undated rows sink to the end.
    def _key(r):
        dt = parse_date(r.get("date"))
        return (dt is None, dt or datetime.max)

    rows.sort(key=_key)

    balance = 0.0
    for r in rows:
        balance = round(balance + r["credit"] - r["debit"], 2)
        r["balance"] = balance

    total_billed = round(sum(r["credit"] for r in rows if r["type"] == "BILL"), 2)
    total_paid = round(sum(r["debit"] for r in rows if r["type"] == "PAYMENT"), 2)
    total_tds = round(
        sum(_f(p.get("tds_amount")) for p in (payments or []) if isinstance(p, dict)), 2
    )
    total_dn = round(sum(r["debit"] for r in rows if r["type"] == "DEBIT_NOTE"), 2)

    return {
        "entries": rows,
        "closing_balance": balance,
        "total_billed": total_billed,
        "total_paid": total_paid,
        "total_tds": total_tds,
        "total_debit_notes": total_dn,
    }
