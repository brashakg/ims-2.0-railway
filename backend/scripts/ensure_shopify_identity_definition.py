"""Ensure the Shopify product metafield definition ims.ims_product_id exists
with UNIQUE VALUES (audit SF-02, PR #1227) -- before the first-publish run.

Why: every first publish now stamps the IMS product id on the new Shopify
product. With the unique definition in place Shopify itself refuses a second
product carrying the same IMS id, and IMS can read "is this product already
on Shopify?" directly (productByIdentifier) instead of searching.

What it does (shopify_push.ensure_identity_definition):
  1. reads the definition;
  2. there with unique values on       -> nothing to do;
     there with unique values off      -> switches unique values on;
     not there                         -> reads the ims_product_id value of
                                          every product IMS has on Shopify;
                                          if two products carry one IMS id it
                                          STOPS and names them (delete the
                                          extra copy first); else creates the
                                          definition with unique values.
The push also runs this itself before its first create of the process; this
script is the explicit, previewable run.

DRY-RUN by default (reads only). Needs the Shopify credentials and Mongo
(the backend service's variables):
  railway run --service <backend> -- .venv\\Scripts\\python.exe backend\\scripts\\ensure_shopify_identity_definition.py
  railway run --service <backend> -- .venv\\Scripts\\python.exe backend\\scripts\\ensure_shopify_identity_definition.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _connect():
    from database.connection import DatabaseConfig, get_db, init_db

    mongo_url = os.getenv("MONGO_PUBLIC_URL") or os.getenv("MONGODB_URL") or os.getenv("MONGO_URL")
    config = (
        DatabaseConfig.from_uri(mongo_url, database="ims_2_0") if mongo_url else DatabaseConfig.from_env()
    )
    return get_db() if init_db(config) else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure the unique ims.ims_product_id definition on Shopify.")
    parser.add_argument("--apply", action="store_true", help="Write to Shopify. Without it: report only.")
    args = parser.parse_args()

    conn = _connect()
    if conn is None or not conn.is_connected:
        print("[ERROR] Could not connect to MongoDB (run via railway run).")
        return 2
    from api.services import shopify_push

    db = conn.db
    if args.apply:
        live, reason = shopify_push._live_or_reason(db)
        if not live:
            print(f"[REFUSED] Shopify writes are not live: {reason}")
            return 2
    result = asyncio.run(shopify_push.ensure_identity_definition(db, apply=args.apply))
    print(f"ready:   {result.get('ready')}")
    print(f"action:  {result.get('action')}")
    print(f"message: {result.get('message')}")
    return 0 if result.get("ready") or not args.apply else 1


if __name__ == "__main__":
    raise SystemExit(main())
