#!/usr/bin/env python3
"""
IMS 2.0 -- catalog_products.sku repair (PIM parent SKU backfill)
================================================================
THE DEFECT
----------
`product_master._build_pim_doc` used to write the catalog_products PIM row with
`parent_sku` and NO top-level `sku`. Every ONLINE consumer joins
catalog_products on `sku`:

  * online_store_push  -- `doc.get("sku") in blocked_set` (the push-sweep
    block classifier)
  * online_block._membership_hit -- `sku = product.get("sku")`, and it returns
    False the moment `sku` is falsy
  * shopify_push._member_product_gids -- `find_one({"sku": sku})`
  * ecom_smart_rules -- `product.get("sku")`

So the "block this collection from online sale" guard silently NO-OPPED for
every PM-created product. On prod 53 of 59 catalog_products rows carry no `sku`
key at all.

WHAT THIS SCRIPT DOES
---------------------
For each affected row: `$set {"sku": <that row's OWN parent_sku>}`.
NOTHING ELSE. In particular `ecom` is NEVER touched -- `ecom.locally_modified`
is False on all 59 rows and must STAY False, so this repair can never enqueue a
Shopify push.

WHY `sku := parent_sku` IS SAFE (not merely assumed)
----------------------------------------------------
Gate G6 proves it per row: a PM-created parent has EXACTLY ONE catalog_variants
row, and that variant's `sku` equals the parent_sku. So the parent row's own
identity IS the parent_sku -- there is no second variant whose sku could be the
"right" answer instead. G4/G5 additionally prove the `products` spine row exists
and agrees. Any row that fails any gate aborts the WHOLE run with 0 writes.

SAFETY CONTRACT
---------------
- DRY-RUN IS THE DEFAULT. Nothing is written unless you pass --apply.
- MANDATORY PRE-FLIGHT FINGERPRINT (db name, collection counts, the sku_1
  index shape). A mismatch aborts with exit 3 BEFORE any write -- this is what
  stops the script from cheerfully "succeeding" against the wrong database.
- The connection string is resolved ONLY from MONGODB_URL / MONGO_PUBLIC_URL /
  MONGO_URL. There is deliberately NO DatabaseConfig.from_env() fallback: that
  helper silently defaults to localhost/ims_2_0, so a missing env var would
  apply the repair to a LOCAL mongo while printing a success banner.
- ONE SHARED PREDICATE is used for target selection, the per-row write filter
  AND the post-verify, so a `sku: null` row can never be skipped by the write
  while still being reported as PASS.
- Writes are per-row and counted; sum(modified_count) MUST equal len(targets)
  or the run exits non-zero. Success is never inferred from a re-count.
- An AUDIT FILE is written in a `finally` -- always, including on an abort or a
  crash -- carrying the host/db fingerprint, the ORDERED ids actually modified,
  and the outcome. Ids are also echoed to stdout as they land, so a killed
  terminal still leaves a record of exactly how far the run got.
- ASCII only (no emoji -- Windows cp1252). No secret values are ever printed;
  only the HOST.

USAGE
-----
  # DRY RUN (default) against prod Mongo -- writes nothing:
  cd "C:\\Users\\avina\\IMS 2.0 CLAUDE COWORK\\ims-2.0-railway"
  railway run --service MongoDB -- ".venv\\Scripts\\python.exe" \\
      backend/scripts/backfill_pim_sku_from_parent.py

  # APPLY (owner-gated):
  railway run --service MongoDB -- ".venv\\Scripts\\python.exe" \\
      backend/scripts/backfill_pim_sku_from_parent.py --apply

EXIT CODES
----------
  0  dry-run OK, or apply COMPLETE (or nothing to do)
  2  connection / driver failure
  3  pre-flight fingerprint mismatch (aborted, 0 writes)
  4  a gate G1-G6 failed (aborted, 0 writes)
  5  write shortfall (PARTIAL -- see the audit file)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# The ONE shared predicate: "this row has no usable sku".
# ---------------------------------------------------------------------------
# Used for (a) target selection, (b) the per-row write filter (ANDed with the
# row's id) and (c) the post-verify. Covers BOTH shapes: the key absent, and an
# explicit null / empty string. Selecting on `$exists: false` alone -- as the
# first draft of this plan did -- would silently skip a `sku: null` row on the
# WRITE while still counting it clean on the verify, i.e. print PASS on an
# unrepaired row.
TARGET_PREDICATE: Dict[str, Any] = {
    "$or": [{"sku": {"$exists": False}}, {"sku": {"$in": [None, ""]}}]
}

# Rows that already carry a real sku (the complement of TARGET_PREDICATE).
HAS_SKU_PREDICATE: Dict[str, Any] = {"sku": {"$exists": True, "$nin": [None, ""]}}

# ---------------------------------------------------------------------------
# Pre-flight fingerprint -- verified live on prod 2026-07-29.
# ---------------------------------------------------------------------------
EXPECTED_DB_NAME = "ims_2_0"
EXPECTED_CATALOG_PRODUCTS = 59
EXPECTED_PRODUCTS = 53
EXPECTED_SKU_INDEX = "sku_1"

GATE_DESCRIPTIONS = {
    "G1": "every target has a non-blank parent_sku",
    "G2": "no duplicates among the repair values",
    "G3": "no intersection with existing catalog_products.sku values",
    "G4": "a products spine row exists at {pim_product_id: doc.id}",
    "G5": "spine.sku == doc.parent_sku",
    "G6": "EXACTLY ONE catalog_variants row at {parent_product_id: doc.id} "
    "AND its sku == parent_sku",
}


class FingerprintMismatch(Exception):
    """Pre-flight fingerprint did not match. Abort before any write."""

    def __init__(self, problems: List[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


class GateFailure(Exception):
    """A safety gate failed. Abort the WHOLE run with zero writes."""

    def __init__(self, gate: str, offenders: List[Any]):
        super().__init__(f"{gate}: {GATE_DESCRIPTIONS.get(gate, gate)}")
        self.gate = gate
        self.offenders = offenders


def _s(value: Any) -> str:
    """Normalised string form: None -> '', trimmed."""
    return str(value).strip() if value is not None else ""


def _out(msg: str) -> None:
    """stdout, flushed -- so a killed terminal keeps everything printed so far."""
    print(msg, flush=True)


# ===========================================================================
# PURE CORE (unit-tested against fakes -- no live Mongo needed)
# ===========================================================================


def select_targets(catalog) -> List[Dict[str, Any]]:
    """Every catalog_products row with no usable sku, in a STABLE order.

    Sorted by `id` so the ordered id list in the audit file is reproducible and
    a PARTIAL run can be reasoned about (everything before the reported id
    landed).
    """
    rows = [dict(d) for d in catalog.find(TARGET_PREDICATE)]
    rows.sort(key=lambda d: _s(d.get("id")))
    return rows


def check_fingerprint(db_name: str, catalog, products) -> List[str]:
    """Return a list of fingerprint problems ([] == matches).

    Checks the db NAME, both collection counts, and the sku_1 index shape
    (unique + sparse). The index shape is not cosmetic: `sparse` is exactly why
    53 key-absent rows coexist today, and `unique` is why writing an explicit
    null instead of a real sku would insert once and then collide forever.
    """
    problems: List[str] = []
    if db_name != EXPECTED_DB_NAME:
        problems.append(f"db name is {db_name!r}, expected {EXPECTED_DB_NAME!r}")

    cp_count = catalog.count_documents({})
    if cp_count != EXPECTED_CATALOG_PRODUCTS:
        problems.append(
            f"catalog_products count is {cp_count}, expected "
            f"{EXPECTED_CATALOG_PRODUCTS}"
        )

    pr_count = products.count_documents({})
    if pr_count != EXPECTED_PRODUCTS:
        problems.append(
            f"products count is {pr_count}, expected {EXPECTED_PRODUCTS}"
        )

    try:
        info = dict(catalog.index_information())
    except Exception as exc:  # noqa: BLE001
        problems.append(f"could not read catalog_products indexes: {exc}")
        return problems

    spec = info.get(EXPECTED_SKU_INDEX)
    if spec is None:
        problems.append(f"index {EXPECTED_SKU_INDEX} is MISSING")
    else:
        if not bool(spec.get("unique")):
            problems.append(f"index {EXPECTED_SKU_INDEX} is not unique")
        if not bool(spec.get("sparse")):
            problems.append(f"index {EXPECTED_SKU_INDEX} is not sparse")
    return problems


def run_gates(targets, catalog, products, variants) -> None:
    """Run G1-G6 over the whole target set. Raises GateFailure on the FIRST
    failing gate, carrying the offenders. Runs BEFORE any write, so a failure
    means zero writes."""
    # --- G1: every target has a non-blank parent_sku ------------------------
    g1 = [d.get("id") for d in targets if not _s(d.get("parent_sku"))]
    if g1:
        raise GateFailure("G1", g1)

    repair_values = [_s(d.get("parent_sku")) for d in targets]

    # --- G2: no duplicates among the repair values --------------------------
    seen: Dict[str, int] = {}
    for v in repair_values:
        seen[v] = seen.get(v, 0) + 1
    g2 = sorted(v for v, n in seen.items() if n > 1)
    if g2:
        raise GateFailure("G2", g2)

    # --- G3: no intersection with EXISTING catalog_products.sku values -------
    existing = {
        _s(d.get("sku")) for d in catalog.find(HAS_SKU_PREDICATE) if _s(d.get("sku"))
    }
    g3 = sorted(existing.intersection(repair_values))
    if g3:
        raise GateFailure("G3", g3)

    # --- G4 / G5: the products spine row exists and agrees -------------------
    g4: List[Any] = []
    g5: List[Any] = []
    for d in targets:
        spine = products.find_one({"pim_product_id": d.get("id")})
        if spine is None:
            g4.append(d.get("id"))
            continue
        if _s(spine.get("sku")) != _s(d.get("parent_sku")):
            g5.append(
                {
                    "id": d.get("id"),
                    "spine_sku": spine.get("sku"),
                    "parent_sku": d.get("parent_sku"),
                }
            )
    if g4:
        raise GateFailure("G4", g4)
    if g5:
        raise GateFailure("G5", g5)

    # --- G6: EXACTLY ONE variant, and its sku == parent_sku ------------------
    # THE parent/variant safety gate: this is what makes sku := parent_sku
    # PROVABLY correct rather than assumed. A parent with 2+ variants would have
    # no single obvious sku, and a variant whose sku disagrees would mean the
    # parent_sku is not this product's identity.
    g6: List[Any] = []
    for d in targets:
        rows = list(variants.find({"parent_product_id": d.get("id")}))
        if len(rows) != 1 or _s(rows[0].get("sku")) != _s(d.get("parent_sku")):
            g6.append(
                {
                    "id": d.get("id"),
                    "variant_count": len(rows),
                    "variant_skus": [r.get("sku") for r in rows][:5],
                    "parent_sku": d.get("parent_sku"),
                }
            )
    if g6:
        raise GateFailure("G6", g6)


def apply_repairs(catalog, targets) -> Tuple[List[str], int]:
    """Write `sku := parent_sku` for each target, one row at a time.

    Returns ``(modified_ids, total_modified)``. The write filter is the row's id
    ANDed with the SAME TARGET_PREDICATE used for selection, so a row that
    somehow gained a sku between selection and write is a no-op (0 modified)
    rather than a clobber -- and the shortfall assertion in main() catches it.
    """
    modified_ids: List[str] = []
    total_modified = 0
    for d in targets:
        doc_id = d.get("id")
        repair = _s(d.get("parent_sku"))
        write_filter = dict(TARGET_PREDICATE)
        write_filter["id"] = doc_id
        res = catalog.update_one(write_filter, {"$set": {"sku": repair}})
        modified = int(getattr(res, "modified_count", 0) or 0)
        total_modified += modified
        if modified:
            modified_ids.append(str(doc_id))
            # Echo as it lands: a killed terminal still leaves the record.
            _out(f"  MODIFIED {doc_id}  sku={repair}")
        else:
            _out(f"  NOT-MODIFIED {doc_id}  (filter matched 0 rows)")
    return modified_ids, total_modified


# ===========================================================================
# Connection + audit
# ===========================================================================


def resolve_uri() -> str:
    """The connection string, from the env ONLY.

    DELIBERATELY no DatabaseConfig.from_env() fallback: it silently defaults to
    localhost/ims_2_0, so a missing env var would make this script APPLY to a
    local mongo while printing a success banner. Missing -> fail loud.
    """
    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGO_PUBLIC_URL")
        or os.getenv("MONGO_URL")
    )
    if not uri or not uri.strip():
        raise RuntimeError(
            "No Mongo connection string. Set MONGODB_URL, MONGO_PUBLIC_URL or "
            "MONGO_URL (e.g. run under: railway run --service MongoDB -- ...)."
        )
    return uri.strip()


def describe_host(uri: str) -> Tuple[str, Optional[str]]:
    """(host_only, db_from_uri). NEVER returns credentials."""
    try:
        from pymongo.uri_parser import parse_uri

        parsed = parse_uri(uri)
        nodes = parsed.get("nodelist") or []
        host = ", ".join(f"{h}:{p}" for h, p in nodes) or "unknown"
        return host, parsed.get("database")
    except Exception:  # noqa: BLE001
        return "unparseable", None


def write_audit(path: str, payload: Dict[str, Any]) -> None:
    """Best-effort audit write. Never raises into the caller's exit path."""
    try:
        with open(path, "w", encoding="ascii", errors="backslashreplace") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True, default=str)
        _out(f"AUDIT written: {os.path.abspath(path)}")
    except Exception as exc:  # noqa: BLE001
        _out(f"AUDIT WRITE FAILED ({exc}). Audit payload follows:")
        try:
            _out(json.dumps(payload, indent=2, sort_keys=True, default=str))
        except Exception:  # noqa: BLE001
            _out(repr(payload))


