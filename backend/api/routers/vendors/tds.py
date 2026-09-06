"""TDS threshold status and the 26Q export."""

from ._shared import (
    Depends,
    HTTPException,
    Optional,
    Query,
    _get_db,
    ap_engine,
    datetime,
    fy_start_year_ist,
    require_roles,
    router,
)


# ============================================================================
# FIN-11: TDS threshold status + 26Q/27EQ quarterly export
# ============================================================================


@router.get("/tds/threshold-status")
async def get_tds_threshold_status(
    vendor_id: str = Query(..., description="Vendor ID to check TDS threshold for"),
    section: str = Query(..., description="TDS section code e.g. 194C_OTHER"),
    current_payment: float = Query(
        ..., ge=0, description="Current payment amount (before TDS)"
    ),
    fy_start: Optional[str] = Query(
        None,
        description="Financial year start date (YYYY-MM-DD); defaults to current FY 1-Apr",
    ),
    current_user: dict = Depends(require_roles("ADMIN", "ACCOUNTANT")),
):
    """FIN-11: Check whether TDS applies on a vendor payment given cumulative
    spend to that vendor in the current financial year.

    Returns the threshold status including whether TDS must be deducted,
    the taxable base (amount above the threshold), and the computed TDS.
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Determine current FY start.
    # BUG-104: this carried its OWN `now.month >= 4` financial-year rule off
    # `datetime.utcnow()`. Railway runs UTC, so between 00:00 and 05:30 IST on
    # 1 April that rule still returns the PREVIOUS financial year -- and this
    # figure decides whether the s.194Q / 206C TDS threshold has been crossed,
    # so a whole extra year of payments got counted into the cumulative and TDS
    # was deducted (or the threshold declared crossed) on the wrong basis.
    # ist.fy_start_year_ist is the single definition of the April rule.
    fy_default = datetime(fy_start_year_ist(), 4, 1)
    if fy_start:
        from ...services.ap_engine import parse_date as _pd

        fy_start_dt = _pd(fy_start) or fy_default
    else:
        fy_start_dt = fy_default

    # Sum all payments to this vendor in the current FY.
    # `payment_date` is a DATE-ONLY ISO string ('2026-04-01'), so the bound has
    # to be date-only too: the old `fy_start_dt.isoformat()` produced
    # '2026-04-01T00:00:00', which sorts AFTER '2026-04-01' and silently
    # excluded every payment made on 1 April from the FY cumulative.
    try:
        past_payments = list(
            db.get_collection("vendor_payments").find(
                {
                    "vendor_id": vendor_id,
                    "payment_date": {"$gte": fy_start_dt.date().isoformat()},
                },
                {"amount": 1, "tds_amount": 1, "_id": 0},
            )
        )
    except Exception:
        past_payments = []

    cumulative_fy = sum(
        float(p.get("amount") or 0) + float(p.get("tds_amount") or 0)
        for p in past_payments
    )

    # Fetch admin-edited TDS rate overrides (fail-soft)
    overrides = None
    try:
        cfg = db.get_collection("tds_rate_config").find_one({}, {"_id": 0})
        if cfg:
            overrides = cfg.get("rates")
    except Exception:
        pass

    result = ap_engine.tds_threshold_status(
        section=section,
        cumulative_paid_fy=cumulative_fy,
        current_payment=current_payment,
        overrides=overrides,
    )
    result["vendor_id"] = vendor_id
    result["fy_start"] = fy_start_dt.date().isoformat()
    result["cumulative_fy_payments"] = round(cumulative_fy, 2)
    return result


@router.get("/tds/26q-export")
async def export_26q(
    fy: Optional[int] = Query(
        None,
        description="Financial year start (e.g. 2025 for FY 2025-26). Defaults to current FY.",
    ),
    quarter: Optional[int] = Query(
        None,
        ge=1,
        le=4,
        description="Quarter (1-4). If omitted, returns all quarters of the FY.",
    ),
    current_user: dict = Depends(require_roles("ADMIN", "ACCOUNTANT")),
):
    """FIN-11: Export TDS deduction data for quarterly 26Q (TDS on payments)
    and 27EQ (TCS under 206C) returns.

    Returns structured rows grouped by FY and quarter, plus a summary.
    Accountant uses this to file the quarterly returns with the Income Tax dept.
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    # BUG-104: same private `now.month >= 4` FY rule off the UTC box clock as
    # the threshold endpoint above. On 1 April 00:00-05:30 IST the accountant
    # opening the 26Q export got the PRIOR year's return. Use the one
    # definition. The bounds are date-only because `payment_date` is a
    # date-only ISO string -- '2026-04-01T00:00:00' sorts after '2026-04-01',
    # which dropped every 1-April payment out of Q1 of the return.
    target_fy = fy if fy else fy_start_year_ist()
    fy_start = datetime(target_fy, 4, 1)
    fy_end = datetime(target_fy + 1, 3, 31, 23, 59, 59)

    try:
        payments = list(
            db.get_collection("vendor_payments").find(
                {
                    "payment_date": {
                        "$gte": fy_start.date().isoformat(),
                        "$lte": fy_end.date().isoformat(),
                    },
                    "tds_amount": {"$gt": 0},
                },
                {"_id": 0},
            )
        )
    except Exception:
        payments = []

    # Enrich with vendor name/PAN from the vendors collection (best-effort).
    try:
        vendor_ids = list({p.get("vendor_id") for p in payments if p.get("vendor_id")})
        vendor_docs = list(
            db.get_collection("vendors").find(
                {"vendor_id": {"$in": vendor_ids}},
                {"vendor_id": 1, "name": 1, "pan": 1, "_id": 0},
            )
        )
        vendor_map = {v["vendor_id"]: v for v in vendor_docs}
        for p in payments:
            vdoc = vendor_map.get(p.get("vendor_id"), {})
            p.setdefault("vendor_name", vdoc.get("name", ""))
            p.setdefault("vendor_pan", vdoc.get("pan", ""))
    except Exception:
        pass

    export = ap_engine.build_26q_export(payments)

    if quarter:
        fy_key = f"{target_fy}-{(target_fy + 1) % 100:02d}"
        q_key = f"Q{quarter}"
        return {
            "fy": fy_key,
            "quarter": q_key,
            "form_26q": export["form_26q"].get(fy_key, {}).get(q_key, []),
            "form_27eq": export["form_27eq"].get(fy_key, {}).get(q_key, []),
            "summary": export["summary"],
        }
    return export
