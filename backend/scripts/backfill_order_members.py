#!/usr/bin/env python3
"""
IMS 2.0 -- Bill-to-member backfill (Account / Member model, Phase 1)
===================================================================
Council decision COUNCIL_ACCOUNT_BILLING_DECISION_2026-06-19 (LOCKED).

Makes the existing data conform to the "every order bills a MEMBER, never a bare
account" invariant BEFORE the (already non-breaking) order-create validator
ships. Two passes, both idempotent:

  PASS A -- customers: ensure every `customers` doc has >=1 member and exactly
            one Primary (is_primary=true) + a root primary_patient_id pointer.
            Empty patients[] -> a Primary is minted from the account name/mobile.

  PASS B -- orders: every order with a missing/null/blank patient_id is
            backfilled to its account's Primary member (owner-locked rule:
            single-member -> that member; multi-member -> the Primary). The
            denormalized billed_to_member_name is stamped too. DRAFT orders are
            included (they also must carry a member once the validator is live).
            Walk-in / synthetic-account orders (no customer doc) get a synthetic
            Primary stamped on the order only.

SAFETY CONTRACT (mirrors scripts/migrate_bvi_pim.py)
----------------------------------------------------
- --dry-run is the DEFAULT. Nothing is written unless you also pass --commit.
- Idempotent: re-running only touches docs still missing a Primary / a
  patient_id, so a second --commit run is a no-op.
- Fail-loud: a missing/unreachable Mongo aborts with exit 1 (NEVER a silent
  no-op that would let the gate flip on un-backfilled data).
- No secrets printed -- only "SET"/"NOT SET" for the connection string.
- pymongo is lazy-imported so a missing driver fails only this script.
- No emojis (Windows cp1252 safe).
- On --commit each change is appended to the `audit_logs` collection
  (action MEMBER_BACKFILL_*) for an auditable trail.

Usage
-----
  # Dry run (default): print counts + samples, write nothing
  python backend/scripts/backfill_order_members.py --dry-run

  # Live backfill:
  python backend/scripts/backfill_order_members.py --commit

  # On Railway prod Mongo:
  railway run --service MongoDB bash -c \\
    'MONGODB_URL=$MONGO_PUBLIC_URL python backend/scripts/backfill_order_members.py --commit'

Exit codes: 0 = success or dry-run OK; 1 = fatal connection error.
"""
from __future__ import annotations

