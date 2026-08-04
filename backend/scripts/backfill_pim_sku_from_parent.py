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
- DEPLOY-ORDERING GATE: --apply is REFUSED (exit 6, 0 writes) unless
  --code-is-deployed is ALSO passed. See STEP 0 in USAGE below: applying this
  repair while the OLD online_catalog.py is still deployed flips
  sellable_online True->False for the 27 live Ray-Ban Meta SKUs (their
  catalog_products rows gain a `sku`, so they stop falling through to the
  variant branch that reads the live variant gid) and the post-sale oversell
  alarm goes SILENT -- the exact P0 the online_catalog.py fix on this branch
  prevents.
- MANDATORY PRE-FLIGHT FINGERPRINT (db name; the sku_1 index shape; GROWTH-
  TOLERANT collection floors counts >= 59 / >= 53 -- an ordinary product
  create adds one row to BOTH collections, so exact counts would break on the
  first post-deploy create; the EXACT no-sku target count == 53, overridable
  via --expect-targets N; and the EXACT count gap catalog_products - products
  == 1, overridable via --expect-gap N). The gap is NOT immutable: catalog-
  first repairs/promotes add a spine row WITHOUT a new catalog row and shrink
  it -- verified live 2026-07-29, when the latent-defects repair created
  spines for 5 of the 6 sku-carrying spineless catalog rows and moved the gap
  6 -> 1 (one stray remains: SGRAYMETARW4006601/715050SMARTGLASSES). Any
  mismatch aborts with exit 3 BEFORE any write -- this is what stops the
  script from cheerfully "succeeding" against the wrong database.
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
  PROGRESS IS RECORDED AS IT LANDS, NOT ON RETURN: apply_repairs writes each id
  into the live audit dict INSIDE the write loop. The earlier shape
  (`ids, n = apply_repairs(...)`, audit updated after) never completed the
  assignment when a write raised mid-loop -- a transient pymongo AutoReconnect /
  NetworkTimeout partway through 53 writes over the Railway proxy -- so the
  `finally` serialised the untouched initial values and the audit claimed ZERO
  writes after a PARTIAL apply. A mid-loop raise is now outcome
  PARTIAL-EXCEPTION@<last id> with exit 5.
- ASCII only (no emoji -- Windows cp1252). No secret values are ever printed;
  only the HOST.

USAGE
-----
  # STEP 0: this branch must be MERGED and DEPLOYED to Railway before --apply.
  #   The fixed online_catalog.py (sellable_online resolved from the live
  #   variant gid on the PRODUCT branch too) must be LIVE first. If the 53
  #   rows gain a sku while the OLD online_catalog.py is deployed,
  #   sellable_online flips True->False for the 27 live Ray-Ban Meta SKUs and
  #   the post-sale oversell alarm goes silent. --apply therefore refuses to
  #   run unless --code-is-deployed is ALSO passed.

  # DRY RUN (default) against prod Mongo -- writes nothing:
  cd "C:\\Users\\avina\\IMS 2.0 CLAUDE COWORK\\ims-2.0-railway"
  railway run --service MongoDB -- ".venv\\Scripts\\python.exe" \\
      backend/scripts/backfill_pim_sku_from_parent.py

  # APPLY (owner-gated; ONLY after STEP 0 is verifiably true):
  railway run --service MongoDB -- ".venv\\Scripts\\python.exe" \\
      backend/scripts/backfill_pim_sku_from_parent.py --apply --code-is-deployed

EXIT CODES
----------
  0  dry-run OK, or apply COMPLETE (or nothing to do)
  2  connection / driver failure
  3  pre-flight fingerprint mismatch (aborted, 0 writes)
  4  a gate G1-G6 failed (aborted, 0 writes)
  5  write shortfall, OR a write raised mid-loop (PARTIAL / PARTIAL-EXCEPTION
     -- the audit file names the ids that DID land)
  6  --apply without --code-is-deployed (deploy-ordering gate; 0 writes)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Module-level so tests can monkeypatch a fake client class onto this module
# (main() reads the MODULE attribute, never re-imports). None = driver missing,
# handled loud in main().
try:
    from pymongo import MongoClient
except Exception:  # noqa: BLE001 - surfaced as ABORTED-DRIVER in main()
    MongoClient = None  # type: ignore[assignment,misc]

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
# Pre-flight fingerprint -- RE-verified live on prod 2026-07-29 (late).
#
# The COUNTS are growth-tolerant FLOORS (>=), not exact: an ordinary product
# create adds one row to BOTH collections, so exact 59/53 would break on the
# first create after the branch deploys. What stays HARD and exact:
#   * the db name and the sku_1 unique+sparse index shape,
#   * the TARGET_PREDICATE count (53 no-sku rows; --expect-targets overrides),
#   * the count gap catalog_products_count - products_count == 1
#     (--expect-gap overrides).
#
# GAP HISTORY -- it is NOT immutable, which is exactly why it is checked:
# at review time the gap was 6 (six sku-carrying catalog rows with no spine,
# SGRAYMETARW4006601 / SB5050 etc.). On 2026-07-29 ~23:21 IST the
# latent-defects repair (sibling branch, actor "latent-defects-repair")
# created spine rows FOR five of those six, moving the gap 6 -> 1. Ordinary
# creates keep the gap constant (both sides +1); catalog-first repairs /
# promotes shrink it (spine only). If the last stray
# (SGRAYMETARW4006601/715050SMARTGLASSES) gains a spine too, the gap becomes
# 0 -- re-verify live and pass --expect-gap 0 rather than editing this file.
# ---------------------------------------------------------------------------
EXPECTED_DB_NAME = "ims_2_0"
MIN_CATALOG_PRODUCTS = 59
MIN_PRODUCTS = 53
EXPECTED_TARGETS = 53
EXPECTED_COUNT_GAP = 1
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


