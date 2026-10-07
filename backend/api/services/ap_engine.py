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

from datetime import datetime, timedelta, timezone, date
from typing import List, Optional, Dict

# IST (TZ-P3): the as_of default must be the IST business day, not the UTC box
# clock (00:00-05:30 IST would otherwise age bills against YESTERDAY).
from api.utils.ist import now_ist_naive

_IST = timezone(timedelta(hours=5, minutes=30))

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

# The declared nature of a vendor bill. GOODS bills must link the goods receipt
# (owner ruling 15: the receipt, not the paperwork, settles a purchase of
# stock); SERVICES covers everything with no receipt to link -- freight, rent,
# job-work, an expense bill. ONE normaliser, used by BOTH create doors
# (vendors.create_vendor_bill and purchase_invoices.create_purchase_invoice),
# so the two cannot drift on what counts as a goods declaration.
BILL_KIND_GOODS = "GOODS"
BILL_KIND_SERVICES = "SERVICES"


def normalize_bill_kind(value):
    """None when absent/blank (a legacy client or stored row); the canonical
    'GOODS' / 'SERVICES' for a recognized declaration; ValueError otherwise
    (so a schema validator surfaces the allowed values instead of silently
    reading a typo as 'not goods')."""
    if value is None or str(value).strip() == "":
        return None
    s = str(value).strip().upper().replace("-", "_").replace(" ", "_")
    if s == BILL_KIND_GOODS:
        return BILL_KIND_GOODS
    if s in ("SERVICES", "SERVICE", "EXPENSE", "EXPENSES", "SERVICES_EXPENSES"):
        return BILL_KIND_SERVICES
    raise ValueError("bill_kind must be GOODS or SERVICES")


