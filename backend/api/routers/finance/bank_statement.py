"""Bank statement CSV import, auto-matching and the statement reads.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from api.utils.event_time import utc_now_iso

import csv
import hashlib
import io
import uuid
from datetime import datetime
from ...utils.ist import ist_today
from typing import Optional, List
from fastapi import Depends, HTTPException, Query, UploadFile, File, Form
from ..auth import get_current_user
from ...dependencies import in_reach_or_404, store_in_view
from ._shared import _get_db, _require_org_finance, router

# ============================================================================
# FIND-5: Bank statement import + auto-reconciliation
# ============================================================================


# Supported CSV column aliases (case-insensitive) for each canonical field.
_BS_DATE_COLS = {
    "date",
    "txn date",
    "transaction date",
    "value date",
    "posting date",
    "value dt",
}
_BS_DESC_COLS = {"description", "narration", "particulars", "details", "remarks"}
_BS_DEBIT_COLS = {
    "debit",
    "withdrawal",
    "dr",
    "amount (dr)",
    "withdrawal amt.",
    "withdrawal amt",
    "debit amount",
}
_BS_CREDIT_COLS = {
    "credit",
    "deposit",
    "cr",
    "amount (cr)",
    "deposit amt.",
    "deposit amt",
    "credit amount",
}
_BS_AMOUNT_COLS = {"amount"}  # single column with sign or +/- indicator
_BS_BALANCE_COLS = {"balance", "closing balance", "running balance", "closing balance"}


def _parse_bank_csv(content: str) -> List[dict]:
    """Parse a bank statement CSV into a canonical list of transaction dicts.

    Handles common Indian bank statement CSV layouts:
      - Separate Debit/Credit columns (HDFC, ICICI, Axis, Kotak)
      - Single Amount column with +/- prefix (SBI, PNB)
      - Various date formats (DD/MM/YYYY, YYYY-MM-DD, DD-MMM-YYYY)

    Returns rows with keys: date (ISO), description, debit, credit, balance.
    Rows with no parseable amount are skipped.
    """

    def _norm(s: str) -> str:
        return s.strip().lower()

    def _parse_amount(s: str) -> float:
        if not s:
            return 0.0
        s = s.replace(",", "").strip()
        # Handle Dr/Cr suffix or prefix
        neg = s.endswith("(Dr)") or s.endswith("Dr") or s.endswith("DR")
        s = (
            s.replace("(Dr)", "")
            .replace("(Cr)", "")
            .replace("Dr", "")
            .replace("Cr", "")
            .replace("DR", "")
            .replace("CR", "")
            .strip()
        )
        try:
            val = float(s)
        except ValueError:
            return 0.0
        return -val if neg else val

    def _parse_date(s: str) -> Optional[str]:
        s = s.strip()
        for fmt in (
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%Y-%m-%d",
            "%d/%m/%y",
            "%d-%b-%Y",
            "%d %b %Y",
            "%Y/%m/%d",
        ):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                continue
        return None

    reader = csv.DictReader(io.StringIO(content))
    headers = {_norm(h): h for h in (reader.fieldnames or [])}

    date_col = next((headers[h] for h in headers if h in _BS_DATE_COLS), None)
    desc_col = next((headers[h] for h in headers if h in _BS_DESC_COLS), None)
    debit_col = next((headers[h] for h in headers if h in _BS_DEBIT_COLS), None)
    credit_col = next((headers[h] for h in headers if h in _BS_CREDIT_COLS), None)
    amount_col = next((headers[h] for h in headers if h in _BS_AMOUNT_COLS), None)
    balance_col = next((headers[h] for h in headers if h in _BS_BALANCE_COLS), None)

    rows = []
    for row in reader:
        date_str = _parse_date(row.get(date_col, "")) if date_col else None
        if not date_str:
            continue

        desc = (row.get(desc_col, "") or "").strip() if desc_col else ""

        if debit_col and credit_col:
            debit = abs(_parse_amount(row.get(debit_col, "")))
            credit = abs(_parse_amount(row.get(credit_col, "")))
        elif amount_col:
            amt = _parse_amount(row.get(amount_col, ""))
            debit = abs(amt) if amt < 0 else 0.0
            credit = amt if amt > 0 else 0.0
        else:
            continue

        balance_raw = _parse_amount(row.get(balance_col, "")) if balance_col else None

        rows.append(
            {
                "date": date_str,
                "description": desc,
                "debit": round(debit, 2),
                "credit": round(credit, 2),
                "balance": round(balance_raw, 2) if balance_raw is not None else None,
            }
        )

    return rows


# Order payment tenders that land in the bank as their own credit (cash goes
# to the drawer; store credit, vouchers, loyalty and pay-later are not money
# received; a marketplace payout settles outside IMS in one lump).
_BANK_TENDERS = ("UPI", "CARD", "BANK_TRANSFER")
_DEAD_LEG_STATUSES = ("VOID", "VOIDED", "REVERSED", "FAILED", "REFUNDED", "CANCELLED")
_LEG_DATE_FIELDS = ("received_at", "recorded_at", "paid_at", "created_at", "payment_date")


_BOOKS_UNREADABLE = "The books could not be read to match this statement. Nothing was saved; upload it again."
_STATEMENTS_UNREADABLE = "Bank statements could not be read right now. Try again."
_NOT_SAVED = "The statement was not saved. Nothing was imported; upload it again."
_SAVE_UNKNOWN = (
    "Could not confirm the statement was saved. Check the imported statements "
    "list before uploading it again."
)
_STATEMENT_NS = uuid.UUID("6f0f3b0e-2f0a-5d1c-9a53-1423b11b11b1")


def _statement_answer(doc: dict) -> dict:
    """What the import answers with: the SAVED statement, nothing else."""
    return {
        "statement_id": doc.get("statement_id"),
        "summary": doc.get("summary"),
        "rows": doc.get("rows"),
    }


def _saved_statement(db, statement_id: str) -> Optional[dict]:
    """The stored statement of this id, or None. A read that fails is a 503
    (_SAVE_UNKNOWN), never "not saved": the caller cannot tell."""
    try:
        return db.get_collection("bank_statements").find_one({"_id": statement_id})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=_SAVE_UNKNOWN) from exc


def _replayed_statement(db, statement_id: str, sent: dict, current_user: dict) -> Optional[dict]:
    """The statement this request_id already saved, answered again -- only to
    a caller who can see it and only when it is the same upload (file, shop,
    account). The same id with anything else is a 409, never the earlier
    statement passed off as this one (the rule of the supplier-payment door,
    ap_payments._replayed_payment). None when the id saved nothing."""
    row = _saved_statement(db, statement_id)
    if row is None:
        return None
    in_reach_or_404(row, current_user, "Statement not found")
    if any((row.get(k) or "") != (v or "") for k, v in sent.items()):
        raise HTTPException(
            status_code=409,
            detail="This upload was already saved with a different file, shop or account - refresh.",
        )
    return _statement_answer(row)


def bank_receipt_legs(db, min_date: str, max_date: str, store_id: Optional[str]) -> List[dict]:
    """The money an order actually RECEIVED into the bank, one row per
    payment leg (audit L3-22): ``{receipt_id, order_id, amount, receipt_date,
    reference}``, the leg's own amount on the leg's own IST day.

    The matcher used to read whole orders: their grand_total (so a part-paid
    Rs 1,000 invoice "matched" a Rs 1,000 credit that was never received while
    the Rs 100 really received matched nothing) and their created_at compared
    as a STRING (so every current BSON-dated order was skipped). The window is
    widened by the matcher's 3-day tolerance on both sides."""
    from datetime import timedelta
    from ...services.tender_routing import canonicalize_tender
    from ...utils.event_time import parse_event_utc_naive
    from ...utils.ist import ist_date_str, ist_day_start_utc

    lo_day = (datetime.fromisoformat(min_date) - timedelta(days=3)).date()
    hi_day = (datetime.fromisoformat(max_date) + timedelta(days=4)).date()
    lo_dt, hi_dt = ist_day_start_utc(lo_day), ist_day_start_utc(hi_day)
    lo_s, hi_s = (lo_dt - timedelta(days=1)).date().isoformat(), (hi_dt + timedelta(days=1)).date().isoformat()
    in_window = [
        {f: {"$gte": v_lo, "$lt": v_hi}}
        for f in _LEG_DATE_FIELDS
        for v_lo, v_hi in ((lo_dt, hi_dt), (lo_s, hi_s))
    ]
    query = {"payments": {"$elemMatch": {"$or": in_window}}}
    if store_id:
        query["store_id"] = store_id
    out: List[dict] = []
    for o in db.get_collection("orders").find(
        query, {"_id": 0, "order_id": 1, "order_number": 1, "payments": 1}
    ):
        for i, leg in enumerate(o.get("payments") or []):
            if not isinstance(leg, dict):
                continue
            if canonicalize_tender(leg.get("method"), leg.get("mode")) not in _BANK_TENDERS:
                continue
            if str(leg.get("status") or "").upper() in _DEAD_LEG_STATUSES or leg.get("settled_outside_ims"):
                continue
            when = next(
                (parse_event_utc_naive(leg.get(f)) for f in _LEG_DATE_FIELDS if leg.get(f)), None
            )
            if when is None or not (lo_dt <= when < hi_dt):
                continue
            try:
                amount = round(float(leg.get("amount") or 0), 2)
            except (TypeError, ValueError):
                continue
            if amount <= 0:
                continue
            out.append({
                "receipt_id": leg.get("payment_id") or f"{o.get('order_id')}:{i}",
                "order_id": o.get("order_id"),
                "amount": amount,
                "receipt_date": ist_date_str(when),
                "reference": leg.get("reference") or o.get("order_number") or o.get("order_id") or "",
            })
    return out