# ===========================================================================
# main
# ===========================================================================


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Repair catalog_products rows that carry parent_sku but no sku. "
            "DRY RUN unless --apply."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually write. Omitted = DRY RUN (the default).",
    )
    parser.add_argument(
        "--audit-path",
        default=None,
        help="where to write the audit JSON (default: ./backfill_pim_sku_audit"
        "_<utc>.json)",
    )
    args = parser.parse_args(argv)

    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    audit_path = args.audit_path or f"backfill_pim_sku_audit_{stamp}.json"

    audit: Dict[str, Any] = {
        "script": "backfill_pim_sku_from_parent",
        "started_utc": started.isoformat(),
        "mode": "APPLY" if args.apply else "DRY-RUN",
        "host": None,
        "db_name": None,
        "fingerprint": None,
        "target_count": None,
        "modified_ids": [],
        "modified_count": 0,
        "outcome": "ABORTED-UNKNOWN",
        "error": None,
    }
    exit_code = 2
    client = None

    try:
        # --- connect (fail loud) --------------------------------------------
        try:
            from pymongo import MongoClient
        except Exception as exc:  # noqa: BLE001
            audit["outcome"] = "ABORTED-DRIVER"
            audit["error"] = f"pymongo import failed: {exc}"
            _out(f"FATAL: pymongo is not importable: {exc}")
            return 2

        try:
            uri = resolve_uri()
            host, uri_db = describe_host(uri)
            audit["host"] = host
            _out("=" * 72)
            _out("catalog_products.sku repair (PIM parent SKU backfill)")
            _out("=" * 72)
            _out(f"MODE : {'APPLY (WILL WRITE)' if args.apply else 'DRY RUN'}")
            _out(f"HOST : {host}")
            client = MongoClient(uri, serverSelectionTimeoutMS=20000)
            db = client[uri_db or EXPECTED_DB_NAME]
            db.command("ping")
        except Exception as exc:  # noqa: BLE001
            audit["outcome"] = "ABORTED-CONNECTION"
            audit["error"] = str(exc)
            _out(f"FATAL: could not connect to Mongo: {exc}")
            return 2

        catalog = db["catalog_products"]
        products = db["products"]
        variants = db["catalog_variants"]
        audit["db_name"] = db.name
        _out(f"DB   : {db.name}")

        # --- MANDATORY pre-flight fingerprint --------------------------------
        _out("")
        _out("--- PRE-FLIGHT FINGERPRINT ---")
        problems = check_fingerprint(db.name, catalog, products)
        fingerprint = {
            "db_name": db.name,
            "catalog_products_count": catalog.count_documents({}),
            "products_count": products.count_documents({}),
            "catalog_variants_count": variants.count_documents({}),
            "sku_index": dict(catalog.index_information()).get(EXPECTED_SKU_INDEX),
        }
        audit["fingerprint"] = fingerprint
        _out(f"  db name                 : {fingerprint['db_name']}")
        _out(
            f"  catalog_products count  : {fingerprint['catalog_products_count']}"
            f" (expected {EXPECTED_CATALOG_PRODUCTS})"
        )
        _out(
            f"  products count          : {fingerprint['products_count']}"
            f" (expected {EXPECTED_PRODUCTS})"
        )
        _out(f"  catalog_variants count  : {fingerprint['catalog_variants_count']}")
        _out(f"  index {EXPECTED_SKU_INDEX}            : {fingerprint['sku_index']}")
        if problems:
            audit["outcome"] = "ABORTED-FINGERPRINT"
            audit["error"] = problems
            _out("")
            _out("FINGERPRINT MISMATCH -- ABORTING WITH 0 WRITES:")
            for p in problems:
                _out(f"  - {p}")
            return 3
        _out("  FINGERPRINT OK")

        # --- targets ----------------------------------------------------------
        targets = select_targets(catalog)
        audit["target_count"] = len(targets)
        _out("")
        _out(f"--- TARGETS: {len(targets)} row(s) with no usable sku ---")
        for d in targets[:5]:
            _out(f"  {d.get('id')}  parent_sku={d.get('parent_sku')}")
        if len(targets) > 5:
            _out(f"  ... and {len(targets) - 5} more")
        if not targets:
            audit["outcome"] = "COMPLETE"
            _out("Nothing to repair. Exiting clean.")
            return 0

        # --- gates G1-G6 (ALL before any write) -------------------------------
        _out("")
        _out("--- GATES ---")
        try:
            run_gates(targets, catalog, products, variants)
        except GateFailure as gf:
            audit["outcome"] = f"ABORTED-GATE-{gf.gate}"
            audit["error"] = {
                "gate": gf.gate,
                "description": GATE_DESCRIPTIONS.get(gf.gate),
                "offenders": gf.offenders,
            }
            _out(f"  {gf.gate} FAILED: {GATE_DESCRIPTIONS.get(gf.gate)}")
            _out(f"  offenders ({len(gf.offenders)}):")
            for off in gf.offenders[:20]:
                _out(f"    {off}")
            if len(gf.offenders) > 20:
                _out(f"    ... and {len(gf.offenders) - 20} more")
            _out("")
            _out("ABORTED WITH 0 WRITES.")
            return 4
        for gate in ("G1", "G2", "G3", "G4", "G5", "G6"):
            _out(f"  {gate} PASS  ({GATE_DESCRIPTIONS[gate]})")

        # --- dry run stops here ----------------------------------------------
        if not args.apply:
            audit["outcome"] = "DRY-RUN-COMPLETE"
            _out("")
            _out("--- DRY RUN: would write ---")
            for d in targets:
                _out(f"  {d.get('id')}  sku := {d.get('parent_sku')}")
            _out("")
            _out(
                f"DRY RUN COMPLETE. {len(targets)} row(s) would be repaired. "
                "Nothing was written. Re-run with --apply to commit."
            )
            return 0

        # --- APPLY -------------------------------------------------------------
        _out("")
        _out(f"--- APPLYING {len(targets)} repair(s) ---")
        modified_ids, total_modified = apply_repairs(catalog, targets)
        audit["modified_ids"] = modified_ids
        audit["modified_count"] = total_modified

        # HARD assertion: success is NEVER inferred from a re-count.
        if total_modified != len(targets):
            last = modified_ids[-1] if modified_ids else "NONE"
            audit["outcome"] = f"PARTIAL@{last}"
            _out("")
            _out(
                f"WRITE SHORTFALL: modified {total_modified} of "
                f"{len(targets)} target(s). Last id written: {last}"
            )
            return 5

        # Post-verify with the SAME predicate used for selection.
        remaining = catalog.count_documents(TARGET_PREDICATE)
        audit["remaining_after"] = remaining
        _out("")
        _out(f"Rows still matching the no-sku predicate: {remaining}")
        if remaining:
            last = modified_ids[-1] if modified_ids else "NONE"
            audit["outcome"] = f"PARTIAL@{last}"
            _out("POST-VERIFY FAILED: some rows still have no sku.")
            return 5

        audit["outcome"] = "COMPLETE"
        _out(f"COMPLETE. {total_modified} row(s) repaired.")
        return 0

    except Exception as exc:  # noqa: BLE001 -- audit must still be written
        audit["outcome"] = "ABORTED-EXCEPTION"
        audit["error"] = repr(exc)
        _out(f"FATAL: unexpected error: {exc!r}")
        exit_code = 2
        return exit_code
    finally:
        audit["finished_utc"] = datetime.now(timezone.utc).isoformat()
        write_audit(audit_path, audit)
        _out(f"OUTCOME: {audit['outcome']}")
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    sys.exit(main())
