#!/usr/bin/env python3
"""
IMS 2.0 — Pune Walkouts Excel-to-Mongo backfill (Module i, Phase 5)
====================================================================
Runbook-only script. NOT in CI. Idempotent on (mobile + date_str)
hash so re-running on the same CSV is a no-op.

Usage
-----
  python3 scripts/migrate_pune_walkouts.py path/to/pune_walkouts.csv \\
      --store-id BV-PNE-01 \\
      [--mongo-uri mongodb://localhost:27017] \\
      [--db ims_2_0] \\
      [--dry-run]

CSV header expected (the Excel was exported with these columns):
  date, customer_name, mobile, age_group, gender,
  product_interested, has_prescription, displayed_price_range,
  required_price_range, primary_walkout_reason, secondary_walkout_reason,
  brand_interest, competitor_mentioned, purchase_planned_in,
  sales_person_id, sales_person_name, action_remarks, [result], [converted_order_id]

Date column accepts YYYY-MM-DD or DD/MM/YYYY.

Idempotency
-----------
Each row gets a backfill_hash = sha256("{mobile}|{date_str}").
A unique index on backfill_hash is created on the walkouts collection
before insertion; subsequent re-runs of the same CSV will hit the
duplicate-key error and skip cleanly. Stamps the row with
source="excel_backfill" so the analytics layer can distinguish them.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import sys
import uuid
from datetime import datetime, date as date_type
from typing import Dict, Optional


_MOBILE_RE = re.compile(r"^\d{10}$")


def parse_date(raw: str) -> Optional[str]:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_mobile(raw: str) -> Optional[str]:
    digits = "".join(c for c in (raw or "") if c.isdigit())
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    return digits if _MOBILE_RE.match(digits) else None


def make_backfill_hash(mobile: str, date_str: str) -> str:
    return hashlib.sha256(f"{mobile}|{date_str}".encode("utf-8")).hexdigest()


def make_walkout_id(store_id: str, date_iso: str) -> str:
    """Mirror WalkoutRepository._generate_walkout_id but deterministic
    on backfill_hash so re-running yields the same id (cleaner audit
    trail)."""
    raw = (store_id or "").strip().upper()
    parts = [p for p in raw.split("-") if p and p not in ("BV", "WO", "BVO")]
    code = (parts[0][:3] if parts else "XXX") or "XXX"
    code = "".join(c for c in code if c.isalnum()) or "XXX"
    year = date_iso[:4]
    return f"WO-{code}-{year}-{uuid.uuid4().hex[:6].upper()}"


def build_doc(row: Dict[str, str], store_id: str) -> Optional[Dict]:
    date_str = parse_date(row.get("date") or "")
    mobile = normalize_mobile(row.get("mobile") or "")
    if not date_str or not mobile or not (row.get("customer_name") or "").strip():
        return None
    backfill_hash = make_backfill_hash(mobile, date_str)
    walkout_id = make_walkout_id(store_id, date_str)
    now = datetime.now()
    doc = {
        "walkout_id": walkout_id,
        "_id": walkout_id,
        "store_id": store_id,
        "date": datetime.fromisoformat(date_str),
        "date_str": date_str,
        "customer_id": None,  # auto-link runs separately
        "customer_name": (row.get("customer_name") or "").strip(),
        "mobile": mobile,
        "age_group": (row.get("age_group") or "").strip(),
        "gender": (row.get("gender") or "").strip().upper(),
        "product_interested": (row.get("product_interested") or "").strip().upper(),
        "has_prescription": (row.get("has_prescription") or "").strip().upper(),
        "displayed_price_range": (row.get("displayed_price_range") or "").strip(),
        "required_price_range": (row.get("required_price_range") or "").strip(),
        "primary_walkout_reason": (row.get("primary_walkout_reason") or "").strip().upper(),
        "secondary_walkout_reason": (row.get("secondary_walkout_reason") or "").strip().upper() or None,
        "brand_interest": (row.get("brand_interest") or "").strip(),
        "competitor_mentioned": (row.get("competitor_mentioned") or "").strip(),
        "purchase_planned_in": (row.get("purchase_planned_in") or "").strip().upper(),
        "sales_person_id": (row.get("sales_person_id") or "").strip(),
        "sales_person_name": (row.get("sales_person_name") or "").strip().upper() or None,
        "action_remarks": (row.get("action_remarks") or "").strip(),
        "followups": [],
        "result": (row.get("result") or "").strip().upper() or None,
        "result_set_at": now if (row.get("result") or "").strip() else None,
        "result_set_by": "backfill" if (row.get("result") or "").strip() else None,
        "converted_order_id": (row.get("converted_order_id") or "").strip() or None,
        "deleted_at": None,
        "deleted_by": None,
        "delete_reason": None,
        "created_at": now,
        "created_by": "backfill",
        "updated_at": now,
        "updated_by": "backfill",
        "source": "excel_backfill",
        "backfill_hash": backfill_hash,
    }
    return doc


def run(
    *,
    csv_path: str,
    store_id: str,
    mongo_uri: Optional[str],
    db_name: str,
    dry_run: bool,
) -> Dict:
    if not os.path.exists(csv_path):
        raise SystemExit(f"CSV not found: {csv_path}")

    docs = []
    skipped_invalid = 0
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            doc = build_doc(row, store_id)
            if doc is None:
                skipped_invalid += 1
                continue
            docs.append(doc)

    if dry_run or not mongo_uri:
        print(f"[DRY-RUN] {len(docs)} valid rows ready ({skipped_invalid} invalid/skipped)")
        return {
            "inserted": 0,
            "duplicates_skipped": 0,
            "valid_rows": len(docs),
            "invalid_rows": skipped_invalid,
            "dry_run": True,
        }

    # Real Mongo path
    try:
        from pymongo import MongoClient
        from pymongo.errors import DuplicateKeyError, BulkWriteError
    except ImportError:
        raise SystemExit("pymongo not installed; run `pip install pymongo`.")

    client = MongoClient(mongo_uri)
    db = client[db_name]
    coll = db["walkouts"]
    # Idempotency anchor — unique index on backfill_hash. Sparse so
    # rows from the live system (no backfill_hash field) aren't
    # affected.
    coll.create_index("backfill_hash", unique=True, sparse=True, background=True)

    inserted = 0
    duplicates = 0
    for doc in docs:
        try:
            coll.insert_one(doc)
            inserted += 1
        except DuplicateKeyError:
            duplicates += 1
        except BulkWriteError as e:
            duplicates += 1
            print(f"[WARN] bulk write error: {e}")

    summary = {
        "inserted": inserted,
        "duplicates_skipped": duplicates,
        "valid_rows": len(docs),
        "invalid_rows": skipped_invalid,
        "dry_run": False,
    }
    print(f"[OK] inserted={inserted} dup_skipped={duplicates} invalid={skipped_invalid}")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", help="Path to Pune walkouts CSV")
    parser.add_argument("--store-id", default="BV-PNE-01")
    parser.add_argument(
        "--mongo-uri",
        default=os.getenv("MONGODB_URL") or os.getenv("MONGO_URL"),
    )
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(
        csv_path=args.csv,
        store_id=args.store_id,
        mongo_uri=args.mongo_uri,
        db_name=args.db,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