def _auto_match_statement(
    statement_rows: List[dict],
    receipts: List[dict],
    payments: List[dict],
    *,
    amount_tolerance: float = 1.0,
    date_window_days: int = 3,
) -> List[dict]:
    """Auto-match statement rows against recorded receipts/payments.

    A match is accepted when:
      - The statement debit is within Rs 1 of a payment amount, OR
        the statement credit is within Rs 1 of a receipt amount,
      AND
      - The statement date is within 3 days of the recorded date.

    Returns an enriched list of statement rows with a `match` field
    (None or the matched record) and `match_type` ("RECEIPT", "PAYMENT",
    "UNMATCHED").
    """
    from ...services.ap_engine import _f, parse_date

    def _amt_close(a: float, b: float) -> bool:
        return abs(a - b) <= amount_tolerance

    def _dt_close(d1: Optional[str], d2: Optional[str]) -> bool:
        if not d1 or not d2:
            return False
        try:
            dd1 = datetime.fromisoformat(d1[:10])
            dd2 = datetime.fromisoformat(d2[:10])
            return abs((dd1 - dd2).days) <= date_window_days
        except (ValueError, TypeError):
            return False

    results = []
    used_receipt_ids: set = set()
    used_payment_ids: set = set()

    for row in statement_rows:
        matched = None
        match_type = "UNMATCHED"

        if row["credit"] > 0:
            # Credit in bank = money received = a receipt (order payment)
            for rec in receipts:
                rid = (
                    rec.get("receipt_id")
                    or rec.get("order_id")
                    or rec.get("_id")
                    or id(rec)
                )
                if rid in used_receipt_ids:
                    continue
                # Orders use grand_total; receipt docs use amount / total_amount.
                rec_amt = _f(
                    rec.get("grand_total")
                    or rec.get("amount")
                    or rec.get("total_amount")
                )
                rec_date = (
                    rec.get("receipt_date")
                    or rec.get("payment_date")
                    or rec.get("created_at")
                    or ""
                )[:10]
                if _amt_close(row["credit"], rec_amt) and _dt_close(
                    row["date"], rec_date
                ):
                    matched = {
                        "id": str(rid),
                        "type": "receipt",
                        "amount": rec_amt,
                        "date": rec_date,
                        "reference": rec.get("reference") or rec.get("order_id") or "",
                    }
                    used_receipt_ids.add(rid)
                    match_type = "RECEIPT"
                    break

        elif row["debit"] > 0:
            # Debit in bank = money paid = a payment
            for pmt in payments:
                pid = pmt.get("payment_id") or pmt.get("_id") or id(pmt)
                if pid in used_payment_ids:
                    continue
                pmt_amt = _f(pmt.get("amount") or pmt.get("total_amount"))
                pmt_date = (pmt.get("payment_date") or pmt.get("created_at") or "")[:10]
                if _amt_close(row["debit"], pmt_amt) and _dt_close(
                    row["date"], pmt_date
                ):
                    matched = {
                        "id": str(pid),
                        "type": "payment",
                        "amount": pmt_amt,
                        "date": pmt_date,
                        "reference": pmt.get("reference") or pmt.get("vendor_id") or "",
                    }
                    used_payment_ids.add(pid)
                    match_type = "PAYMENT"
                    break

        results.append({**row, "match": matched, "match_type": match_type})

    return results


