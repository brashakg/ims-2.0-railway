"""B2B invoices to Tally: worklist, XML export, status marks, sales JV.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from datetime import datetime
from ...utils.ist import now_ist_naive, ist_date_str, ist_today
from typing import Optional, List
from fastapi import Depends, HTTPException, Query, Body
from fastapi.responses import Response
from pydantic import BaseModel, Field
from ..auth import get_current_user
from ._shared import (
    _REAL_ORDER_STATUS_FILTER,
    _apply_created_at_range,
    _customer_state_map,
    _get_db,
    _order_is_interstate,
    _require_finance_admin,
    _store_maps,
    _store_state_map,
    logger,
    router,
)
from .gst_crosscheck import _jv_cgst_sgst_split

# ===========================================================================
# B2B invoices -> Tally (accountant console + worklist)
# ===========================================================================
# Owner decision (2026-06-17): GST e-invoice (IRN) + e-way bill are NOT
# generated in IMS -- they are issued in Tally. So the accountant needs to
# (1) pull every B2B sales invoice as Tally-importable XML and (2) keep a
# reminder worklist of which B2B invoices still need handling in Tally.
#
# Identity: an order is a B2B invoice when its CUSTOMER is B2B -- i.e.
# customer_type == "B2B" OR the customer carries a non-empty GSTIN. Walk-in /
# B2C retail sales are excluded.
#
# tally_status lifecycle (stored on the order doc, additive):
#   PENDING   -- default, not yet handled in Tally (the reminder backlog)
#   IN_TALLY  -- exported to Tally XML (optional auto-advance on bulk export)
#   DONE      -- accountant confirmed the invoice + e-invoice/e-way exist in
#                Tally (terminal; stamps done_at / done_by)
#
# needs_eway is DERIVED (never trusted from input): an e-way bill is generally
# required for an inter-state movement OR when the invoice value is >= the
# Rs 50,000 threshold. We derive it fail-soft from the per-invoice GST split
# (inter-state flag) + grand_total; a missing field never raises.

# E-way bill consignment-value threshold (Rs). Inter-state OR value at/above
# this generally needs an e-way bill (Rule 138). Derived hint only -- Tally is
# the system of record; the accountant confirms.
_EWAY_VALUE_THRESHOLD = 50000.0

# How long (days) a B2B invoice may sit PENDING before the worklist flags it as
# an overdue reminder. Small by design -- the accountant should clear the
# Tally backlog promptly so e-invoice/e-way are not missed.
_B2B_PENDING_REMINDER_DAYS = 3

_VALID_TALLY_STATUS = {"PENDING", "IN_TALLY", "DONE"}


def _b2b_customer_map(db) -> dict:
    """customer_id -> {gstin, customer_type, state, name} for B2B classification.

    Fail-soft: any DB error yields an empty map (callers then treat every order
    as non-B2B, returning an empty list rather than a 500)."""
    out: dict = {}
    try:
        for c in db.get_collection("customers").find(
            {},
            {
                "_id": 0,
                "customer_id": 1,
                "gstin": 1,
                "customer_type": 1,
                "state": 1,
                "name": 1,
            },
        ):
            cid = c.get("customer_id")
            if cid:
                out[cid] = {
                    "gstin": str(c.get("gstin") or "").strip(),
                    "customer_type": str(c.get("customer_type") or "").strip().upper(),
                    "state": str(c.get("state") or ""),
                    "name": c.get("name") or "",
                }
    except Exception:  # noqa: BLE001
        pass
    return out


def _is_b2b_customer(cust: Optional[dict]) -> bool:
    """A customer is B2B when customer_type == 'B2B' OR a non-empty GSTIN is
    present (an order placed against a GSTIN-bearing party is a B2B invoice)."""
    if not isinstance(cust, dict):
        return False
    if cust.get("customer_type") == "B2B":
        return True
    return bool(str(cust.get("gstin") or "").strip())


def _days_since(when, now: datetime) -> Optional[int]:
    """Whole days between a stored created_at (BSON datetime or ISO string) and
    now. None when the value is missing/unparseable (never raises)."""
    if when is None:
        return None
    dt = None
    if isinstance(when, datetime):
        dt = when
    else:
        try:
            dt = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    try:
        delta = now - dt
    except (TypeError, ValueError):
        return None
    return max(0, delta.days)


def _b2b_invoice_row(o: dict, cust: dict, split: dict, now: datetime) -> dict:
    """Build one B2B invoice list/worklist row from an order doc + its customer
    + the precomputed GST split. Pure (no I/O)."""
    totals = split.get("totals") or {}
    taxable = round(float(totals.get("taxable") or 0.0), 2)
    cgst = round(float(totals.get("cgst") or 0.0), 2)
    sgst = round(float(totals.get("sgst") or 0.0), 2)
    igst = round(float(totals.get("igst") or 0.0), 2)
    grand = round(float(o.get("grand_total") or o.get("total") or 0.0), 2)
    interstate = bool(split.get("interstate"))

    # needs_eway (derived, fail-soft): inter-state movement OR consignment value
    # at/above the Rs 50,000 threshold generally requires an e-way bill.
    needs_eway = bool(interstate or grand >= _EWAY_VALUE_THRESHOLD)

    status = str(o.get("tally_status") or "PENDING").upper()
    if status not in _VALID_TALLY_STATUS:
        status = "PENDING"

    age_days = _days_since(o.get("created_at"), now)
    # Only PENDING invoices accrue a reminder; once IN_TALLY/DONE it is handled.
    overdue = bool(
        status == "PENDING"
        and age_days is not None
        and age_days >= _B2B_PENDING_REMINDER_DAYS
    )

    # Display number: the stamped GST invoice_number if present, else the
    # order_number (the export list must NOT mint a new invoice serial -- that
    # has accounting-sequence side effects and is owned by the invoice route).
    invoice_number = (
        o.get("invoice_number") or o.get("order_number") or o.get("order_id")
    )

    return {
        "order_id": o.get("order_id"),
        "invoice_number": invoice_number,
        "has_invoice_number": bool(o.get("invoice_number")),
        # BUG-104: the invoice date an accountant reads off the GST / Tally
        # reconciliation screen. IST business day, not the UTC box clock.
        "date": ist_date_str(o.get("created_at")),
        "store_id": o.get("store_id"),
        "customer_id": o.get("customer_id"),
        "customer_name": o.get("customer_name") or cust.get("name") or "",
        "customer_gstin": cust.get("gstin") or split.get("customer_gstin") or "",
        "place_of_supply": split.get("place_of_supply") or "",
        "interstate": interstate,
        "taxable": taxable,
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
        "tax": round(cgst + sgst + igst, 2),
        "total": grand,
        "needs_eway": needs_eway,
        "tally_status": status,
        "exported_to_tally": bool(o.get("exported_to_tally")),
        "exported_at": o.get("exported_at"),
        "exported_by": o.get("exported_by"),
        "done_at": o.get("done_at"),
        "done_by": o.get("done_by"),
        "attention_note": o.get("attention_note") or "",
        "age_days": age_days,
        "overdue": overdue,
    }


def _b2b_invoices(
    db,
    *,
    from_date=None,
    to_date=None,
    store_id=None,
    entity_id=None,
    tally_status=None,
) -> List[dict]:
    """Shared B2B-invoice list builder used by BOTH the export console and the
    worklist. Returns rows for every real (non-DRAFT/CANCELLED) order whose
    customer is B2B, with the per-invoice GST split + needs_eway + tally_status
    + age/overdue reminder fields. Fail-soft: a DB error yields []."""
    from ...routers.orders import _build_invoice_gst_split  # reuse GST math

    cust_map = _b2b_customer_map(db)
    store_state = _store_state_map(db)

    match: dict = {"status": _REAL_ORDER_STATUS_FILTER}
    store_ids = None
    if store_id:
        store_ids = [store_id]
    elif entity_id:
        s2e, _ = _store_maps(db)
        store_ids = [sid for sid, eid in s2e.items() if eid == entity_id]
    if store_ids is not None:
        match["store_id"] = {"$in": store_ids}
    _apply_created_at_range(match, from_date, to_date)

    try:
        orders = list(db.get_collection("orders").find(match, {"_id": 0}))
    except Exception:  # noqa: BLE001
        return []

    now = now_ist_naive()
    rows: List[dict] = []
    for o in orders:
        cust = cust_map.get(o.get("customer_id"))
        # An order may carry a GSTIN even if the customer record is gone; fall
        # back to a synthetic customer dict so it is still classified B2B.
        if cust is None:
            cust = {
                "gstin": str(o.get("customer_gstin") or "").strip(),
                "customer_type": "",
                "state": "",
                "name": o.get("customer_name") or "",
            }
        if not _is_b2b_customer(cust):
            continue

        # Build the store + customer dicts the GST-split helper expects. The
        # store provides supplier state; the customer provides place-of-supply.
        store_doc = {
            "state": store_state.get(o.get("store_id"), ""),
            "state_code": store_state.get(o.get("store_id"), ""),
            "gstin": "",
        }
        cust_doc = {"gstin": cust.get("gstin"), "state": cust.get("state")}
        try:
            split = _build_invoice_gst_split(o.get("items") or [], store_doc, cust_doc)
        except Exception:  # noqa: BLE001 -- never let one bad order kill the list
            split = {
                "totals": {},
                "interstate": False,
                "customer_gstin": cust.get("gstin", ""),
            }

        row = _b2b_invoice_row(o, cust, split, now)
        if tally_status and row["tally_status"] != str(tally_status).upper():
            continue
        rows.append(row)
    return rows


def _b2b_summary(rows: List[dict]) -> dict:
    """Aggregate counts/totals for the console header cards."""
    return {
        "count": len(rows),
        "pending": sum(1 for r in rows if r["tally_status"] == "PENDING"),
        "in_tally": sum(1 for r in rows if r["tally_status"] == "IN_TALLY"),
        "done": sum(1 for r in rows if r["tally_status"] == "DONE"),
        "needs_eway": sum(1 for r in rows if r["needs_eway"]),
        "overdue": sum(1 for r in rows if r["overdue"]),
        "exported": sum(1 for r in rows if r["exported_to_tally"]),
        "total_taxable": round(sum(r["taxable"] for r in rows), 2),
        "total_tax": round(sum(r["tax"] for r in rows), 2),
        "total_value": round(sum(r["total"] for r in rows), 2),
    }


class B2BExportRequest(BaseModel):
    """Bulk export of selected B2B invoices to a single Tally XML file."""

    order_ids: List[str] = Field(..., min_length=1)
    # When true, PENDING invoices in the selection are advanced to IN_TALLY
    # (they have now been handed to Tally as XML). DONE rows are never demoted.
    mark_in_tally: bool = True


class B2BMarkExportedRequest(BaseModel):
    order_ids: List[str] = Field(..., min_length=1)


class B2BAttentionNoteRequest(BaseModel):
    # Free-text reminder. Empty string clears the note. Capped to keep the doc
    # small and avoid an unbounded write.
    note: str = Field("", max_length=2000)


def _b2b_fetch_orders(db, order_ids: List[str]) -> List[dict]:
    """Fetch the selected B2B orders (real status only), preserving the
    per-invoice GST/IGST split the day-voucher builder needs. Fail-soft []."""
    cust_map = _b2b_customer_map(db)
    store_state = _store_state_map(db)
    try:
        from ...routers.orders import _build_invoice_gst_split

        docs = list(
            db.get_collection("orders").find(
                {
                    "order_id": {"$in": list(order_ids)},
                    "status": _REAL_ORDER_STATUS_FILTER,
                },
                {"_id": 0},
            )
        )
    except Exception:  # noqa: BLE001
        return []

    out: List[dict] = []
    for o in docs:
        cust = cust_map.get(o.get("customer_id")) or {
            "gstin": str(o.get("customer_gstin") or "").strip(),
            "customer_type": "",
            "state": "",
            "name": o.get("customer_name") or "",
        }
        if not _is_b2b_customer(cust):
            continue
        store_doc = {
            "state": store_state.get(o.get("store_id"), ""),
            "state_code": store_state.get(o.get("store_id"), ""),
            "gstin": "",
        }
        cust_doc = {"gstin": cust.get("gstin"), "state": cust.get("state")}
        try:
            split = _build_invoice_gst_split(o.get("items") or [], store_doc, cust_doc)
            totals = split.get("totals") or {}
        except Exception:  # noqa: BLE001
            totals = {}
        grand = float(o.get("grand_total") or o.get("total") or 0.0)
        taxable = float(totals.get("taxable") or 0.0)
        # Shape the order for tally_build_day_voucher_xml (subtotal + tax legs).
        o["subtotal"] = (
            round(taxable, 2)
            if taxable
            else round(grand - float(totals.get("tax") or 0.0), 2)
        )
        o["cgst_amount"] = round(float(totals.get("cgst") or 0.0), 2)
        o["sgst_amount"] = round(float(totals.get("sgst") or 0.0), 2)
        o["igst_amount"] = round(float(totals.get("igst") or 0.0), 2)
        o["grand_total"] = round(grand, 2)
        # Use the stamped invoice number as the Tally VOUCHERNUMBER when present
        # (falls back to order_id inside the builder otherwise).
        if o.get("invoice_number"):
            o["order_id"] = o["invoice_number"]
        out.append(o)
    return out


@router.get("/b2b-invoices")
async def list_b2b_invoices(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    store_id: Optional[str] = None,
    entity_id: Optional[str] = None,
    tally_status: Optional[str] = Query(
        None, description="Filter PENDING / IN_TALLY / DONE"
    ),
    current_user: dict = Depends(get_current_user),
):
    """List B2B sales invoices (customer is B2B / has a GSTIN) for a date range
    + optional store/entity scope. Powers BOTH the 'Export to Tally' console and
    the 'Tally worklist'. Each row carries the GST split (CGST/SGST/IGST),
    needs_eway, tally_status, exported flag, and the PENDING-age reminder.

    Accountant material -- ADMIN / ACCOUNTANT / SUPERADMIN only."""
    _require_finance_admin(current_user)
    db = _get_db()
    rows = _b2b_invoices(
        db,
        from_date=from_date,
        to_date=to_date,
        store_id=store_id,
        entity_id=entity_id,
        tally_status=tally_status,
    )
    return {
        "invoices": rows,
        "summary": _b2b_summary(rows),
        "eway_threshold": _EWAY_VALUE_THRESHOLD,
        "pending_reminder_days": _B2B_PENDING_REMINDER_DAYS,
    }


@router.get("/b2b-invoices/{order_id}/tally-xml")
async def get_b2b_invoice_tally_xml(
    order_id: str, current_user: dict = Depends(get_current_user)
):
    """Tally sales-voucher XML for ONE B2B invoice (downloadable). The
    accountant imports it into Tally, which then issues the e-invoice/e-way."""
    _require_finance_admin(current_user)
    db = _get_db()
    orders = _b2b_fetch_orders(db, [order_id])
    if not orders:
        raise HTTPException(status_code=404, detail="B2B invoice not found")

    from agents.nexus_providers import tally_build_day_voucher_xml

    xml = tally_build_day_voucher_xml(orders)
    safe = str(order_id).replace("/", "-")
    fname = f"b2b_tally_{safe}.xml"
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/b2b-invoices/export")
async def export_b2b_invoices_to_tally(
    body: B2BExportRequest = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """Build ONE Tally XML for the selected B2B invoices (a Sales voucher per
    invoice with party + per-rate tax ledgers). Optionally advances PENDING
    rows to IN_TALLY (they have been handed to Tally). Returns the XML so the
    accountant imports it into Tally (which issues the e-invoice/e-way bill)."""
    _require_finance_admin(current_user)
    db = _get_db()
    orders = _b2b_fetch_orders(db, body.order_ids)
    if not orders:
        raise HTTPException(
            status_code=404, detail="No B2B invoices found for the selection"
        )

    from agents.nexus_providers import tally_build_day_voucher_xml

    xml = tally_build_day_voucher_xml(orders)

    # Optionally move PENDING -> IN_TALLY (never demote DONE). Fail-soft: a write
    # error does not block the XML download (the accountant still gets the file).
    if body.mark_in_tally:
        try:
            db.get_collection("orders").update_many(
                {
                    "order_id": {"$in": list(body.order_ids)},
                    "tally_status": {"$in": [None, "PENDING"]},
                },
                {
                    "$set": {
                        "tally_status": "IN_TALLY",
                        "tally_in_tally_at": now_ist_naive(),
                    }
                },
            )
        except Exception:  # noqa: BLE001
            pass

    fname = f"b2b_tally_export_{ist_today().isoformat()}.xml"
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/b2b-invoices/mark-exported")
async def mark_b2b_invoices_exported(
    body: B2BMarkExportedRequest = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """Stamp the selected B2B invoices as handed to Tally: exported_to_tally =
    true + exported_at/exported_by, and advance PENDING -> IN_TALLY. The list
    then shows what has already been given to the accountant's Tally."""
    _require_finance_admin(current_user)
    db = _get_db()
    who = current_user.get("user_id") or current_user.get("username") or "system"
    now = now_ist_naive()
    try:
        res = db.get_collection("orders").update_many(
            {"order_id": {"$in": list(body.order_ids)}},
            {
                "$set": {
                    "exported_to_tally": True,
                    "exported_at": now,
                    "exported_by": who,
                }
            },
        )
        # Advance any still-PENDING rows to IN_TALLY (DONE is left intact).
        db.get_collection("orders").update_many(
            {
                "order_id": {"$in": list(body.order_ids)},
                "tally_status": {"$in": [None, "PENDING"]},
            },
            {"$set": {"tally_status": "IN_TALLY", "tally_in_tally_at": now}},
        )
        modified = getattr(res, "modified_count", 0)
    except Exception:  # noqa: BLE001 -- fail loudly, do not pretend success
        logger.exception("Tally mark-exported failed")
        raise HTTPException(
            status_code=503,
            detail="Could not mark the export - try again or contact support",
        )
    return {"ok": True, "marked": modified, "exported_by": who}


