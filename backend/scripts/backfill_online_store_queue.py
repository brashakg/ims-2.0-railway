"""
IMS 2.0 - One-time backfill: queue the existing catalogue for the Shopify push
=============================================================================
The Online Store push selects rows -- and computes the `pending` count the
screen shows -- from ONE dirty flag, `ecom.locally_modified`. Nothing ever set
it before PR #1003, so the whole EXISTING catalogue (~59 items after the
2026-07-29 purge) carries no flag: pressing "push all pending" today would sweep
nothing and the owner would watch a button do visibly nothing.

This stamps that flag ONCE on the rows that should be on the website and are not
there yet. It queues; it does NOT publish. Nothing here talks to Shopify --
after this run someone still has to press publish, and every row still goes
through the same push engine, the same gates and the same refusals.

DRY-RUN BY DEFAULT: prints the plan and writes NOTHING. Pass --apply to write.

WHAT IS SKIPPED, AND WHY (every bucket is printed):
  already_live    ecom.shopify_product_id present AND ecom.status == PUBLISHED
                  -- IMS has recorded a successful publish, so the row is on the
                  live storefront. Never touched: re-queueing a live product
                  would re-push (and re-price) it for no reason. A mapped row
                  that was never published (status DRAFT / absent) is NOT live:
                  it went up as an invisible draft and IS queued, which is the
                  main thing this backfill rescues.
  already_queued  ecom.locally_modified is already True -- nothing to do. This
                  is what makes a second run a no-op.
  no_photograph   Owner ruling 2026-08-25, "no photo, no publish". The push
                  refuses these anyway; queueing one would just park a
                  guaranteed refusal in the queue. Printed by SKU so the owner
                  knows exactly which products need a photograph.
  archived        ecom.status ARCHIVED -- a deliberate retirement, never
                  resurrected by a backfill.
  inactive        is_active is False (a soft-deleted / withdrawn product).
  blocked         a member of an online_sync_blocked collection (the SUPERADMIN
                  "block from online" ban). FAIL-CLOSED: if the block config
                  cannot be read, the run ABORTS rather than queue a possibly
                  banned product.

IDEMPOTENT: run it twice and the second run reports 0 to queue (every row it
stamped is now `already_queued`, and every row a subsequent push shipped is
`already_live`).

USAGE (run via Railway so the MONGO_* vars are injected):
  # preview (default -- writes nothing):
  railway run --service MongoDB -- .venv\\Scripts\\python.exe backend\\scripts\\backfill_online_store_queue.py
  # write the flags:
  railway run --service MongoDB -- .venv\\Scripts\\python.exe backend\\scripts\\backfill_online_store_queue.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Set, Tuple

# Make the backend package importable whether run from repo root or backend/.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from api.services.shopify_push import product_photo_urls  # noqa: E402

# The buckets, in the order they are decided and printed.
BUCKETS = (
    "already_live",
    "already_queued",
    "blocked",
    "inactive",
    "archived",
    "no_photograph",
    "queue",
)


def classify(doc: Dict[str, Any], blocked: Set[str]) -> str:
    """Which bucket this catalogue row falls in. Pure; never raises.

    ORDER MATTERS. `already_live` wins over everything: a row on the live
    storefront is never touched by a backfill, whatever else is true of it.
    After that every refusal is checked BEFORE `queue`, so no row the push would
    refuse is ever queued.

    "ALREADY MAPPED" IS NOT "ALREADY LIVE". A Shopify id alone proves only that
    SOME press reached Shopify -- and the premise of this whole change is that
    every one of those presses sent a DRAFT, published to no sales channel and
    invisible. Skipping on the id alone would skip exactly the rows the backfill
    exists to rescue. Live therefore requires BOTH the id AND a recorded
    successful publish: ecom.status == "PUBLISHED", which is only ever written
    after publishablePublish succeeded (shopify_push._writeback_product) and is
    cleared back to DRAFT by a take-down.
    """
    ecom = doc.get("ecom") or {}
    if (
        ecom.get("shopify_product_id")
        and str(ecom.get("status") or "").upper() == "PUBLISHED"
    ):
        return "already_live"
    if ecom.get("locally_modified"):
        return "already_queued"
    if doc.get("sku") and doc.get("sku") in blocked:
        return "blocked"
    if doc.get("is_active") is False:
        return "inactive"
    if str(ecom.get("status") or "").upper() == "ARCHIVED":
        return "archived"
    if not product_photo_urls(doc):
        return "no_photograph"
    return "queue"


def backfill(
    products, blocked: Set[str], *, apply: bool
) -> Tuple[Dict[str, int], List[str], List[str]]:
    """Walk the catalogue, classify every row, and (when apply) stamp the dirty
    flag on the `queue` rows. Takes the collection so it is unit-testable
    against a fake. Returns (counts, queued_labels, no_photo_labels).

    The write is a READ-MERGE-WRITE of the WHOLE `ecom` sub-doc, not a
    dot-notation $set -- the same idiom shopify_push._writeback_product uses,
    for the same reason: it keeps the sibling ecom fields (status / handle /
    seo) explicitly intact and behaves identically on every collection double.
    `status` defaults to DRAFT when absent so a queued row always belongs to a
    status bucket on the Online Store screen (the fix in 6eede9b).
    """
    counts = {b: 0 for b in BUCKETS}
    queued: List[str] = []
    no_photo: List[str] = []

    for doc in products.find({}):
        bucket = classify(doc, blocked)
        counts[bucket] += 1
        label = str(doc.get("sku") or doc.get("id") or "?")
        if bucket == "no_photograph":
            no_photo.append(label)
            continue
        if bucket != "queue":
            continue
        queued.append(label)
        if not apply:
            continue
        ecom = dict(doc.get("ecom") or {})
        ecom["locally_modified"] = True
        ecom.setdefault("status", "DRAFT")
        products.update_one({"id": doc.get("id")}, {"$set": {"ecom": ecom}})

    return counts, queued, no_photo


def _connect():
    """Connect to MongoDB the way api/main.py does (mirrors the sibling
    backfills). Returns the DatabaseConnection or None."""
    from database.connection import init_db, get_db, DatabaseConfig

    mongo_url = (
        os.getenv("MONGO_PUBLIC_URL")
        or os.getenv("MONGODB_URL")
        or os.getenv("MONGO_URL")
    )
    config = (
        DatabaseConfig.from_uri(mongo_url, database="ims_2_0")
        if mongo_url
        else DatabaseConfig.from_env()
    )
    if init_db(config):
        return get_db()
    return None


def run(apply: bool) -> int:
    conn = _connect()
    if conn is None or not conn.is_connected:
        print(
            "[ERROR] Could not connect to MongoDB. Run via `railway run "
            "--service MongoDB` so the MONGO_* vars are injected. Nothing changed."
        )
        return 2
    db = conn.db
    products = db["catalog_products"]

    rows = list(products.find({}))
    # FAIL-CLOSED block classification, resolved ONCE for the whole catalogue
    # (the same batch classifier the push sweep uses).
    from api.services.online_block import classify_blocked_skus

    blocked, verifiable = classify_blocked_skus(
        db, [r.get("sku") for r in rows if r.get("sku")]
    )
    if not verifiable:
        print(
            "[ABORT] the online-block config could not be read, so a banned "
            "product cannot be told apart from a clean one. Nothing changed."
        )
        return 2

    mode = "APPLY" if apply else "DRY-RUN"
    counts, queued, no_photo = backfill(products, blocked, apply=apply)

    print("=" * 70)
    print("ONLINE-STORE QUEUE BACKFILL  [%s]" % mode)
    print("=" * 70)
    print("  catalogue rows read ................... %d" % len(rows))
    print("  ALREADY LIVE on Shopify (untouched) ... %d" % counts["already_live"])
    print("  already queued (nothing to do) ........ %d" % counts["already_queued"])
    print("  blocked from online (untouched) ....... %d" % counts["blocked"])
    print("  inactive / withdrawn (untouched) ...... %d" % counts["inactive"])
    print("  archived (untouched) .................. %d" % counts["archived"])
    print("  NO PHOTOGRAPH -- not queued ........... %d" % counts["no_photograph"])
    print(
        "  %s ...................... %d"
        % ("QUEUED" if apply else "WOULD QUEUE", counts["queue"])
    )
    print("")
    if no_photo:
        print("  ! these need a photograph before they can go live:")
        for label in no_photo:
            print("      %s" % label)
        print("")
    if queued:
        print("  %s:" % ("queued" if apply else "would queue"))
        for label in queued[:100]:
            print("      %s" % label)
        if len(queued) > 100:
            print("      ... and %d more" % (len(queued) - 100))
        print("")
    if not apply:
        print(
            "[DRY-RUN] nothing was written. Re-run with --apply to queue "
            "%d product(s)." % counts["queue"]
        )
    else:
        print(
            "[APPLY] %d product(s) queued. They are NOT live yet -- someone "
            "still has to press publish. Re-running is a no-op." % counts["queue"]
        )
    print("=" * 70)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Queue the existing catalogue for the Shopify push by stamping "
            "ecom.locally_modified. Dry-run by default; queues only, never "
            "publishes."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the flags. Without this the script is a dry-run.",
    )
    args = parser.parse_args()
    return run(apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