def check_fingerprint(
    db_name: str,
    catalog,
    products,
    expect_targets: int = EXPECTED_TARGETS,
    expect_gap: int = EXPECTED_COUNT_GAP,
) -> List[str]:
    """Return a list of fingerprint problems ([] == matches).

    HARD + exact: the db NAME and the sku_1 index shape (unique + sparse) --
    `sparse` is exactly why 53 key-absent rows coexist today, and `unique` is
    why writing an explicit null instead of a real sku would insert once and
    then collide forever. Also HARD + exact: the TARGET_PREDICATE count
    (== expect_targets, default 53; --expect-targets overrides) and the count
    gap catalog_products_count - products_count (== expect_gap, default 1 as
    re-verified live 2026-07-29 after the latent-defects repair;
    --expect-gap overrides after re-verifying).

    GROWTH-TOLERANT: the raw collection counts are FLOORS (>= 59 / >= 53),
    because an ordinary product create adds one row to BOTH collections -- an
    exact count would brick the script on the first create, while the gap
    check and the exact target count still pin the shape.
    """
    problems: List[str] = []
    if db_name != EXPECTED_DB_NAME:
        problems.append(f"db name is {db_name!r}, expected {EXPECTED_DB_NAME!r}")

    cp_count = catalog.count_documents({})
    if cp_count < MIN_CATALOG_PRODUCTS:
        problems.append(
            f"catalog_products count is {cp_count}, expected >= "
            f"{MIN_CATALOG_PRODUCTS}"
        )

    pr_count = products.count_documents({})
    if pr_count < MIN_PRODUCTS:
        problems.append(f"products count is {pr_count}, expected >= {MIN_PRODUCTS}")

    if (cp_count - pr_count) != int(expect_gap):
        problems.append(
            f"count gap (catalog_products - products) is {cp_count - pr_count}, "
            f"expected exactly {expect_gap} (sku-carrying catalog rows with no "
            "spine; re-verify live and pass --expect-gap N if a catalog-first "
            "repair/promote legitimately changed it)"
        )

    target_count = catalog.count_documents(TARGET_PREDICATE)
    if target_count != int(expect_targets):
        problems.append(
            f"no-sku target count is {target_count}, expected exactly "
            f"{expect_targets} (pass --expect-targets N if this changed for a "
            "verified reason)"
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


def apply_repairs(
    catalog, targets, audit: Optional[Dict[str, Any]] = None
) -> Tuple[List[str], int]:
    """Write `sku := parent_sku` for each target, one row at a time.

    Returns ``(modified_ids, total_modified)``. The write filter is the row's id
    ANDed with the SAME TARGET_PREDICATE used for selection, so a row that
    somehow gained a sku between selection and write is a no-op (0 modified)
    rather than a clobber -- and the shortfall assertion in main() catches it.

    `audit` (the LIVE audit dict, passed by main): progress is recorded INSIDE
    the loop -- the id is appended and `modified_count` bumped BEFORE the next
    update_one is issued. THIS IS NOT COSMETIC. A caller that only assigned on
    RETURN (`ids, n = apply_repairs(...)`) never completed the assignment when a
    write raised mid-loop, so the audit-writing `finally` serialised the
    untouched initial values: rows really were written, yet the audit file said
    modified_ids [] / modified_count 0. The realistic live trigger is not a
    duplicate key (G3 proves 0 intersection) but a transient pymongo
    AutoReconnect / NetworkTimeout partway through 53 writes over the Railway
    proxy. `audit["modified_ids"]` is REBOUND to the same list object this
    function appends to, so every append is visible to the `finally` instantly.
    """
    modified_ids: List[str] = []
    total_modified = 0
    if audit is not None:
        audit["modified_ids"] = modified_ids  # same object -> appends are live
        audit["modified_count"] = 0
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
        # Recorded BEFORE the next update_one, so a raise on any later row
        # leaves the audit naming exactly the rows that already landed.
        if audit is not None:
            audit["modified_count"] = total_modified
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
        help="actually write. Omitted = DRY RUN (the default). REFUSED unless "
        "--code-is-deployed is also passed (STEP 0: deploy ordering).",
    )
    parser.add_argument(
        "--code-is-deployed",
        action="store_true",
        help="required WITH --apply: confirms this branch (the fixed "
        "online_catalog.py) is MERGED and DEPLOYED to Railway. Without the "
        "new code live, repairing the 53 rows flips sellable_online "
        "True->False for the 27 live Ray-Ban Meta SKUs and silences the "
        "oversell alarm.",
    )
    parser.add_argument(
        "--expect-targets",
        type=int,
        default=EXPECTED_TARGETS,
        help=f"exact TARGET_PREDICATE row count required by the pre-flight "
        f"fingerprint (default {EXPECTED_TARGETS}). Override ONLY after "
        "verifying why the target set changed.",
    )
    parser.add_argument(
        "--expect-gap",
        type=int,
        default=EXPECTED_COUNT_GAP,
        help=f"exact (catalog_products - products) count gap required by the "
        f"pre-flight fingerprint (default {EXPECTED_COUNT_GAP}, re-verified "
        "live 2026-07-29). Catalog-first repairs/promotes shrink it; override "
        "ONLY after re-verifying live.",
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
        # --- STEP 0: deploy-ordering gate (BEFORE any connection) -----------
        if args.apply and not args.code_is_deployed:
            audit["outcome"] = "ABORTED-DEPLOY-GATE"
            audit["error"] = "--apply without --code-is-deployed"
            _out("=" * 72)
            _out("APPLY REFUSED -- DEPLOY-ORDERING GATE (0 writes)")
            _out("=" * 72)
            _out("STEP 0: this branch must be MERGED and DEPLOYED to Railway")
            _out("BEFORE --apply. If the 53 rows gain a sku while the OLD")
            _out("online_catalog.py is still deployed, sellable_online flips")
            _out("True->False for the 27 live Ray-Ban Meta SKUs and the")
            _out("post-sale oversell alarm goes SILENT.")
            _out("")
            _out("Verify the deploy is live, then re-run with BOTH flags:")
            _out("    --apply --code-is-deployed")
            return 6

        if args.apply:
            _out("=" * 72)
            _out("STEP 0 ACKNOWLEDGED via --code-is-deployed: you are asserting")
            _out("the fixed online_catalog.py is LIVE on Railway. If that is")
            _out("not true, STOP NOW (Ctrl+C) -- applying against the old code")
            _out("silences the oversell alarm for the 27 live Ray-Ban Meta SKUs.")
            _out("=" * 72)

        # --- connect (fail loud) --------------------------------------------
        if MongoClient is None:
            audit["outcome"] = "ABORTED-DRIVER"
            audit["error"] = "pymongo import failed at module load"
            _out("FATAL: pymongo is not importable.")
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
        problems = check_fingerprint(
            db.name,
            catalog,
            products,
            expect_targets=args.expect_targets,
            expect_gap=args.expect_gap,
        )
        fingerprint = {
            "db_name": db.name,
            "catalog_products_count": catalog.count_documents({}),
            "products_count": products.count_documents({}),
            "catalog_variants_count": variants.count_documents({}),
            "no_sku_target_count": catalog.count_documents(TARGET_PREDICATE),
            "sku_index": dict(catalog.index_information()).get(EXPECTED_SKU_INDEX),
        }
        audit["fingerprint"] = fingerprint
        _out(f"  db name                 : {fingerprint['db_name']}")
        _out(
            f"  catalog_products count  : {fingerprint['catalog_products_count']}"
            f" (expected >= {MIN_CATALOG_PRODUCTS})"
        )
        _out(
            f"  products count          : {fingerprint['products_count']}"
            f" (expected >= {MIN_PRODUCTS})"
        )
        _out(
            "  count gap (cp - pr)     : "
            f"{fingerprint['catalog_products_count'] - fingerprint['products_count']}"
            f" (expected exactly {args.expect_gap})"
        )
        _out(
            f"  no-sku target count     : {fingerprint['no_sku_target_count']}"
            f" (expected exactly {args.expect_targets})"
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
        # apply_repairs OWNS audit["modified_ids"] / ["modified_count"] from
        # here on (it rebinds them and updates in-loop). Do NOT re-assign them
        # after the call: the assignment would never run on a mid-loop raise,
        # which is exactly the bug this shape fixes.
        try:
            modified_ids, total_modified = apply_repairs(
                catalog, targets, audit=audit
            )
        except Exception as exc:  # noqa: BLE001 - a raise mid-write is PARTIAL
            landed = audit.get("modified_ids") or []
            last = landed[-1] if landed else "NONE"
            audit["outcome"] = f"PARTIAL-EXCEPTION@{last}"
            audit["error"] = repr(exc)
            _out("")
            _out(f"WRITE FAILED MID-LOOP: {exc!r}")
            _out(
                f"  rows CONFIRMED written before the failure: "
                f"{audit.get('modified_count')} of {len(targets)}"
            )
            _out(f"  last id written: {last}")
            _out("  The audit file names every id that landed -- re-running is")
            _out("  safe (the shared predicate skips repaired rows).")
            return 5

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