def _f(v) -> float:
    """Coerce anything to a float, defaulting to 0.0."""
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _naive_ist(dt: datetime) -> datetime:
    """An offset-aware instant as the naive IST wall clock; naive passes through.

    Bill and payment dates are naive 'YYYY-MM-DD' (IST calendar), but some
    writers stamp aware UTC strings (rebate_engine's credit note created_at).
    Mixing the two in one sort or comparison raises TypeError, so every
    parsed value leaves here naive, on the IST calendar the business uses.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(_IST).replace(tzinfo=None)


def parse_date(s) -> Optional[datetime]:
    """Tolerant ISO parse for 'YYYY-MM-DD' or full ISO datetimes. None on junk.

    Always naive (an aware value is converted to IST wall clock) so rows from
    different writers can be sorted and compared together."""
    if isinstance(s, datetime):
        return _naive_ist(s)
    if not s or not isinstance(s, str):
        return None
    txt = s.strip()
    if not txt:
        return None
    # Try full ISO first, then date-only.
    try:
        return _naive_ist(datetime.fromisoformat(txt.replace("Z", "+00:00")))
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(txt[:10])
    except ValueError:
        return None


# GST began on 1 July 2017: no GSTR-3B exists for an earlier month.
GST_START = date(2017, 7, 1)


def iso_bill_date(value) -> str:
    """THE bill-date rule of every bill door (the line-detail invoice and the
    Cash Flow '+ bill'): a real calendar date written YYYY-MM-DD, from the
    start of GST (GST_START) to today in IST, returned in that form. Every GST
    return places a bill in its month by comparing this string
    (reports.gst_itc._itc_month) and the period lock parses it, so a bill
    dated '' or '09/05/2026' was on no GSTR-3B and under no lock -- and one
    dated '0202-05-09' (a half-typed year) or '2062-05-09' only on a return
    nobody files. ValueError otherwise, for the schema validator to report."""
    txt = str(value or "").strip()
    d = None
    if len(txt) == 10 and txt[4] == txt[7] == "-" and (txt[:4] + txt[5:7] + txt[8:]).isdigit():
        try:
            d = date.fromisoformat(txt)
        except ValueError:
            pass
    if d is None:
        raise ValueError(
            "Bill date must be a real date written YYYY-MM-DD, as printed on "
            "the supplier's bill"
        )
    if not GST_START <= d <= now_ist_naive().date():
        raise ValueError(
            f"Bill date {txt} is not between 1 July 2017 (the start of GST) and "
            "today -- check the year as printed on the supplier's bill"
        )
    return d.isoformat()


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
    return round(max(_bill_balance(bill, payments, debit_notes), 0.0), 2)


def _bill_balance(bill: dict, payments: List[dict], debit_notes: List[dict]) -> float:
    """bill_outstanding before the floor: negative = money allocated to the
    bill beyond its total (an over-payment the ledger still counts)."""
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
    return round(total - paid - dn, 2)


# --- aging -----------------------------------------------------------------


def build_aging(
    bills: List[dict],
    payments: List[dict],
    debit_notes: List[dict],
    as_of_iso: Optional[str] = None,
) -> dict:
    """AP aging for one vendor (or any flat list of bills).

    Buckets what is still owed on each bill by how far past its due date it is
    as of `as_of_iso` (default: today).

    Money that names no bill in this set (an advance, an on-account payment, a
    debit note with no bill) and money paid on a bill beyond its total settle
    the SAME vendor's open bills, oldest due date first -- what an accountant
    does with on-account money (F56). So `items`, every bucket and
    `total_outstanding` are what is still owed after it, and an 'overdue' figure
    can never exceed what we owe that vendor. Credit left over once all of a
    vendor's bills are settled is that vendor's advance (paid ahead of its
    bills).

    THE ONE 'WE OWE' RULE (F56): per supplier, its ledger closing balance on
    the as-of day is either owed (> 0: its open bills after its own credit) or
    an advance (< 0: its credit beyond its bills) -- never both. So, over any
    set of suppliers:
      owed     = SUM of max(balance, 0) = total_outstanding = SUM(buckets)
      advances = SUM of max(-balance, 0) = unallocated_credits
    An advance is its own figure ('paid ahead to suppliers'). It is NEVER taken
    off owed: one supplier's advance does not settle another supplier's bills,
    so netting them (and flooring the difference at 0) hid what we owe.
    `net_payable` is kept for older readers and is owed, nothing else.
    """
    as_of = parse_date(as_of_iso) or now_ist_naive()
    items: List[dict] = []
    credit: Dict[object, float] = {}  # vendor_id -> money not yet set off a bill

    def _credit(vendor_id, amount: float) -> None:
        credit[vendor_id] = round(credit.get(vendor_id, 0.0) + amount, 2)

    bill_ids = {b.get("bill_id") for b in (bills or []) if isinstance(b, dict)}

    for b in bills or []:
        if not isinstance(b, dict):
            continue
        out = _bill_balance(b, payments, debit_notes)
        if out <= 0:
            _credit(b.get("vendor_id"), -out)  # over-paid: money with the supplier
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

    for p in payments or []:
        if isinstance(p, dict) and p.get("bill_id") not in bill_ids:
            _credit(p.get("vendor_id"), _payment_gross(p))
    for d in debit_notes or []:
        if isinstance(d, dict) and d.get("bill_id") not in bill_ids:
            _credit(d.get("vendor_id"), _f(d.get("amount")))

    # Each vendor's credit settles its own bills: undatable first (they bucket
    # as 90+), then by due date, oldest first.
    def _due_order(it):
        due = parse_date(it["due_date"])
        return (due is not None, due.date() if due else date.min)

    for it in sorted(items, key=_due_order):
        have = credit.get(it["vendor_id"], 0.0)
        if have > 0:
            used = min(have, it["outstanding"])
            it["outstanding"] = round(it["outstanding"] - used, 2)
            credit[it["vendor_id"]] = round(have - used, 2)
    items = [it for it in items if it["outstanding"] > 0]

    buckets = {k: 0.0 for k in AGING_BUCKETS}
    for it in items:
        buckets[it["bucket"]] = round(buckets[it["bucket"]] + it["outstanding"], 2)
    total_out = round(sum(it["outstanding"] for it in items), 2)
    unallocated = round(sum(credit.values()), 2)

    return {
        "as_of": as_of.date().isoformat(),
        "buckets": buckets,
        "total_outstanding": total_out,
        # THE 'we owe' figure and the money paid ahead, apart (see above).
        "owed": total_out,
        "advances": unallocated,
        # Older key, same figure as `advances`.
        "unallocated_credits": unallocated,
        # Older key, same figure as `owed` (no advance is subtracted).
        "net_payable": total_out,
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
    Each vendor row carries its own bucket split + outstanding, and its own
    signed ledger `balance` (owed - advances; below 0 = paid ahead). Every
    vendor in the rows is a row -- one holding only an advance too.

    The totals follow THE ONE 'WE OWE' RULE (build_aging): `owed` (= the
    buckets added up = total_outstanding) is the sum of each supplier's
    positive balance, and `advances` the sum of each supplier's money paid
    ahead, a figure apart. One supplier's advance is never taken off what we
    owe another (F56); `net_payable` (older key) is owed.
    """
    bills, payments, debit_notes = (
        [d for d in docs or [] if isinstance(d, dict)]
        for docs in (bills, payments, debit_notes)
    )
    vendor_ids = dict.fromkeys(d.get("vendor_id") for d in bills + payments + debit_notes)

    vendor_rows: List[dict] = []
    totals = {k: 0.0 for k in AGING_BUCKETS}
    grand_out = 0.0
    grand_unalloc = 0.0

    for vendor_id in vendor_ids:
        v_bills, v_payments, v_dn = (
            [d for d in docs if d.get("vendor_id") == vendor_id]
            for docs in (bills, payments, debit_notes)
        )
        ag = build_aging(v_bills, v_payments, v_dn, as_of_iso)
        name = next(
            (d.get("vendor_name") for d in v_bills + v_payments + v_dn if d.get("vendor_name")),
            vendor_id,
        )
        vendor_rows.append(
            {
                "vendor_id": vendor_id,
                "vendor_name": name,
                "buckets": ag["buckets"],
                "total_outstanding": ag["total_outstanding"],
                "owed": ag["owed"],
                "advances": ag["advances"],
                # This supplier's own ledger balance, signed (< 0 = advance).
                "balance": round(ag["owed"] - ag["advances"], 2),
                "unallocated_credits": ag["unallocated_credits"],
                "net_payable": ag["net_payable"],
            }
        )
        for k in AGING_BUCKETS:
            totals[k] = round(totals[k] + ag["buckets"][k], 2)
        grand_out = round(grand_out + ag["owed"], 2)
        grand_unalloc = round(grand_unalloc + ag["advances"], 2)

    return {
        "as_of": (parse_date(as_of_iso) or now_ist_naive()).date().isoformat(),
        "totals": {
            "buckets": totals,
            "total_outstanding": grand_out,
            "owed": grand_out,
            "advances": grand_unalloc,
            "unallocated_credits": grand_unalloc,
            # Older key: owed. No supplier's advance comes off it.
            "net_payable": grand_out,
        },
        "vendors": sorted(vendor_rows, key=lambda x: -x["net_payable"]),
    }


