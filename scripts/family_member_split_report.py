#!/usr/bin/env python3
"""
IMS 2.0 - Family-member splits: READ-ONLY report
=================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252). WRITES NOTHING.

WHY
---
Customer identity is mobile-primary. A person can be recorded as a FAMILY
MEMBER (customers.patients[].mobile) on someone else's account AND as their
own top-level customer (customers.mobile / .phone). It happened from BOTH
sides: the create door only ever checked the top-level number (forward), and
the member-adding doors never checked it at all (reverse). Each such split
scatters one person's prescriptions and purchase history across two records,
and Rx reminders run off that history. Both directions are now refused (owner
ruling 2026-09-04); this script lists the EXISTING ones so the owner can decide
per case (promote / merge / leave). It never merges or edits anything.

DIRECTION
---------
A split is the same data state whichever door made it, and member rows carry
no timestamp, so the direction is only PROVABLE one way: when the own account
was created BEFORE the family account existed, the member row can only have
been added afterwards -> REVERSE. Everything else is reported as UNKNOWN,
never guessed.

PRIVACY
-------
Prints COUNTS and IDS only -- never a name, never a number.

USAGE
-----
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/family_member_split_report.py

Locally with an explicit URI:
    python scripts/family_member_split_report.py --mongo-uri mongodb://...

Connection resolution: --mongo-uri, else MONGO_PUBLIC_URL, else MONGODB_URI,
else MONGODB_URL/MONGO_URL, else the MONGO_* component vars Railway injects.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional


def resolve_mongo_uri(explicit: Optional[str]) -> Optional[str]:
    uri = (
        explicit
        or os.getenv("MONGO_PUBLIC_URL")
        or os.getenv("MONGODB_URI")
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
    cred = f"{user}:{pw}@" if user else ""
    return f"mongodb://{cred}{host}:{port}/?authSource={auth_source}"


def _stamp(v) -> str:
    """created_at as a comparable ISO string ('' when absent). Docs carry it as
    an ISO string or a datetime depending on which door wrote them."""
    if v is None:
        return ""
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def find_splits(customers) -> List[Dict[str, str]]:
    """One row per (account holding the member, member, own account), with
    ``direction`` = REVERSE when provable (own account created before the
    family account), else UNKNOWN.

    Pure function over a collection handle so it can be run against an
    in-memory double. Two projected scans; fine for a few thousand docs."""
    own_by_mobile: Dict[str, str] = {}
    created_at: Dict[str, str] = {}
    for d in customers.find(
        {}, {"customer_id": 1, "mobile": 1, "phone": 1, "created_at": 1, "_id": 0}
    ):
        cid = str(d.get("customer_id") or "")
        if not cid:
            continue
        created_at[cid] = _stamp(d.get("created_at"))
        for key in ("mobile", "phone"):
            m = d.get(key)
            if isinstance(m, str) and m.strip():
                own_by_mobile.setdefault(m.strip(), cid)

    rows: List[Dict[str, str]] = []
    for d in customers.find(
        {"patients.mobile": {"$exists": True}},
        {"customer_id": 1, "patients": 1, "_id": 0},
    ):
        holder_id = str(d.get("customer_id") or "")
        for p in d.get("patients") or []:
            if not isinstance(p, dict):
                continue
            m = p.get("mobile")
            if not (isinstance(m, str) and m.strip()):
                continue
            own_id = own_by_mobile.get(m.strip())
            if own_id and own_id != holder_id:
                own_at, holder_at = created_at.get(own_id, ""), created_at.get(holder_id, "")
                rows.append(
                    {
                        "holder_customer_id": holder_id,
                        "patient_id": str(p.get("patient_id") or "<no patient_id>"),
                        "own_customer_id": own_id,
                        "direction": (
                            "REVERSE" if own_at and holder_at and own_at < holder_at else "UNKNOWN"
                        ),
                    }
                )
    return rows


def print_report(rows: List[Dict[str, str]]) -> None:
    print("[FAMILY-MEMBER SPLITS] read-only report -- nothing written")
    print(f"  splits found: {len(rows)}")
    if not rows:
        return
    reverse = sum(1 for r in rows if r["direction"] == "REVERSE")
    print(
        f"  of which provably REVERSE (own account predates the family account): "
        f"{reverse}; direction unknown: {len(rows) - reverse}"
    )
    print("  holder_customer_id | patient_id | own_customer_id | direction")
    for r in rows:
        print(
            f"  {r['holder_customer_id']} | {r['patient_id']} | {r['own_customer_id']}"
            f" | {r['direction']}"
        )
    print(
        "\n  Per case the owner decides: promote (moves the member's Rx/eye tests to"
        " the own account -- only possible once the own account is gone), merge,"
        " or leave. This script changes nothing."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGODB_DB_NAME", "ims_2_0"))
    args = parser.parse_args()

    uri = resolve_mongo_uri(args.mongo_uri)
    if not uri:
        print(
            "No Mongo connection. Set MONGO_PUBLIC_URL / MONGODB_URI, pass "
            "--mongo-uri, or run via `railway run`.",
            file=sys.stderr,
        )
        return 2
    try:
        from pymongo import MongoClient
    except ImportError:
        print("pymongo not installed; run `pip install pymongo`.", file=sys.stderr)
        return 2
    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    rows = find_splits(client[args.db]["customers"])
    print_report(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
