"""
IMS 2.0 - One-time backfill: stamp catalog_status=ACTIVE on every legacy product
=================================================================================
Hub Phase 0 introduces the catalog-done chokepoint: new/edited products carry a
`catalog_status` of ACTIVE (complete) or DRAFT (incomplete, with `done_gaps`),
and the Buy Desk gates the "Purchase" action on ACTIVE.

OWNER DECISION A (locked 2026-06-12, verbatim): the migration DRAFTs NOTHING --
every existing (~10,800) product is marked ACTIVE regardless of completeness; the
done-gate is FORWARD-ONLY (it applies to newly created/edited products only); no
existing row is demoted to DRAFT.

This script stamps catalog_status=ACTIVE + done_gaps=[] on every product row that
does NOT already carry a catalog_status. Readers already treat a MISSING status as
ACTIVE (product_master.effective_catalog_status), so this is belt-and-suspenders
for READS -- but it is REQUIRED for QUERIES: the Buy Desk and completion screens
filter on `catalog_status`, and a legacy row without the field would otherwise be
invisible to a `{"catalog_status": "ACTIVE"}` query.

SAFETY
------
- IDEMPOTENT. The filter matches only rows whose catalog_status is missing / null
  / blank, so a second run matches 0 rows. A row that ALREADY has an explicit
  DRAFT (created after the feature shipped) is NEVER touched -- it is not flipped
  to ACTIVE. An already-ACTIVE row is a no-op.
- NEVER DEMOTES. The script only ADDS the field where absent; it never changes an
  existing DRAFT/ACTIVE value.
- AUDIT-LOGGED. Writes ONE summary row to `audit_log` (kind=catalog_status_backfill_phase0)
  capturing the matched/modified counts and the filter -- the change is reversible
  by hand ($unset catalog_status/done_gaps on the affected rows) and is in any case
  read-equivalent to the pre-migration state.
- FAIL-LOUD on no DB. If MongoDB is unreachable the script prints an error and
  exits non-zero rather than silently doing nothing.
- APPLY BY DEFAULT (per DECISION A: "no dry-run" gate). The write is purely
  additive and never demotes, so it is safe to apply directly. Pass --dry-run to
  preview the counts without writing.

USAGE
-----
  # apply (default -- safe, additive, idempotent):
  railway run .venv\\Scripts\\python.exe backend/scripts/backfill_catalog_status_active.py

  # preview only (counts, writes nothing):
  railway run .venv\\Scripts\\python.exe backend/scripts/backfill_catalog_status_active.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from typing import Any, Dict

# Make the backend package importable whether run from repo root or backend/.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

AUDIT_KIND = "catalog_status_backfill_phase0"
TARGET_STATUS = "ACTIVE"

# "No catalog_status yet": field missing, explicitly null, empty, or whitespace.
# A row already carrying ACTIVE or DRAFT is intentionally excluded (never clobbered).
NO_STATUS_FILTER: Dict[str, Any] = {
    "$or": [
        {"catalog_status": {"$exists": False}},
        {"catalog_status": None},
        {"catalog_status": ""},
        {"catalog_status": {"$regex": r"^\s*$"}},
    ]
}


def _backfill(products, *, apply: bool) -> Dict[str, int]:
    """Pure-ish core: count the rows missing catalog_status and (when apply) stamp
    them ACTIVE. Takes the products collection so it is unit-testable against a
    fake. Returns {"matched": N, "modified": M}. Never demotes, never clobbers an
    existing DRAFT/ACTIVE value (the filter excludes them).
    """
    matched = products.count_documents(NO_STATUS_FILTER)
    if not apply or matched == 0:
        return {"matched": matched, "modified": 0}

    res = products.update_many(
        NO_STATUS_FILTER,
        {
            "$set": {
                "catalog_status": TARGET_STATUS,
                "done_gaps": [],
                "updated_by": "backfill:" + AUDIT_KIND,
            }
        },
    )
    return {"matched": matched, "modified": int(getattr(res, "modified_count", 0))}


def _connect():
    """Connect to MongoDB the way api/main.py does. Returns DatabaseConnection or None."""
    from database.connection import init_db, get_db, DatabaseConfig

    mongo_url = os.getenv("MONGODB_URL") or os.getenv("MONGO_URL")
    if mongo_url:
        config = DatabaseConfig.from_uri(mongo_url, database="ims_2_0")
    else:
        config = DatabaseConfig.from_env()

    if init_db(config):
        return get_db()
    return None


def run(apply: bool) -> int:
    """Execute the backfill. Returns the process exit code (0 = OK)."""
    db = _connect()
    if db is None or not db.is_connected:
        print(
            "[ERROR] Could not connect to MongoDB. Set MONGODB_URL / MONGO_URL "
            "(or run via `railway run`). Nothing changed."
        )
        return 2

    products = db.get_collection("products")
    if products is None:
        print("[ERROR] products collection unavailable. Nothing changed.")
        return 2

    mode = "APPLY" if apply else "DRY-RUN"
    print("=" * 70)
    print(f"catalog_status -> {TARGET_STATUS} backfill (Hub Phase 0)  [{mode}]")
    print("=" * 70)

    result = _backfill(products, apply=apply)
    matched = result["matched"]
    print(f"Products with NO catalog_status: {matched}")

    if matched == 0:
        print("Nothing to backfill (idempotent: already stamped). Exit 0.")
        return 0

    if not apply:
        print(
            f"\n[DRY-RUN] No changes written. {matched} product(s) WOULD be "
            f"stamped catalog_status={TARGET_STATUS}. Re-run without --dry-run "
            f"to commit."
        )
        return 0

    print(
        f"\n[APPLY] Stamped {result['modified']} product(s) catalog_status={TARGET_STATUS}."
    )
    print("Re-running is a no-op (idempotent).")

    # One summary audit row (per-doc audit would be 10k+ rows for an additive,
    # read-equivalent change). Best-effort -- never fail the migration on audit.
    audit = db.get_collection("audit_log")
    if audit is not None:
        try:
            audit.insert_one(
                {
                    "kind": AUDIT_KIND,
                    "collection": "products",
                    "matched": matched,
                    "modified": result["modified"],
                    "filter": "catalog_status missing/null/blank",
                    "target": {"catalog_status": TARGET_STATUS, "done_gaps": []},
                    "reason": (
                        "Hub Phase 0 / owner DECISION A: mark every legacy product "
                        "ACTIVE (forward-only done-gate; no row demoted)."
                    ),
                    "created_at": datetime.utcnow(),
                }
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] audit_log summary write failed: {exc}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill catalog_status=ACTIVE on legacy products (Hub Phase 0)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the count without writing (default is APPLY, per DECISION A).",
    )
    args = parser.parse_args()
    return run(apply=not args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
