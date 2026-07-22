# Pull Shopify Payments PAYOUTS (id, status, date, amount, currency) and mirror
# them into the Mongo `shopify_payouts` collection so Finance can reconcile
# settlements. READ-ONLY vs Shopify -- this script never mutates the store.
#
# DRY-RUN BY DEFAULT (prints what WOULD be written, no Mongo writes). Pass
# --apply to upsert. Idempotent by payout id, so re-running is safe. Fail-soft:
# a store without Shopify Payments enabled surfaces "not enabled" cleanly.
#
# Run against prod via Railway so Shopify creds + Mongo URL are injected, e.g.:
#   railway run --service MongoDB -- .venv\Scripts\python.exe scripts\pull_shopify_payouts.py
#   railway run --service MongoDB -- .venv\Scripts\python.exe scripts\pull_shopify_payouts.py --apply

import asyncio
import os
import sys

from pymongo import MongoClient

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
)

from api.services import shopify_payouts  # noqa: E402


def _resolve_limit(argv):
    for i, arg in enumerate(argv):
        if arg == "--limit" and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except ValueError:
                return 50
        if arg.startswith("--limit="):
            try:
                return int(arg.split("=", 1)[1])
            except ValueError:
                return 50
    return 50


def main():
    apply = "--apply" in sys.argv
    limit = _resolve_limit(sys.argv)

    url = os.environ.get("MONGO_PUBLIC_URL") or os.environ.get("MONGODB_URL")
    db = None
    if url:
        db = MongoClient(url, serverSelectionTimeoutMS=20000)["ims_2_0"]
    else:
        print(
            "NOTE: no MONGO_PUBLIC_URL / MONGODB_URL -- running without a DB "
            "(upsert is a no-op; fetch still runs if creds resolve)."
        )

    result = asyncio.run(shopify_payouts.pull_payouts(db, apply=apply, limit=limit))

    print(f"applied   : {result.get('applied')}")
    print(f"ok        : {result.get('ok')}")
    print(f"enabled   : {result.get('enabled')}")
    print(f"reason    : {result.get('reason')}")
    print(f"fetched   : {result.get('fetched')}")
    print(f"upserted  : {result.get('upserted')}")
    print(f"updated   : {result.get('updated')}")
    if result.get("sample"):
        print("sample    :")
        for row in result["sample"]:
            print(f"  - {row}")
    if not apply:
        print("\nDRY-RUN (no Mongo writes). Re-run with --apply to upsert payouts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
