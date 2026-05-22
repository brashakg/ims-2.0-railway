"""
IMS 2.0 - Online catalog bridge (IMS Mongo <-> e-commerce BVI Postgres)
=======================================================================
The e-commerce app (bettervision-inventory) now lives in the SAME Railway
project as IMS and owns the online Shopify catalog in Postgres. This module
lets the IMS backend read that Postgres READ-ONLY to answer one question per
SKU: "is this product online (in Shopify), and how much online stock is there?"

Design rules:
- Fully FAIL-SOFT. Missing env / driver / DB down -> empty result, never raise,
  never break the existing IMS endpoints.
- Read-only. We never write to the e-commerce DB.
- Connection string comes from ECOMMERCE_DATABASE_URL (a Railway reference to
  the Postgres service in the same project). Unset locally -> no-op.
- Match key is SKU (the canonical id shared by IMS products and BVI variants);
  storeBarcode is a future fallback.

Tables/columns are Prisma-generated (PascalCase, quoted): "ProductVariant"
(sku, productId, id), "VariantLocation" (variantId, quantity), "Product"
(id, status, shopifyProductId).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


def ecommerce_db_configured() -> bool:
    """True when the e-commerce Postgres URL is configured."""
    return bool(os.getenv("ECOMMERCE_DATABASE_URL"))


def normalize_sku(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


# A physical store barcode here is numeric, 4-14 digits (e.g. "00050567" or a
# 13-digit EAN). Excel often coerces these to floats, leaving a trailing ".0".
_BARCODE_RE = re.compile(r"^\d{4,14}$")


def clean_barcode(value: Any) -> str:
    """Normalize a spreadsheet barcode cell; return '' if it's not a plausible
    barcode (filters junk like 'SHEET'/'ACETATE' that bled in from other cols)."""
    s = str(value).strip() if value not in (None, "") else ""
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s if _BARCODE_RE.match(s) else ""


def map_rows(rows: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    """Pure: turn DB rows (sku, online_stock, status, pushed) into the per-SKU
    response dict. A product is 'online' if it's pushed to Shopify OR PUBLISHED."""
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        sku, online_stock, status, pushed = row
        sku = normalize_sku(sku)
        if not sku:
            continue
        online = bool(pushed) or (str(status or "").upper() == "PUBLISHED")
        out[sku] = {
            "online": online,
            "online_stock": int(online_stock or 0),
            "status": status,
        }
    return out


def _connect():
    """Open a short-lived read-only connection to the e-commerce Postgres, or
    None if unavailable. Lazy psycopg2 import so a missing driver never breaks
    backend startup."""
    url = os.getenv("ECOMMERCE_DATABASE_URL")
    if not url:
        return None
    try:
        import psycopg2  # lazy import — optional dependency
    except Exception as e:  # noqa: BLE001
        logger.warning("[ONLINE_CATALOG] psycopg2 not available: %s", e)
        return None
    try:
        return psycopg2.connect(url, connect_timeout=5)
    except Exception as e:  # noqa: BLE001
        logger.warning("[ONLINE_CATALOG] connect failed: %s", e)
        return None


# Match the caller's identifier against ANY of the three variant keys a
# physical-store product might carry: sku, storeBarcode (the canonical
# store<->online reconciliation key per the BVI schema) or barcode (GTIN).
# We key the result by the REQUESTED identifier (req.key) so the caller can
# map the response straight back onto whatever id it sent. The 4-column row
# shape (key, online_stock, status, pushed) is preserved so map_rows() and
# its unit tests are unchanged.
_QUERY = """
    WITH req(key) AS (SELECT DISTINCT unnest(%s::text[]))
    SELECT req.key,
           COALESCE(SUM(vl.quantity), 0) AS online_stock,
           MAX(p.status) AS status,
           bool_or(p."shopifyProductId" IS NOT NULL) AS pushed
    FROM req
    JOIN "ProductVariant" v
      ON v.sku = req.key OR v."storeBarcode" = req.key OR v.barcode = req.key
    JOIN "Product" p ON p.id = v."productId"
    LEFT JOIN "VariantLocation" vl ON vl."variantId" = v.id
    GROUP BY req.key
"""


