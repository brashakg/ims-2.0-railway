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
A split is the same data state whichever door made it. Member rows minted
after 2026-09-04 carry ``created_at``; comparing it with the own account's
``created_at`` proves the direction both ways (member row first -> FORWARD,
own account first -> REVERSE). Older rows have no timestamp, so the direction
is only PROVABLE one way: when the own account was created BEFORE the family
account existed, the member row can only have been added afterwards ->
REVERSE. Everything else is reported as UNKNOWN, never guessed.

ONE HOUSEHOLD (owner ruling 2026-09-04: "Block it, one household account")
----------------------------------------------------------------------------
A number may be a family member on only ONE account. The second section lists
member numbers that sit in ``patients[]`` of two or more accounts (a row that
carries the holder's own number is the holder, not an overlap). Ids only.

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


def _direction(row_at: str, own_at: str, holder_at: str) -> str:
    """Provable direction of a split, never guessed. With a member-row
    timestamp the order of the two creates decides; without one, only an own
    account older than the family account is provable (REVERSE)."""
    if row_at and own_at and row_at != own_at:
        return "REVERSE" if own_at < row_at else "FORWARD"
    if own_at and holder_at and own_at < holder_at:
        return "REVERSE"
    return "UNKNOWN"


def _scan(customers):
    """The two projected scans every listing is built from: the own-account
    index (mobile -> customer_id), per-account created_at, and every member
    row as (holder_id, patient_id, mobile, row_created_at)."""
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

    member_rows: List[tuple] = []
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
            member_rows.append(
                (
                    holder_id,
                    str(p.get("patient_id") or "<no patient_id>"),
                    m.strip(),
                    _stamp(p.get("created_at")),
                )
            )
    return own_by_mobile, created_at, member_rows


def find_splits(customers) -> List[Dict[str, str]]:
    """One row per (account holding the member, member, own account), with
    ``direction`` = FORWARD / REVERSE when provable, else UNKNOWN.

    Pure function over a collection handle so it can be run against an
    in-memory double. Two projected scans; fine for a few thousand docs."""
    own_by_mobile, created_at, member_rows = _scan(customers)
    rows: List[Dict[str, str]] = []
    for holder_id, patient_id, mobile, row_at in member_rows:
        own_id = own_by_mobile.get(mobile)
        if own_id and own_id != holder_id:
            rows.append(
                {
                    "holder_customer_id": holder_id,
                    "patient_id": patient_id,
                    "own_customer_id": own_id,
                    "direction": _direction(
                        row_at, created_at.get(own_id, ""), created_at.get(holder_id, "")
                    ),
                }
            )
    return rows


def find_household_overlaps(customers) -> List[Dict[str, str]]:
    """One row per EXTRA household holding a member number: the first account
    (in scan order) that carries the number as a family member, paired with
    each other account that also does. A row carrying its holder's own number
    is the holder (Self row / a child under the parent's phone), not a
    household, and is skipped. Ids only."""
    own_by_mobile, _created_at, member_rows = _scan(customers)
    holders: Dict[str, List[tuple]] = {}
    for holder_id, patient_id, mobile, _row_at in member_rows:
        if own_by_mobile.get(mobile) == holder_id:
            continue
        seen = holders.setdefault(mobile, [])
        if all(h != holder_id for h, _ in seen):
            seen.append((holder_id, patient_id))
    rows: List[Dict[str, str]] = []
    for accounts in holders.values():
        first_id, first_pid = accounts[0]
        for other_id, other_pid in accounts[1:]:
            rows.append(
                {
                    "first_customer_id": first_id,
                    "first_patient_id": first_pid,
                    "second_customer_id": other_id,
                    "second_patient_id": other_pid,
                }
            )
    return rows


def print_report(rows: List[Dict[str, str]], overlaps: Optional[List[Dict[str, str]]] = None) -> None:
    print("[FAMILY-MEMBER SPLITS] read-only report -- nothing written")
    print(f"  splits found: {len(rows)}")
    if rows:
        reverse = sum(1 for r in rows if r["direction"] == "REVERSE")
        forward = sum(1 for r in rows if r["direction"] == "FORWARD")
        print(
            f"  of which provably REVERSE (own account predates the family account): "
            f"{reverse}; provably FORWARD (member row predates the own account): "
            f"{forward}; direction unknown: {len(rows) - reverse - forward}"
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
    if overlaps is None:
        return
    print("\n[FAMILY-MEMBER ON TWO HOUSEHOLDS] a number that is a member on more than one account")
    print(f"  overlaps found: {len(overlaps)}")
    if not overlaps:
        return
    print("  first_customer_id | first_patient_id | second_customer_id | second_patient_id")
    for r in overlaps:
        print(
            f"  {r['first_customer_id']} | {r['first_patient_id']} | "
            f"{r['second_customer_id']} | {r['second_patient_id']}"
        )
    print(
        "\n  Owner ruling: one household account per person. Per case the owner"
        " decides which household keeps the member. This script changes nothing."
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
    customers = client[args.db]["customers"]
    print_report(find_splits(customers), find_household_overlaps(customers))
    return 0


if __name__ == "__main__":
    sys.exit(main())
