"""IMS 2.0 - Purchase document numbering (per-store, per-financial-year serials).

Purchase orders, goods receipts (GRN) and purchase/vendor invoices each need a
human, consecutive, per-store, per-FY number -- the same discipline the GST sales
invoice already uses (see order_repository.next_invoice_number). This module is
the single source of that logic for the buy side.

Format: ``{PREFIX}/{STORE}/{FY}/{serial}`` e.g. ``PO/BOK-01/26-27/0001``.
  * PREFIX -- per doc-type: PO (purchase order), RCPT (goods receipt / GRN),
    PINV (purchase / vendor invoice).
  * STORE  -- the store's human code (the opaque store_id UUID never prints).
  * FY     -- Indian financial year (Apr-Mar) short label ``YY-YY``; serial
    RESETS each FY.
  * serial -- consecutive within (prefix, store, FY), zero-padded to 4.

Atomic per (prefix, store, FY): a single ``find_one_and_update`` with ``$inc``
on a shared ``counters`` doc keyed ``purchase:{prefix}:{store}:{fy_start}``.
Mongo serialises the increment so two users raising a PO at the same instant get
distinct serials -- no read-modify-write window, no duplicates.

Fail-soft: with no counters collection (DB-less / mock), falls back to a
time-derived suffix in the SAME format so the caller still gets a usable, unique
string rather than a 500. No emojis (Windows cp1252).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

# Reuse the canonical FY-start boundary used by GST invoice numbering so the
# buy side and the sell side share one financial-year definition.
from database.repositories.order_repository import fy_start_year

# Doc-type -> printed prefix. GRN and RECEIPT are aliases for the goods-receipt
# document (RCPT). Anything unknown falls back to its own upper-cased name.
_PREFIXES = {
    "PO": "PO",
    "GRN": "RCPT",
    "RECEIPT": "RCPT",
    "RCPT": "RCPT",
    "PINV": "PINV",
    "PURCHASE_INVOICE": "PINV",
}


def _fy_short(start_year: int) -> str:
    """Short Indian FY label, e.g. start 2026 -> '26-27'."""
    return f"{start_year % 100:02d}-{(start_year + 1) % 100:02d}"


def _store_segment(store_id: Optional[str], store_code: Optional[str]) -> str:
    """Human store segment for the number. Prefers the readable store_code;
    falls back to store_id, then 'HQ'. Sanitised so '/' can't break the format."""
    raw = store_code or store_id or "HQ"
    seg = "".join(c for c in str(raw).upper() if c.isalnum() or c in ("-", "_"))
    return seg or "HQ"


def doc_prefix(doc_type: str) -> str:
    """Resolve the printed prefix for a doc_type (PO / GRN / RECEIPT / PINV)."""
    return _PREFIXES.get(str(doc_type or "").strip().upper(), str(doc_type or "").strip().upper())


def format_purchase_number(prefix: str, store_seg: str, fy_label: str, seq: int) -> str:
    """Pure formatter: ``{prefix}/{store}/{fy}/{serial:04d}``."""
    return f"{prefix}/{store_seg}/{fy_label}/{int(seq):04d}"


def next_purchase_number(
    counters,
    *,
    doc_type: str,
    store_id: Optional[str] = None,
    store_code: Optional[str] = None,
    when: Optional[datetime] = None,
) -> str:
    """Allocate the next per-store, per-FY serial for a purchase document.

    Args:
        counters: the shared ``counters`` pymongo collection (or None -> fail-soft
            fallback). Callers typically pass ``db.get_collection("counters")``.
        doc_type: PO | GRN | RECEIPT | PINV (case-insensitive).
        store_id / store_code: the store the doc belongs to (store_code preferred
            for the printed segment).
        when: timestamp deciding the FY; defaults to now (IST).

    Returns a number string in the documented format. Never raises.
    """
    if when is None:
        try:
            from api.utils.ist import now_ist_naive

            when = now_ist_naive()
        except Exception:  # noqa: BLE001
            when = datetime.now()

    start = fy_start_year(when)
    fy = _fy_short(start)
    prefix = doc_prefix(doc_type)
    store_seg = _store_segment(store_id, store_code)

    if counters is not None:
        try:
            from pymongo import ReturnDocument

            key = f"purchase:{prefix}:{store_seg}:{start}"
            doc = counters.find_one_and_update(
                {"_id": key},
                {"$inc": {"seq": 1}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            seq = (doc or {}).get("seq")
            if isinstance(seq, int) and seq > 0:
                return format_purchase_number(prefix, store_seg, fy, seq)
        except Exception:  # noqa: BLE001 -- fall through to the safe fallback
            pass

    # Fail-soft (DB-less / counter error): time-derived suffix, same format.
    suffix = when.strftime("%m%d%H%M%S")
    return f"{prefix}/{store_seg}/{fy}/{suffix}"
