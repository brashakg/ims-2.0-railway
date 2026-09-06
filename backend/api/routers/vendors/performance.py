"""Vendor performance scorecard and purchase history."""

from ._shared import (
    Depends,
    Query,
    _get_db,
    datetime,
    get_current_user,
    get_vendor_repository,
    ist_date_str,
    logger,
    now_ist,
    router,
    timedelta,
)


# ============================================================================
# INV-13: VENDOR PERFORMANCE SCORING + PURCHASE-HISTORY ANALYTICS
# ============================================================================
# Score vendors on delivery timeliness and goods quality from GRN data, and
# surface a purchase-history breakdown so buying decisions are data-driven
# rather than WhatsApp-tracked.
#
# Performance score:
#   on_time_rate    = GRNs accepted within PO expected_date / total GRNs
#   acceptance_rate = total_accepted / total_received across GRNs
#   overall_score   = simple weighted average (40 % timeliness + 60 % quality)
#
# All computations are fail-soft: a missing DB or collection returns honest
# empty states.  No fabricated numbers (SYSTEM_INTENT).


def _vendor_mtd_spend(db, vendor_id: str) -> float:
    """Sum of this vendor's bills dated in the CURRENT calendar month (rupees).

    Reads vendor_bills.total_amount where bill_date (fallback created_at) falls
    in the current month. Fail-soft: missing DB / collection / parse error -> 0.0
    (an honest zero, never a fabricated number per SYSTEM_INTENT)."""
    if db is None:
        return 0.0
    # BUG-104. The VALUE side (`bill_date`) is already an IST business-date
    # string, so the month PREFIX it is matched against must be the IST month
    # too. On the UTC clock, between 00:00 and 05:30 IST on the 1st this asked
    # for LAST month and the vendor's month-to-date spend read as the previous
    # month's total for five and a half hours every month.
    month_prefix = now_ist().strftime("%Y-%m")  # "2026-06"
    total = 0.0
    try:
        bills = db.get_collection("vendor_bills").find(
            {"vendor_id": vendor_id},
            {"_id": 0, "total_amount": 1, "bill_date": 1, "created_at": 1},
        )
        for b in bills:
            when = str(b.get("bill_date") or b.get("created_at") or "")[:7]
            if when == month_prefix:
                try:
                    total += float(b.get("total_amount") or 0)
                except (TypeError, ValueError):
                    pass
    except Exception:  # noqa: BLE001
        return 0.0
    return round(total, 2)


def _vendor_qc_pass_rate(db, vendor_id: str):
    """Quality pass-rate for a vendor, joining GRN QC + workshop QC outcomes.

    Two QC signals are unioned into one pass-rate:
      * GRN QC: units accepted vs received across this vendor's ACCEPTED GRNs
        (total_accepted / total_received).
      * Workshop QC: lens jobs routed to this vendor (lab) -- a job passes when
        qc_passed is True (or waived), fails when qc_passed is False. Only jobs
        that actually went through QC count toward the sample.

    Returns (pass_rate, sample_size). pass_rate is units+jobs passed / total
    units+jobs evaluated, rounded to 4 dp; None when there is NO QC signal at
    all. Fail-soft: any error -> (None, 0). No fabricated numbers."""
    if db is None:
        return None, 0
    passed = 0
    total = 0
    # --- GRN QC (goods-receipt inspection outcome) ---
    try:
        grns = db.get_collection("grns").find(
            {"vendor_id": vendor_id, "status": "ACCEPTED"},
            {"_id": 0, "total_received": 1, "total_accepted": 1},
        )
        for g in grns:
            try:
                recv = int(g.get("total_received") or 0)
                acc = int(g.get("total_accepted") or 0)
            except (TypeError, ValueError):
                continue
            if recv > 0:
                total += recv
                passed += max(0, min(acc, recv))
    except Exception:  # noqa: BLE001
        pass
    # --- Workshop QC (lens jobs routed to this lab/vendor) ---
    try:
        jobs = db.get_collection("workshop_jobs").find(
            {"vendor_id": vendor_id},
            {"_id": 0, "qc_passed": 1, "qc_waived": 1, "qc_history": 1},
        )
        for j in jobs:
            history = [h for h in (j.get("qc_history") or []) if isinstance(h, dict)]
            had_qc = bool(history) or (j.get("qc_passed") is not None)
            if not had_qc:
                continue
            total += 1
            if history:
                ok = not any(h.get("passed") is False for h in history)
            else:
                ok = bool(j.get("qc_passed")) or bool(j.get("qc_waived"))
            if ok:
                passed += 1
    except Exception:  # noqa: BLE001
        pass

    if total <= 0:
        return None, 0
    return round(passed / total, 4), total


