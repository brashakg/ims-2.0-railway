"""Per-entity GST reconciliation, the month-end cross-check and its sign-off.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from datetime import date
from ...utils.ist import now_ist, ist_day_start_utc
from typing import Optional
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from ..auth import get_current_user
from ._shared import (
    _REAL_ORDER_STATUS_FILTER,
    _customer_state_map,
    _get_db,
    _iso_now,
    _order_is_interstate,
    _require_finance_admin,
    _store_gstin_map,
    _store_maps,
    _store_state_map,
    gst_reconciliation,
    logger,
    router,
)

# === GST Reconciliation (per entity) + Tally export ===


@router.get("/gst/reconciliation")
async def get_gst_reconciliation(
    month: Optional[int] = None,
    year: Optional[int] = None,
    entity_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """GST output (sales tax) vs input credit (purchase tax), grouped by entity.
    You file the actual returns through Tally; this is the cross-check."""
    # Org-wide, entity-grouped GST reconciliation is a filing/accounting view --
    # finance-admin only (owner decision 2026-06-16), so a single-store
    # STORE_MANAGER/AREA_MANAGER cannot read every entity's GST position.
    _require_finance_admin(current_user)
    db = _get_db()
    now = now_ist()
    m = month or now.month
    y = year or now.year
    # IST calendar month -> naive-UTC created_at bounds (same as /gst/summary),
    # so the reconciliation and GSTR-1/3B agree on the period's invoices.
    start = ist_day_start_utc(date(y, m, 1))
    end = ist_day_start_utc(date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1))

    s2e, enames = _store_maps(db)
    store_ids = None
    if entity_id:
        store_ids = [sid for sid, eid in s2e.items() if eid == entity_id]

    # Orders persist created_at as a BSON datetime; the previous .isoformat()
    # string bound matched nothing, so GST-collected per entity read zero.
    # Exclude DRAFT/CANCELLED -- only real sales carry GST liability.
    o_match = {
        "created_at": {"$gte": start, "$lt": end},
        "status": _REAL_ORDER_STATUS_FILTER,
    }
    if store_ids is not None:
        o_match["store_id"] = {"$in": store_ids}
    orders = list(
        db.get_collection("orders").find(
            o_match,
            {
                "_id": 0,
                "store_id": 1,
                "customer_id": 1,
                "tax_amount": 1,
                "tax_total": 1,
                # OS-008: the order-carried inter-state flag (online orders).
                "interstate": 1,
            },
        )
    )

    # purchase_orders persist created_at as a BSON datetime (BaseRepository
    # _add_timestamps). No writer ever set a top-level `date` field, so the old
    # {"date": <string>} filter matched NOTHING and input_credit read a permanent
    # 0. Use the SAME IST created_at window as the orders side, and map a PO to an
    # entity via delivery_store_id OR store_id (gst_reconciliation's own mapping).
    # HR-3: exclude DRAFT / CANCELLED POs so a human draft or cancelled PO with
    # tax_amount does not inflate input_credit (parity with the cross-check leg).
    p_match = {
        "created_at": {"$gte": start, "$lt": end},
        "status": {"$nin": ["DRAFT", "draft", "CANCELLED", "cancelled"]},
    }
    if store_ids is not None:
        p_match["$or"] = [
            {"delivery_store_id": {"$in": store_ids}},
            {"store_id": {"$in": store_ids}},
        ]
    purchases = list(
        db.get_collection("purchase_orders").find(
            p_match, {"_id": 0, "delivery_store_id": 1, "store_id": 1, "tax_amount": 1}
        )
    )

    recon = gst_reconciliation(
        orders,
        purchases,
        s2e,
        enames,
        store_state_by_id=_store_state_map(db),
        customer_state_by_id=_customer_state_map(db),
    )
    recon.update(
        {
            "month": m,
            "year": y,
            "note": (
                "Output tax split intra-state (CGST+SGST) vs inter-state (IGST) "
                "by store vs customer state; file via Tally."
            ),
        }
    )
    return recon


# ---------------------------------------------------------------------------
# GST cross-check (accountant month-end sign-off)
# ---------------------------------------------------------------------------
# The accountant's reconciliation aid: for a chosen (month, entity) it lays the
# IMS GSTR-1 / GSTR-3B numbers SIDE BY SIDE against the books (orders, payments,
# Tally sales-JV totals, purchase-side ITC) so they can confirm everything
# agrees before filing, then record a CHECKED sign-off with notes. The tax math
# is entirely REUSED (reports._compute_gstr1 / _compute_gstr3b and the finance
# order aggregations); this layer only compares + audits. It does NOT lock the
# period -- that is the separate /period-lock action.

_GST_CROSSCHECK_SIGNOFFS = "gst_crosscheck_signoffs"


class GstCrossCheckSignoff(BaseModel):
    month: int = Field(..., ge=1, le=12)
    year: int = Field(..., ge=2000, le=2100)
    entity_id: Optional[str] = None
    note: Optional[str] = None
    # What the CLIENT claims it saw. The server RECOMPUTES the cross-check at
    # sign-off and records its OWN mismatch_count / gst_payable as the
    # authoritative audit snapshot; these client values are kept only for drift
    # forensics and must never be trusted as the record of what was signed off.
    mismatch_count: Optional[int] = None
    gst_payable: Optional[float] = None


def _gst_month_window(y: int, m: int):
    """IST-calendar-month -> naive-UTC created_at bounds [start, end). Same
    framing as /gst/summary and /gst/reconciliation so every GST view agrees
    on which invoices belong to the period."""
    start = ist_day_start_utc(date(y, m, 1))
    end = ist_day_start_utc(date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1))
    return start, end


def _books_and_tally_for_stores(db, store_ids, start, end) -> tuple:
    """Books + Tally-JV totals for a store set over a created_at window.

    Returns (books, tally). books = {sales_taxable, sales_tax,
    sales_grand_total, payments_collected}; tally = {taxable, tax, cgst, sgst,
    igst}. Reuses the SAME field expressions and intra/inter-state split as the
    finance /gst/summary and /tally/sales-jv endpoints -- no new tax math."""
    o_match = {
        "created_at": {"$gte": start, "$lt": end},
        "status": _REAL_ORDER_STATUS_FILTER,
    }
    if store_ids is not None:
        o_match["store_id"] = {"$in": store_ids}

    store_states = _store_state_map(db)
    customer_states = _customer_state_map(db)

    sales_grand = 0.0
    sales_tax = 0.0
    payments_collected = 0.0
    t_cgst = t_sgst = t_igst = 0.0

    cursor = db.get_collection("orders").find(
        o_match,
        {
            "_id": 0,
            "store_id": 1,
            "customer_id": 1,
            "grand_total": 1,
            "total": 1,
            "tax_amount": 1,
            "tax_total": 1,
            "payments": 1,
            # OS-008: the order-carried inter-state flag (online orders).
            "interstate": 1,
        },
    )
    for o in cursor:
        grand = float(o.get("grand_total") or o.get("total") or 0)
        tax = float(o.get("tax_amount") or o.get("tax_total") or 0)
        sales_grand += grand
        sales_tax += tax
        # OS-008: the order's own interstate flag wins (online orders carry it);
        # store-vs-customer state stays the fallback for docs without it.
        if _order_is_interstate(o, store_states, customer_states):
            t_igst += tax
        else:
            cgst, sgst = _jv_cgst_sgst_split(tax)
            t_cgst += cgst
            t_sgst += sgst
        for p in o.get("payments") or []:
            try:
                payments_collected += float(p.get("amount", 0) or 0)
            except (TypeError, ValueError):
                pass

    books = {
        "sales_grand_total": round(sales_grand, 2),
        "sales_tax": round(sales_tax, 2),
        "sales_taxable": round(sales_grand - sales_tax, 2),
        "payments_collected": round(payments_collected, 2),
    }
    tally = {
        "taxable": round(sales_grand - sales_tax, 2),
        "tax": round(sales_tax, 2),
        "cgst": round(t_cgst, 2),
        "sgst": round(t_sgst, 2),
        "igst": round(t_igst, 2),
    }
    return books, tally


def _run_gst_cross_check(db, m: int, y: int, entity_id: Optional[str]) -> dict:
    """Compute the full GST cross-check payload for (month, year, entity).

    Shared by the GET (display) and the POST sign-off, which RECOMPUTES
    server-side so the durable audit snapshot is the SERVER's figure, never the
    client's claim. Raises 503 when the DB is unreachable and 404 when a named
    entity resolves to no stores (which would otherwise render every source as
    0.00 -- a false 'all sources reconcile' green screen). Returns the result
    WITHOUT the sign-off marker (the GET attaches that separately)."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    from ...services import gst_crosscheck as _xc
    from ..reports import _compute_gstr1, _compute_gstr3b

    period = f"{y:04d}-{m:02d}"

    s2e, enames = _store_maps(db)
    s2g = _store_gstin_map(db)
    if entity_id:
        store_ids = [sid for sid, eid in s2e.items() if eid == entity_id]
        if not store_ids:
            raise HTTPException(
                status_code=404, detail="No stores found for this entity"
            )
    else:
        store_ids = list(s2e.keys())
        # HR-2: an empty store map (wiped foundation data or a transient stores
        # query failure) would otherwise render every source 0.00 and sign off a
        # false all-entities "all sources reconcile" green -- the same failure
        # mode the named-entity 404 guards. Refuse the unscoped figure.
        if not store_ids:
            raise HTTPException(
                status_code=404, detail="No stores configured - nothing to reconcile"
            )

    # Reuse the per-store GST-return computations (single source of truth for
    # the tax math), then aggregate to the entity level in the pure service. A
    # per-store compute failure is tracked (not silently dropped) so the screen
    # can flag partial data instead of presenting understated GSTR totals as a
    # tax break, and so sign-off can be blocked.
    g1_reports, g3_reports, g3_entities, g3_gstins = [], [], [], []
    failed: set = set()
    for sid in store_ids:
        try:
            g1_reports.append(_compute_gstr1(period, sid))
        except Exception:  # noqa: BLE001 -- one bad store must not sink the view
            logger.exception("cross-check: GSTR-1 compute failed for %s", sid)
            failed.add(sid)
        try:
            g3_reports.append(_compute_gstr3b(period, sid))
            g3_entities.append(s2e.get(sid))
            g3_gstins.append(s2g.get(sid))
        except Exception:  # noqa: BLE001
            logger.exception("cross-check: GSTR-3B compute failed for %s", sid)
            failed.add(sid)

    gstr1 = _xc.aggregate_gstr1(g1_reports)
    # Regular ITC / RCM are entity-scoped (identical for every sibling store), so
    # pass the entity per store report -- aggregate_gstr3b counts them ONCE per
    # entity. Transfer-borne ITC is GSTIN-scoped (R1), so pass the GSTIN per
    # store report -- it is counted ONCE per GSTIN, making a multi-GSTIN entity's
    # ITC independent of store enumeration order.
    gstr3b = _xc.aggregate_gstr3b(g3_reports, g3_entities, g3_gstins)

    start, end = _gst_month_window(y, m)
    books, tally = _books_and_tally_for_stores(db, store_ids, start, end)

    # Purchase-side ITC (from purchase_orders) as an INDEPENDENT cross-check
    # against GSTR-3B's vendor-bills ITC. Reuse gst_reconciliation()'s math.
    itc_leg_failed = False
    try:
        # purchase_orders.created_at is a BSON datetime (BaseRepository
        # _add_timestamps); no writer ever set a `date` field, so the old
        # `date` string filter matched NOTHING and ITC read a permanent 0. Use
        # the SAME IST created_at window as the orders side, and map a PO to an
        # entity via delivery_store_id OR store_id (gst_reconciliation's mapping).
        # HR-3: exclude DRAFT / CANCELLED POs (auto-draft POs are tax-less, but a
        # human DRAFT or CANCELLED PO with tax_amount would inflate books ITC and
        # flag a phantom mismatch) -- match the case variants PO writers write.
        p_match: dict = {
            "created_at": {"$gte": start, "$lt": end},
            "status": {"$nin": ["DRAFT", "draft", "CANCELLED", "cancelled"]},
        }
        if entity_id:
            p_match["$or"] = [
                {"delivery_store_id": {"$in": store_ids}},
                {"store_id": {"$in": store_ids}},
            ]
        purchases = list(
            db.get_collection("purchase_orders").find(
                p_match,
                {"_id": 0, "delivery_store_id": 1, "store_id": 1, "tax_amount": 1},
            )
        )
        recon = gst_reconciliation(
            [],
            purchases,
            s2e,
            enames,
            store_state_by_id=_store_state_map(db),
            customer_state_by_id=_customer_state_map(db),
        )
        if entity_id:
            match = next(
                (e for e in recon["entities"] if e["entity_id"] == entity_id), None
            )
            books["input_credit"] = match["input_credit"] if match else 0.0
        else:
            books["input_credit"] = recon.get("total_input_credit", 0.0)
    except Exception:  # noqa: BLE001
        logger.exception("cross-check: purchase-side ITC failed")
        # HR-1: a dead ITC leg must not sign off green. input_credit=None renders
        # the ITC row INFO (one source), which the mismatch_count silently
        # ignores; flag it so the sign-off gate can block (409) and the record
        # is stamped for forensics.
        books["input_credit"] = None
        itc_leg_failed = True

    result = _xc.build_crosscheck(gstr1, gstr3b, books, tally)
    result.update(
        {
            "month": m,
            "year": y,
            "period": period,
            "entity_id": entity_id,
            "entity_name": enames.get(entity_id) if entity_id else "All entities",
            "store_count": len(store_ids),
            "stores_computed": len(store_ids) - len(failed),
            "failed_store_ids": sorted(failed),
            "partial": bool(failed),
            "itc_leg_failed": itc_leg_failed,
            "gstr1": {
                "totalTaxableValue": gstr1["totalTaxableValue"],
                "totalTax": gstr1["totalTax"],
                "cgst": gstr1["cgst"],
                "sgst": gstr1["sgst"],
                "igst": gstr1["igst"],
            },
            "gstr3b": {
                "outwardTaxableValue": gstr3b["outwardTaxableValue"],
                "outwardTax": gstr3b["outwardTax"],
                "itc": gstr3b["itc"],
                "netCash": gstr3b["netCash"],
                "rcm": gstr3b["rcm"],
            },
            "books": books,
            "tally": tally,
        }
    )
    return result


