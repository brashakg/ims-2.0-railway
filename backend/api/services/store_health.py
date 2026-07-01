"""
IMS 2.0 - Online-store "Store health" readiness checks (read-only)
==================================================================
The pre-cutover readiness dashboard behind the Online Store shell's "Store
health" card (SECTIONS[8]). It answers a single question for the owner: BEFORE
any product goes live online, is the catalog actually ready?

Three checks, one composite score:

  1. orphan_skus()        -- online-eligible products that are NOT ready to list:
                             no online mapping (Shopify gid), not in any
                             collection, or missing the catalog spine link.
  2. attribute_coverage() -- % of online-eligible products carrying each of the
                             storefront-critical attributes (HSN, category,
                             brand, barcode, image) + an overall coverage %.
  3. barcode_match_rate() -- % of products whose barcode is present AND unique
                             (a duplicate barcode breaks scan-to-advance + the
                             Shopify SKU/barcode match on the cutover).

readiness_score() folds the three into a 0-100 composite plus a concrete
fixes_needed list ("12 products missing HSN", ...) the owner can act on.

CONTRACT (mirrors the rest of the online-store bridge):
- 100% FAIL-SOFT. No DB / driver error -> zeros + empty lists, NEVER raises,
  never 500s the card. Read-only throughout.
- REUSES the existing pieces where they already compute something:
  online_sync_health.parity_summary() for the pushed-vs-total mapping counts.
  Everything attribute-side is derived here from the product master docs.

"Online-eligible" = the billing/catalog spine (`catalog_products`) rows that
carry an `ecom` sub-doc (the same definition the module summary's `products`
count uses). When that collection is empty we fall back to the physical
`products` master so the checks still say something useful pre-consolidation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Cap the per-check scan so the card stays cheap on a large catalog.
_SCAN_LIMIT = 5000

# The storefront-critical attributes we score coverage on, in display order.
# Each maps to the doc field(s) that satisfy it (first non-empty wins).
_COVERAGE_ATTRS = ("hsn", "category", "brand", "barcode", "image")


def _coll(db, name: str):
    """Subscript collection access that works on both a real pymongo Database
    and the in-memory MockDatabase. Fail-soft -> None."""
    try:
        return db[name] if db is not None else None
    except Exception:  # noqa: BLE001
        return None


def _online_eligible_products(db, limit: int = _SCAN_LIMIT) -> List[Dict[str, Any]]:
    """The set of products the readiness checks run over.

    Prefers the catalog spine rows that carry an `ecom` sub-doc (online-eligible
    -- same definition as the module summary's `products` count). If that yields
    nothing (spine not populated / pre-consolidation) it falls back to the active
    physical `products` master so the checks are still meaningful. Fail-soft -> []."""
    if db is None:
        return []

    projection = {
        "_id": 0,
        "product_id": 1,
        "sku": 1,
        "brand": 1,
        "category": 1,
        "hsn_code": 1,
        "barcode": 1,
        "images": 1,
        "image": 1,
        "image_url": 1,
        "ecom": 1,
    }

    # 1) Catalog spine, online-eligible (has an ecom sub-doc).
    try:
        cat = _coll(db, "catalog_products")
        if cat is not None:
            rows = list(
                cat.find({"ecom": {"$exists": True}}, projection).limit(limit)
            )
            if rows:
                return rows
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STORE_HEALTH] catalog_products scan failed: %s", exc)

    # 2) Fallback: active physical product master.
    try:
        prods = _coll(db, "products")
        if prods is not None:
            return list(
                prods.find({"is_active": {"$ne": False}}, projection).limit(limit)
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STORE_HEALTH] products fallback scan failed: %s", exc)
    return []


def _has_image(doc: Dict[str, Any]) -> bool:
    """True when a product doc carries at least one image reference (any of the
    several shapes the catalog has used: images[], image, image_url, or an
    ecom.images[] / ecom.image)."""
    imgs = doc.get("images")
    if isinstance(imgs, (list, tuple)) and any(imgs):
        return True
    if doc.get("image") or doc.get("image_url"):
        return True
    ecom = doc.get("ecom") or {}
    if isinstance(ecom, dict):
        eimgs = ecom.get("images")
        if isinstance(eimgs, (list, tuple)) and any(eimgs):
            return True
        if ecom.get("image") or ecom.get("image_url"):
            return True
    return False


def _attr_present(doc: Dict[str, Any], attr: str) -> bool:
    """Whether one storefront-critical attribute is present on a product doc."""
    if attr == "hsn":
        return bool(str(doc.get("hsn_code") or "").strip())
    if attr == "category":
        return bool(str(doc.get("category") or "").strip())
    if attr == "brand":
        return bool(str(doc.get("brand") or "").strip())
    if attr == "barcode":
        return bool(str(doc.get("barcode") or "").strip())
    if attr == "image":
        return _has_image(doc)
    return False


def _has_online_mapping(doc: Dict[str, Any]) -> bool:
    """True when a product already carries a Shopify product gid (ecom mapping)."""
    ecom = doc.get("ecom") or {}
    if not isinstance(ecom, dict):
        return False
    gid = ecom.get("shopify_product_id")
    return bool(str(gid or "").strip())


def _skus_in_any_collection(db, limit: int = _SCAN_LIMIT) -> set:
    """The set of SKUs that belong to at least one collection. Reads the
    materialised `collection_products` membership view (keyed on sku), falling
    back to the `ecom_collections` member arrays. Fail-soft -> empty set."""
    out: set = set()
    if db is None:
        return out
    # Materialised membership view (unification step-13): one row per (handle, sku).
    try:
        cp = _coll(db, "collection_products")
        if cp is not None:
            for row in cp.find({}, {"_id": 0, "sku": 1}).limit(limit):
                sku = row.get("sku")
                if sku:
                    out.add(str(sku))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STORE_HEALTH] collection_products scan failed: %s", exc)
    if out:
        return out
    # Fallback: embedded member arrays on ecom_collections.
    try:
        ec = _coll(db, "ecom_collections")
        if ec is not None:
            for col in ec.find(
                {}, {"_id": 0, "product_skus": 1, "products": 1}
            ).limit(limit):
                for key in ("product_skus", "products"):
                    members = col.get(key)
                    if isinstance(members, (list, tuple)):
                        for m in members:
                            if isinstance(m, str):
                                out.add(m)
                            elif isinstance(m, dict) and m.get("sku"):
                                out.add(str(m["sku"]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STORE_HEALTH] ecom_collections scan failed: %s", exc)
    return out


# ===========================================================================
# The three checks (pure helpers over the eligible product set)
# ===========================================================================


def orphan_skus(db, limit: int = 50) -> Dict[str, Any]:
    """Online-eligible products that are NOT list-ready. A product is an orphan
    when ANY of:
      - no_mapping        : it has no Shopify product gid yet, OR
      - not_in_collection : it is in no collection (won't surface in nav), OR
      - missing_spine     : it has no usable catalog spine link (no sku).

    Returns a summary + a capped sample list for the UI:
        {
          "total": int,            # eligible products scanned
          "orphan_count": int,     # distinct orphaned products
          "no_mapping": int,
          "not_in_collection": int,
          "missing_spine": int,
          "orphans": [{"sku", "product_id", "reasons": [...]}]  # capped at `limit`
        }
    Fail-soft -> zeros / empty."""
    base = {
        "total": 0,
        "orphan_count": 0,
        "no_mapping": 0,
        "not_in_collection": 0,
        "missing_spine": 0,
        "orphans": [],
    }
    products = _online_eligible_products(db)
    if not products:
        return base

    in_collection = _skus_in_any_collection(db)
    no_mapping = 0
    not_in_collection = 0
    missing_spine = 0
    orphan_count = 0
    sample: List[Dict[str, Any]] = []

    for p in products:
        sku = str(p.get("sku") or "").strip()
        reasons: List[str] = []
        if not sku:
            reasons.append("missing_spine")
            missing_spine += 1
        if not _has_online_mapping(p):
            reasons.append("no_mapping")
            no_mapping += 1
        # A product with no sku can't be matched into a collection either, but we
        # don't double-count it as not_in_collection (missing_spine dominates).
        if sku and sku not in in_collection:
            reasons.append("not_in_collection")
            not_in_collection += 1
        if reasons:
            orphan_count += 1
            if len(sample) < max(0, limit):
                sample.append(
                    {
                        "sku": sku or None,
                        "product_id": str(p.get("product_id") or "") or None,
                        "reasons": reasons,
                    }
                )

    return {
        "total": len(products),
        "orphan_count": orphan_count,
        "no_mapping": no_mapping,
        "not_in_collection": not_in_collection,
        "missing_spine": missing_spine,
        "orphans": sample,
    }


def attribute_coverage(db) -> Dict[str, Any]:
    """Per-attribute + overall coverage across the online-eligible product set.

    Returns:
        {
          "total": int,
          "hsn_pct": float, "category_pct": float, "brand_pct": float,
          "barcode_pct": float, "image_pct": float,
          "overall_pct": float,           # mean of the five attribute %s
          "missing": {"hsn": int, "category": int, ...}  # count missing each attr
        }
    All percentages are 0-100 rounded to 1 dp. Fail-soft -> zeros."""
    products = _online_eligible_products(db)
    total = len(products)
    present = {a: 0 for a in _COVERAGE_ATTRS}
    if total == 0:
        out: Dict[str, Any] = {"total": 0, "overall_pct": 0.0, "missing": {}}
        for a in _COVERAGE_ATTRS:
            out[f"{a}_pct"] = 0.0
            out.setdefault("missing", {})[a] = 0
        return out

    for p in products:
        for a in _COVERAGE_ATTRS:
            if _attr_present(p, a):
                present[a] += 1

    out = {"total": total, "missing": {}}
    pcts: List[float] = []
    for a in _COVERAGE_ATTRS:
        pct = round(100.0 * present[a] / total, 1)
        out[f"{a}_pct"] = pct
        out["missing"][a] = total - present[a]
        pcts.append(pct)
    out["overall_pct"] = round(sum(pcts) / len(pcts), 1) if pcts else 0.0
    return out


def barcode_match_rate(db) -> Dict[str, Any]:
    """Share of online-eligible products whose barcode is present AND unique.

    A missing barcode blocks scan-to-advance; a DUPLICATE barcode is worse -- it
    makes a scan ambiguous and breaks the Shopify barcode match on the cutover.
    Both count against the match rate.

    Returns:
        {
          "total": int,
          "with_barcode": int,
          "missing_barcode": int,
          "duplicate_barcode": int,     # products sharing a barcode with another
          "unique_matched": int,        # present AND unique
          "match_pct": float            # unique_matched / total, 0-100
        }
    Fail-soft -> zeros."""
    products = _online_eligible_products(db)
    total = len(products)
    if total == 0:
        return {
            "total": 0,
            "with_barcode": 0,
            "missing_barcode": 0,
            "duplicate_barcode": 0,
            "unique_matched": 0,
            "match_pct": 0.0,
        }

    counts: Dict[str, int] = {}
    with_barcode = 0
    for p in products:
        bc = str(p.get("barcode") or "").strip()
        if bc:
            with_barcode += 1
            counts[bc] = counts.get(bc, 0) + 1

    duplicate_barcode = 0
    unique_matched = 0
    for p in products:
        bc = str(p.get("barcode") or "").strip()
        if not bc:
            continue
        if counts.get(bc, 0) > 1:
            duplicate_barcode += 1
        else:
            unique_matched += 1

    return {
        "total": total,
        "with_barcode": with_barcode,
        "missing_barcode": total - with_barcode,
        "duplicate_barcode": duplicate_barcode,
        "unique_matched": unique_matched,
        "match_pct": round(100.0 * unique_matched / total, 1),
    }


def _parity(db) -> Dict[str, Any]:
    """Thin wrapper over the existing parity oracle so store-health surfaces the
    same IMS-vs-Shopify pushed/missing counts without re-deriving them. Fail-soft."""
    try:
        from .online_sync_health import parity_summary

        return parity_summary(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STORE_HEALTH] parity reuse failed: %s", exc)
        return {"entities": {}, "ok": False}


def readiness_score(db) -> Dict[str, Any]:
    """Assemble the full store-health readiness envelope + composite 0-100 score.

    Composite (equal thirds of three sub-scores, each 0-100):
      - attribute coverage overall_pct
      - barcode match_pct
      - orphan-free rate: 100 * (1 - orphan_count / total)

    Plus a concrete `fixes_needed` list the owner can act on, largest first.

    Returns the shape the endpoint hands the FE verbatim:
        {
          "readiness_pct": float,
          "orphans": {...},                 # orphan_skus() summary + sample
          "coverage": {hsn_pct, category_pct, brand_pct, barcode_pct,
                       image_pct, overall_pct, total, missing},
          "barcode_match": {...},           # barcode_match_rate()
          "barcode_match_pct": float,       # convenience mirror
          "parity": {...},                  # reused online_sync_health.parity_summary
          "total_products": int,
          "fixes_needed": [{"issue", "count", "ids?"}],
        }
    Fully fail-soft -> a zeroed envelope, never raises."""
    orphans = orphan_skus(db)
    coverage = attribute_coverage(db)
    barcode = barcode_match_rate(db)
    parity = _parity(db)

    total = coverage.get("total", 0) or orphans.get("total", 0)

    coverage_score = float(coverage.get("overall_pct", 0.0) or 0.0)
    barcode_score = float(barcode.get("match_pct", 0.0) or 0.0)
    if total > 0:
        orphan_free = 100.0 * (1.0 - (orphans.get("orphan_count", 0) / total))
    else:
        orphan_free = 0.0
    orphan_free = max(0.0, min(100.0, orphan_free))

    readiness_pct = round((coverage_score + barcode_score + orphan_free) / 3.0, 1)

    # ---- Concrete fixes, largest count first --------------------------------
    fixes: List[Dict[str, Any]] = []
    _attr_label = {
        "hsn": "missing HSN code",
        "category": "missing category",
        "brand": "missing brand",
        "barcode": "missing barcode",
        "image": "missing an image",
    }
    missing = coverage.get("missing", {}) or {}
    for attr in _COVERAGE_ATTRS:
        cnt = int(missing.get(attr, 0) or 0)
        if cnt > 0:
            fixes.append({"issue": _attr_label[attr], "count": cnt, "check": attr})

    dup = int(barcode.get("duplicate_barcode", 0) or 0)
    if dup > 0:
        fixes.append(
            {"issue": "duplicate barcode (ambiguous scan)", "count": dup, "check": "barcode_dup"}
        )

    no_map = int(orphans.get("no_mapping", 0) or 0)
    if no_map > 0:
        fixes.append(
            {"issue": "not yet mapped to Shopify", "count": no_map, "check": "no_mapping"}
        )
    not_in_col = int(orphans.get("not_in_collection", 0) or 0)
    if not_in_col > 0:
        fixes.append(
            {
                "issue": "not in any collection",
                "count": not_in_col,
                "check": "not_in_collection",
            }
        )
    missing_spine = int(orphans.get("missing_spine", 0) or 0)
    if missing_spine > 0:
        fixes.append(
            {"issue": "missing catalog spine link", "count": missing_spine, "check": "missing_spine"}
        )

    fixes.sort(key=lambda f: f["count"], reverse=True)

    return {
        "readiness_pct": readiness_pct,
        "total_products": total,
        "orphans": orphans,
        "coverage": coverage,
        "barcode_match": barcode,
        "barcode_match_pct": barcode_score,
        "parity": parity,
        "fixes_needed": fixes,
        "sub_scores": {
            "coverage_pct": round(coverage_score, 1),
            "barcode_pct": round(barcode_score, 1),
            "orphan_free_pct": round(orphan_free, 1),
        },
    }


def store_health_envelope(db) -> Dict[str, Any]:
    """The exact payload GET /online-store/store-health returns. A fail-soft
    wrapper around readiness_score that guarantees the documented shape even if
    an unexpected error slips through (no DB -> zeros/empty, never 500)."""
    empty: Dict[str, Any] = {
        "readiness_pct": 0.0,
        "total_products": 0,
        "orphans": {
            "total": 0,
            "orphan_count": 0,
            "no_mapping": 0,
            "not_in_collection": 0,
            "missing_spine": 0,
            "orphans": [],
        },
        "coverage": {
            "total": 0,
            "hsn_pct": 0.0,
            "category_pct": 0.0,
            "brand_pct": 0.0,
            "barcode_pct": 0.0,
            "image_pct": 0.0,
            "overall_pct": 0.0,
            "missing": {},
        },
        "barcode_match": {
            "total": 0,
            "with_barcode": 0,
            "missing_barcode": 0,
            "duplicate_barcode": 0,
            "unique_matched": 0,
            "match_pct": 0.0,
        },
        "barcode_match_pct": 0.0,
        "parity": {"entities": {}, "ok": False},
        "fixes_needed": [],
        "sub_scores": {"coverage_pct": 0.0, "barcode_pct": 0.0, "orphan_free_pct": 0.0},
        "db_connected": db is not None,
    }
    if db is None:
        return empty
    try:
        out = readiness_score(db)
        out["db_connected"] = True
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("[STORE_HEALTH] readiness assemble failed: %s", exc)
        return empty