def online_status_for_skus(skus: List[str]) -> Dict[str, Dict[str, Any]]:
    """Return {sku: {online, online_stock, status}} for the given SKUs that
    exist in the e-commerce catalog. Empty dict on any failure (fail-soft)."""
    clean = sorted({normalize_sku(s) for s in (skus or []) if normalize_sku(s)})
    if not clean:
        return {}
    conn = _connect()
    if conn is None:
        return {}
    try:
        with conn, conn.cursor() as cur:
            cur.execute(_QUERY, (clean,))
            return map_rows(cur.fetchall())
    except Exception as e:  # noqa: BLE001
        logger.warning("[ONLINE_CATALOG] query failed: %s", e)
        return {}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def online_summary() -> Dict[str, Any]:
    """Small health/summary for diagnostics: configured + reachable + counts."""
    if not ecommerce_db_configured():
        return {"configured": False, "reachable": False}
    conn = _connect()
    if conn is None:
        return {"configured": True, "reachable": False}
    try:
        with conn, conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM "Product"')
            products = cur.fetchone()[0]
            cur.execute('SELECT count(*) FROM "ProductVariant"')
            variants = cur.fetchone()[0]
            # Physical-barcode coverage on the online side. Because the chosen
            # store<->online match key is the physical barcode, this sizes the
            # reconciliation: how many online variants already carry a
            # storeBarcode / barcode that an IMS product could match.
            cur.execute(
                'SELECT '
                "count(*) FILTER (WHERE \"storeBarcode\" IS NOT NULL AND \"storeBarcode\" <> ''), "
                "count(*) FILTER (WHERE barcode IS NOT NULL AND barcode <> '') "
                'FROM "ProductVariant"'
            )
            cov = cur.fetchone()
            variants_with_store_barcode = int(cov[0] or 0)
            variants_with_barcode = int(cov[1] or 0)
            # A few real identifiers so ops can sanity-check the SKU/barcode
            # match end-to-end (these are catalog ids, not sensitive data).
            cur.execute(
                'SELECT sku, "storeBarcode", barcode FROM "ProductVariant" '
                "WHERE sku IS NOT NULL ORDER BY sku LIMIT 5"
            )
            sample = [
                {"sku": r[0], "store_barcode": r[1], "barcode": r[2]}
                for r in cur.fetchall()
            ]
        return {
            "configured": True,
            "reachable": True,
            "online_products": int(products),
            "online_variants": int(variants),
            "variants_with_store_barcode": variants_with_store_barcode,
            "variants_with_barcode": variants_with_barcode,
            "sample": sample,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("[ONLINE_CATALOG] summary failed: %s", e)
        return {"configured": True, "reachable": False}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def reconcile_store_barcodes(
    pairs: Dict[str, Any], apply: bool = False, only_empty: bool = True
) -> Dict[str, Any]:
    """One-time reconciliation: fill ProductVariant.storeBarcode from an
    external SKU -> barcode map (e.g. the store's master spreadsheet).

    Safety contract:
    - WRITES ONLY the storeBarcode column, matched by exact sku.
    - By default (only_empty=True) never overwrites a storeBarcode that's
      already set.
    - apply=False (default) is a DRY RUN — counts what would change, writes
      nothing.
    - Barcodes are validated/cleaned (clean_barcode); junk is skipped.
    storeBarcode is never pushed to Shopify (it's the in-store physical code),
    so this is safe for the online catalog.
    """
    cleaned: Dict[str, str] = {}
    invalid = 0
    for sku, bc in (pairs or {}).items():
        nsku = normalize_sku(sku)
        nbc = clean_barcode(bc)
        if not nsku:
            continue
        if not nbc:
            invalid += 1
            continue
        cleaned[nsku] = nbc

    if not cleaned:
        return {"matched": 0, "updated": 0, "invalid_barcode": invalid, "applied": apply}

    conn = _connect()
    if conn is None:
        return {"error": "e-commerce DB unavailable", "applied": False}

    matched = updated = skipped_existing = no_match = 0
    sample: List[Dict[str, str]] = []
    try:
        with conn:
            with conn.cursor() as cur:
                skus = list(cleaned.keys())
                for i in range(0, len(skus), 1000):
                    chunk = skus[i : i + 1000]
                    cur.execute(
                        'SELECT sku, "storeBarcode" FROM "ProductVariant" WHERE sku = ANY(%s)',
                        (chunk,),
                    )
                    existing = {r[0]: r[1] for r in cur.fetchall()}
                    for sku in chunk:
                        if sku not in existing:
                            no_match += 1
                            continue
                        matched += 1
                        current = existing[sku]
                        if only_empty and current not in (None, ""):
                            skipped_existing += 1
                            continue
                        if apply:
                            cur.execute(
                                'UPDATE "ProductVariant" SET "storeBarcode" = %s WHERE sku = %s',
                                (cleaned[sku], sku),
                            )
                        updated += 1
                        if len(sample) < 10:
                            sample.append({"sku": sku, "store_barcode": cleaned[sku]})
        return {
            "applied": apply,
            "input_pairs": len(pairs or {}),
            "valid_pairs": len(cleaned),
            "invalid_barcode": invalid,
            "matched_in_bvi": matched,
            "no_match_in_bvi": no_match,
            "skipped_existing": skipped_existing,
            ("updated" if apply else "would_update"): updated,
            "sample": sample,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("[ONLINE_CATALOG] reconcile failed: %s", e)
        return {"error": str(e)[:200], "applied": False}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
