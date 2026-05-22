"""
IMS 2.0 - Catalog Autopilot (Phase 1)
=====================================
Given a BRAND + MODEL (+ optional color/size), find candidate product matches
from prioritised sources, score them, and let a human approve before anything
is published. This module holds the PURE, testable brain (model normalization,
confidence scoring, copyright gating, source priority) plus a fail-soft
provider framework.

Source priority (highest first), per the operator's requirement:
  1. The brand's own regional/country site (e.g. Ray-Ban India) - AUTHORIZED
  2. Supplier/dealer portals we have accounts on (myluxottica) - AUTHORIZED
  3. Other authorized distributors - AUTHORIZED
  4. Marketplaces / competitors (Amazon, Flipkart, Google) - UNVERIFIED

Phase 1 ships the pipeline skeleton + a WORKING internal source (our own BVI
e-commerce catalog, so "do we already list this model online?" works with no
credentials) and fail-soft scaffolds for the credentialed web sources, which
get live-wired in Phase 1b once portal creds are configured.

Copyright stance: images from AUTHORIZED sources may be used; images from
UNVERIFIED sources are SPECS-ONLY unless a reviewer explicitly confirms rights.
Editing/cropping does NOT clear copyright, so we never auto-use unverified art.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

AUTHORIZED = "AUTHORIZED"
UNVERIFIED = "UNVERIFIED"

# Confidence weights. Visual similarity (vision model) lands in Phase 2; in
# Phase 1 we score on text signals and renormalize over the factors we can
# actually assess, so a brand+model-only query still scores cleanly.
WEIGHTS = {"model": 0.45, "color": 0.25, "size": 0.15, "brand": 0.15}


def normalize_model(value: Any) -> str:
    """Canonical model key: uppercase alphanumerics only.
    'B 4291', 'b-4291' and 'B4291' all collapse to 'B4291'."""
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).upper()


def score_candidate(query: Dict[str, Any], cand: Dict[str, Any]) -> Dict[str, Any]:
    """Weighted confidence in [0,1] + a per-field matched map. A query field
    that wasn't provided is treated as neutral (doesn't penalise)."""
    matched: Dict[str, bool] = {}
    parts: List[tuple] = []

    qm = normalize_model(query.get("model"))
    cm = normalize_model(cand.get("model"))
    if qm:
        if cm and (qm == cm):
            s = 1.0
        elif cm and (qm in cm or cm in qm):
            s = 0.6
        else:
            s = 0.0
        matched["model"] = s >= 0.6
        parts.append((WEIGHTS["model"], s))

    qc = _norm(query.get("color"))
    if qc:
        cc = _norm(cand.get("color")) + " " + _norm(cand.get("color_name"))
        s = 1.0 if qc and qc in cc else 0.0
        matched["color"] = s == 1.0
        parts.append((WEIGHTS["color"], s))

    qs = normalize_model(query.get("size"))
    if qs:
        cs = normalize_model(cand.get("size"))
        s = 1.0 if cs and (qs == cs or qs in cs) else 0.0
        matched["size"] = s == 1.0
        parts.append((WEIGHTS["size"], s))

    qb = _norm(query.get("brand"))
    if qb:
        cb = _norm(cand.get("brand"))
        s = 1.0 if cb and (qb == cb or qb in cb or cb in qb) else 0.0
        matched["brand"] = s == 1.0
        parts.append((WEIGHTS["brand"], s))

    total_w = sum(w for w, _ in parts)
    score = round(sum(w * s for w, s in parts) / total_w, 4) if total_w else 0.0
    return {"score": score, "matched": matched}


def image_use_allowed(source_class: str, rights_confirmed: bool = False) -> bool:
    """AUTHORIZED sources -> images usable. UNVERIFIED -> only if a reviewer
    explicitly confirmed we have the right to use the image."""
    if source_class == AUTHORIZED:
        return True
    return bool(rights_confirmed)


# ---------------------------------------------------------------------------
# Source providers (fail-soft). Each returns a list of candidate dicts.
# ---------------------------------------------------------------------------


def _ecommerce_configured() -> bool:
    return bool(os.getenv("ECOMMERCE_DATABASE_URL"))


def search_internal_catalog(brand: str, model: str, limit: int = 25) -> List[Dict[str, Any]]:
    """WORKING source: our own BVI e-commerce catalog. Answers 'do we already
    list this model online?' (dedup + enrichment). Read-only, fail-soft."""
    if not _ecommerce_configured():
        return []
    try:
        from .online_catalog import _connect  # lazy; shares the bridge conn
    except Exception:  # noqa: BLE001
        return []
    conn = _connect()
    if conn is None:
        return []
    nmodel = normalize_model(model)
    if not nmodel:
        return []
    out: List[Dict[str, Any]] = []
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.brand, p."modelNo", p."fullModelNo", p."productName",
                       p.status, p."shopifyProductId", p.category, p.shape, p.gender
                FROM "Product" p
                WHERE (%s = '' OR upper(p.brand) LIKE upper(%s))
                  AND upper(regexp_replace(
                        coalesce(p."fullModelNo", p."modelNo", ''),
                        '[^[:alnum:]]', '', 'g')) LIKE %s
                LIMIT %s
                """,
                (
                    brand or "",
                    f"%{brand}%" if brand else "%",
                    f"%{nmodel}%",
                    int(limit),
                ),
            )
            for r in cur.fetchall():
                out.append(
                    {
                        "source": "internal_bvi",
                        "source_class": AUTHORIZED,
                        "url": None,
                        "title": r[4] or f"{r[1]} {r[2] or ''}".strip(),
                        "brand": r[1],
                        "model": r[3] or r[2],
                        "color": None,
                        "size": None,
                        "image_urls": [],
                        "specs": {"category": r[7], "shape": r[8], "gender": r[9]},
                        "existing_status": r[5],
                        "existing_shopify_product_id": r[6],
                    }
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("[AUTOPILOT] internal catalog search failed: %s", e)
        return []
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return out


# Brand -> regional site (India first). Extend freely; live scraping is Phase 1b.
DEFAULT_BRAND_SITES: Dict[str, str] = {
    "RAY-BAN": "https://www.ray-ban.com/india",
    "RAYBAN": "https://www.ray-ban.com/india",
    "OAKLEY": "https://in.oakley.com",
    "VOGUE": "https://www.vogue-eyewear.com/in",
    "PERSOL": "https://www.persol.com/india",
}


def _provider_status() -> List[Dict[str, Any]]:
    """What each source is and whether it's active right now (UI surfaces this
    so the operator knows which sources will run)."""
    return [
        {
            "name": "brand_site",
            "label": "Brand regional site (India-first)",
            "source_class": AUTHORIZED,
            "priority": 1,
            "enabled": False,
            "reason": "Live scraping wired in Phase 1b (per-brand selectors).",
        },
        {
            "name": "myluxottica",
            "label": "myLuxottica dealer portal",
            "source_class": AUTHORIZED,
            "priority": 2,
            "enabled": bool(os.getenv("MYLUXOTTICA_USER")),
            "reason": (
                "Set MYLUXOTTICA_USER/MYLUXOTTICA_PASS to enable (Phase 1b auth scrape)."
                if not os.getenv("MYLUXOTTICA_USER")
                else "Credentials present."
            ),
        },
        {
            "name": "internal_bvi",
            "label": "Our online catalog (dedup/enrich)",
            "source_class": AUTHORIZED,
            "priority": 3,
            "enabled": _ecommerce_configured(),
            "reason": "Searches existing BVI products by brand + model.",
        },
        {
            "name": "marketplace",
            "label": "Marketplaces / web (specs-only by default)",
            "source_class": UNVERIFIED,
            "priority": 4,
            "enabled": False,
            "reason": "Last resort; needs a search API key. Images need rights confirmation.",
        },
    ]


def run_search(
    brand: str, model: str, color: str = "", size: str = "", limit: int = 25
) -> Dict[str, Any]:
    """Run all ENABLED providers in priority order, score + sort candidates.
    Pure orchestration; persistence is the router's job. Always fail-soft."""
    query = {"brand": brand, "model": model, "color": color, "size": size}
    candidates: List[Dict[str, Any]] = []

    # Priority 3 (internal) is the only live source in Phase 1; the higher-
    # priority credentialed web sources return nothing until Phase 1b.
    for cand in search_internal_catalog(brand, model, limit=limit):
        scored = score_candidate(query, cand)
        cand.update(scored)
        candidates.append(cand)

    candidates.sort(key=lambda c: c.get("score", 0), reverse=True)
    return {
        "query": query,
        "candidates": candidates,
        "sources": _provider_status(),
        "candidate_count": len(candidates),
    }
