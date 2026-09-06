"""Bank statement CSV import, auto-matching and the statement reads.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

import csv
import io
import uuid
from datetime import datetime
from ...utils.ist import ist_today
from typing import Optional, List
from fastapi import Depends, HTTPException, Query, UploadFile, File, Form
from ..auth import get_current_user
from ._shared import _get_db, router

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
    current_user: dict = Depends(get_current_user),
):
    """FIND-5: Import a bank statement CSV and auto-match against recorded
    receipts and vendor payments.

    Accepts common Indian bank CSV layouts (HDFC / ICICI / SBI / Axis /
    Kotak). Returns a statement_id and the matched/unmatched rows so the
    accountant can review and confirm each match.

    The import is non-destructive: no existing records are modified until
    the accountant calls POST /finance/bank-statement/{id}/confirm.
    """
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
    effective_store = store_id or current_user.get("active_store_id") or ""

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
        try:
            # Order receipts (payments against orders): look in orders where
            # payment_status is PAID, using created_at within the statement window.
            receipts = list(
                db.get_collection("orders").find(
                    {
                        "payment_status": {
                            "$in": ["PAID", "paid", "PARTIAL", "partial"]
                        },
                        "created_at": {
                            "$gte": min_date,
                            "$lte": max_date + "T23:59:59",
                        },
                        **({"store_id": effective_store} if effective_store else {}),
                    },
                    {
                        "order_id": 1,
                        "grand_total": 1,
                        "total_amount": 1,
                        "created_at": 1,
                        "_id": 0,
                    },
                )
            )
        except Exception:
            pass
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
        except Exception:
            pass

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

    # Persist the import for later confirmation (best-effort; fail-soft).
    statement_id = str(uuid.uuid4())
    if db is not None:
        try:
            db.get_collection("bank_statements").insert_one(
                {
                    "statement_id": statement_id,
                    "store_id": effective_store,
                    "account_name": account_name or "",
                    "filename": file.filename,
                    "uploaded_by": current_user.get("id")
                    or current_user.get("user_id")
                    or "",
                    "uploaded_at": datetime.utcnow().isoformat(),
                    "row_count": len(matched_rows),
                    "summary": summary,
                    "rows": matched_rows,
                    "status": "PENDING_REVIEW",
                }
            )
        except Exception:
            pass  # non-fatal; data returned in response regardless

    return {
        "statement_id": statement_id,
        "summary": summary,
        "rows": matched_rows,
    }


@router.get("/bank-statement")
async def list_bank_statements(
    store_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """FIND-5: List previously imported bank statements."""
    db = _get_db()
    if db is None:
        return {"statements": []}
    effective_store = store_id or current_user.get("active_store_id") or ""
    flt = {"store_id": effective_store} if effective_store else {}
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
    except Exception:
        docs = []
    return {"statements": docs}


@router.get("/bank-statement/{statement_id}")
async def get_bank_statement(
    statement_id: str,
    current_user: dict = Depends(get_current_user),
):
    """FIND-5: Retrieve an imported bank statement with all rows and matches."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        doc = db.get_collection("bank_statements").find_one(
            {"statement_id": statement_id}, {"_id": 0}
        )
    except Exception:
        doc = None
    if not doc:
        raise HTTPException(status_code=404, detail="Statement not found")
    return doc