# --- whose rows: supplier bills only, and one shop's share -------------------


def _day(value) -> str:
    """'YYYY-MM-DD' of a stored date or instant ('' when undatable)."""
    dt = parse_date(value)
    return dt.date().isoformat() if dt else ""


def ledger_day(doc: dict) -> str:
    """'YYYY-MM-DD' a ledger row is entered on -- the date build_ledger sorts
    by: a bill's bill_date, a payment's payment_date, a note's date, else the
    row's created_at ('' when undatable)."""
    return _day(
        doc.get("bill_date") or doc.get("payment_date") or doc.get("date") or doc.get("created_at")
    )


def as_of_day(as_of_iso: Optional[str] = None) -> str:
    """THE as-of day every payable figure is struck on: the day asked for,
    never later than today (IST); today when none is asked. A row dated after
    it (a post-dated cheque, a bill keyed ahead) has not happened yet."""
    today = now_ist_naive().date().isoformat()
    asked = _day(as_of_iso) if as_of_iso else ""
    return min(asked, today) if asked else today


def is_post_dated(doc: dict, as_of_iso: Optional[str] = None) -> bool:
    """True for a row dated after the as-of day (default today): recorded, but
    not yet counted in any 'what we owe' figure. Undated rows always count."""
    return ledger_day(doc) > as_of_day(as_of_iso)


def supplier_rows(
    bills: List[dict],
    payments: List[dict],
    debit_notes: List[dict],
    store_id: Optional[str] = None,
) -> tuple:
    """Every RECORDED (bill, payment, debit note) of the supplier ledger, with
    no as-of cutoff -- what the payments / debit-notes lists and the ledger's
    post-dated list show. supplier_ledger_rows is these rows struck on a day.

    1. An inter-company transfer's mirror bill (source_transfer_id) is not a
       supplier purchase: one of our companies 'bills' another for frames the
       external supplier's bill already counts. It -- and any money naming it --
       is left out (owner ruling D13: such a move is a valued challan).
    2. `store_id` narrows the rows to one shop, and every row has exactly one
       shop, decided from ALL the supplier's bills whatever their date -- so a
       row's shop never depends on the day the figures are struck:
         * a bill: the shop its goods landed in (its store_id);
         * money naming a bill: that bill's shop (the money settles it, so the
           bill and its money are always in the same shop's share);
         * money naming none (an advance, an on-account payment, a debit note
           with no bill): the shop stamped on it when it was recorded
           (store_id; POST /vendors/{id}/payments and /debit-notes stamp it);
         * a legacy row with no stamp: the shop of the same supplier's latest
           bill dated on or before it, else of its earliest bill.
       So the shops add up to the supplier ledger. A supplier that has never
       billed and an unstamped row have no shop: such money counts under all
       stores only.
    """
    bills, payments, debit_notes = (
        [d for d in docs or [] if isinstance(d, dict)]
        for docs in (bills, payments, debit_notes)
    )
    mirror = {b.get("bill_id") for b in bills if b.get("source_transfer_id")}
    mirror.discard(None)  # a mirror bill with no id must not swallow on-account money
    bills = [b for b in bills if not b.get("source_transfer_id")]
    payments, debit_notes = (
        [d for d in docs if d.get("bill_id") not in mirror]
        for docs in (payments, debit_notes)
    )
    if not store_id:
        return bills, payments, debit_notes

    shop_of_bill = {b.get("bill_id"): b.get("store_id") for b in bills if b.get("bill_id")}
    billed: Dict[object, list] = {}
    for b in bills:
        billed.setdefault(b.get("vendor_id"), []).append((ledger_day(b), b.get("store_id") or ""))
    for history in billed.values():
        history.sort(key=lambda r: (r[0] == "", r))

    def _shop(money: dict) -> Optional[str]:
        if money.get("bill_id") in shop_of_bill:
            return shop_of_bill[money["bill_id"]]
        if money.get("store_id"):
            return money["store_id"]
        dated = billed.get(money.get("vendor_id"))
        if not dated:
            return None
        day = ledger_day(money)
        before = [r for r in dated if r[0] and day and r[0] <= day]
        return (before[-1] if before else dated[0])[1]

    return (
        [b for b in bills if b.get("store_id") == store_id],
        [p for p in payments if _shop(p) == store_id],
        [d for d in debit_notes if _shop(d) == store_id],
    )