@router.get("/{vendor_id}/performance")
async def vendor_performance(
    vendor_id: str,
    months: int = Query(
        6,
        ge=1,
        le=24,
        description="Number of rolling months to include in the score window.",
    ),
    current_user: dict = Depends(get_current_user),
):
    """Return a performance score for the vendor over the last `months` months (INV-13).

    Score components:
      - acceptance_rate: fraction of received units accepted (quality signal).
      - on_time_rate: fraction of GRN accepts that landed on or before the PO
        expected_date (delivery punctuality signal).
      - overall_score: 60 % acceptance + 40 % on_time (0-100 index).

    When fewer than 3 GRNs exist in the window the score is flagged
    ``insufficient_data: true`` so callers know the rating has low confidence.
    Honest empty state: if the vendor has no GRN history an honest zero is
    returned, not a fabricated average.
    """
    db = _get_db()
    vendor_repo = get_vendor_repository()
    vendor = vendor_repo.find_by_id(vendor_id) if vendor_repo is not None else None

    # MTD spend is independent of GRN history -- compute it up front so even a
    # vendor with no GRNs in the window still reports what we've billed this
    # month. Fail-soft: any error -> 0.0 (honest, never fabricated).
    mtd_spend = _vendor_mtd_spend(db, vendor_id)
    # QC pass-rate joins GRN QC (accepted/received) + workshop QC (job pass/fail)
    # for this vendor. Fail-soft: returns None when there is no QC signal at all.
    qc_pass_rate, qc_sample = _vendor_qc_pass_rate(db, vendor_id)

    empty = {
        "vendor_id": vendor_id,
        "vendor_name": (
            (vendor.get("trade_name") or vendor.get("legal_name")) if vendor else None
        ),
        "window_months": months,
        "grns_evaluated": 0,
        "acceptance_rate": None,
        "on_time_rate": None,
        "qc_pass_rate": qc_pass_rate,
        "qc_sample_size": qc_sample,
        "mtd_spend": mtd_spend,
        "overall_score": None,
        "insufficient_data": True,
        "note": "No GRN data found for this vendor in the selected window.",
    }
    if db is None:
        return empty

    try:
        from datetime import timedelta

        # BUG-104 sweep finding, NOT an IST bug -- a FRAME-TYPE bug of the same
        # family as buy_desk.py:110. `grns.created_at` is written by
        # BaseRepository._add_timestamps as a BSON **datetime** (it overwrites
        # whatever the caller passed), and Mongo type-brackets: a STRING $gte
        # never matches a Date, so this query returned ZERO rows and the vendor
        # GRN scorecard has been permanently, silently empty. Pass the datetime.
        # `cutoff` itself is pure elapsed time (now minus N days) in the same
        # naive-UTC frame the instants are stored in, so it needs no IST shift.
        cutoff = datetime.now() - timedelta(days=months * 30)

        grn_coll = db.get_collection("grns")
        grns = list(
            grn_coll.find(
                {
                    "vendor_id": vendor_id,
                    "status": "ACCEPTED",
                    "created_at": {"$gte": cutoff},
                },
                {
                    "_id": 0,
                    "grn_id": 1,
                    "po_id": 1,
                    "created_at": 1,
                    "total_received": 1,
                    "total_accepted": 1,
                    "total_rejected": 1,
                    "accepted_at": 1,
                },
            ).limit(500)
        )

        if not grns:
            return empty

        total_received = 0
        total_accepted = 0
        on_time_count = 0
        grns_with_po_date = 0

        po_coll = db.get_collection("purchase_orders")

        for grn in grns:
            recv = int(grn.get("total_received") or 0)
            acc = int(grn.get("total_accepted") or 0)
            total_received += recv
            total_accepted += acc

            # Punctuality: compare GRN accepted_at vs PO expected_date
            po_id = grn.get("po_id")
            if po_id:
                try:
                    po = po_coll.find_one(
                        {"po_id": po_id}, {"_id": 0, "expected_date": 1}
                    )
                    if po and po.get("expected_date"):
                        expected = str(po["expected_date"])[:10]
                        accepted_at = str(
                            grn.get("accepted_at") or grn.get("created_at") or ""
                        )[:10]
                        if accepted_at and accepted_at <= expected:
                            on_time_count += 1
                        grns_with_po_date += 1
                except Exception:
                    pass

        n = len(grns)
        acceptance_rate = (
            round(total_accepted / total_received, 4) if total_received > 0 else None
        )
        on_time_rate = (
            round(on_time_count / grns_with_po_date, 4)
            if grns_with_po_date > 0
            else None
        )

        if acceptance_rate is not None and on_time_rate is not None:
            overall_score = round((acceptance_rate * 0.6 + on_time_rate * 0.4) * 100, 1)
        elif acceptance_rate is not None:
            overall_score = round(acceptance_rate * 100, 1)
        else:
            overall_score = None

        return {
            "vendor_id": vendor_id,
            "vendor_name": (
                (vendor.get("trade_name") or vendor.get("legal_name"))
                if vendor
                else None
            ),
            "window_months": months,
            "grns_evaluated": n,
            "total_received": total_received,
            "total_accepted": total_accepted,
            "total_rejected": total_received - total_accepted,
            "acceptance_rate": acceptance_rate,
            "on_time_rate": on_time_rate,
            "qc_pass_rate": qc_pass_rate,
            "qc_sample_size": qc_sample,
            "mtd_spend": mtd_spend,
            "on_time_grns": on_time_count,
            "grns_with_po_date": grns_with_po_date,
            "overall_score": overall_score,
            "score_label": (
                (
                    "Excellent"
                    if (overall_score or 0) >= 90
                    else (
                        "Good"
                        if (overall_score or 0) >= 75
                        else "Average" if (overall_score or 0) >= 50 else "Poor"
                    )
                )
                if overall_score is not None
                else None
            ),
            "insufficient_data": n < 3,
        }

    except Exception as exc:
        logger.warning("[INV-13] vendor_performance failed for %s: %s", vendor_id, exc)
        return empty