import argparse
import copy
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Make the backend package importable so we can reuse the SAME pure resolver the
# router + customer-create use (no logic drift between runtime and migration).
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from api.services.member_billing import (  # noqa: E402
    choose_primary_member,
    ensure_primary_member,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("backfill_order_members")


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _blank(v: Any) -> bool:
    """True when a patient_id field is absent / None / empty / whitespace."""
    return v is None or (isinstance(v, str) and v.strip() == "")


# ---------------------------------------------------------------------------
# Mongo connection (mirrors migrate_bvi_pim._mongo_connect)
# ---------------------------------------------------------------------------

def _mongo_connect(mongo_url: str, db_name: str):
    """Return (client, db) or (None, None) on failure."""
    try:
        from pymongo import MongoClient  # noqa: PLC0415 -- lazy import
        client = MongoClient(mongo_url, serverSelectionTimeoutMS=10_000)
        client.admin.command("ping")
        return client, client[db_name]
    except ImportError as e:
        logger.error("[MONGO] pymongo not available: %s", e)
        return None, None
    except Exception as e:  # noqa: BLE001
        logger.error("[MONGO] connect failed: %s", e)
        return None, None


def _audit(db, action: str, payload: Dict[str, Any]) -> None:
    """Append a best-effort audit row. Never aborts the migration."""
    try:
        db["audit_logs"].insert_one(
            {
                "action": action,
                "entity_type": "ORDER_MEMBER_BACKFILL",
                "performed_by": "system:backfill_order_members",
                "timestamp": _now_utc(),
                "after_state": payload,
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[AUDIT] could not write %s: %s", action, e)


# ---------------------------------------------------------------------------
# PASS A -- customers: ensure a Primary member
# ---------------------------------------------------------------------------

def run_customers(db, *, dry_run: bool, sample_n: int = 3) -> Dict[str, Any]:
    coll = db["customers"]
    total = coll.count_documents({})
    logger.info("[CUSTOMERS] scanning %d account docs...", total)

    seeded_empty = 0          # patients[] was empty -> minted a Primary
    flagged_existing = 0      # had members but no is_primary -> flagged one
    already_ok = 0            # already had a Primary + pointer
    samples: List[str] = []

    for doc in coll.find({}):
        was_empty = len(list(doc.get("patients") or [])) == 0

        # Work on a deep COPY so a dry-run never mutates the stored doc (real
        # Mongo find() returns fresh dicts, but the in-memory test FakeDB yields
        # references -- copying makes both behave identically + keeps dry-run a
        # true read-only preview).
        work = copy.deepcopy(doc)
        primary, changed = ensure_primary_member(work)
        if not changed:
            already_ok += 1
            continue

        if was_empty:
            seeded_empty += 1
        else:
            flagged_existing += 1

        if len(samples) < sample_n:
            samples.append(
                f"{doc.get('customer_id')} -> primary={primary.get('name')} "
                f"({'minted' if was_empty else 'flagged'})"
            )

        if not dry_run:
            coll.update_one(
                {"customer_id": doc.get("customer_id")},
                {
                    "$set": {
                        "patients": work.get("patients", []),
                        "primary_patient_id": work.get("primary_patient_id"),
                    }
                },
            )
            _audit(
                db,
                "MEMBER_BACKFILL_PRIMARY_SEEDED",
                {
                    "customer_id": doc.get("customer_id"),
                    "primary_patient_id": primary.get("patient_id"),
                    "was_empty": was_empty,
                },
            )

    logger.info(
        "[CUSTOMERS] minted=%d flagged=%d already_ok=%d (total=%d)",
        seeded_empty, flagged_existing, already_ok, total,
    )
    for s in samples:
        logger.info("  sample: %s", s)
    return {
        "pass": "customers",
        "total": total,
        "minted_primary": seeded_empty,
        "flagged_primary": flagged_existing,
        "already_ok": already_ok,
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
# PASS B -- orders: backfill patient_id to the account's Primary
# ---------------------------------------------------------------------------

def run_orders(db, *, dry_run: bool, sample_n: int = 3) -> Dict[str, Any]:
    coll = db["orders"]
    # Orders that need a member: patient_id missing, null, or empty string.
    needs = {
        "$or": [
            {"patient_id": {"$exists": False}},
            {"patient_id": None},
            {"patient_id": ""},
        ]
    }
    total_missing = coll.count_documents(needs)
    logger.info("[ORDERS] %d orders missing a member...", total_missing)

    backfilled_account = 0    # resolved to a real account's Primary
    synthetic_primary = 0     # walk-in / no customer doc -> synthetic Primary
    skipped_no_customer_ref = 0
    # Cache the RESOLVED Primary per account so every order under one account
    # gets the SAME member id (and a no-Primary account is fixed + persisted
    # exactly once). Maps customer_id -> (patient_id, name) or None when there
    # is no account doc.
    resolved_cache: Dict[str, Optional[tuple]] = {}
    samples: List[str] = []

    def _resolve_account_primary(cid: str) -> Optional[tuple]:
        if cid in resolved_cache:
            return resolved_cache[cid]
        doc = db["customers"].find_one({"customer_id": cid})
        if not doc:
            resolved_cache[cid] = None
            return None
        # Deep-copy so a dry-run never mutates the stored doc; the real find()
        # already returns fresh dicts, copying just makes the test FakeDB match.
        work = copy.deepcopy(doc)
        primary, changed = ensure_primary_member(work)
        if changed and not dry_run:
            db["customers"].update_one(
                {"customer_id": cid},
                {
                    "$set": {
                        "patients": work.get("patients", []),
                        "primary_patient_id": work.get("primary_patient_id"),
                    }
                },
            )
        out = (primary.get("patient_id"), primary.get("name"))
        resolved_cache[cid] = out
        return out

    for order in coll.find(needs):
        order_id = order.get("order_id") or str(order.get("_id"))
        customer_id = (order.get("customer_id") or "").strip()

        primary_pid: Optional[str] = None
        primary_name: Optional[str] = None
        kind = ""

        resolved = _resolve_account_primary(customer_id) if customer_id else None
        if resolved:
            primary_pid, primary_name = resolved
            kind = "account_primary"
            backfilled_account += 1
        else:
            # Walk-in / synthetic / orphaned customer ref: stamp a synthetic
            # Primary on the ORDER only (the council walk-in rule). Use the
            # order's stored customer_name so the member name is meaningful.
            import uuid as _uuid  # noqa: PLC0415
            primary_pid = f"synthetic-{_uuid.uuid4()}"
            primary_name = order.get("customer_name") or "Walk-in Customer"
            kind = "synthetic_primary"
            synthetic_primary += 1

        if len(samples) < sample_n:
            samples.append(f"{order_id} -> {primary_name} ({kind})")

        if not dry_run:
            coll.update_one(
                {"order_id": order.get("order_id")}
                if order.get("order_id")
                else {"_id": order.get("_id")},
                {
                    "$set": {
                        "patient_id": primary_pid,
                        "billed_to_member_name": primary_name,
                        "migration_source": kind,
                    }
                },
            )
            _audit(
                db,
                "MEMBER_BACKFILL_ORDER",
                {
                    "order_id": order_id,
                    "customer_id": customer_id,
                    "patient_id": primary_pid,
                    "kind": kind,
                },
            )

    logger.info(
        "[ORDERS] backfilled_account=%d synthetic=%d (missing total=%d)",
        backfilled_account, synthetic_primary, total_missing,
    )
    for s in samples:
        logger.info("  sample: %s", s)
    return {
        "pass": "orders",
        "missing_total": total_missing,
        "backfilled_account_primary": backfilled_account,
        "synthetic_primary": synthetic_primary,
        "skipped_no_customer_ref": skipped_no_customer_ref,
        "dry_run": dry_run,
    }


# ---------------------------------------------------------------------------
# Verification (read-only; run after --commit)
# ---------------------------------------------------------------------------

def verify(db) -> int:
    """Assert no order is left bare-account. Returns the residual count."""
    needs = {
        "$or": [
            {"patient_id": {"$exists": False}},
            {"patient_id": None},
            {"patient_id": ""},
        ]
    }
    residual = db["orders"].count_documents(needs)
    no_primary = 0
    for doc in db["customers"].find({}):
        if choose_primary_member(list(doc.get("patients") or [])) is None:
            no_primary += 1
    logger.info(
        "[VERIFY] orders still missing a member: %d ; accounts with no member: %d",
        residual, no_primary,
    )
    return residual + no_primary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Print counts + samples; write NOTHING (default: ON).",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        default=False,
        help="Actually write to Mongo (disables --dry-run). Idempotent.",
    )
    parser.add_argument(
        "--mongo-url",
        default=os.getenv("MONGODB_URL") or os.getenv("MONGO_URL"),
        help="IMS Mongo URL (default: $MONGODB_URL / $MONGO_URL).",
    )
    parser.add_argument(
        "--db",
        default=os.getenv("MONGO_DATABASE", "ims_2_0"),
        help="Mongo database name (default: ims_2_0).",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=3,
        help="Number of sample rows to print per pass (default: 3).",
    )
    args = parser.parse_args()

    dry_run = not args.commit
    mode = "DRY-RUN (no writes)" if dry_run else "COMMIT (live writes)"
    logger.info("=" * 60)
    logger.info("Bill-to-member backfill  --  mode: %s", mode)
    logger.info("mongo_url: %s", "SET" if args.mongo_url else "NOT SET")
    logger.info("db: %s", args.db)
    logger.info("=" * 60)

    if not args.mongo_url:
        # Fail loud -- never a silent no-op (a "success" with no DB would let the
        # validator flip on un-backfilled data).
        logger.error(
            "MONGODB_URL is not set. Set it (or pass --mongo-url) to connect."
        )
        sys.exit(1)

    client, db = _mongo_connect(args.mongo_url, args.db)
    if db is None:
        logger.error("Cannot connect to IMS Mongo. Aborting.")
        sys.exit(1)
    logger.info("[MONGO] connected to db=%s", args.db)

    results: List[Dict[str, Any]] = []
    try:
        results.append(run_customers(db, dry_run=dry_run, sample_n=args.sample))
        results.append(run_orders(db, dry_run=dry_run, sample_n=args.sample))
        if not dry_run:
            residual = verify(db)
            if residual:
                logger.warning(
                    "[VERIFY] %d residual item(s) remain -- re-run --commit.",
                    residual,
                )
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass

    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY  --  mode: %s", mode)
    for r in results:
        logger.info("  %s", r)
    logger.info("=" * 60)
    if dry_run:
        logger.info(
            "REMINDER: DRY-RUN only. Re-run with --commit to write to Mongo."
        )
    else:
        logger.info("Backfill complete.")


if __name__ == "__main__":
    main()
