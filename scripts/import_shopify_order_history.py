#!/usr/bin/env python3
"""
IMS 2.0 - Shopify ORDER-HISTORY importer (back-catalogue -> real IMS orders)
============================================================================
Runbook-only script. NOT in CI. DRY-RUN by default (reads + reports, writes
NOTHING); pass --apply to book the orders.

WHAT IT DOES
------------
Pulls the FULL Shopify order history via the Admin REST API and books each REAL
order (see the filter below) as a canonical IMS order attributed to the online
store BV-ONLINE-01, so finance / GST / reports finally see the online back-
catalogue (target ~Rs 23,14,013). Every order is routed through the SAME mapper
+ ingest the live webhook path uses (api.services.online_order_mapper.
map_shopify_order), so an imported order is shape-identical to a webhook-ingested
one -- same channel='ONLINE' tag, same inclusive-GST place-of-supply split, same
ONL- order-number namespace, same uniq_shopify_order_id idempotency.

The one difference is `historical=True`, which makes the reused create path:
  * fire NO live side effect -- no loyalty earn, no messaging/MEGAPHONE queue,
    no inventory decrement (units shipped long ago), no oversell write-back, no
    Rx flag-and-hold, no task creation;
  * mint NO fresh per-FY GST invoice serial -- a back-dated order must never
    consume the CURRENT financial year's consecutive sequence (CGST Rule 46(b));
    invoice_number stays None and GST reconciliation falls back to order_number;
  * preserve the REAL Shopify order date (created_at) so the sale lands in its
    true accounting period, and land the order in a TERMINAL status (DELIVERED /
    REFUNDED), tagged historical=True + import_source="shopify_order_history".

Customers are LINKED, never duplicated: the 768 buyers were already imported
(matched by mobile/email); historical mode only MATCHES an existing customer
(by Shopify customer id -> mobile -> email) and records a guest sale otherwise.

"REAL" ORDER FILTER (matches the owner's 932-of-1,354 definition)
  not test  AND  not cancelled  AND  total_price > 0  AND
  financial_status in {paid, partially_paid, partially_refunded, refunded}

IDEMPOTENT: re-running never double-books (the orders.uniq_shopify_order_id
partial-unique index + the mapper's dedupe return "duplicate"; historical orders
are further protected from status re-sync).

USAGE (run via Railway so MONGO_* + SHOPIFY_* creds are injected; I run this):
  Dry-run (DEFAULT -- prints the plan, writes nothing):
    railway run --service MongoDB -- .venv\\Scripts\\python.exe scripts\\import_shopify_order_history.py
  Apply (books the orders):
    railway run --service MongoDB -- .venv\\Scripts\\python.exe scripts\\import_shopify_order_history.py --apply

Credentials: SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET (client-credentials mint,
preferred over the stale SHOPIFY_ACCESS_TOKEN) + a resolvable shop (SHOPIFY_STORE_URL
or the Mongo integrations config.shop_url). Never prints any secret value.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

# Make the backend package importable (mirrors scripts/backfill_order_customer_id.py).
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
)

# The financial states that count as a real, money-collected online sale.
_REAL_FINANCIAL_STATUSES = {
    "paid",
    "partially_paid",
    "partially_refunded",
    "refunded",
}

_DEFAULT_ONLINE_STORE_ID = "BV-ONLINE-01"


def resolve_mongo_uri(explicit: Optional[str]) -> Optional[str]:
    """Prefer an explicit/standard URI; otherwise assemble one from the MONGO_*
    component vars Railway injects. MONGO_PUBLIC_URL is checked first so this
    runbook works via `railway run -s MongoDB` (the internal host only resolves
    inside Railway's network)."""
    uri = (
        explicit
        or os.getenv("MONGO_PUBLIC_URL")
        or os.getenv("MONGODB_URL")
        or os.getenv("MONGO_URL")
    )
    if uri:
        return uri
    host = os.getenv("MONGO_HOST")
    if not host:
        return None
    user = os.getenv("MONGO_USERNAME") or ""
    pw = os.getenv("MONGO_PASSWORD") or ""
    port = os.getenv("MONGO_PORT", "27017")
    auth_source = os.getenv("MONGO_AUTH_SOURCE", "admin")
    cred = f"{user}:{pw}@" if user and pw else ""
    opts = f"?authSource={auth_source}"
    if (os.getenv("MONGO_SSL", "") or "").lower() in ("true", "1", "yes"):
        opts += "&tls=true"
    return f"mongodb://{cred}{host}:{port}/{opts}"


def connect_db(mongo_uri: str, db_name: str):
    """Configure + connect the app's connection singleton so the reused mapper /
    ingest resolve their repositories against the REAL prod Mongo, then return the
    seeded-db handle (what the live NEXUS path passes to the mapper)."""
    from database.connection import DatabaseConfig, db as conn, get_seeded_db

    conn.configure(DatabaseConfig.from_uri(mongo_uri, db_name))
    if not conn.connect():
        raise SystemExit(
            "[ERROR] Could not connect to MongoDB. Check the MONGO_* creds / URI."
        )
    dbh = get_seeded_db()
    if not getattr(dbh, "is_connected", False):
        raise SystemExit("[ERROR] MongoDB reports not-connected; aborting.")
    return dbh


def _parse_next_link(link_header: Optional[str]) -> Optional[str]:
    """Extract the rel="next" URL from a Shopify REST `Link` header (cursor
    pagination). Returns None when there is no next page."""
    if not link_header:
        return None
    for part in link_header.split(","):
        seg = part.strip()
        if 'rel="next"' in seg:
            lt = seg.find("<")
            gt = seg.find(">", lt + 1)
            if lt != -1 and gt != -1:
                return seg[lt + 1 : gt]
    return None


def fetch_all_orders(
    shop: str, token: str, *, page_size: int, sleep_s: float, cap: Optional[int]
) -> List[Dict[str, Any]]:
    """Pull EVERY Shopify order (status=any) via the Admin REST API, following the
    Link-header cursor. Returns the raw order dicts. Raises on a transport / HTTP
    error so the run fails loudly rather than importing a partial history."""
    import httpx

    version = (os.getenv("SHOPIFY_API_VERSION") or "2024-10").strip()
    url = f"https://{shop}/admin/api/{version}/orders.json"
    params: Dict[str, Any] = {"status": "any", "limit": page_size}
    headers = {"X-Shopify-Access-Token": token}

    out: List[Dict[str, Any]] = []
    page = 0
    with httpx.Client(timeout=60.0) as client:
        resp = client.get(url, params=params, headers=headers)
        while True:
            if resp.status_code != 200:
                raise SystemExit(
                    f"[ERROR] Shopify Admin API HTTP {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
            batch = resp.json().get("orders", []) or []
            out.extend(batch)
            page += 1
            print(f"[FETCH] page {page}: +{len(batch)} orders (total {len(out)})")
            if cap and len(out) >= cap:
                out = out[:cap]
                break
            next_url = _parse_next_link(
                resp.headers.get("Link") or resp.headers.get("link")
            )
            if not next_url:
                break
            time.sleep(sleep_s)
            resp = client.get(next_url, headers=headers)
    return out


def is_real_order(o: Dict[str, Any]) -> bool:
    """The owner's real-order filter: not a test order, not cancelled, a positive
    total, and a money-collected financial status."""
    if o.get("test"):
        return False
    if o.get("cancelled_at"):
        return False
    try:
        if float(o.get("total_price") or 0) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    fin = str(o.get("financial_status") or "").strip().lower()
    return fin in _REAL_FINANCIAL_STATUSES


def _map_for_report(payload: Dict[str, Any], db) -> Dict[str, Any]:
    """Compute the tax-accurate figures for the dry-run report by REUSING the exact
    mapping the ingest would run (variant->sku enrich, then _map_line_items's
    inclusive-GST split) -- WITHOUT writing anything. Returns grand_total, the
    per-GST-rate breakdown and the mapped-item count."""
    from api.services import online_order_mapper, shopify_ingest

    variant_repo = online_order_mapper._get_variant_repo(db)
    online_order_mapper._enrich_line_items_with_sku(payload, variant_repo)

    product_repo = None
    try:
        from api.dependencies import get_product_repository

        product_repo = get_product_repository()
    except Exception:  # noqa: BLE001
        product_repo = None

    items = shopify_ingest._map_line_items(payload, product_repo)
    taxable = round(sum(shopify_ingest._f(i.get("taxable_value")) for i in items), 2)
    tax = round(sum(shopify_ingest._f(i.get("tax_amount")) for i in items), 2)
    grand_total = round(taxable + tax, 2)

    per_rate: Dict[float, Dict[str, float]] = defaultdict(
        lambda: {"gross": 0.0, "taxable": 0.0, "tax": 0.0, "lines": 0}
    )
    for i in items:
        r = round(float(i.get("gst_rate") or 0.0), 2)
        per_rate[r]["gross"] += shopify_ingest._f(i.get("item_total"))
        per_rate[r]["taxable"] += shopify_ingest._f(i.get("taxable_value"))
        per_rate[r]["tax"] += shopify_ingest._f(i.get("tax_amount"))
        per_rate[r]["lines"] += 1

    year = shopify_ingest._order_datetime(payload).year
    return {
        "items": len(items),
        "mapped_items": items,
        "grand_total": grand_total,
        "per_rate": per_rate,
        "year": year,
    }


def _simulate_refund_credit_notes(
    o: Dict[str, Any], mapped_items: List[Dict[str, Any]]
) -> Dict[str, int]:
    """DRY-RUN twin of shopify_ingest._book_historical_refund_credit_notes'
    LINE-MAPPING decision (no writes): reuses the SAME live mapper
    (shopify_refund._build_return_lines) against the same mapped items the ingest
    would store, so the dry-run reports what WOULD book instead of an
    unconditional 'refunds are booked as CDNR' claim.

    Returns {"mappable": <refunds that book a line-mapped CN>,
             "whole_order": <orders covered by the whole-order reversal fallback>,
             "amount_only": <refunds left as an UNMAPPED residual (no CN)>}."""
    from api.services import shopify_refund

    fin = str(o.get("financial_status") or "").strip().lower()
    out = {"mappable": 0, "whole_order": 0, "amount_only": 0}
    if fin not in ("refunded", "partially_refunded"):
        return out
    refunds = o.get("refunds")
    if not isinstance(refunds, list) or not refunds:
        # No per-refund detail: full refund -> whole-order reversal; a partial
        # with no detail is an unmapped residual.
        if fin == "refunded":
            out["whole_order"] = 1
        else:
            out["amount_only"] = 1
        return out
    order_shim = {"items": mapped_items, "order_id": o.get("id")}
    mappable = 0
    unmapped = 0
    for r in refunds:
        if not isinstance(r, dict):
            continue
        try:
            lines = shopify_refund._build_return_lines(r, order_shim)
        except Exception:  # noqa: BLE001
            lines = []
        if lines:
            mappable += 1
        else:
            unmapped += 1
    out["mappable"] = mappable
    if fin == "refunded" and mappable == 0 and unmapped > 0:
        # Amount-only refunds on a TERMINALLY refunded order are covered by the
        # whole-order reversal fallback at ingest.
        out["whole_order"] = 1
    else:
        out["amount_only"] = unmapped
    return out


def _existing_order(db, shopify_order_id: str):
    """The IMS order doc already carrying this Shopify order id, or None.
    Returned (not a bool) so the caller can DISTINGUISH a genuinely-booked online
    order from a pre-IMS bvi_import shadow doc that would silently dedupe it."""
    try:
        coll = db.get_collection("orders")
        if coll is None:
            return None
        return coll.find_one({"shopify_order_id": shopify_order_id})
    except Exception:  # noqa: BLE001
        return None


def _is_bvi_shadow(doc: Optional[Dict[str, Any]]) -> bool:
    """True when an existing order is a pre-IMS customer-360 shadow doc
    (scripts/migrate_bvi_pim.py orders leg: source=bvi_import, usually status
    HISTORICAL) rather than a genuinely-booked online order. Such a doc dedupes the
    import (same shopify_order_id) but is EXCLUDED from all finance/GST aggregation
    by its status/source -- so lumping it into 'already present (idempotent)' would
    silently hide orders that were never actually booked as revenue."""
    if not doc:
        return False
    if str(doc.get("source") or "").strip().lower() == "bvi_import":
        return True
    # A HISTORICAL-status doc that is NOT our own order-history import is a shadow.
    status = str(doc.get("status") or "").strip().upper()
    if status == "HISTORICAL" and doc.get("import_source") != "shopify_order_history":
        return True
    return False


def _shipping_total(o: Dict[str, Any]) -> float:
    """Sum of a Shopify order's shipping_lines (the shipping the buyer paid).
    IMS's line-items-only grand_total deliberately does NOT map shipping (the live
    ingest path doesn't either -- adding it would change the tax math), so this is
    reported as a known UNDERSHOOT vs the Shopify-collected total, never booked."""
    total = 0.0
    for sl in o.get("shipping_lines") or []:
        if not isinstance(sl, dict):
            continue
        try:
            total += float(sl.get("price") or 0)
        except (TypeError, ValueError):
            continue
    return total


def _match_customer_readonly(db, payload: Dict[str, Any]) -> Optional[str]:
    from api.services import online_order_mapper

    buyer = online_order_mapper._extract_buyer(payload)
    return online_order_mapper._match_existing_customer(db, buyer)


def run(
    *,
    mongo_uri: Optional[str],
    db_name: str,
    apply: bool,
    store_id: str,
    page_size: int,
    sleep_s: float,
    cap: Optional[int],
) -> Dict[str, Any]:
    if not mongo_uri:
        raise SystemExit(
            "No Mongo connection. Set MONGODB_URL / MONGO_URL, pass --mongo-uri, or "
            "run via `railway run` so the MONGO_* component vars are injected."
        )
    dbh = connect_db(mongo_uri, db_name)

    # Resolve Shopify creds (client-credentials mint preferred; never logs the token).
    from api.services.shopify_auth import resolve_shopify_credentials

    creds = resolve_shopify_credentials(dbh)
    shop = (creds or {}).get("shop_url")
    token = (creds or {}).get("access_token")
    source = (creds or {}).get("source")
    if not shop or not token:
        raise SystemExit(
            "[ERROR] No usable Shopify Admin credentials. Set SHOPIFY_CLIENT_ID + "
            "SHOPIFY_CLIENT_SECRET (+ a resolvable shop via SHOPIFY_STORE_URL or the "
            "integrations config) so a client-credentials token can be minted."
        )
    print(f"[AUTH] Shopify creds resolved via source={source} shop={shop}")

    raw_orders = fetch_all_orders(
        shop, token, page_size=page_size, sleep_s=sleep_s, cap=cap
    )
    scanned = len(raw_orders)

    real = [o for o in raw_orders if is_real_order(o)]

    # P2 pre-flight: how many pre-IMS bvi_import shadow docs carry a shopify_order_id
    # at all. If >0, some real orders may be silently deduped by a shadow that is
    # excluded from finance/GST -- report that bucket SEPARATELY (below) and list the
    # colliding ids so nothing is silently lumped into 'already present'.
    bvi_shadow_total = 0
    try:
        _ocoll = dbh.get_collection("orders")
        if _ocoll is not None:
            bvi_shadow_total = int(
                _ocoll.count_documents(
                    {"source": "bvi_import", "shopify_order_id": {"$exists": True}}
                )
            )
    except Exception:  # noqa: BLE001
        bvi_shadow_total = 0

    # Dry-run accounting.
    mapped_ok = 0
    unmappable = 0
    already = 0
    would_insert = 0
    shadowed = 0
    shadowed_ids: List[str] = []
    unmatched_customer = 0
    matched_customer = 0
    grand_total_sum = 0.0
    shipping_undershoot = 0.0
    refunded_orders = 0
    partially_refunded_orders = 0
    refunds_mappable = 0
    refunds_whole_order = 0
    refunds_amount_only = 0
    residual_refund_order_ids: List[str] = []
    partially_paid_orders = 0
    partially_paid_outstanding = 0.0
    per_year: Dict[int, Dict[str, float]] = defaultdict(lambda: {"count": 0, "gross": 0.0})
    per_rate_all: Dict[float, Dict[str, float]] = defaultdict(
        lambda: {"gross": 0.0, "taxable": 0.0, "tax": 0.0, "lines": 0}
    )

    for o in real:
        oid = str(o.get("id") or o.get("order_id") or "").strip()
        if not oid:
            unmappable += 1
            continue
        report = _map_for_report(dict(o), dbh)  # dict(o): don't mutate the source
        if report["items"] <= 0:
            unmappable += 1
            continue
        mapped_ok += 1
        existing = _existing_order(dbh, oid)
        if existing is not None and _is_bvi_shadow(existing):
            # SHADOWED: a pre-IMS bvi_import doc already holds this shopify_order_id.
            # It will dedupe the import but is NOT booked as revenue -- surface it
            # separately (skip-and-list), never lump it into 'already present'.
            shadowed += 1
            if len(shadowed_ids) < 50:
                shadowed_ids.append(oid)
        elif existing is not None:
            already += 1
        else:
            would_insert += 1
        grand_total_sum += report["grand_total"]
        shipping_undershoot += _shipping_total(o)
        fin = str(o.get("financial_status") or "").strip().lower()
        if fin == "refunded":
            refunded_orders += 1
        elif fin == "partially_refunded":
            partially_refunded_orders += 1
        elif fin == "partially_paid":
            # Booked as fully PAID at import (no phantom receivable) -- the
            # uncollected remainder is an AR WRITE-OFF the owner must see.
            partially_paid_orders += 1
            try:
                partially_paid_outstanding += float(o.get("total_outstanding") or 0)
            except (TypeError, ValueError):
                pass
        if fin in ("refunded", "partially_refunded"):
            sim = _simulate_refund_credit_notes(o, report["mapped_items"])
            refunds_mappable += sim["mappable"]
            refunds_whole_order += sim["whole_order"]
            refunds_amount_only += sim["amount_only"]
            if sim["amount_only"] and len(residual_refund_order_ids) < 50:
                residual_refund_order_ids.append(oid)
        py = per_year[report["year"]]
        py["count"] += 1
        py["gross"] += report["grand_total"]
        for r, agg in report["per_rate"].items():
            dst = per_rate_all[r]
            dst["gross"] += agg["gross"]
            dst["taxable"] += agg["taxable"]
            dst["tax"] += agg["tax"]
            dst["lines"] += agg["lines"]
        cid = _match_customer_readonly(dbh, dict(o))
        if cid:
            matched_customer += 1
        else:
            unmatched_customer += 1

    # ------------------------------------------------------------------ report
    print("")
    print("=" * 68)
    print("SHOPIFY ORDER-HISTORY IMPORT  --  %s" % ("APPLY" if apply else "DRY-RUN"))
    print("=" * 68)
    print(f"  Shopify orders scanned .............. {scanned}")
    print(f"  REAL (filter passes) ................ {len(real)}")
    print(f"  mapped OK (>=1 billable line) ....... {mapped_ok}")
    print(f"  unmappable (skipped) ................ {unmappable}")
    print(f"  already present as ONLINE order ..... {already}")
    print(f"  SHADOWED by pre-IMS bvi_import ...... {shadowed}")
    print(f"  WOULD INSERT ........................ {would_insert}")
    print(f"  customer matched (existing) ......... {matched_customer}")
    print(f"  customer UNMATCHED (guest sale) ..... {unmatched_customer}")
    print(f"  refunded orders (full) .............. {refunded_orders}")
    print(f"  partially_refunded orders .......... {partially_refunded_orders}")
    print(f"  sum of order totals (all mapped) .... Rs {grand_total_sum:,.2f}")
    print("")
    # P2: bvi_import shadow docs are EXCLUDED from finance/GST -- report them apart
    # from genuine online duplicates so nothing is silently lumped as 'idempotent'.
    print(f"  Pre-IMS bvi_import shadow docs (with shopify_order_id): {bvi_shadow_total}")
    if shadowed:
        preview = ", ".join(shadowed_ids)
        more = "" if shadowed <= len(shadowed_ids) else f" (+{shadowed - len(shadowed_ids)} more)"
        print(
            f"  ! {shadowed} REAL order(s) are SHADOWED by a bvi_import doc and will be"
        )
        print(
            "    skipped by dedupe WITHOUT being booked as revenue. Decide"
            " upgrade-in-place vs skip BEFORE --apply. Ids: "
            f"{preview}{more}"
        )
    print("")
    # P3: shipping is NOT mapped into grand_total (the live ingest path doesn't map
    # it either; adding it would change the tax math). Report the undershoot so the
    # Rs 23,14,013 sanity target is compared like-for-like (line-items-only).
    print(
        f"  shipping NOT booked (line-items-only undershoot): Rs {shipping_undershoot:,.2f}"
    )
    if refunded_orders or partially_refunded_orders:
        # SIMULATED line-mapping (same mapper the ingest uses) -- an honest CDNR
        # forecast, not an unconditional claim. Amount-only refunds on a
        # partially_refunded order book NO credit note (residual reported).
        print("  refunds (simulated line-mapping, same mapper as ingest):")
        print(f"    line-mappable (book per-refund CDNR) ...... {refunds_mappable}")
        print(f"    whole-order reversal fallback ............. {refunds_whole_order} order(s)")
        print(f"    amount-only/unmappable (NO CDNR booked) ... {refunds_amount_only} refund(s)")
        if residual_refund_order_ids:
            print(
                "    ! orders left with an UNMAPPED refund residual (GST stays "
                "overstated for them): "
                + ", ".join(residual_refund_order_ids)
            )
    if partially_paid_orders:
        print("")
        print(
            f"  ! partially_paid write-off: {partially_paid_orders} order(s) will be "
            f"booked as fully PAID;"
        )
        print(
            f"    Rs {partially_paid_outstanding:,.2f} outstanding (Shopify "
            f"total_outstanding) is written off as collected."
        )
    print("")
    print("  Sum of totals per year:")
    for yr in sorted(per_year):
        row = per_year[yr]
        print(f"    {yr}: {int(row['count']):>5} orders   Rs {row['gross']:,.2f}")
    print("")
    print("  GST-rate breakdown (by line gross, desc):")
    for r in sorted(per_rate_all, key=lambda k: per_rate_all[k]["gross"], reverse=True):
        agg = per_rate_all[r]
        print(
            f"    {r:>5.1f}%  lines={int(agg['lines']):>5}  "
            f"gross=Rs {agg['gross']:,.2f}  taxable=Rs {agg['taxable']:,.2f}  "
            f"tax=Rs {agg['tax']:,.2f}"
        )
    print("")
    print("  NOTE: historical orders mint NO GST invoice serial (invoice_number=None)")
    print("        so the live per-FY sequence is never consumed by back-dated sales;")
    print("        GST reconciliation falls back to the ONL- order_number.")
    print("        Sanity-check the total above against Rs 23,14,013.")
    print("=" * 68)

    summary = {
        "scanned": scanned,
        "real": len(real),
        "mapped_ok": mapped_ok,
        "unmappable": unmappable,
        "already_present": already,
        "shadowed_by_bvi_import": shadowed,
        "shadowed_ids_sample": shadowed_ids,
        "bvi_shadow_total": bvi_shadow_total,
        "would_insert": would_insert,
        "customer_matched": matched_customer,
        "customer_unmatched": unmatched_customer,
        "refunded_orders": refunded_orders,
        "partially_refunded_orders": partially_refunded_orders,
        "refunds_mappable": refunds_mappable,
        "refunds_whole_order": refunds_whole_order,
        "refunds_amount_only": refunds_amount_only,
        "residual_refund_order_ids": residual_refund_order_ids,
        "partially_paid_orders": partially_paid_orders,
        "partially_paid_outstanding": round(partially_paid_outstanding, 2),
        "shipping_undershoot": round(shipping_undershoot, 2),
        "grand_total_sum": round(grand_total_sum, 2),
        "applied": apply,
    }

    if not apply:
        if would_insert:
            print(
                f"[DRY-RUN] re-run with --apply to book {would_insert} historical "
                f"order(s) to store {store_id}."
            )
        return summary

    # ------------------------------------------------------------------- apply
    from api.services.online_order_mapper import map_shopify_order

    created = 0
    duplicate = 0
    skipped = 0
    shadow_skipped = 0
    errors = 0
    done = 0
    # Refund credit-note aggregation across the apply loop -- the per-order
    # summary ingest returns must reach the operator, not be discarded.
    cn_count = 0
    cn_gross = 0.0
    cn_tax = 0.0
    cn_unmapped = 0
    cn_whole_order_fallbacks = 0
    cn_failed = 0
    unmapped_refund_order_ids: List[str] = []
    cn_failed_order_ids: List[str] = []
    for o in real:
        oid = str(o.get("id") or o.get("order_id") or "").strip()
        if not oid:
            skipped += 1
            continue
        # P2: never let a pre-IMS bvi_import shadow doc silently swallow a real
        # order as a 'duplicate'. Skip-and-LIST it explicitly (the owner decides
        # upgrade-in-place vs skip out-of-band) so the count is honest.
        existing = _existing_order(dbh, oid)
        if existing is not None and _is_bvi_shadow(existing):
            shadow_skipped += 1
            print(f"[APPLY] order {oid} SHADOWED by bvi_import doc -- skipped (not booked)")
            continue
        payload = dict(o)
        # Deterministic attribution to the online store, independent of env config.
        payload["_ims_online_store_id"] = store_id
        try:
            res = map_shopify_order(
                payload, dbh, topic="orders/create", historical=True
            )
        except Exception as exc:  # noqa: BLE001 -- one bad order must not stop the run
            errors += 1
            print(f"[APPLY] order {oid} FAILED: {exc}")
            continue
        st = (res or {}).get("status")
        if st == "created":
            created += 1
        elif st in ("duplicate", "replayed"):
            duplicate += 1
        else:
            skipped += 1
        rcn = (res or {}).get("refund_credit_notes") or {}
        if isinstance(rcn, dict) and rcn:
            cn_count += int(rcn.get("credit_notes") or 0)
            cn_gross += float(rcn.get("gross") or 0.0)
            cn_tax += float(rcn.get("tax") or 0.0)
            if rcn.get("whole_order_fallback"):
                cn_whole_order_fallbacks += 1
            um = int(rcn.get("unmapped_refunds") or 0)
            if um > 0:
                cn_unmapped += um
                if len(unmapped_refund_order_ids) < 50:
                    unmapped_refund_order_ids.append(oid)
            cf = int(rcn.get("cn_failed") or 0)
            if cf > 0:
                cn_failed += cf
                if len(cn_failed_order_ids) < 50:
                    cn_failed_order_ids.append(oid)
        done += 1
        if done % 100 == 0:
            print(
                f"[APPLY] progress {done}/{len(real)}  "
                f"created={created} duplicate={duplicate} skipped={skipped}"
            )

    print("")
    print("=" * 68)
    print("APPLY RESULT")
    print(f"  created ............... {created}")
    print(f"  duplicate ............. {duplicate}")
    print(f"  shadowed (skipped) .... {shadow_skipped}")
    print(f"  skipped ............... {skipped}")
    print(f"  errors ................ {errors}")
    print("  -- refund credit notes (CDNR) --")
    print(f"  credit notes booked ... {cn_count}  "
          f"(gross Rs {cn_gross:,.2f}, tax Rs {cn_tax:,.2f})")
    print(f"  whole-order fallbacks . {cn_whole_order_fallbacks}")
    print(f"  UNMAPPED refunds ...... {cn_unmapped}  (residual -- NO credit note; "
          f"GST stays overstated for these)")
    if unmapped_refund_order_ids:
        print(
            "  ! shopify order ids with an unmapped refund residual: "
            + ", ".join(unmapped_refund_order_ids)
        )
    print(f"  CN FAILED ............. {cn_failed}  (re-run to heal -- the booking "
          f"is idempotent and re-runs on the duplicate path)")
    if cn_failed_order_ids:
        print(
            "  ! shopify order ids with a FAILED credit-note booking: "
            + ", ".join(cn_failed_order_ids)
        )
    print("=" * 68)

    summary.update(
        {
            "created": created,
            "duplicate": duplicate,
            "shadow_skipped": shadow_skipped,
            "skipped_apply": skipped,
            "errors": errors,
            "credit_notes_booked": cn_count,
            "credit_notes_gross": round(cn_gross, 2),
            "credit_notes_tax": round(cn_tax, 2),
            "credit_notes_whole_order_fallbacks": cn_whole_order_fallbacks,
            "credit_notes_unmapped_refunds": cn_unmapped,
            "credit_notes_failed": cn_failed,
            "cn_failed_order_ids": cn_failed_order_ids,
            "unmapped_refund_order_ids": unmapped_refund_order_ids,
        }
    )
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Import the Shopify order history as real IMS orders (dry-run by default)."
    )
    parser.add_argument(
        "--mongo-uri",
        default=None,
        help="Mongo URI; falls back to MONGODB_URL/MONGO_URL then MONGO_* components.",
    )
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Book the orders. Without this flag the script is a dry-run.",
    )
    parser.add_argument(
        "--store-id",
        default=os.getenv("ONLINE_STORE_ID", _DEFAULT_ONLINE_STORE_ID),
        help=f"Online store to attribute orders to (default {_DEFAULT_ONLINE_STORE_ID}).",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=250,
        help="Shopify REST page size (max 250).",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.6,
        help="Seconds to sleep between REST pages (rate-limit courtesy).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of orders FETCHED (for a small trial run).",
    )
    args = parser.parse_args()
    run(
        mongo_uri=resolve_mongo_uri(args.mongo_uri),
        db_name=args.db,
        apply=args.apply,
        store_id=args.store_id,
        page_size=max(1, min(args.page_size, 250)),
        sleep_s=max(0.0, args.sleep),
        cap=args.limit,
    )


if __name__ == "__main__":
    sys.exit(main())