@router.get("/{vendor_id}/purchase-history")
async def vendor_purchase_history(
    vendor_id: str,
    months: int = Query(
        12,
        ge=1,
        le=36,
        description="Rolling-months window for the purchase-history report.",
    ),
    current_user: dict = Depends(get_current_user),
):
    """Return purchase-history analytics for one vendor (INV-13).

    Returns:
      - monthly breakdown of PO value, received units, and accepted units.
      - top-5 products ordered from this vendor by spend.
      - summary totals for the window.

    Honest empty state: no fabricated numbers; all fields are derived from real
    PO + GRN data or explicitly null.
    """
    db = _get_db()
    vendor_repo = get_vendor_repository()
    vendor = vendor_repo.find_by_id(vendor_id) if vendor_repo is not None else None

    empty = {
        "vendor_id": vendor_id,
        "vendor_name": (
            (vendor.get("trade_name") or vendor.get("legal_name")) if vendor else None
        ),
        "window_months": months,
        "total_pos": 0,
        "total_spend": 0.0,
        "total_units_received": 0,
        "monthly": [],
        "top_products": [],
    }
    if db is None:
        return empty

    try:
        from datetime import timedelta

        # Same frame-type bug as the scorecard above: purchase_orders /
        # grns.created_at are BSON datetimes (BaseRepository._add_timestamps),
        # so the old ISO-STRING $gte matched nothing and this whole vendor
        # spend chart rendered empty. Compare Date to Date.
        cutoff = datetime.now() - timedelta(days=months * 30)

        po_coll = db.get_collection("purchase_orders")
        grn_coll = db.get_collection("grns")

        pos = list(
            po_coll.find(
                {
                    "vendor_id": vendor_id,
                    "status": {"$nin": ["CANCELLED", "DRAFT"]},
                    "created_at": {"$gte": cutoff},
                },
                {
                    "_id": 0,
                    "po_id": 1,
                    "po_number": 1,
                    "created_at": 1,
                    "total_amount": 1,
                    "items": 1,
                    "status": 1,
                },
            )
            .sort("created_at", 1)
            .limit(500)
        )

        grns = list(
            grn_coll.find(
                {
                    "vendor_id": vendor_id,
                    "status": "ACCEPTED",
                    "created_at": {"$gte": cutoff},
                },
                {
                    "_id": 0,
                    "created_at": 1,
                    "total_received": 1,
                    "total_accepted": 1,
                    "po_id": 1,
                },
            ).limit(1000)
        )

        # Monthly breakdown keyed by YYYY-MM
        monthly: dict = {}
        product_spend: dict = {}

        for po in pos:
            # BUG-104, VALUE rule (+5:30): the month this spend is charted
            # under is a business month. A PO raised 1-Jun 02:00 IST used to
            # land in MAY's bar.
            month = ist_date_str(po.get("created_at"))[:7]
            if not month:
                continue
            bucket = monthly.setdefault(
                month, {"month": month, "pos": 0, "spend": 0.0, "units_received": 0}
            )
            bucket["pos"] += 1
            bucket["spend"] = round(
                bucket["spend"] + float(po.get("total_amount") or 0), 2
            )
            # Accumulate per-product spend
            for item in po.get("items") or []:
                pid = item.get("product_id", "")
                name = item.get("product_name", "") or item.get("name", "")
                sku = item.get("sku", "")
                spend = float(item.get("unit_price") or 0) * int(
                    item.get("quantity") or 0
                )
                if pid:
                    if pid not in product_spend:
                        product_spend[pid] = {
                            "product_id": pid,
                            "name": name,
                            "sku": sku,
                            "spend": 0.0,
                            "units": 0,
                        }
                    product_spend[pid]["spend"] = round(
                        product_spend[pid]["spend"] + spend, 2
                    )
                    product_spend[pid]["units"] += int(item.get("quantity") or 0)

        for grn in grns:
            # Same chart, same frame as the PO leg above (VALUE rule).
            month = ist_date_str(grn.get("created_at"))[:7]
            if not month:
                continue
            bucket = monthly.setdefault(
                month, {"month": month, "pos": 0, "spend": 0.0, "units_received": 0}
            )
            bucket["units_received"] += int(grn.get("total_received") or 0)

        monthly_list = sorted(monthly.values(), key=lambda x: x["month"])
        top_products = sorted(product_spend.values(), key=lambda x: -x["spend"])[:5]

        total_spend = sum(b["spend"] for b in monthly_list)
        total_units = sum(b["units_received"] for b in monthly_list)

        return {
            "vendor_id": vendor_id,
            "vendor_name": (
                (vendor.get("trade_name") or vendor.get("legal_name"))
                if vendor
                else None
            ),
            "window_months": months,
            "total_pos": len(pos),
            "total_spend": round(total_spend, 2),
            "total_units_received": total_units,
            "monthly": monthly_list,
            "top_products": top_products,
        }

    except Exception as exc:
        logger.warning(
            "[INV-13] vendor_purchase_history failed for %s: %s", vendor_id, exc
        )
        return empty