@router.get("/gst/cross-check")
async def get_gst_cross_check(
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    entity_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Accountant GST cross-check for a (month, entity): GSTR-1 / GSTR-3B vs the
    books (orders / payments / Tally / purchase-side ITC), side by side, with
    per-rate breakup, CDNR + deemed-supply detail, and mismatch flags. A review
    aid only -- it changes no figure and locks no period."""
    _require_finance_admin(current_user)
    db = _get_db()

    now = now_ist()
    m = month or now.month
    y = year or now.year

    result = _run_gst_cross_check(db, m, y, entity_id)

    # Attach the latest sign-off (if any) so the screen shows CHECKED status.
    signoff = None
    try:
        signoff = db.get_collection(_GST_CROSSCHECK_SIGNOFFS).find_one(
            {"year": y, "month": m, "entity_id": entity_id or "_all"}, {"_id": 0}
        )
    except Exception:  # noqa: BLE001
        signoff = None
    result["signoff"] = signoff
    return result


@router.post("/gst/cross-check-signoff")
async def gst_cross_check_signoff(
    body: GstCrossCheckSignoff,
    current_user: dict = Depends(get_current_user),
):
    """Accountant marks a (month, entity) GST cross-check as CHECKED, with notes.

    Idempotent upsert keyed on (year, month, entity_id). Audit-logged. This is a
    review marker only -- it does NOT lock the accounting period (that is the
    separate /period-lock action) and changes no GST figure."""
    _require_finance_admin(current_user)
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    m = int(body.month)
    y = int(body.year)

    # Recompute the cross-check SERVER-SIDE so the durable audit snapshot is the
    # server's figure, never a (forgeable) client-supplied mismatch_count=0.
    server = _run_gst_cross_check(db, m, y, body.entity_id)
    # HR-1: block sign-off when EITHER reconciliation leg is degraded -- store
    # compute failure (understated GSTR totals) or a dead purchase-side ITC leg
    # (ITC row silently drops to INFO and escapes mismatch_count). Name the leg.
    if server.get("partial") or server.get("itc_leg_failed"):
        reasons = []
        if server.get("partial"):
            reasons.append(
                "%d store(s) failed to compute"
                % len(server.get("failed_store_ids") or [])
            )
        if server.get("itc_leg_failed"):
            reasons.append("the purchase-side ITC leg failed to compute")
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot sign off: %s, so the figures are understated. Resolve "
                "and retry." % " and ".join(reasons)
            ),
        )
    server_summary = server.get("summary", {})
    server_mismatch = int(server_summary.get("mismatch_count", 0) or 0)
    server_payable = float(server_summary.get("gst_payable", 0.0) or 0.0)

    key = {
        "year": y,
        "month": m,
        "entity_id": body.entity_id or "_all",
    }
    reviewer = current_user.get("name") or current_user.get("full_name")
    record = {
        **key,
        "checked": True,
        "checked_by": current_user.get("user_id"),
        "checked_by_name": reviewer,
        "checked_at": _iso_now(),
        "note": body.note,
        # Server-recomputed authoritative snapshot.
        "mismatch_count": server_mismatch,
        "gst_payable": server_payable,
        "mismatch_metrics": server_summary.get("mismatch_metrics", []),
        # Forensic marker: both legs computed cleanly (the gate above blocks a
        # sign-off otherwise, so this is always False on a stored record).
        "itc_leg_failed": bool(server.get("itc_leg_failed")),
        # What the client claimed it saw (drift forensics only, never trusted).
        "client_mismatch_count": body.mismatch_count,
        "client_gst_payable": body.gst_payable,
    }

    # Preserve any prior sign-off in a history array instead of silently
    # overwriting another reviewer's record.
    prior = None
    try:
        prior = db.get_collection(_GST_CROSSCHECK_SIGNOFFS).find_one(key, {"_id": 0})
    except Exception:  # noqa: BLE001
        prior = None
    update: dict = {"$set": record}
    if prior:
        # HR-5: keep BOTH drift-forensics figures (server + client gst_payable)
        # and cap the history at the last 50 sign-offs so it cannot grow without
        # bound.
        update["$push"] = {
            "history": {
                "$each": [
                    {
                        k: prior.get(k)
                        for k in (
                            "checked_by",
                            "checked_by_name",
                            "checked_at",
                            "note",
                            "mismatch_count",
                            "gst_payable",
                            "client_mismatch_count",
                            "client_gst_payable",
                        )
                    }
                ],
                "$slice": -50,
            }
        }
    try:
        db.get_collection(_GST_CROSSCHECK_SIGNOFFS).update_one(key, update, upsert=True)
    except Exception:  # noqa: BLE001
        logger.exception("GST cross-check sign-off failed")
        raise HTTPException(
            status_code=500,
            detail="Could not record the sign-off - try again or contact support",
        )

    # Audit (fail-soft; never undoes the sign-off write).
    try:
        from api.dependencies import get_audit_repository

        repo = get_audit_repository()
        if repo is not None:
            repo.create(
                {
                    "action": "gst_crosscheck.signoff",
                    "entity_type": "gst_crosscheck",
                    "entity_id": f"{key['year']}-{key['month']:02d}:{key['entity_id']}",
                    "user_id": current_user.get("user_id"),
                    "user_name": reviewer,
                    "severity": "INFO",
                    "source": "finance",
                    "after_state": {
                        "checked": True,
                        "note": body.note,
                        "mismatch_count": server_mismatch,
                        "gst_payable": server_payable,
                        "client_mismatch_count": body.mismatch_count,
                    },
                }
            )
    except Exception:  # noqa: BLE001
        pass

    return {"ok": True, "signoff": record}


def _jv_cgst_sgst_split(tax: float) -> tuple:
    """Split a line's total GST into intra-state CGST + SGST so they sum to the
    tax EXACTLY (the rounding residual goes on SGST). A naive round(tax/2) on both
    sides over-states by a paisa on odd-paise tax (100.01 -> 50.01 + 50.01 =
    100.02), which IMBALANCES the Tally voucher and gets it rejected on import.
    Mirrors orders._build_invoice_gst_split."""
    cgst = round(tax / 2.0, 2)
    sgst = round(tax - cgst, 2)
    return cgst, sgst
