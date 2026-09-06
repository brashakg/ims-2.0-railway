#!/usr/bin/env python3
"""
IMS 2.0 - Per-store Shopify locations: migrate off the #1125 pooled row
=======================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252).

WHY
---
PR #1125 wrote ONE pooled number to ONE Shopify location and remembered that
location on the storefront registry row (storefronts.BV.online_location_id /
online_location_name / online_location_resolved_at). The owner's ruling of
2026-09-06 ("product will be shipped from whichever store holds the
inventory") makes every physical shop its own Shopify location, mapped on the
STORE record (stores.<doc>.shopify_location_id) from the Organization page.
This script:

  1. prints every active physical store (the ONE reader,
     stores_util.physical_stores) with its current mapping, plus the registry
     row's pooled location -- the measurement design section 6 asks for
     (how many Shopify locations are still missing);
  2. --apply: $unset the three pooled keys on the registry row (harmless when
     absent; removes the Sector-4-Bokaro hazard the hint picker could have
     persisted);
  3. --set CODE=ID: map a store by store_code through the router's OWN
     validator (routers/stores._validate_store_payload: gid form, ONLINE
     refusal, duplicate refusal) and the store repository -- no second rule --
     copying the display name from Shopify's locations read when the push
     gates are live. Writes only with --apply; one refusal stops the run
     before any write (mappings first, then the registry $unset).

THE ONLY --set TO RUN NOW (design section 7, critic finding 1):
    --set BV-BOK-02=58793230523     Better Vision Sector 4 (Bokaro): 0 units on
                                    both sides, so its first write is the
                                    explicit 0 it already shows.

*** DO NOT --set BV-PUN-01 (Gangadham Pune, 76684427513) YET. ***
Shopify holds 49 units at Gangadham Pune; the IMS ledger holds 1. IMS is
master: the moment Pune is mapped AND PR 2 (the per-store writer) is deployed,
the next 01:00/09:00 IST scheduled sync, the all-pending sweep or ANY product
push writes IMS's Pune count over Shopify's 49 -- no button involved;
"Preview first" gates only the button. Pune is mapped ONLY AFTER the 49
opening-stock units are committed at Pune (owner ruling 2026-09-06 evening,
memory work_queue_2026_09_07.md) and the dry-run plan shows Pune ~49.
ENFORCED, not just documented: a --set naming BV-PUN-01 or location
76684427513 (on any code) is REFUSED at plan time -- dry-run and --apply
alike, nothing written -- unless --i-know-pune is passed. Pass it only once
the ledger shows the 49 at Pune.

USAGE
-----
Dry-run (DEFAULT - prints the store table and what --set would do; writes nothing):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/migrate_store_locations.py
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/migrate_store_locations.py --set BV-BOK-02=58793230523

Apply (writes every --set, then unsets the registry row):
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" scripts/migrate_store_locations.py --apply --set BV-BOK-02=58793230523

Connection resolution: --mongo-uri, else MONGO_PUBLIC_URL, else MONGODB_URI,
else MONGODB_URL/MONGO_URL (the vars `railway run` injects). No secret value
is ever printed. Exit 2 when a --set is refused (nothing is written then).
"""

import argparse
import asyncio
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "backend"))
os.environ.setdefault("JWT_SECRET_KEY", "migrate")
os.environ.setdefault("ENVIRONMENT", "script")

from fastapi import HTTPException  # noqa: E402

from api.routers import stores as stores_router  # noqa: E402
from api.services.stores_util import physical_stores  # noqa: E402
from database.repositories.store_repository import StoreRepository  # noqa: E402

_REGISTRY_KEYS = (
    "online_location_id",
    "online_location_name",
    "online_location_resolved_at",
)

# Gangadham Pune: the store code AND the Shopify location number (either one
# in a --set trips the guard, so a typo'd code cannot smuggle the gid in).
PUNE_STORE_CODE = "BV-PUN-01"
PUNE_LOCATION_NUMBER = "76684427513"
PUNE_REFUSAL = (
    "Gangadham Pune (BV-PUN-01 / location 76684427513) must NOT be "
    "mapped before the 49 opening-stock units are committed at Pune -- a "
    "mapped Pune plus PR 2 writes IMS's Pune count over Shopify's 49 at the "
    "next schedule tick, sweep or product push. Re-run with --i-know-pune "
    "once the ledger shows them."
)


def pune_guarded(code: str, raw: str) -> bool:
    """Pure: True when a --set names Pune's store code or Pune's location
    number (bare or as a gid)."""
    return code == PUNE_STORE_CODE or raw.rstrip("/").rsplit("/", 1)[-1] == PUNE_LOCATION_NUMBER


def resolve_mongo_uri(explicit: Optional[str]) -> Optional[str]:
    return (
        explicit
        or os.getenv("MONGO_PUBLIC_URL")
        or os.getenv("MONGODB_URI")
        or os.getenv("MONGODB_URL")
        or os.getenv("MONGO_URL")
    )


def parse_sets(items: List[str]) -> List[Tuple[str, str]]:
    """Pure: ``CODE=ID`` -> ``(CODE, ID)``; the code is uppercased; a malformed
    item is a usage error."""
    out: List[Tuple[str, str]] = []
    for raw in items or []:
        code, sep, loc = str(raw).partition("=")
        if not sep or not code.strip() or not loc.strip():
            raise SystemExit(f"--set expects STORE_CODE=LOCATION_ID, got {raw!r}")
        out.append((code.strip().upper(), loc.strip()))
    return out


