"""
IMS 2.0 - BVI catalog parity service (Postgres BVI <-> Mongo IMS)
=================================================================
The importable home of the parity ORACLE that scripts/bvi_parity_check.py
introduced for manual runs. The BVI -> IMS catalog sync is complete, but until
the old BVI app is decommissioned BOTH systems exist and can drift (someone
edits a product in BVI and IMS never hears about it). This module powers:

  1. The manual CLI (scripts/bvi_parity_check.py) -- which now imports its
     comparison core from HERE, so the logic exists exactly once.
  2. The nightly parity monitor (SENTINEL agent tick, IST ~02:30) via
     run_parity_snapshot() / run_and_record_parity().
  3. GET /api/v1/admin/online-store/parity -- which returns the latest stored
     nightly snapshot alongside the existing IMS-vs-Shopify parity block.

Design rules (same as api/services/online_catalog.py):
  * STRICTLY READ-ONLY against the BVI Postgres. Connections are short-lived
    and opened with set_session(readonly=True) so the server itself rejects
    any accidental write.
  * Fully FAIL-SOFT for the monitor paths: a missing env var, a missing
    driver, or an unreachable DB yields {"ok": False, "reason": ...} -- it
    NEVER raises, because the nightly agent tick must never take down the
    scheduler. (The CLI keeps its own fail-LOUD exit codes in the script.)
  * NEVER logs or returns a connection string or any secret.
  * Snapshots are stored in the `bvi_parity_snapshots` collection with a
    30-day retention trim applied on every write (mirrors SENTINEL's
    prune_health_checks pattern).
  * The stored report is COMPACT: sample lists are capped (default 25) so a
    badly-drifted catalog cannot bloat a snapshot document with thousands of
    SKUs. Counts are always exact; only the example lists are capped.
  * No emojis (Windows cp1252 safe).

Config:
  * BVI_PARITY_MONITOR=on|off       -- master switch, default ON.
  * BVI_PARITY_COUNT_TOLERANCE=<n>  -- product/variant count delta tolerated
                                       before drift is flagged (default 5).
  * BVI_DATABASE_URL / ECOMMERCE_DATABASE_URL -- the BVI Postgres URL
                                       (same resolution order as the CLI).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Collection the nightly snapshots land in.
PARITY_SNAPSHOT_COLLECTION = "bvi_parity_snapshots"

# Hard cap for sample lists inside a STORED snapshot (missing SKUs, barcode
# mismatches). The full lists remain available via the CLI's --json output.
SNAPSHOT_SAMPLE_LIMIT = 25

# Snapshots older than this are deleted on every write (fail-soft).
SNAPSHOT_RETENTION_DAYS = 30

# Default product/variant count delta tolerated before drift is flagged.
DEFAULT_COUNT_TOLERANCE = 5


# ===========================================================================
# Pure helpers (no I/O)
# ===========================================================================


def _norm_sku(v: Any) -> str:
    """Normalise a SKU for comparison: strip + upper-case. Empty stays empty."""
    if v is None:
        return ""
    return str(v).strip().upper()


def _norm_barcode(v: Any) -> str:
    """Normalise a barcode for comparison: strip only (barcodes are exact, but
    leading/trailing whitespace from a spreadsheet import is noise)."""
    if v is None:
        return ""
    return str(v).strip()


def is_durable_image_url(url: Any) -> bool:
    """True if the image URL points at a DURABLE host (survives BVI shutdown).

    A durable URL is an absolute http(s):// URL (Shopify CDN, S3, etc.). A
    LOCAL-DISK url is the BVI fallback "/uploads/<file>" (relative path served
    from the Next.js app's own disk) -- it 404s the moment BVI is turned off,
    so every such row is in the Phase-4 re-host scope.

    Anything blank/None is treated as NON-durable (it needs attention too).
    """
    if url is None:
        return False
    s = str(url).strip().lower()
    if not s:
        return False
    return s.startswith("http://") or s.startswith("https://")


# ===========================================================================
# Snapshot dataclasses -- the in-memory shapes the pure comparator consumes
# ===========================================================================


@dataclass
class BviSnapshot:
    """A read-only snapshot of the BVI Postgres catalog (counts + index lists).

    `variant_skus` / `variant_barcode_by_sku` are the full lists used for the
    SKU and barcode diffs. The image-url list drives the storage audit.
    Everything here is derived from SELECTs only.
    """

    products: int = 0
    variants: int = 0
    collections: int = 0
    menus: int = 0
    customers: int = 0
    orders: int = 0
    # variant identity, for the diff: list of skus
    variant_skus: List[str] = field(default_factory=list)
    # sku -> store_barcode (only variants that carry a storeBarcode)
    variant_barcode_by_sku: Dict[str, str] = field(default_factory=dict)
    # image urls (both ProductImage.url and VariantImage.url) for the storage audit
    image_urls: List[str] = field(default_factory=list)


@dataclass
class ImsSnapshot:
    """A read-only snapshot of the IMS Mongo catalog (counts + index maps)."""

    catalog_products: int = 0
    catalog_variants: int = 0
    ecom_collections: int = 0
    ecom_menus: int = 0
    customers: int = 0
    online_orders: int = 0
    # the set of SKUs present in catalog_variants (normalised)
    variant_skus: List[str] = field(default_factory=list)
    # sku -> barcode (IMS catalog_variants stores BVI storeBarcode as store_barcode
    # AND/OR the billing spine `products` carries `barcode`); we map sku -> the
    # IMS barcode we can find for that sku
    barcode_by_sku: Dict[str, str] = field(default_factory=dict)


# ===========================================================================
# PURE comparator -- unit-tested without a DB
# ===========================================================================


def build_parity_report(
    bvi: BviSnapshot,
    ims: ImsSnapshot,
    *,
    sample_limit: int = 25,
) -> Dict[str, Any]:
    """Compare a BVI snapshot to an IMS snapshot. PURE -- no I/O.

    Returns a structured report dict:
      {
        "counts": { entity: {bvi, ims, delta, match} ... },
        "sku_diff": {
            "bvi_total", "ims_total",
            "missing_in_ims": [skus...],   (in BVI variants, absent from IMS)
            "missing_count",
            "sample_missing": [...up to sample_limit...],
        },
        "barcode_diff": {
            "checked",                     (BVI variants carrying a storeBarcode)
            "mismatched": [{sku, bvi, ims}...],   (IMS barcode missing OR != BVI)
            "mismatched_count",
            "sample_mismatched": [...up to sample_limit...],
        },
        "image_storage": {
            "total", "durable", "local_disk",
            "local_disk_pct",
            "phase4_rehost_needed": bool,  (any local-disk url -> True)
        },
        "gate_pass": bool,   (counts match AND no missing SKU AND no barcode mismatch)
      }
    """

    # --- counts, per entity ---
    def _count_row(b: int, i: int) -> Dict[str, Any]:
        return {"bvi": b, "ims": i, "delta": i - b, "match": b == i}

    counts = {
        "products": _count_row(bvi.products, ims.catalog_products),
        "variants": _count_row(bvi.variants, ims.catalog_variants),
        "collections": _count_row(bvi.collections, ims.ecom_collections),
        "menus": _count_row(bvi.menus, ims.ecom_menus),
        "customers": _count_row(bvi.customers, ims.customers),
        "orders": _count_row(bvi.orders, ims.online_orders),
    }

    # --- SKU diff (BVI variants missing from IMS catalog_variants) ---
    bvi_sku_set = {_norm_sku(s) for s in bvi.variant_skus if _norm_sku(s)}
    ims_sku_set = {_norm_sku(s) for s in ims.variant_skus if _norm_sku(s)}
    missing_in_ims = sorted(bvi_sku_set - ims_sku_set)
    sku_diff = {
        "bvi_total": len(bvi_sku_set),
        "ims_total": len(ims_sku_set),
        "missing_in_ims": missing_in_ims,
        "missing_count": len(missing_in_ims),
        "sample_missing": missing_in_ims[:sample_limit],
        # informational: SKUs IMS has that BVI does not (NOT a gate failure, but
        # surfaced so a stale/over-migrated IMS row is visible)
        "extra_in_ims": sorted(ims_sku_set - bvi_sku_set)[:sample_limit],
        "extra_in_ims_count": len(ims_sku_set - bvi_sku_set),
    }

    # --- barcode diff (storeBarcode present in BVI must equal IMS barcode) ---
    ims_barcode_by_sku = {
        _norm_sku(k): _norm_barcode(v) for k, v in (ims.barcode_by_sku or {}).items()
    }
    mismatched: List[Dict[str, str]] = []
    checked = 0
    for sku, bvi_bc in (bvi.variant_barcode_by_sku or {}).items():
        nsku = _norm_sku(sku)
        nbc = _norm_barcode(bvi_bc)
        if not nbc:
            continue  # only check variants that actually carry a storeBarcode
        checked += 1
        ims_bc = ims_barcode_by_sku.get(nsku, "")
        if ims_bc != nbc:
            mismatched.append({"sku": nsku, "bvi": nbc, "ims": ims_bc})
    mismatched.sort(key=lambda m: m["sku"])
    barcode_diff = {
        "checked": checked,
        "mismatched": mismatched,
        "mismatched_count": len(mismatched),
        "sample_mismatched": mismatched[:sample_limit],
    }

    # --- image storage audit (from live data) ---
    total_imgs = len(bvi.image_urls)
    durable = sum(1 for u in bvi.image_urls if is_durable_image_url(u))
    local_disk = total_imgs - durable
    image_storage = {
        "total": total_imgs,
        "durable": durable,
        "local_disk": local_disk,
        "local_disk_pct": (
            round((local_disk / total_imgs * 100.0), 2) if total_imgs else 0.0
        ),
        "phase4_rehost_needed": local_disk > 0,
    }

    gate_pass = (
        counts["products"]["match"]
        and counts["variants"]["match"]
        and sku_diff["missing_count"] == 0
        and barcode_diff["mismatched_count"] == 0
    )

    return {
        "counts": counts,
        "sku_diff": sku_diff,
        "barcode_diff": barcode_diff,
        "image_storage": image_storage,
        "gate_pass": gate_pass,
    }


def render_text_report(report: Dict[str, Any]) -> str:
    """Render the parity report dict as a human-readable text block."""
    lines: List[str] = []
    lines.append("=" * 64)
    lines.append("BVI <-> IMS PARITY REPORT (read-only)")
    lines.append("=" * 64)

    lines.append("")
    lines.append("ENTITY COUNTS  (BVI Postgres vs IMS Mongo)")
    lines.append("-" * 64)
    lines.append(f"{'entity':<14}{'bvi':>10}{'ims':>10}{'delta':>10}  {'match':<6}")
    for entity, row in report["counts"].items():
        lines.append(
            f"{entity:<14}{row['bvi']:>10}{row['ims']:>10}{row['delta']:>10}  "
            f"{'OK' if row['match'] else 'DIFF':<6}"
        )

    sd = report["sku_diff"]
    lines.append("")
    lines.append("SKU PARITY  (BVI ProductVariant -> IMS catalog_variants)")
    lines.append("-" * 64)
    lines.append(f"  BVI variant SKUs : {sd['bvi_total']}")
    lines.append(f"  IMS variant SKUs : {sd['ims_total']}")
    lines.append(f"  MISSING in IMS   : {sd['missing_count']}")
    if sd["sample_missing"]:
        lines.append(f"  sample missing   : {', '.join(sd['sample_missing'])}")
    if sd["extra_in_ims_count"]:
        lines.append(f"  extra in IMS     : {sd['extra_in_ims_count']} (not in BVI)")

    bd = report["barcode_diff"]
    lines.append("")
    lines.append("BARCODE PARITY  (BVI storeBarcode -> IMS barcode)")
    lines.append("-" * 64)
    lines.append(f"  checked (have storeBarcode) : {bd['checked']}")
    lines.append(f"  MISMATCHED / missing in IMS : {bd['mismatched_count']}")
    for m in bd["sample_mismatched"]:
        lines.append(
            f"    sku={m['sku']:<20} bvi={m['bvi'] or '(none)':<16} "
            f"ims={m['ims'] or '(none)'}"
        )

    img = report["image_storage"]
    lines.append("")
    lines.append("IMAGE STORAGE  (Phase-4 re-host scope, from live data)")
    lines.append("-" * 64)
    lines.append(f"  total images        : {img['total']}")
    lines.append(f"  durable (http/https): {img['durable']}")
    lines.append(
        f"  LOCAL DISK /uploads : {img['local_disk']}  ({img['local_disk_pct']}%)"
    )
    lines.append(
        f"  Phase-4 re-host     : "
        f"{'NEEDED (local-disk images present)' if img['phase4_rehost_needed'] else 'not needed (all durable)'}"
    )

    lines.append("")
    lines.append("=" * 64)
    lines.append(
        f"GATE: {'PASS' if report['gate_pass'] else 'FAIL -- parity not yet met'}"
    )
    lines.append("=" * 64)
    return "\n".join(lines)


# ===========================================================================
# Postgres (read-only) snapshot collection -- I/O
# ===========================================================================


def _resolve_pg_url() -> Optional[str]:
    """The BVI Postgres URL from env. Prefer BVI_DATABASE_URL (the Phase-0 spec
    name), fall back to ECOMMERCE_DATABASE_URL (what the live backend + the
    migration script use on Railway). NEVER returns/prints the value to a log."""
    return os.getenv("BVI_DATABASE_URL") or os.getenv("ECOMMERCE_DATABASE_URL")


def _pg_connect(pg_url: str):
    """Open a SHORT-LIVED, READ-ONLY Postgres connection, or None on failure.

    Mirrors online_catalog._connect / migrate_bvi_pim._pg_connect. The session is
    set read-only at the server so even a buggy query cannot write.
    """
    try:
        import psycopg2  # noqa: PLC0415 -- lazy import
        import psycopg2.extras  # noqa: PLC0415 (used by _pg_fetchall)
    except ImportError as e:
        logger.warning(
            "[BVI_PARITY] psycopg2 is not installed (%s). Install it: "
            "pip install psycopg2-binary (it is already pinned in "
            "backend/requirements.txt).",
            e,
        )
        return None
    try:
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        # READ-ONLY at the server: any INSERT/UPDATE/DELETE is rejected by PG.
        conn.set_session(readonly=True, autocommit=True)
        return conn
    except Exception as e:  # noqa: BLE001
        logger.warning("[BVI_PARITY] Postgres connect failed: %s", e)
        return None


# The only BVI tables _pg_count may touch. Identifiers cannot be bound as SQL
# parameters, so the interpolation below is gated on this closed set instead.
_COUNTABLE_TABLES = frozenset(
    {"Product", "ProductVariant", "Collection", "Menu", "Customer", "Order"}
)


def _pg_count(conn, table: str) -> int:
    """SELECT COUNT(*) for a BVI table (quoted -- Prisma uses PascalCase).
    Returns -1 on error so a failure is visible (never silently 0)."""
    if table not in _COUNTABLE_TABLES:
        logger.warning("[BVI_PARITY] count(%s) refused: not an allowed table", table)
        return -1
    try:
        with conn.cursor() as cur:
            # nosec B608: `table` is validated against the closed allowlist
            # above (never caller/user input), and the session is server-side
            # read-only; identifiers can't be parameterized in Postgres.
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')  # nosec B608
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception as e:  # noqa: BLE001
        logger.warning("[BVI_PARITY] count(%s) failed: %s", table, e)
        return -1


def _pg_fetchall(conn, sql: str) -> List[Dict]:
    """Run a SELECT, return list-of-dicts. [] on error (logged)."""
    try:
        import psycopg2.extras  # noqa: PLC0415

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:  # noqa: BLE001
        logger.warning("[BVI_PARITY] query failed: %s", e)
        return []


def collect_bvi_snapshot(conn) -> BviSnapshot:
    """Read the BVI Postgres catalog into an in-memory snapshot. SELECT-only."""
    snap = BviSnapshot()
    snap.products = _pg_count(conn, "Product")
    snap.variants = _pg_count(conn, "ProductVariant")
    snap.collections = _pg_count(conn, "Collection")
    snap.menus = _pg_count(conn, "Menu")
    snap.customers = _pg_count(conn, "Customer")
    snap.orders = _pg_count(conn, "Order")

    # Variant identity: sku + storeBarcode (the two-barcode model -- storeBarcode
    # is the physical store barcode, the Phase-1 gate key).
    variant_rows = _pg_fetchall(
        conn, 'SELECT sku, "storeBarcode" FROM "ProductVariant"'
    )
    for r in variant_rows:
        sku = _norm_sku(r.get("sku"))
        if not sku:
            continue
        snap.variant_skus.append(sku)
        bc = _norm_barcode(r.get("storeBarcode"))
        if bc:
            snap.variant_barcode_by_sku[sku] = bc

    # Image urls for the storage audit (ProductImage + VariantImage).
    prod_img_rows = _pg_fetchall(conn, 'SELECT url, "originalUrl" FROM "ProductImage"')
    var_img_rows = _pg_fetchall(conn, 'SELECT url, "originalUrl" FROM "VariantImage"')
    for r in prod_img_rows + var_img_rows:
        # Count both url AND originalUrl -- either can be a local-disk path.
        if r.get("url") is not None:
            snap.image_urls.append(str(r.get("url")))
        if r.get("originalUrl"):
            snap.image_urls.append(str(r.get("originalUrl")))

    return snap


# ===========================================================================
# Mongo (read-only) snapshot collection -- I/O
# ===========================================================================


def collect_ims_snapshot(db) -> ImsSnapshot:
    """Read the IMS Mongo catalog into an in-memory snapshot. find/count only.

    IMS collection map (verified against product_master.py + migrate_bvi_pim.py):
      catalog_products  -- the PIM superset (Shopify/BVI lineage)
      catalog_variants  -- the per-SKU variant identity tier (carries store_barcode)
      ecom_collections  -- migrated Collection docs
      ecom_menus        -- migrated Menu docs
      customers         -- IMS customers
      online_orders     -- online-store orders (store-scoped)
    The billing spine `products` also carries `barcode`, so we fold spine barcodes
    into the barcode-by-sku map as a fallback when catalog_variants has none.
    """
    snap = ImsSnapshot()

    def _count(name: str) -> int:
        try:
            return int(db.get_collection(name).count_documents({}))
        except Exception as e:  # noqa: BLE001
            logger.warning("[BVI_PARITY] Mongo count(%s) failed: %s", name, e)
            return -1

    snap.catalog_products = _count("catalog_products")
    snap.catalog_variants = _count("catalog_variants")
    snap.ecom_collections = _count("ecom_collections")
    snap.ecom_menus = _count("ecom_menus")
    snap.customers = _count("customers")
    snap.online_orders = _count("online_orders")

    # SKU + barcode index from catalog_variants (store_barcode is the migrated
    # BVI storeBarcode; barcode/gtin may also be present).
    try:
        cur = db.get_collection("catalog_variants").find(
            {}, {"sku": 1, "store_barcode": 1, "barcode": 1, "gtin": 1, "_id": 0}
        )
        for d in cur:
            sku = _norm_sku(d.get("sku"))
            if not sku:
                continue
            snap.variant_skus.append(sku)
            bc = _norm_barcode(
                d.get("store_barcode") or d.get("barcode") or d.get("gtin")
            )
            if bc:
                snap.barcode_by_sku[sku] = bc
    except Exception as e:  # noqa: BLE001
        logger.warning("[BVI_PARITY] Mongo read(catalog_variants) failed: %s", e)

    # Fold in spine `products.barcode` for any sku not already mapped (the billing
    # spine is the source of truth for the physical store barcode in IMS).
    try:
        cur = db.get_collection("products").find({}, {"sku": 1, "barcode": 1, "_id": 0})
        for d in cur:
            sku = _norm_sku(d.get("sku"))
            bc = _norm_barcode(d.get("barcode"))
            if sku and bc and sku not in snap.barcode_by_sku:
                snap.barcode_by_sku[sku] = bc
    except Exception as e:  # noqa: BLE001
        # The spine fallback is best-effort -- a missing `products` collection is
        # not fatal (catalog_variants is the primary source).
        logger.warning("[BVI_PARITY] Mongo read(products) fallback failed: %s", e)

    return snap


# ===========================================================================
# Nightly monitor: snapshot -> store -> drift check (all fail-soft)
# ===========================================================================


def monitor_enabled() -> bool:
    """BVI_PARITY_MONITOR env gate. Default ON."""
    val = os.getenv("BVI_PARITY_MONITOR", "on").strip().lower()
    return val not in ("0", "false", "no", "off")


def count_tolerance() -> int:
    """Product/variant count delta tolerated before drift is flagged."""
    try:
        return max(
            0,
            int(os.getenv("BVI_PARITY_COUNT_TOLERANCE", str(DEFAULT_COUNT_TOLERANCE))),
        )
    except (TypeError, ValueError):
        return DEFAULT_COUNT_TOLERANCE


def _ist_now() -> datetime:
    """tz-aware IST now (fail-soft fallback to a fixed +05:30 offset)."""
    try:
        from api.utils.ist import now_ist  # noqa: PLC0415

        return now_ist()
    except Exception:  # noqa: BLE001
        return datetime.now(timezone(timedelta(hours=5, minutes=30)))


def _compact_report(report: Dict[str, Any], sample_limit: int) -> Dict[str, Any]:
    """Compact a full build_parity_report() dict for STORAGE: exact counts are
    kept; the potentially huge example lists (missing_in_ims, mismatched,
    extra_in_ims) are replaced by their capped samples. PURE."""
    sd = report.get("sku_diff", {}) or {}
    bd = report.get("barcode_diff", {}) or {}
    return {
        "counts": report.get("counts", {}),
        "sku_diff": {
            "bvi_total": sd.get("bvi_total", 0),
            "ims_total": sd.get("ims_total", 0),
            "missing_count": sd.get("missing_count", 0),
            "sample_missing": list(sd.get("sample_missing", []) or [])[:sample_limit],
            "extra_in_ims_count": sd.get("extra_in_ims_count", 0),
            "sample_extra_in_ims": list(sd.get("extra_in_ims", []) or [])[
                :sample_limit
            ],
        },
        "barcode_diff": {
            "checked": bd.get("checked", 0),
            "mismatched_count": bd.get("mismatched_count", 0),
            "sample_mismatched": list(bd.get("sample_mismatched", []) or [])[
                :sample_limit
            ],
        },
        "image_storage": report.get("image_storage", {}),
        "gate_pass": bool(report.get("gate_pass", False)),
    }


def run_parity_snapshot(
    db, sample_limit: int = SNAPSHOT_SAMPLE_LIMIT
) -> Dict[str, Any]:
    """Run a full BVI-Postgres vs IMS-Mongo parity comparison. FAIL-SOFT:
    NEVER raises. Returns:

      {"ok": True,  "generated_at": iso, "reason": None, "report": {compact}}
      {"ok": False, "generated_at": iso, "reason": "...", "report": None}

    ``report`` is the compact form (exact counts, capped samples -- see
    _compact_report). ``sample_limit`` is hard-capped at SNAPSHOT_SAMPLE_LIMIT
    so a caller cannot bloat the stored snapshot.
    """
    try:
        sample_limit = max(1, min(int(sample_limit), SNAPSHOT_SAMPLE_LIMIT))
    except (TypeError, ValueError):
        sample_limit = SNAPSHOT_SAMPLE_LIMIT

    out: Dict[str, Any] = {
        "ok": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reason": None,
        "report": None,
    }
    try:
        if db is None:
            out["reason"] = "mongo unavailable"
            return out

        pg_url = _resolve_pg_url()
        if not pg_url:
            out["reason"] = (
                "postgres url not configured "
                "(BVI_DATABASE_URL / ECOMMERCE_DATABASE_URL)"
            )
            return out

        conn = _pg_connect(pg_url)
        if conn is None:
            out["reason"] = "postgres unreachable"
            return out

        try:
            bvi = collect_bvi_snapshot(conn)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

        ims = collect_ims_snapshot(db)

        # A -1 count means a read error -- report a failed snapshot rather
        # than fabricate parity numbers from partial data.
        failed_reads = [
            label
            for label, val in (
                ("bvi.products", bvi.products),
                ("bvi.variants", bvi.variants),
                ("ims.catalog_products", ims.catalog_products),
                ("ims.catalog_variants", ims.catalog_variants),
            )
            if val < 0
        ]
        if failed_reads:
            out["reason"] = "count read failed: " + ", ".join(failed_reads)
            return out

        report = build_parity_report(bvi, ims, sample_limit=sample_limit)
        out["ok"] = True
        out["report"] = _compact_report(report, sample_limit)
        return out
    except Exception as e:  # noqa: BLE001 -- fail-soft contract
        logger.warning("[BVI_PARITY] snapshot run failed (soft): %s", e)
        out["reason"] = f"unexpected error: {e}"
        out["report"] = None
        return out


def evaluate_drift(
    snapshot: Optional[Dict[str, Any]],
    tolerance: Optional[int] = None,
) -> Dict[str, Any]:
    """PURE drift verdict for a snapshot produced by run_parity_snapshot().

    Drift when: missing SKUs > 0, OR the product/variant count delta exceeds
    the tolerance (env BVI_PARITY_COUNT_TOLERANCE, default 5). A failed
    (ok=False) snapshot is NOT drift -- there is nothing trustworthy to judge.
    Returns {"drift": bool, "reasons": [str, ...]}.
    """
    if tolerance is None:
        tolerance = count_tolerance()
    reasons: List[str] = []
    if not snapshot or not snapshot.get("ok") or not snapshot.get("report"):
        return {"drift": False, "reasons": reasons}

    report = snapshot["report"]
    missing = int((report.get("sku_diff") or {}).get("missing_count", 0) or 0)
    if missing > 0:
        reasons.append(f"{missing} BVI SKU(s) missing from IMS")

    counts = report.get("counts") or {}
    for entity in ("products", "variants"):
        row = counts.get(entity) or {}
        try:
            delta = int(row.get("delta", 0) or 0)
        except (TypeError, ValueError):
            delta = 0
        if abs(delta) > tolerance:
            reasons.append(
                f"{entity} count delta {delta:+d} exceeds tolerance {tolerance}"
            )

    return {"drift": bool(reasons), "reasons": reasons}


def prune_parity_snapshots(
    col,
    retention_days: int = SNAPSHOT_RETENTION_DAYS,
    now: Optional[datetime] = None,
) -> int:
    """Delete bvi_parity_snapshots rows older than `retention_days`. Fail-soft:
    returns the number deleted, or 0 on any error / missing collection. Kept
    pure so it is unit-testable with a fake collection (mirrors SENTINEL's
    prune_health_checks)."""
    if col is None:
        return 0
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    try:
        res = col.delete_many({"timestamp": {"$lt": cutoff}})
        return int(getattr(res, "deleted_count", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[BVI_PARITY] snapshot prune skipped: %s", exc)
        return 0


def store_parity_snapshot(db, snapshot: Dict[str, Any]) -> bool:
    """Persist a snapshot in `bvi_parity_snapshots` (timestamped, tagged with
    the IST calendar date) and trim rows older than 30 days. Fail-soft: any
    error returns False, never raises. Mutates `snapshot` to carry the
    timestamp/ist_date it was stored under (so callers can dedupe by day)."""
    if db is None:
        return False
    try:
        col = db.get_collection(PARITY_SNAPSHOT_COLLECTION)
        if col is None:
            return False
        now_utc = datetime.now(timezone.utc)
        snapshot.setdefault("timestamp", now_utc)
        snapshot.setdefault("ist_date", _ist_now().strftime("%Y-%m-%d"))
        doc = dict(snapshot)
        col.insert_one(doc)
        # insert_one mutates doc with _id; keep the caller's snapshot clean.
        prune_parity_snapshots(col, SNAPSHOT_RETENTION_DAYS, now=now_utc)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[BVI_PARITY] snapshot store failed (soft): %s", e)
        return False


def latest_parity_snapshot(db) -> Optional[Dict[str, Any]]:
    """The newest stored snapshot (by timestamp), `_id` stripped, or None.
    Fail-soft: any error returns None."""
    if db is None:
        return None
    try:
        col = db.get_collection(PARITY_SNAPSHOT_COLLECTION)
        if col is None:
            return None
        cur = col.find({}).sort("timestamp", -1).limit(1)
        for doc in cur:
            doc = dict(doc)
            doc.pop("_id", None)
            return doc
        return None
    except Exception as e:  # noqa: BLE001
        logger.debug("[BVI_PARITY] latest snapshot read failed (soft): %s", e)
        return None


def _create_drift_task(
    db, snapshot: Dict[str, Any], drift: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """File a deduped SYSTEM task (one per IST day) so drift is visible on the
    SUPERADMIN task board. Fail-soft: returns the created task or None."""
    try:
        from api.services.task_triggers import create_system_task  # noqa: PLC0415
        from database.repositories.task_repository import (
            TaskRepository,
        )  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        logger.debug("[BVI_PARITY] drift-task imports failed (soft): %s", e)
        return None
    try:
        tasks_coll = db.get_collection("tasks") if db is not None else None
        if tasks_coll is None:
            return None
        repo = TaskRepository(tasks_coll)
        ist_date = snapshot.get("ist_date") or _ist_now().strftime("%Y-%m-%d")
        reasons = "; ".join(drift.get("reasons") or []) or "parity drift"
        sample = ((snapshot.get("report") or {}).get("sku_diff") or {}).get(
            "sample_missing"
        ) or []
        description = (
            "The nightly BVI (Postgres) vs IMS (Mongo) catalog parity check "
            f"found drift: {reasons}. "
            "Someone likely edited the catalog in the old BVI admin after the "
            "sync -- review and re-sync before the BVI decommission. "
            "Full snapshot: Settings -> GET /api/v1/admin/online-store/parity."
        )
        if sample:
            description += " Sample missing SKUs: " + ", ".join(sample[:10]) + "."
        return create_system_task(
            repo,
            title="BVI catalog parity drift detected",
            description=description,
            priority="P2",
            category="Inventory",
            store_id=None,
            dedupe_ref=f"bvi_parity_drift:{ist_date}",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[BVI_PARITY] drift task create failed (soft): %s", e)
        return None


def run_and_record_parity(db) -> Dict[str, Any]:
    """The nightly monitor body: run the parity snapshot, persist it (with
    retention trim), and -- when drift is detected -- WARN + file a deduped
    SYSTEM task. FAIL-SOFT end to end: never raises, so the agent tick that
    calls it can never take down the scheduler. Returns the snapshot (with a
    `drift` block attached)."""
    try:
        snapshot = run_parity_snapshot(db)
        drift = evaluate_drift(snapshot)
        snapshot["drift"] = drift
        store_parity_snapshot(db, snapshot)

        if drift.get("drift"):
            logger.warning(
                "[BVI_PARITY] DRIFT detected: %s", "; ".join(drift.get("reasons") or [])
            )
            _create_drift_task(db, snapshot, drift)
        elif not snapshot.get("ok"):
            logger.warning(
                "[BVI_PARITY] nightly snapshot could not run: %s",
                snapshot.get("reason"),
            )
        else:
            logger.info(
                "[BVI_PARITY] nightly snapshot OK (gate_pass=%s)",
                (snapshot.get("report") or {}).get("gate_pass"),
            )
        return snapshot
    except Exception as e:  # noqa: BLE001 -- last-resort belt and braces
        logger.warning("[BVI_PARITY] run_and_record_parity failed (soft): %s", e)
        return {"ok": False, "reason": f"unexpected error: {e}", "report": None}
