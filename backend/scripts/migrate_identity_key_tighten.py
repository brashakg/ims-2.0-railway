"""Rebuild every stored identity_key under the tightened normaliser.

WHY THIS IS MANDATORY, NOT OPTIONAL
-----------------------------------
normalise_identity_component changed: separators are now DELETED rather than
folded to a space, and a leading zero in front of a letter is dropped (the
Luxottica catalogue prefix -- 0RB4350 is RB4350). That is what makes
"Ray-Ban RB4350 002/20", "Ray-Ban 0RB 4350 002/20" and
"Ray-Ban RB4350 002-20" one product instead of three.

The duplicate guard works by computing a key for the incoming product and
looking it up among the STORED keys. Every stored key is in the old format. So
between the deploy and this migration the guard compares a new-format key
against old-format rows, matches nothing, and lets duplicates straight through
-- the exact failure the change exists to prevent. Run it with the deploy.

SAFETY
------
  * Dry run by default. Nothing is written without --apply.
  * Collisions are detected BEFORE any write. If two products would land on the
    same new key, NOTHING is written and both rows are printed: that means the
    tightened rule considers them the same product, which is a merge decision
    for a human, never for a migration.
  * Idempotent: a row whose key is already correct is skipped.
  * Only `products` carries identity_key (verified on production: 76/76 rows;
    catalog_products and catalog_variants store none), so only that collection
    is touched.

USAGE
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" backend/scripts/migrate_identity_key_tighten.py
    railway run --service MongoDB -- ".venv\\Scripts\\python.exe" backend/scripts/migrate_identity_key_tighten.py --apply
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from typing import Any, Dict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.services.product_master import compute_identity_key  # noqa: E402


def _identity_of(doc: Dict[str, Any]):
    """Read the identity fields the way the spine writes them.

    Mirrors backfill_dedupe_prep._identity_of: the spine stores brand/model/
    color/size at the top level, with attributes as the fallback for rows that
    came in through the catalogue door.
    """
    attrs = doc.get("attributes") if isinstance(doc.get("attributes"), dict) else {}
    brand = doc.get("brand") or doc.get("brand_name") or attrs.get("brand_name") or attrs.get("brand")
    model = doc.get("model") or doc.get("model_no") or attrs.get("model_no") or attrs.get("model")
    colour = (
        doc.get("color")
        or doc.get("colour")
        or doc.get("colour_code")
        or attrs.get("colour_code")
        or attrs.get("color")
    )
    size = doc.get("size") or attrs.get("size")
    return compute_identity_key(brand, model, colour, size)


def run(products, *, apply: bool) -> Dict[str, Any]:
    rows = list(products.find({}))
    stats = {"scanned": len(rows), "unchanged": 0, "rewritten": 0,
             "now_none": 0, "collisions": 0}

    planned: Dict[str, Any] = {}
    by_new = defaultdict(list)

    for doc in rows:
        pid = doc.get("product_id") or doc.get("_id")
        old = doc.get("identity_key")
        new = _identity_of(doc)
        if new is None:
            # Brand or model missing -> no identity at all. Leave whatever is
            # there alone; clearing it is a separate decision.
            if old:
                stats["now_none"] += 1
                print(f"  [skip] {doc.get('sku')}: no derivable identity now, keeping {old!r}")
            continue
        if new == old:
            stats["unchanged"] += 1
            continue
        planned[pid] = (old, new, doc.get("sku"))
        by_new[new].append(doc.get("sku") or pid)

    # Collisions: two rows that the tightened rule says are the same product.
    for key, skus in by_new.items():
        existing_others = [
            d.get("sku")
            for d in products.find({"identity_key": key}, {"sku": 1})
            if (d.get("sku") not in skus)
        ]
        if len(skus) > 1 or existing_others:
            stats["collisions"] += 1
            print(f"  [COLLISION] new key {key!r} claimed by: {skus + existing_others}")

    if stats["collisions"]:
        print(
            f"\nREFUSING TO WRITE: {stats['collisions']} collision(s). The tightened "
            "rule considers those rows the same product. Merging them is a human "
            "decision -- resolve in the catalogue first, then re-run."
        )
        return stats

    for pid, (old, new, sku) in sorted(planned.items(), key=lambda kv: str(kv[0])):
        stats["rewritten"] += 1
        print(f"  {sku}: {old!r} -> {new!r}")
        if apply:
            products.update_one({"product_id": pid}, {"$set": {"identity_key": new}})
            if products.count_documents({"product_id": pid}) == 0:
                products.update_one({"_id": pid}, {"$set": {"identity_key": new}})

    return stats


def main() -> int:
    apply = "--apply" in sys.argv
    uri = os.environ.get("MONGO_PUBLIC_URL") or os.environ.get("MONGO_URL")
    if not uri:
        print("No MONGO_PUBLIC_URL / MONGO_URL in the environment.")
        return 2

    from pymongo import MongoClient

    db = MongoClient(uri, serverSelectionTimeoutMS=20000)["ims_2_0"]
    print("DRY RUN -- nothing will be written." if not apply else "APPLYING.")
    stats = run(db["products"], apply=apply)
    print(
        "\nscanned={scanned} unchanged={unchanged} rewritten={rewritten} "
        "no-identity={now_none} collisions={collisions}".format(**stats)
    )
    if not apply and stats["rewritten"]:
        print("\nRe-run with --apply to write these.")
    return 1 if stats["collisions"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