def print_table(db) -> List[Dict[str, Any]]:
    rows = physical_stores(db)
    print(f"active physical stores: {len(rows)}")
    print(f"  {'store_code':12} {'store_id':38} {'store_name':28} shopify_location")
    for r in rows:
        loc = r.get("shopify_location_id") or "-"
        name = r.get("shopify_location_name") or ""
        print(
            f"  {str(r.get('store_code') or ''):12} {str(r.get('store_id') or ''):38} "
            f"{str(r.get('store_name') or '')[:28]:28} {loc}{(' (' + name + ')') if name else ''}"
        )
    row = db.get_collection("storefronts").find_one({"storefront_id": "BV"}) or {}
    present = {k: row.get(k) for k in _REGISTRY_KEYS if row.get(k) not in (None, "")}
    print(f"registry row storefronts.BV pooled location keys: {present or 'none'}")
    return rows


def plan_sets(db, sets: List[Tuple[str, str]], *, allow_pune: bool = False) -> List[Dict[str, Any]]:
    """One row per --set: ``{code, store_id, gid, name, error}``. The gid is
    normalised and refused by the router's own validator (ONE rule); ``error``
    carries the refusal text. Pune is refused here (PUNE_REFUSAL) unless
    ``allow_pune`` -- the --i-know-pune flag. Nothing is written here."""
    coll = db.get_collection("stores")
    out: List[Dict[str, Any]] = []
    for code, raw in sets:
        if pune_guarded(code, raw) and not allow_pune:
            out.append({"code": code, "store_id": None, "gid": raw, "name": None,
                        "error": PUNE_REFUSAL})
            continue
        doc = coll.find_one({"store_code": code})
        if doc is None:
            out.append({"code": code, "store_id": None, "gid": raw, "name": None,
                        "error": "no store with that store_code"})
            continue
        data: Dict[str, Any] = {"shopify_location_id": raw}
        try:
            stores_router._validate_store_payload(
                data, db=db, store_id=doc.get("store_id"), existing=doc
            )
        except HTTPException as exc:
            out.append({"code": code, "store_id": doc.get("store_id"), "gid": raw,
                        "name": None, "error": f"{exc.status_code}: {exc.detail}"})
            continue
        gid = data["shopify_location_id"]
        name = asyncio.run(stores_router._shopify_location_name(db, gid)) if gid else None
        out.append({"code": code, "store_id": doc.get("store_id"), "gid": gid,
                    "name": name, "error": None})
    return out


def apply_sets(db, plan: List[Dict[str, Any]]) -> int:
    """Write every planned row through the store repository (the router's
    door). Caller guarantees the plan has no refusals. Returns how many docs
    actually changed: re-applying an identical mapping counts 0."""
    repo = StoreRepository(db.get_collection("stores"))
    written = 0
    for row in plan:
        ok = repo.update(
            row["store_id"],
            {"shopify_location_id": row["gid"], "shopify_location_name": row["name"]},
        )
        written += int(bool(ok))
    return written


def unset_registry(db) -> int:
    res = db.get_collection("storefronts").update_one(
        {"storefront_id": "BV"}, {"$unset": {k: "" for k in _REGISTRY_KEYS}}
    )
    return int(getattr(res, "modified_count", 0) or 0)


def run(*, mongo_uri: Optional[str], db_name: str, apply: bool, sets: List[Tuple[str, str]],
        allow_pune: bool = False) -> Dict[str, Any]:
    if not mongo_uri:
        raise SystemExit(
            "No Mongo connection. Set MONGO_PUBLIC_URL / MONGODB_URI, pass "
            "--mongo-uri, or run via `railway run` so the vars are injected."
        )
    from pymongo import MongoClient

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    db = client[db_name]
    print_table(db)

    if allow_pune:
        print("\n*** --i-know-pune: the Gangadham Pune guard is OFF for this run. "
              "Only correct once the 49 opening-stock units are committed at Pune. ***")
    plan = plan_sets(db, sets, allow_pune=allow_pune)
    for row in plan:
        verdict = f"REFUSED {row['error']}" if row["error"] else f"-> {row['gid']} ({row['name'] or 'name unknown: gates dark'})"
        print(f"  --set {row['code']:12} {verdict}")
    refused = [r for r in plan if r["error"]]

    if not apply:
        print("\nDRY RUN - nothing written. Re-run with --apply to write.")
        return {"stores": len(plan), "refused": len(refused), "written": 0, "unset": 0}
    if refused:
        print(f"\nREFUSED {len(refused)} --set(s); nothing written (all-or-nothing).")
        raise SystemExit(2)

    written = apply_sets(db, plan)
    unset = unset_registry(db)
    print(f"\nWROTE {written} of {len(plan)} store mapping(s) ({len(plan) - written} already identical); "
          f"UNSET registry pooled location on {unset} row(s)")
    print_table(db)
    return {"stores": len(plan), "refused": 0, "written": written, "unset": unset}


def main():
    parser = argparse.ArgumentParser(
        description="Per-store Shopify locations: print the store table, unset the #1125 pooled row, map stores. Dry-run by default."
    )
    parser.add_argument("--mongo-uri", default=None)
    parser.add_argument("--db", default=os.getenv("MONGO_DATABASE", "ims_2_0"))
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run).")
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="STORE_CODE=LOCATION_ID",
        help="Map a store to a Shopify location gid or bare id (repeatable). See the Pune warning at the top of this file.",
    )
    parser.add_argument(
        "--i-know-pune",
        action="store_true",
        help="Lift the Gangadham Pune refusal. ONLY after the 49 opening-stock units are committed at Pune.",
    )
    args = parser.parse_args()
    run(
        mongo_uri=resolve_mongo_uri(args.mongo_uri),
        db_name=args.db,
        apply=args.apply,
        sets=parse_sets(args.set),
        allow_pune=args.i_know_pune,
    )


if __name__ == "__main__":
    sys.exit(main())