@router.post("/bank-statement/import")
async def import_bank_statement(
    file: UploadFile = File(..., description="Bank statement CSV file"),
    store_id: Optional[str] = Form(None),
    account_name: Optional[str] = Form(None, description="Bank account name/label"),
    request_id: Optional[str] = Form(
        None, max_length=100,
        description="The client's own id for this upload: a retry with it never saves twice",
    ),
    current_user: dict = Depends(get_current_user),
):
    """FIND-5: Import a bank statement CSV and auto-match against recorded
    receipts and vendor payments.

    Accepts common Indian bank CSV layouts (HDFC / ICICI / SBI / Axis /
    Kotak). Returns a statement_id and the matched/unmatched rows so the
    accountant can review and confirm each match.

    The import is non-destructive: no existing records are modified until
    the accountant calls POST /finance/bank-statement/{id}/confirm.

    ADMIN / ACCOUNTANT only (the rbac row), not the finance router's manager set.
    The statement is the shop's it is imported at: a named shop must be the
    caller's, else his own -- an accountant's shop only (owner 2026-10-18), an
    admin's top-bar shop.

    The answer is a SAVED statement or an error (PR #1423 review R2-B): a save
    that did not land is a 503, never a 200 carrying an id nothing is stored
    under. ``request_id`` (optional) makes a retry safe: the same id with the
    same file, shop and account answers with the statement already saved; with
    anything else it is a 409.
    """
    _require_org_finance(current_user, store_id)
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="Only CSV files are accepted")

    content_bytes = await file.read()
    try:
        content = content_bytes.decode("utf-8-sig")  # strip BOM if present
    except UnicodeDecodeError:
        try:
            content = content_bytes.decode("latin-1")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=422,
                detail="Could not decode CSV file. Use UTF-8 or Latin-1 encoding.",
            )

    rows = _parse_bank_csv(content)
    if not rows:
        raise HTTPException(
            status_code=422,
            detail="No parseable transactions found in the CSV. Check column names: Date, Description, Debit/Credit (or Amount), Balance.",
        )

    db = _get_db()
    if db is None:
        # Nowhere to save: never a 200 with an id nothing is stored under.
        raise HTTPException(status_code=503, detail="Database unavailable")
    effective_store = store_in_view(store_id, current_user, writing=True) or ""

    # One statement per (shop, request_id): its own _id, so a retry (or the
    # same request racing itself) can save only once -- Mongo's _id index
    # always exists, no index of ours to be missing. The shop is part of the
    # key (decision D132): another shop's client that happens to send the same
    # request_id saves its own statement and learns nothing of this one.
    key = request_id.strip() if isinstance(request_id, str) else ""
    statement_id = (str(uuid.uuid5(_STATEMENT_NS, f"{effective_store}{key}")) if key
                    else str(uuid.uuid4()))
    sent = {
        "file_sha256": hashlib.sha256(content_bytes).hexdigest(),
        "store_id": effective_store,
        "account_name": account_name or "",
    }
    if key:
        done = _replayed_statement(db, statement_id, sent, current_user)
        if done is not None:
            return done

    # Fetch recorded receipts and vendor payments for the date range in the statement
    dates = [r["date"] for r in rows if r.get("date")]
    if dates:
        min_date = min(dates)
        max_date = max(dates)
    else:
        min_date = max_date = ist_today().isoformat()

    receipts: List[dict] = []
    payments: List[dict] = []
    if db is not None:
        # An unreadable book is a 503, never every bank line UNMATCHED saved
        # for review as if nothing in the books matched (PR #1423 review).
        try:
            receipts = bank_receipt_legs(db, min_date, max_date, effective_store)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=_BOOKS_UNREADABLE) from exc
        try:
            payments = list(
                db.get_collection("vendor_payments").find(
                    {
                        "payment_date": {"$gte": min_date, "$lte": max_date},
                        **({"store_id": effective_store} if effective_store else {}),
                    },
                    {
                        "payment_id": 1,
                        "amount": 1,
                        "payment_date": 1,
                        "vendor_id": 1,
                        "_id": 0,
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=_BOOKS_UNREADABLE) from exc

    matched_rows = _auto_match_statement(rows, receipts, payments)

    summary = {
        "total": len(matched_rows),
        "matched_receipts": sum(
            1 for r in matched_rows if r["match_type"] == "RECEIPT"
        ),
        "matched_payments": sum(
            1 for r in matched_rows if r["match_type"] == "PAYMENT"
        ),
        "unmatched": sum(1 for r in matched_rows if r["match_type"] == "UNMATCHED"),
        "total_credits": round(sum(r["credit"] for r in matched_rows), 2),
        "total_debits": round(sum(r["debit"] for r in matched_rows), 2),
    }

    # Persist the import for later confirmation. The answer is the SAVED
    # statement: a save that did not land is never a 200.
    doc = {
        "_id": statement_id,
        "statement_id": statement_id,
        **sent,
        "filename": file.filename,
        "uploaded_by": current_user.get("id")
        or current_user.get("user_id")
        or "",
        "uploaded_at": utc_now_iso(),
        "row_count": len(matched_rows),
        "summary": summary,
        "rows": matched_rows,
        "status": "PENDING_REVIEW",
        **({"request_id": key} if key else {}),
    }
    try:
        db.get_collection("bank_statements").insert_one(dict(doc))
    except Exception as exc:  # noqa: BLE001
        # Refused, or it landed and the reply was lost, or (with a request_id)
        # the same request racing itself saved it first: read what is stored.
        if key:
            done = _replayed_statement(db, statement_id, sent, current_user)
            if done is not None:
                return done
        elif _saved_statement(db, statement_id) is not None:
            return _statement_answer(doc)
        raise HTTPException(status_code=503, detail=_NOT_SAVED) from exc

    return _statement_answer(doc)


@router.get("/bank-statement")
async def list_bank_statements(
    store_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """FIND-5: List previously imported bank statements. ADMIN / ACCOUNTANT:
    an accountant his own shop's only (owner 2026-10-18), an admin every
    shop's or the one he names."""
    shop = _require_org_finance(current_user, store_id)
    db = _get_db()
    if db is None:
        # No answer is never "no statements imported".
        raise HTTPException(status_code=503, detail=_STATEMENTS_UNREADABLE)
    flt = {"store_id": shop} if shop else {}
    try:
        docs = list(
            db.get_collection("bank_statements").find(
                flt,
                {
                    "statement_id": 1,
                    "account_name": 1,
                    "filename": 1,
                    "uploaded_at": 1,
                    "row_count": 1,
                    "summary": 1,
                    "status": 1,
                    "_id": 0,
                },
                sort=[("uploaded_at", -1)],
                limit=limit,
            )
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=_STATEMENTS_UNREADABLE) from exc
    return {"statements": docs}


@router.get("/bank-statement/{statement_id}")
async def get_bank_statement(
    statement_id: str,
    current_user: dict = Depends(get_current_user),
):
    """FIND-5: Retrieve an imported bank statement with all rows and matches.
    ADMIN / ACCOUNTANT."""
    _require_org_finance(current_user)
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        doc = db.get_collection("bank_statements").find_one(
            {"statement_id": statement_id}, {"_id": 0}
        )
    except Exception as exc:  # noqa: BLE001
        # No answer is never "Statement not found".
        raise HTTPException(status_code=503, detail=_STATEMENTS_UNREADABLE) from exc
    # Another shop's statement reads as one that does not exist.
    return in_reach_or_404(doc, current_user, "Statement not found")