def split_as_of(rows: tuple, as_of: Optional[str] = None) -> tuple:
    """((bills, payments, notes) counted on the as-of day, (bills, payments,
    notes) dated after it). Each row by its own ledger_day; undated rows count."""
    cutoff = as_of_day(as_of)
    counted = tuple([d for d in docs if ledger_day(d) <= cutoff] for docs in rows)
    later = tuple([d for d in docs if ledger_day(d) > cutoff] for docs in rows)
    return counted, later


def supplier_ledger_rows(
    bills: List[dict],
    payments: List[dict],
    debit_notes: List[dict],
    store_id: Optional[str] = None,
    as_of: Optional[str] = None,
) -> tuple:
    """THE (bills, payments, debit notes) every 'what we owe our suppliers'
    figure is built from (F56/F63): supplier_rows (no transfer mirror bills;
    with `store_id` one shop's share), struck on the as-of day -- only rows
    dated on or before as_of_day (the day asked for, clamped to today) count;
    undated rows always count. So the Purchases report, Cash Flow, AP aging,
    the Suppliers card and the vendor ledger strike 'we owe' on the same day.

    The cutoff applies to each row by its OWN date, after every row's shop is
    known: a payment naming a bill keyed ahead is still that bill's shop's
    money, never the shop of whatever earlier bill happens to be in range.
    """
    return split_as_of(supplier_rows(bills, payments, debit_notes, store_id), as_of)[0]


def post_dated_entries(
    bills: List[dict],
    payments: List[dict],
    debit_notes: List[dict],
) -> List[dict]:
    """The rows dated after the as-of day as ledger entries (build_ledger's
    shape, chronological) with no running balance: recorded, so the ledger
    shows them, but not counted in the balance until their day."""
    entries = build_ledger(bills, payments, debit_notes)["entries"]
    return [{k: v for k, v in e.items() if k != "balance"} for e in entries]


def bill_as_of(
    bill: dict,
    payments: List[dict],
    debit_notes: List[dict],
    as_of: Optional[str] = None,
) -> dict:
    """One bill's figures on the ledger's as-of rule (default today).

    outstanding     -- what is owed on it on the as-of day: its total less the
                       money naming it dated on or before that day (floored at
                       0) -- the figure the supplier ledger and AP aging count.
    post_dated_money-- money naming it dated after that day (a post-dated
                       cheque): recorded, not yet counted.
    post_dated_until-- the latest such day, else None.
    post_dated      -- the bill itself is dated after that day (keyed ahead).
    """
    bid = bill.get("bill_id")
    mine = tuple(
        [d for d in docs or [] if isinstance(d, dict) and d.get("bill_id") == bid]
        for docs in (payments, debit_notes)
    )
    (pays, notes), (later_pays, later_notes) = split_as_of(mine, as_of)
    later = later_pays + later_notes
    return {
        "outstanding": bill_outstanding(bill, pays, notes),
        "post_dated_money": round(
            sum(_payment_gross(p) for p in later_pays) + sum(_f(d.get("amount")) for d in later_notes),
            2,
        ),
        "post_dated_until": max((ledger_day(d) for d in later), default=None),
        "post_dated": is_post_dated(bill, as_of),
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
        # Credit-note category (RETURN_CN / SCHEME_CN / DISCOUNT_CN / QUALITY_CN
        # for manual notes; VOLUME_REBATE for machine-posted rebate CNs). Surface
        # it on the ledger row so the running-balance reduction is explained.
        cn_type = d.get("cn_type") or d.get("source")
        rows.append(
            {
                "date": d.get("date") or d.get("created_at"),
                "type": "DEBIT_NOTE",
                "cn_type": cn_type,
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
