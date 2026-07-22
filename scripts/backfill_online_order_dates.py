#!/usr/bin/env python3
"""
IMS 2.0 - Backfill online-order date fields: ISO STRING -> naive-UTC BSON datetime
=================================================================================
Runbook-only script. NOT in CI. DRY-RUN by default (reads + reports, writes
NOTHING); pass --apply to convert.

WHY
---
Online orders (Shopify webhook ingest + the order-history import) used to persist
created_at / updated_at / invoice_date as ISO STRINGS (now.isoformat()), while
every date-windowed finance/GST query bounds created_at with naive-UTC DATETIMES
(finance._parse_range_dt / _apply_created_at_range, reports.ist_day_start_utc).
MongoDB type-bracketing means a Date-typed $gte/$lt range NEVER matches a STRING
field, so every string-dated online order was silently invisible to GSTR-1/3B, the
GST cross-check, GST reconciliation, P&L and cash-flow.

shopify_ingest now writes DATETIMES for new orders. This script converts the
EXISTING string-dated online orders (the 2 live ones + any others with
source='shopify' or channel='ONLINE') so the whole online book becomes visible.

SCOPE (safe): only orders whose created_at is CURRENTLY a string AND that are
online (source='shopify' OR channel='ONLINE'). Offline POS orders already store a
datetime (BaseRepository._add_timestamps) and are never touched. Idempotent:
re-running converts nothing once every online order is a datetime.

USAGE (run via Railway so MONGO_* is injected; I run this):
  Dry-run (DEFAULT -- prints the plan, writes nothing):
    railway run --service MongoDB -- .venv\\Scripts\\python.exe scripts\\backfill_online_order_dates.py
  Apply (converts the dates):
    railway run --service MongoDB -- .venv\\Scripts\\python.exe scripts\\backfill_online_order_dates.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Make the backend package importable (mirrors import_shopify_order_history.py).
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
)

# The date fields on an order doc that must be BSON datetimes for finance/GST.
_DATE_FIELDS = ("created_at", "updated_at", "invoice_date")


def resolve_mongo_uri(explicit: Optional[str]) -> Optional[str]:
    """Prefer an explicit/standard URI; else assemble from the MONGO_* component
    vars Railway injects (MONGO_PUBLIC_URL first, so `railway run -s MongoDB`
    works from outside Railway's private network)."""
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
    """Connect the app's connection singleton and return the seeded-db handle."""
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


def _to_naive_utc(raw: Any) -> Optional[datetime]:
    """Parse an ISO timestamp string to a NAIVE-UTC datetime (the exact shape
    finance/GST windows compare against). Returns None when not a parseable string
    (a value that is already a datetime is left to the caller to skip)."""
    if not isinstance(raw, str):
        return None
    try:
        s = raw.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return None


def _online_string_dated_filter() -> Dict[str, Any]:
    """Online orders whose created_at is CURRENTLY a string. `$type: string`
    selects only the not-yet-converted docs, so the run is idempotent."""
    return {
        "created_at": {"$type": "string"},
        "$or": [{"source": "shopify"}, {"channel": "ONLINE"}],
    }


def run(
    *, mongo_uri: Optional[str], db_name: str, apply: bool
) -> Dict[str, Any]:
    if not mongo_uri:
        raise SystemExit(
            "No Mongo connection. Set MONGODB_URL / MONGO_URL, pass --mongo-uri, or "
            "run via `railway run` so the MONGO_* component vars are injected."
        )
    dbh = connect_db(mongo_uri, db_name)
    coll = dbh.get_collection("orders")
    if coll is None:
        raise SystemExit("[ERROR] orders collection unavailable.")

    flt = _online_string_dated_filter()
    try:
        candidates: List[Dict[str, Any]] = list(coll.find(flt))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"[ERROR] query failed: {exc}")

    total = len(candidates)
    converted = 0
    unparseable = 0
    fields_set = 0
    samples: List[str] = []

    for doc in candidates:
        update: Dict[str, Any] = {}
        for field in _DATE_FIELDS:
            dt = _to_naive_utc(doc.get(field))
            if dt is not None:
                update[field] = dt
        if not update:
            unparseable += 1
            continue
        converted += 1
        fields_set += len(update)
        if len(samples) < 20:
            samples.append(
                f"{doc.get('order_id') or doc.get('_id')} "
                f"created_at={doc.get('created_at')} -> {update.get('created_at')}"
            )
        if apply:
            try:
                coll.update_one(
                    {"_id": doc.get("_id")}, {"$set": update}
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[APPLY] update failed for {doc.get('_id')}: {exc}")

    print("")
    print("=" * 68)
    print("ONLINE-ORDER DATE BACKFILL  --  %s" % ("APPLY" if apply else "DRY-RUN"))
    print("=" * 68)
    print(f"  string-dated online orders found ...... {total}")
    print(f"  would convert (>=1 parseable field) ... {converted}")
    print(f"  date fields to set (created/updated/invoice): {fields_set}")
    print(f"  unparseable (left as-is) .............. {unparseable}")
    print("")
    if samples:
        print("  sample conversions:")
        for s in samples:
            print(f"    {s}")
        print("")
    if not apply:
        if converted:
            print(f"[DRY-RUN] re-run with --apply to convert {converted} online order(s).")
        else:
            print("[DRY-RUN] nothing to convert (all online orders already datetime).")
    else:
        print(f"[APPLY] converted {converted} online order(s) to BSON datetimes.")
    print("=" * 68)

    return {
        "found": total,
        "converted": converted,
        "fields_set": fields_set,
        "unparseable": unparseable,
        "applied": apply,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Backfill online-order created_at/updated_at/invoice_date from ISO "
            "strings to BSON datetimes (dry-run by default)."
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
        help="Write the conversions. Without this flag the script is a dry-run.",
    )
    args = parser.parse_args()
    run(
        mongo_uri=resolve_mongo_uri(args.mongo_uri),
        db_name=args.db,
        apply=args.apply,
    )


if __name__ == "__main__":
    sys.exit(main())
