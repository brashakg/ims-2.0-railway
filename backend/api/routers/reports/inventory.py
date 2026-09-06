"""Inventory reports (summary, valuation, tax-code audit) and eye tests."""

from fastapi import Depends, Query
from typing import Any, Dict, Optional
from datetime import date
from ..auth import get_current_user, require_roles
from ...dependencies import (
    get_stock_repository,
    get_eye_test_repository,
    get_db,
    validate_store_access,
)
from ._shared import (
    _REPORT_FINANCE_ROLES,
    _row_category,
    _stock_category_map,
    router,
)

# ============================================================================
# INVENTORY REPORTS
# ============================================================================


@router.get("/inventory/summary")
async def inventory_summary(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get inventory summary"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    stock_repo = get_stock_repository()

    if stock_repo is None:
        return {
            "summary": {
                "total_items": 0,
                "total_quantity": 0,
                "total_value": 0,
                "low_stock_count": 0,
                "out_of_stock_count": 0,
            }
        }

    # Get all stock
    all_stock = stock_repo.find_many({"store_id": active_store}, limit=0)
    low_stock = stock_repo.find_low_stock(active_store, threshold=5)

    total_value = sum(
        (s.get("quantity", 0) * s.get("cost_price", 0)) for s in all_stock
    )

    out_of_stock = [s for s in all_stock if s.get("quantity", 0) <= 0]

    return {
        "summary": {
            "total_items": len(all_stock),
            "total_quantity": sum(s.get("quantity", 0) for s in all_stock),
            "total_value": round(total_value, 2),
            "low_stock_count": len(low_stock) if low_stock else 0,
            "out_of_stock_count": len(out_of_stock),
        }
    }


@router.get("/inventory/valuation")
async def inventory_valuation(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Get inventory valuation by category (management report; store-scoped)."""
    active_store = validate_store_access(store_id, current_user)
    stock_repo = get_stock_repository()

    if stock_repo is None:
        return {"valuation": {"by_category": [], "total": 0}}

    all_stock = stock_repo.find_many({"store_id": active_store}, limit=0)

    # category lives on the product master, not the stock doc -> join it so the
    # by-category split is real (FRAME etc.) instead of everything in "Other".
    cat_map = _stock_category_map(all_stock)

    # Group by category
    by_category = {}
    for item in all_stock:
        category = _row_category(item, cat_map)
        if category not in by_category:
            by_category[category] = {"category": category, "quantity": 0, "value": 0}
        by_category[category]["quantity"] += item.get("quantity", 0)
        by_category[category]["value"] += item.get("quantity", 0) * item.get(
            "cost_price", 0
        )

    total = sum(c["value"] for c in by_category.values())

    return {
        "valuation": {
            "by_category": list(by_category.values()),
            "total": round(total, 2),
        }
    }


@router.get("/inventory/tax-code-audit")
async def tax_code_audit(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Go-live readiness check: flag every product whose stored HSN code or GST
    rate disagrees with the canonical table for its category.

    Why: products may have been bulk-loaded with the wrong tax code (the classic
    case is a sunglass tagged 5% when non-corrective sunglasses are 18%, or a
    blank/unknown category that silently falls back to 5%). POS bills whatever is
    on the product, so a wrong code here means wrong GST on every sale of it.
    This is READ-ONLY — it never edits a product; it produces the worklist the
    catalog manager fixes before the first live invoice.

    A product is flagged when, for its category:
      * `category` is blank/unknown (not in the canonical table) -> needs a
        category before it can be tax-checked at all; or
      * stored `gst_rate` != the canonical rate for that category; or
      * stored `hsn_code` != the canonical 6-digit HSN for that category
        (a 4-digit prefix of the canonical code is accepted: small businesses
        <=Rs 5 Cr may legitimately use 4-digit HSNs).

    Response:
        {
          "data": [ { product_id, sku, name, category, stored_hsn, stored_gst,
                      expected_hsn, expected_gst, issues: [str], severity } ],
          "summary": { total_products, flagged, ok, by_issue: {...},
                       uncategorized, gst_mismatch, hsn_mismatch },
        }
    """
    from ...services.gst_rates import (
        GST_CATEGORY_TABLE,
        gst_rate_for_category,
        hsn_for_category,
    )

    db = get_db()
    empty = {
        "data": [],
        "summary": {
            "total_products": 0,
            "flagged": 0,
            "ok": 0,
            "uncategorized": 0,
            "gst_mismatch": 0,
            "hsn_mismatch": 0,
        },
    }
    if db is None or not getattr(db, "is_connected", True):
        return empty

    products = db.get_collection("products")
    if products is None:
        return empty

    query: Dict[str, Any] = {"is_active": {"$ne": False}}
    if store_id:
        # Products are catalog-global, but some deployments scope by store; honour
        # it when present without excluding global rows.
        query = {
            "is_active": {"$ne": False},
            "$or": [
                {"store_id": store_id},
                {"store_id": {"$exists": False}},
                {"store_id": None},
            ],
        }

    data = []
    total = 0
    gst_mismatch = 0
    hsn_mismatch = 0
    uncategorized = 0

    for p in products.find(query):
        total += 1
        category = (p.get("category") or "").strip()
        stored_hsn = str(p.get("hsn_code") or "").strip()
        stored_gst = p.get("gst_rate")

        issues = []
        known = bool(category) and category.strip().upper() in GST_CATEGORY_TABLE
        expected_gst = gst_rate_for_category(category) if known else None
        expected_hsn = hsn_for_category(category) if known else None

        if not known:
            uncategorized += 1
            issues.append(
                "Blank or unrecognized category — set a category so the tax code "
                "can be verified."
            )
        else:
            # GST rate mismatch (tolerate float vs int: 5 == 5.0).
            try:
                stored_gst_f = float(stored_gst) if stored_gst is not None else None
            except (TypeError, ValueError):
                stored_gst_f = None
            if stored_gst_f is None:
                gst_mismatch += 1
                issues.append(f"No GST rate set (expected {expected_gst}%).")
            elif abs(stored_gst_f - float(expected_gst)) > 0.001:
                gst_mismatch += 1
                issues.append(
                    f"GST rate {stored_gst_f}% does not match the {expected_gst}% "
                    f"expected for {category}."
                )

            # HSN mismatch — accept a 4-digit prefix of the canonical 6-digit code.
            if expected_hsn:
                if not stored_hsn:
                    hsn_mismatch += 1
                    issues.append(f"No HSN code set (expected {expected_hsn}).")
                elif stored_hsn != expected_hsn and not expected_hsn.startswith(
                    stored_hsn
                ):
                    hsn_mismatch += 1
                    issues.append(
                        f"HSN {stored_hsn} does not match the expected "
                        f"{expected_hsn} for {category}."
                    )

        if issues:
            # Severity: a wrong/blank GST rate is the costly one (bills wrong tax);
            # an HSN-only or category issue is high but slightly less urgent.
            has_gst_issue = any("GST" in i for i in issues)
            data.append(
                {
                    "product_id": p.get("product_id") or str(p.get("_id", "")),
                    "sku": p.get("sku") or p.get("barcode") or "",
                    "name": p.get("name") or p.get("product_name") or "",
                    "category": category or None,
                    "stored_hsn": stored_hsn or None,
                    "stored_gst": stored_gst,
                    "expected_hsn": expected_hsn,
                    "expected_gst": expected_gst,
                    "issues": issues,
                    "severity": "CRITICAL" if has_gst_issue else "HIGH",
                }
            )

    # Worst first: CRITICAL (wrong GST) above HIGH (HSN/category only).
    data.sort(key=lambda r: (r["severity"] != "CRITICAL", r["category"] or ""))

    flagged = len(data)
    return {
        "data": data,
        "summary": {
            "total_products": total,
            "flagged": flagged,
            "ok": total - flagged,
            "uncategorized": uncategorized,
            "gst_mismatch": gst_mismatch,
            "hsn_mismatch": hsn_mismatch,
        },
    }


@router.get("/clinical/eye-tests")
async def eye_test_report(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Eye test count report (by optometrist) for a date range.

    Queries the ``eye_tests`` collection via EyeTestRepository using the
    COMPLETED status + test_date string range (same lexicographic strategy
    as the Test-History page).  Returns each test record for the FE plus
    an aggregation by optometrist and a grand total.
    """
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    test_repo = get_eye_test_repository()
    if test_repo is None:
        return {"data": [], "by_optometrist": [], "total": 0}

    tests = test_repo.get_store_tests_in_range(
        store_id=active_store,
        from_date=from_date.isoformat(),
        to_date=to_date.isoformat(),
        status="COMPLETED",
        limit=1000,
    )

    by_optometrist: dict = {}
    for t in tests:
        optom_id = t.get("optometrist_id") or t.get("assigned_to") or "Unknown"
        optom_name = t.get("optometrist_name") or t.get("assigned_to_name") or optom_id
        if optom_id not in by_optometrist:
            by_optometrist[optom_id] = {
                "optometrist_id": optom_id,
                "optometrist_name": optom_name,
                "test_count": 0,
            }
        by_optometrist[optom_id]["test_count"] += 1

    return {
        "data": tests,
        "by_optometrist": list(by_optometrist.values()),
        "total": len(tests),
    }


