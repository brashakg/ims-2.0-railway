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
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


def ecommerce_db_configured() -> bool:
    """True when the e-commerce Postgres URL is configured."""
    return bool(os.getenv("ECOMMERCE_DATABASE_URL"))


def normalize_sku(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


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
