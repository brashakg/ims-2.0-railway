#!/usr/bin/env python3
"""
IMS 2.0 - Migrate users.roles SALES_CASHIER -> SALES_STAFF
==========================================================
Runbook-only script. NOT in CI. Idempotent: only touches user docs whose roles
array still contains the deprecated SALES_CASHIER role, rewrites it to the
survivor SALES_STAFF, and de-duplicates (a user holding BOTH ends up with a
single SALES_STAFF). Re-running is a clean no-op.

WHY
---
Backlog item #12 (owner decision): SALES_CASHIER and SALES_STAFF were
functionally identical (same 10% discount cap, same POS / module gating, same
geo-fence), so SALES_CASHIER is merged INTO SALES_STAFF. The application already
treats SALES_CASHIER as an alias of SALES_STAFF at the auth layer
(auth.decode_token + user_roles.normalize_roles), so an un-migrated user is never
locked out. This script makes the data correct AT REST so the deprecated role no
longer appears in stored documents.

SAFE: at the time of writing prod has no real SALES_CASHIER users, so this is
expected to report 0 changes there. It is provided so the merge is complete and
re-runnable if any such user is ever created via an old client.

USAGE
-----
Dry-run (DEFAULT - reports, writes nothing):
    railway run python scripts/migrate_sales_cashier_to_sales_staff.py

Apply (writes the rewritten roles):
    railway run python scripts/migrate_sales_cashier_to_sales_staff.py --apply

Locally with an explicit URI:
    python scripts/migrate_sales_cashier_to_sales_staff.py --mongo-uri mongodb://... --apply

On Railway the MONGO_* component vars are injected by `railway run`; this script
builds the connection URI from them when MONGODB_URL/MONGO_URL is unset (mirrors
scripts/backfill_order_customer_id.py).
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

DEPRECATED_ROLE = "SALES_CASHIER"
SURVIVOR_ROLE = "SALES_STAFF"


def resolve_mongo_uri(explicit: Optional[str]) -> Optional[str]:
    """Prefer an explicit/standard URI; otherwise assemble one from the MONGO_*
    component vars Railway injects (no single MONGODB_URL there).

    MONGO_PUBLIC_URL is checked first so this runbook works locally via
    `railway run -s MongoDB ...` (the internal `mongodb.railway.internal` host is
    only resolvable inside Railway's network)."""
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


def _merged_roles(roles: List[str]) -> List[str]:
    """Rewrite SALES_CASHIER -> SALES_STAFF and de-duplicate, preserving order."""
    out: List[str] = []
    seen = set()
    for r in roles or []:
        nr = SURVIVOR_ROLE if r == DEPRECATED_ROLE else r
        if nr not in seen:
            out.append(nr)
            seen.add(nr)
    return out


def run(*, mongo_uri: Optional[str], db_name: str, apply: bool) -> dict:
    if not mongo_uri:
        raise SystemExit(
            "No Mongo connection. Set MONGODB_URL / MONGO_URL, pass --mongo-uri, "
            "or run via `railway run` so the MONGO_* component vars are injected."
        )
    try:
        from pymongo import MongoClient
    except ImportError:
        raise SystemExit("pymongo not installed; run `pip install pymongo`.")

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    db = client[db_name]
    users = db["users"]

    # Only users whose roles array still carries the deprecated role.
    user_filter = {"roles": DEPRECATED_ROLE}

    scanned = 0
    updated = 0
    examples: List[str] = []

    cursor = users.find(user_filter, {"user_id": 1, "username": 1, "roles": 1, "_id": 1})
    for doc in cursor:
        scanned += 1
        old_roles = doc.get("roles") or []
        new_roles = _merged_roles(old_roles)
        if new_roles == old_roles:
            # Already only contains the survivor (shouldn't happen given the
            # filter, but keep it a no-op for safety).
            continue
        if len(examples) < 10:
            examples.append(doc.get("username") or doc.get("user_id") or str(doc.get("_id")))
        if apply:
            key = (
                {"user_id": doc["user_id"]}
                if doc.get("user_id")
                else {"_id": doc["_id"]}
            )
            res = users.update_one(key, {"$set": {"roles": new_roles}})
            updated += res.modified_count

    summary = {
        "scanned": scanned,
        "updated": updated,
        "applied": apply,
        "examples": examples,
    }
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] users with {DEPRECATED_ROLE}={scanned} updated={updated}")
    if examples:
        print(f"[{mode}] affected (first {len(examples)}): {', '.join(examples)}")
    if not apply and scanned:
        print(
            f"[DRY-RUN] re-run with --apply to rewrite {scanned} user(s) "
            f"{DEPRECATED_ROLE} -> {SURVIVOR_ROLE}."
        )
    if scanned == 0:
        print(f"[{mode}] nothing to do - no user carries {DEPRECATED_ROLE}.")
    return summary


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Migrate users.roles SALES_CASHIER -> SALES_STAFF (idempotent, "
            "dry-run by default)."
        )
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
        help="Write changes. Without this flag the script is a dry-run.",
    )
    args = parser.parse_args()
    run(
        mongo_uri=resolve_mongo_uri(args.mongo_uri),
        db_name=args.db,
        apply=args.apply,
    )


if __name__ == "__main__":
    sys.exit(main())