@router.post("/b2b-invoices/{order_id}/mark-done")
async def mark_b2b_invoice_done(
    order_id: str, current_user: dict = Depends(get_current_user)
):
    """Confirm a B2B invoice has been created in Tally (with its e-invoice +
    e-way bill where required): tally_status = DONE + done_at/done_by. Clears it
    off the reminder worklist."""
    _require_finance_admin(current_user)
    db = _get_db()
    who = current_user.get("user_id") or current_user.get("username") or "system"
    try:
        res = db.get_collection("orders").update_one(
            {"order_id": order_id},
            {
                "$set": {
                    "tally_status": "DONE",
                    "done_at": now_ist_naive(),
                    "done_by": who,
                }
            },
        )
        matched = getattr(res, "matched_count", 0)
    except Exception:  # noqa: BLE001
        logger.exception("Tally mark-done failed")
        raise HTTPException(
            status_code=503,
            detail="Could not mark as done - try again or contact support",
        )
    if not matched:
        raise HTTPException(status_code=404, detail="B2B invoice not found")
    return {"ok": True, "order_id": order_id, "tally_status": "DONE", "done_by": who}


@router.post("/b2b-invoices/{order_id}/attention-note")
async def set_b2b_attention_note(
    order_id: str,
    body: B2BAttentionNoteRequest = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """Set (or clear, with an empty string) the free-text attention note on a
    B2B invoice -- a reminder for the accountant about special Tally handling."""
    _require_finance_admin(current_user)
    db = _get_db()
    note = (body.note or "").strip()
    who = current_user.get("user_id") or current_user.get("username") or "system"
    try:
        res = db.get_collection("orders").update_one(
            {"order_id": order_id},
            {
                "$set": {
                    "attention_note": note,
                    "attention_note_by": who,
                    "attention_note_at": now_ist_naive(),
                }
            },
        )
        matched = getattr(res, "matched_count", 0)
    except Exception:  # noqa: BLE001
        logger.exception("Attention-note save failed")
        raise HTTPException(
            status_code=503,
            detail="Could not save the attention note - try again or contact support",
        )
    if not matched:
        raise HTTPException(status_code=404, detail="B2B invoice not found")
    return {"ok": True, "order_id": order_id, "attention_note": note}


@router.get("/tally/sales-jv")
async def get_tally_sales_jv(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    store_id: Optional[str] = None,
    entity_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Tally sales-voucher XML for a period + scope, ready to import into Tally."""
    # Org-wide sales-voucher export is an accounting function -- finance-admin
    # only (owner decision 2026-06-16); a store-level role can't export all
    # stores' sales JV. Mirror of the tender-receipt-jv sibling gate.
    _require_finance_admin(current_user)
    db = _get_db()
    # Never export DRAFT/CANCELLED orders to Tally -- they aren't real sales
    # vouchers. created_at is a BSON datetime so the range is built as datetimes
    # (a 'YYYY-MM-DD' string bound never matched -> empty export).
    match: dict = {"status": _REAL_ORDER_STATUS_FILTER}
    store_ids = None
    if store_id:
        store_ids = [store_id]
    elif entity_id:
        s2e, _ = _store_maps(db)
        store_ids = [sid for sid, eid in s2e.items() if eid == entity_id]
    if store_ids is not None:
        match["store_id"] = {"$in": store_ids}
    _apply_created_at_range(match, from_date, to_date)

    orders = list(db.get_collection("orders").find(match, {"_id": 0}))
    # Determine inter-state vs intra-state for each order so the Tally voucher
    # uses the correct output ledger (IGST for inter-state, CGST+SGST for intra).
    _store_states = _store_state_map(db)
    _customer_states = _customer_state_map(db)
    for o in orders:
        tax = float(o.get("tax_amount") or o.get("tax_total") or 0)
        grand = float(o.get("grand_total") or o.get("total") or 0)
        # OS-008: the order's own interstate flag wins (online orders carry it);
        # store-vs-customer state stays the fallback for docs without it.
        is_inter_state = _order_is_interstate(o, _store_states, _customer_states)
        if is_inter_state:
            o["igst_amount"] = round(tax, 2)
            o["cgst_amount"] = 0.0
            o["sgst_amount"] = 0.0
        else:
            cgst, sgst = _jv_cgst_sgst_split(tax)
            o["igst_amount"] = 0.0
            o["cgst_amount"] = cgst
            o["sgst_amount"] = sgst
        o["subtotal"] = round(grand - tax, 2)
        o["grand_total"] = grand

    store_meta = {}
    if store_id:
        s = db.get_collection("stores").find_one({"store_id": store_id}) or {}
        store_meta = {
            "store_id": store_id,
            "store_code": s.get("store_code"),
            "store_name": s.get("store_name"),
        }

    from agents.nexus_providers import tally_build_day_voucher_xml

    xml = tally_build_day_voucher_xml(orders, store_meta)
    fname = f"sales_jv_{(from_date or 'all')[:10]}_{(to_date or 'all')[:10]}.xml"
    headers = {"Content-Disposition": f'attachment; filename="{fname}"'}
    # E5 wiring (flag-gated, ADDITIVE): when policy tally.tender_receipt_voucher
    # is ON, OFFER the companion tender-routed Receipt voucher via a response
    # header. The XML BODY of this Sales export is byte-identical whether the
    # flag is on or off -- per the adversarial-chair guidance, receipt legs are
    # NEVER injected into the existing Sales vouchers; they live on the sibling
    # route below. Fail-dark: a policy-read error adds nothing.
    try:
        from ..reconciliation import tender_receipt_policy_enabled

        if tender_receipt_policy_enabled(store_id=store_id, entity_id=entity_id):
            headers["X-Tally-Tender-Receipt"] = (
                "/api/v1/finance/tally/tender-receipt-jv"
            )
    except Exception:  # noqa: BLE001
        pass
    return Response(
        content=xml,
        media_type="application/xml",
        headers=headers,
    )
