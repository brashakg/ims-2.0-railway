#!/usr/bin/env python3
"""
IMS 2.0 - Create the 11 Ray-Ban Meta "Large" size products as variant-of
=========================================================================
Runbook-only script. NOT in CI. ASCII only (Windows cp1252).

WHY
---
The 36 Ray-Ban Meta listings on Shopify carry a Size option (base + Large).
IMS keeps ONE product per SKU and held only the base size, so the 11 Large
units on Shopify (SKUs ending "-L") had no IMS product, no IMS stock and no
IMS price. Owner ruling 2026-09-06: a size product is its OWN spine product
(own SKU, own stock, own POS sale) LINKED to its parent -- it pushes ONLY its
own price, barcode and stock to the parent's Shopify listing (mapped to the
parent's gid + its own variant gid) and never a listing-level field.

WHAT IT DOES (per row of the input, in this order)
---------------------------------------------------
  1. CREATE through the ONE product door -- product_master.create_via_door
     (-> normalise_payload, the registry, the duplicate hard-block) with the
     EXPLICIT "-L" sku (build_sku would mint "...601/71LARGE"; Shopify holds
     "...601/71-L" and a reseed writes inventoryItem.sku = row.sku, so IMS
     must equal Shopify), attributes.size = "Large", category SMARTGLASSES,
     variant_of = the parent spine's product_id, name from the input's
     name_hint (else the child would share the base's display name on every
     bill), the base photo, sync_to_shopify True. The door writes the spine
     (variant_of, size, identity_key ...|large), the child twin (born CLEAN,
     ecom.variant_of, no gid) and the parent-linked catalog_variants row.
  2. LINK the Shopify ids -- the Large variant's ProductVariant +
     InventoryItem gids from the snapshot onto that row (the same
     CatalogVariantRepository.upsert the push engine uses; a $set patch, the
     link survives). This is what makes the parent's _needs_repair False (no
     duplicate Large variant on the next parent push) and the stock target
     resolvable.
  3. ASSERT by re-reading: spine / twin / row / the parent's row set.
Then, once for all rows:
  4. OPENING STOCK through the door -- the SAME opening_stock_preview /
     opening_stock_commit functions the UI's POST /inventory/opening-stock
     routes run (repositories, role gate, EAN-13 counter, online-store guard,
     batch doc, audit row): 1 unit per Large product at BV-PUN-01. Preview
     must say 11 WILL_ADD / 0 skip / 0 error before the commit.
  5. Print the REVERSAL LIST (every doc this run minted). It is built from
     the INPUT skus (find by sku), not from the rows that returned, so a row
     that raised AFTER the door wrote is in it; ANY exception in the apply
     loop prints it and lands in the run record.

Nothing is pushed to Shopify. The Large variants already exist there at
qty 1 / DENY; the next parent stock pass carries {base: pooled, base-L: 1}
per parent -- gated by the same owner blocker as the 49-unit press.

FENCES (each exits before any write)
------------------------------------
  INPUT   the file must hash to INPUT_SHA256 and hold exactly 11 rows, one
          per "-L" sku, every row shaped as expected
  CLOCK   the door stamps naive datetime.now(); the box must be UTC
  MIRROR  pm.mirror_enabled() must be ON (registry default True): OFF, the
          door writes the spine + twin but NO catalog_variants row, and the
          gid stamp would then insert an orphan row
  STORE   4dc49c44-... must be BV-PUN-01 / RETAIL on this database
  ACTOR   a real active users doc, ADMIN/SUPERADMIN (or Pune in store_ids)
  ROWS    for every row: the parent spine exists by product_id with that sku,
          category SMARTGLASSES, no variant_of of its own; the parent twin
          (pim_product_id) carries ecom.shopify_product_id == the Large
          variant's product gid and is CLEAN (locally_modified False -- a
          dirty parent could be pushed between the create and the gid stamp
          and mint a duplicate Large variant); no products / catalog_products
          / catalog_variants doc holds the "-L" sku; no row holds the Large
          variant gid; the door's own three duplicate guards pass
  WINDOW  --apply refuses inside the 00:30-01:30 / 08:30-09:30 IST live-sync
          windows
  OWNER   --apply refuses without --owner-ack: the four OWNER POINTS every
          run prints (parent take-down drafts the Large too; a deleted parent
          orphans its child; child online price = own MRP under the family
          rule; child delist lands DELIST_FAILED until the online location
          is set) were put to the owner and answered
  RERUN   --apply refuses when opening_stock_batches already holds a line for
          any "-L" sku, or when any "-L" product exists (the ROWS fence)
Dry-run (default) = every fence + the door's normalise_payload + the three
duplicate pre-checks per row, no write, the would-be spine/twin/row printed.
The opening-stock PREVIEW cannot run in a dry-run (the products do not exist
yet); it runs under --apply, after the creates, before the commit.

USAGE
-----
Dry-run (DEFAULT, read-only):
    TZ=UTC railway run --service ims-2.0-railway -- ".venv\\Scripts\\python.exe" scripts/create_large_variants.py
Apply:
    TZ=UTC railway run --service ims-2.0-railway -- ".venv\\Scripts\\python.exe" scripts/create_large_variants.py --apply --actor <username> --owner-ack
Optional: --input <large_11.json> (must match INPUT_SHA256), --out <response json>.
DEFAULT_INPUT is the session scratchpad copy of the approved file -- a temp
directory. If it is gone, --input a copy that still hashes to INPUT_SHA256
(a different file is a NEW approval: re-pin after reading it).

REVERSAL (printed by the run; do it in this order, only while every minted
unit is still AVAILABLE -- never after a sale; audit rows stay; NEVER touch
Shopify, the Large variants pre-date IMS):
    stock_units      {product_id: <child>, source: "OPENING_STOCK"}
    opening_stock_batches {batch_id: <printed>}
    catalog_variants {sku: "<-L sku>"}
    catalog_products {id: <child twin id>}
    products         {product_id: <child product_id>}

Connection: MONGO_PUBLIC_URL, else MONGO_URL (the vars `railway run` injects).
Nothing secret is printed.
"""
import os

if __name__ == "__main__":
    # Must precede the first localtime call (the CRT reads TZ once).
    os.environ["TZ"] = "UTC"

import argparse  # noqa: E402
import asyncio  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from typing import Any, Dict, List  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "backend"))
os.environ.setdefault("JWT_SECRET_KEY", "runbook")
os.environ.setdefault("ENVIRONMENT", "script")

from fastapi import HTTPException  # noqa: E402

from api.services import product_master as pm  # noqa: E402
from api.services import shopify_push  # noqa: E402
from api.services.shopify_live_sync import variants_for_product  # noqa: E402
from database.repositories.audit_repository import AuditRepository  # noqa: E402
from database.repositories.catalog_variant_repository import (  # noqa: E402
    CatalogVariantRepository,
)
from database.repositories.product_repository import ProductRepository  # noqa: E402

DEFAULT_INPUT = (
    r"C:\Users\avina\AppData\Local\Temp\claude\C--Users-avina-IMS-2-0-CLAUDE-COWORK"
    r"\1bf19447-46d0-41c4-aa33-c4d199aeaafd\scratchpad\variant_of\large_11.json"
)
# THE approval: this exact file, these exact 11 rows.
INPUT_SHA256 = "5d7e3e8ccf02ce658dd2c318bf247d4361da56788d09202a20fbb609bf12d6cb"
EXPECTED_ROWS = 11
PUNE_STORE_ID = "4dc49c44-08a1-46e1-85fb-8b7eca55f560"  # BV-PUN-01 GANGADHAM- PUNE
IST = timezone(timedelta(hours=5, minutes=30))
SYNC_WINDOWS_IST = ((0, 30, 1, 30), (8, 30, 9, 30))  # around the 01:00 / 09:00 runs
DB_NAME = "ims_2_0"
EXIT_FENCE, EXIT_DRIFT, EXIT_SHORT = 1, 2, 4

# Design section 6 / verifier round 3: not code defects -- decisions the
# owner has not been asked yet. Printed by every run; --apply refuses until
# --owner-ack says they were put to him and answered.
OWNER_POINTS = (
    "A. PARENT TAKE-DOWN DRAFTS THE WHOLE LISTING: the parent's take-down button / Delete runs "
    "productUpdate status DRAFT, so its Large variant goes off sale with it (a child take-down only "
    "DENIES + zeroes its own variant). Accepted?",
    "B. A DELETED / DEACTIVATED PARENT ORPHANS ITS CHILD: no guard today -- the child stays POS-sellable, "
    "its row points at a soft-deleted twin, and the stock pass keeps writing its quantity to a DRAFT "
    "listing. Block the parent's deactivation while a child is active, cascade, or accept for now?",
    "C. A CHILD'S ONLINE PRICE = ITS OWN MRP UNDER THE FAMILY'S DISCOUNT RULE, never its in-store "
    "offer_price (moot for these 11: mrp == offer_price, no gtin). Accepted?",
    "D. A DEACTIVATED SIZE LANDS DELIST_FAILED ON PROD TODAY: SHOPIFY_ONLINE_LOCATION_ID is not set, so "
    "the Catalog screen says 'take-down failed' for a deactivated size until the online-location "
    "decision lands (the same blocker as the 49-unit stock press). Known?",
)


def check_owner_points(apply: bool, acknowledged: bool) -> None:
    """Print the open owner points on EVERY run; --apply refuses until
    --owner-ack. A dry-run never needs the flag."""
    print("OWNER POINTS (put to the owner before --apply):")
    for p in OWNER_POINTS:
        print("  ", p)
    if apply and not acknowledged:
        sys.exit("--apply needs --owner-ack: the OWNER POINTS above must be put to the owner and "
                 "answered first. Nothing done.")

_PAYLOAD_KEYS = (
    "category", "sku", "attributes", "mrp", "offer_price", "cost_price",
    "hsn_code", "gst_rate", "discount_category", "tags",
)


def utc_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Input + fences (read-only)
# ---------------------------------------------------------------------------


def load_input(path: str) -> List[Dict[str, Any]]:
    if not os.path.isfile(path):
        sys.exit(f"input {path} does not exist (the scratchpad copy is gone?) -- pass --input <a copy that "
                 f"hashes to {INPUT_SHA256}>. Nothing done.")
    with open(path, "rb") as fh:
        raw = fh.read()
    digest = hashlib.sha256(raw).hexdigest()
    print(f"INPUT  {path}\n       sha256={digest}")
    if digest != INPUT_SHA256:
        sys.exit(f"input hash {digest} != approved {INPUT_SHA256} -- a different file is a NEW approval; "
                 "re-pin INPUT_SHA256 after reading it. Nothing done.")
    rows = json.loads(raw)["rows"]
    if len(rows) != EXPECTED_ROWS:
        sys.exit(f"input holds {len(rows)} rows, approved {EXPECTED_ROWS}. Nothing done.")
    skus = [r["large_sku"] for r in rows]
    if len(set(skus)) != len(skus):
        sys.exit("input lists a large_sku twice. Nothing done.")
    for r in rows:
        base = r["create_payload_base_fields"]
        assert base["sku"] == r["large_sku"] == r["shopify_large_variant"]["sku"], r["large_sku"]
        assert r["large_sku"].endswith("-L") and r["large_sku"] == r["parent"]["sku"] + "-L", r["large_sku"]
        assert base["category"] == "SMARTGLASSES" and base["attributes"].get("size") == "Large", r["large_sku"]
        assert base.get("name_hint"), f"{r['large_sku']}: no name_hint (the child would share the base's name)"
        assert r["opening_stock"]["store_id"] == PUNE_STORE_ID and r["opening_stock"]["qty"] == 1, r["large_sku"]
        assert r["shopify_large_variant"]["product_id"] == r["parent"]["shopify_product_id"], r["large_sku"]
    print(f"INPUT OK  {len(rows)} rows, {sum(r['opening_stock']['qty'] for r in rows)} units at BV-PUN-01")
    return rows


def check_clock():
    skew = abs((datetime.now() - utc_naive()).total_seconds())
    print(f"CLOCK  local {datetime.now():%Y-%m-%d %H:%M:%S}  utc {utc_naive():%Y-%m-%d %H:%M:%S}  skew {skew:.0f}s")
    if skew > 5:
        sys.exit("local clock is not UTC: run as  TZ=UTC railway run --service ims-2.0-railway -- ...")


def in_sync_window(now_utc: datetime) -> bool:
    t = now_utc.astimezone(IST)
    minutes = t.hour * 60 + t.minute
    return any(h1 * 60 + m1 <= minutes < h2 * 60 + m2 for h1, m1, h2, m2 in SYNC_WINDOWS_IST)


def connect_prod():
    """Configure the app's own DatabaseConnection singleton (what
    api.dependencies.get_*_repository() read) from MONGO_PUBLIC_URL."""
    from database.connection import DatabaseConfig, get_db, init_db

    uri = os.environ.get("MONGO_PUBLIC_URL") or os.environ.get("MONGO_URL")
    if not uri:
        sys.exit("MONGO_PUBLIC_URL / MONGO_URL not set (run via `railway run`)")
    uri += ("&" if "?" in uri else "?") + "serverSelectionTimeoutMS=20000"
    for attempt in (1, 2):
        if init_db(DatabaseConfig.from_uri(uri, database=DB_NAME)):
            return get_db().db
        print(f"[WARN] connect attempt {attempt} failed")
    sys.exit("could not connect to MongoDB")


def check_store(db):
    store = db["stores"].find_one({"store_id": PUNE_STORE_ID}, {"_id": 0, "store_code": 1, "store_type": 1})
    print(f"STORE  {PUNE_STORE_ID} = {json.dumps(store)}")
    if not store or store.get("store_code") != "BV-PUN-01" or store.get("store_type") != "RETAIL":
        sys.exit(f"store {PUNE_STORE_ID} is not BV-PUN-01 / RETAIL on this database. Nothing done.")


def resolve_actor(db, label, apply):
    """A real, active users doc allowed to act at Pune -> the JWT-shaped
    current_user the doors expect. A dry-run without --actor uses a
    synthetic SUPERADMIN (nothing is written)."""
    if label:
        user = db["users"].find_one({"$or": [{"username": label}, {"user_id": label}]})
        if user is None:
            sys.exit(f"--actor {label!r}: no users doc with that username/user_id")
        roles = user.get("roles") or ([user["role"]] if user.get("role") else [])
        if user.get("is_active", True) is False:
            sys.exit(f"--actor {label!r}: account is disabled")
        if not ({"SUPERADMIN", "ADMIN"} & set(roles) or PUNE_STORE_ID in (user.get("store_ids") or [])):
            sys.exit(f"--actor {label!r}: roles {roles} are not ADMIN/SUPERADMIN and BV-PUN-01 is not in store_ids")
        return {"user_id": user.get("user_id") or str(user.get("_id")), "username": user.get("username"),
                "roles": roles, "active_store_id": PUNE_STORE_ID}
    if apply:
        sys.exit("--apply needs --actor <username|user_id>")
    return {"user_id": "dry-run", "username": "dry-run", "roles": ["SUPERADMIN"], "active_store_id": PUNE_STORE_ID}


def build_payload(row: Dict[str, Any]):
    """(door payload, extra_fields) for one row -- the create step's input."""
    base = row["create_payload_base_fields"]
    payload = {k: base.get(k) for k in _PAYLOAD_KEYS}
    payload["variant_of"] = row["parent"]["product_id"]
    extra = {"name": base["name_hint"], "images": list(base.get("images") or []),
             "sync_to_shopify": bool(base.get("sync_to_shopify", True))}
    return payload, extra


def row_fences(db, rows: List[Dict[str, Any]], product_repo) -> List[str]:
    """Every per-row precondition, read-only. Returns the problems (empty = ok)."""
    problems: List[str] = []
    if not pm.mirror_enabled():
        problems.append("pm.mirror_enabled is OFF: the door would write the spine + twin but NO catalog_variants "
                        "row, and the gid stamp would insert an orphan row. Turn the policy on first.")
    large_skus = {r["large_sku"] for r in rows}
    for r in rows:
        sku, parent = r["large_sku"], r["parent"]
        spine = db["products"].find_one({"product_id": parent["product_id"]})
        if spine is None:
            problems.append(f"{sku}: parent spine {parent['product_id']} missing")
            continue
        if spine.get("sku") != parent["sku"]:
            problems.append(f"{sku}: parent spine sku {spine.get('sku')!r} != {parent['sku']!r}")
        if pm.resolve_category(spine.get("category")) != "SMARTGLASSES":
            problems.append(f"{sku}: parent category {spine.get('category')!r} is not SMARTGLASSES")
        if spine.get("variant_of"):
            problems.append(f"{sku}: parent is itself a variant of {spine['variant_of']} (no chains)")
        twin = db["catalog_products"].find_one({"id": spine.get("pim_product_id") or parent["pim_product_id"]})
        ecom = (twin or {}).get("ecom") or {}
        if twin is None:
            problems.append(f"{sku}: parent twin {parent['pim_product_id']} missing")
        elif ecom.get("shopify_product_id") != r["shopify_large_variant"]["product_id"]:
            problems.append(f"{sku}: parent twin gid {ecom.get('shopify_product_id')} != the Large variant's product "
                            f"{r['shopify_large_variant']['product_id']}")
        elif ecom.get("locally_modified"):
            problems.append(f"{sku}: parent twin is DIRTY (locally_modified) -- a push between the create and the "
                            "gid stamp could mint a duplicate Large variant; let the sync clear it first")
        elif shopify_push.is_variant_of(twin):
            problems.append(f"{sku}: parent twin is itself a variant-of twin")
        for coll in ("products", "catalog_products", "catalog_variants"):
            if db[coll].find_one({"sku": sku}) is not None:
                problems.append(f"{sku}: {coll} already holds this sku (this run has been pressed before?)")
        if db["catalog_variants"].find_one({"shopify_variant_id": r["shopify_large_variant"]["variant_id"]}) is not None:
            problems.append(f"{sku}: a catalog_variants row already carries the Large variant gid")
        # The door's own three duplicate guards, dry (normalise_payload does not write).
        payload, extra = build_payload(r)
        try:
            spine_preview = pm.build_canonical_product(
                payload, source="MASTER", extra_fields=extra, product_repo=product_repo, db=db
            )
        except pm.ProductMasterError as exc:
            problems.append(f"{sku}: the door would refuse ({exc.status} {getattr(exc, 'field', '')}): {exc}")
            continue
        if spine_preview.get("sku") != sku:
            problems.append(f"{sku}: the door would mint sku {spine_preview.get('sku')!r}")
        if product_repo.find_by_identity_key(spine_preview.get("identity_key") or "__none__") is not None:
            problems.append(f"{sku}: identity_key {spine_preview.get('identity_key')} already exists")
        r["_preview"] = spine_preview
    for batch in db["opening_stock_batches"].find({}, {"_id": 0, "batch_id": 1, "lines": 1}):
        hit = sorted({ln.get("sku") for ln in (batch.get("lines") or []) if ln.get("sku") in large_skus})
        if hit:
            problems.append(f"opening_stock_batches {batch.get('batch_id')} already holds {hit}")
    return problems


# ---------------------------------------------------------------------------
# The per-product helper (exercised by backend/tests/test_variant_of_rule.py)
# ---------------------------------------------------------------------------


def create_variant_of(db, row: Dict[str, Any], current_user: Dict[str, Any], *,
                      product_repo, variant_repo, audit_repo) -> Dict[str, Any]:
    """Create ONE Large product through the door, stamp its Shopify ids on
    its row, assert the result by re-reading. Raises on any drift (the door
    is fail-soft; a lost mirror target must be caught here, not later)."""
    sku, parent, large = row["large_sku"], row["parent"], row["shopify_large_variant"]
    payload, extra = build_payload(row)
    created = pm.create_via_door(
        payload, source="MASTER", actor=current_user["user_id"], actor_name=current_user.get("username"),
        extra_fields=extra, product_repo=product_repo, variant_repo=variant_repo, audit_repo=audit_repo, db=db,
    )
    variant_repo.upsert({"sku": sku, "shopify_variant_id": large["variant_id"],
                         "shopify_inventory_item_id": large["inventory_item_id"]})

    spine = product_repo.find_by_sku(sku)
    assert spine and spine["product_id"] == created["product_id"], f"{sku}: spine not found after create"
    assert spine.get("variant_of") == parent["product_id"], f"{sku}: spine.variant_of {spine.get('variant_of')}"
    assert spine.get("size") == "Large" and str(spine.get("identity_key", "")).endswith("|large"), f"{sku}: size/identity"
    assert spine.get("name") == extra["name"], f"{sku}: name {spine.get('name')!r}"
    twin = db["catalog_products"].find_one({"id": spine["pim_product_id"]})
    assert twin is not None, f"{sku}: child twin missing (sync_status={created.get('sync_status')})"
    link = (twin.get("ecom") or {}).get("variant_of") or {}
    parent_twin_id = parent["pim_product_id"]
    assert link.get("twin_id") == parent_twin_id and link.get("product_id") == parent["product_id"], f"{sku}: twin link {link}"
    assert twin["ecom"].get("locally_modified") is False, f"{sku}: child twin is not clean"
    assert not twin["ecom"].get("shopify_product_id"), f"{sku}: child twin carries a gid"
    assert shopify_push.is_variant_of(twin)
    vrow = variant_repo.get_by_sku(sku)
    assert vrow and vrow.get("parent_product_id") == parent_twin_id, f"{sku}: row link {vrow}"
    assert vrow.get("shopify_variant_id") == large["variant_id"], f"{sku}: row variant gid"
    assert vrow.get("shopify_inventory_item_id") == large["inventory_item_id"], f"{sku}: row inventory gid"
    assert vrow.get("mrp") == payload["mrp"] and vrow.get("option_size") == "Large", f"{sku}: row mrp/size"
    parent_twin = db["catalog_products"].find_one({"id": parent_twin_id})
    prow = variants_for_product(db, parent_twin)
    assert sorted(v["sku"] for v in prow) == sorted([parent["sku"], sku]), f"{sku}: parent row set {[v['sku'] for v in prow]}"
    assert shopify_push.product_skus(parent_twin, prow) == [parent["sku"], sku]
    assert shopify_push._needs_repair(parent_twin, prow) is False, f"{sku}: parent still needs repair"
    assert not (parent_twin.get("ecom") or {}).get("locally_modified"), f"{sku}: parent got queued"
    return {"sku": sku, "product_id": spine["product_id"], "twin_id": spine["pim_product_id"],
            "variant_row_id": vrow.get("variant_id"), "parent_product_id": parent["product_id"],
            "parent_twin_id": parent_twin_id, "sync_status": created.get("sync_status")}


# ---------------------------------------------------------------------------
# Opening stock through the door
# ---------------------------------------------------------------------------


def through_door(coro):
    try:
        return asyncio.run(coro)
    except HTTPException as exc:
        sys.exit(f"door refused ({exc.status_code}): {exc.detail}")


def opening_stock(rows: List[Dict[str, Any]], current_user: Dict[str, Any], apply: bool) -> Dict[str, Any]:
    """The UI's opening-stock door: preview (must be all WILL_ADD), then commit."""
    from api.routers.inventory import opening_stock as door

    current_user = through_door(door.require_roles(*door._INVENTORY_ROLES)(current_user))
    payload = door.OpeningStockImport(
        rows=[door.OpeningStockRow(sku=r["large_sku"], quantity=int(r["opening_stock"]["qty"])) for r in rows],
        skip_if_existing=True,
    )
    units = sum(int(r["opening_stock"]["qty"]) for r in rows)
    preview = through_door(door.opening_stock_preview(payload, current_user))
    got = {k: preview["summary"].get(k) for k in ("total_rows", "units_to_add", "rows_to_skip", "rows_with_errors")}
    want = {"total_rows": len(rows), "units_to_add": units, "rows_to_skip": 0, "rows_with_errors": 0}
    print("PREVIEW", json.dumps(got))
    for r in preview["rows"]:
        print(f"   {r['status']:<10} {r.get('sku') or r.get('identifier')}  {r.get('message', '')}")
    if got != want:
        print(f"PLAN DRIFT: preview {json.dumps(got)} != {json.dumps(want)} -- opening stock NOT committed")
        sys.exit(EXIT_DRIFT)
    if not apply:
        return {"preview": preview}
    result = through_door(door.opening_stock_commit(payload, current_user))
    s = result["summary"]
    print("COMMIT ", json.dumps(s))
    if s.get("units_added") != units or s.get("rows_skipped") or s.get("rows_with_errors") or not s.get("batch_id"):
        print("COMMIT SHORT -- DO NOT re-run blindly (skip_if_existing would skip every touched row)")
        sys.exit(EXIT_SHORT)
    return {"preview": preview, "commit": result}


def reversal_list(db, skus: List[str], batch_id=None) -> Dict[str, Any]:
    """Every doc this run minted, in the order to delete them (only while
    every unit is still AVAILABLE; never Shopify). Found by the INPUT skus,
    never by what create_variant_of returned: a row that raised after the
    door wrote (or before opening stock reported its batch) is listed too."""
    out = {"note": "delete in this order, only while every unit is AVAILABLE; audit rows stay; NEVER touch Shopify",
           "stock_units": [], "opening_stock_batches": [batch_id] if batch_id else [],
           "catalog_variants_sku": [], "catalog_products_id": [], "products_product_id": []}
    for sku in skus:
        if db["catalog_variants"].find_one({"sku": sku}) is not None:
            out["catalog_variants_sku"].append(sku)
        twin = db["catalog_products"].find_one({"sku": sku}, {"_id": 0, "id": 1})
        if twin is not None:
            out["catalog_products_id"].append(twin.get("id"))
        spine = db["products"].find_one({"sku": sku}, {"_id": 0, "product_id": 1})
        if spine is None:
            continue
        out["products_product_id"].append(spine["product_id"])
        for u in db["stock_units"].find({"product_id": spine["product_id"], "source": "OPENING_STOCK"},
                                        {"_id": 0, "stock_id": 1, "barcode": 1, "status": 1}):
            out["stock_units"].append({"product_id": spine["product_id"], **u})
    if not batch_id:
        for batch in db["opening_stock_batches"].find({}, {"_id": 0, "batch_id": 1, "lines": 1}):
            if any(ln.get("sku") in skus for ln in (batch.get("lines") or [])):
                out["opening_stock_batches"].append(batch.get("batch_id"))
    return out


def apply_run(db, rows: List[Dict[str, Any]], current_user: Dict[str, Any], *,
              product_repo, variant_repo, audit_repo, record: Dict[str, Any]) -> Dict[str, Any]:
    """The writes, in order, into `record` (the caller persists it in a
    finally): every create, then opening stock, then the reversal list. ANY
    failure (assert, door 4xx/5xx, a Mongo error, a sys.exit) records the
    error + the reversal list built from the input skus, then re-raises."""
    created: List[Dict[str, Any]] = []
    record["created"] = created
    skus = [r["large_sku"] for r in rows]
    try:
        for r in rows:
            created.append(create_variant_of(db, r, current_user, product_repo=product_repo,
                                             variant_repo=variant_repo, audit_repo=audit_repo))
            print(f"CREATED {r['large_sku']} product_id={created[-1]['product_id']} twin={created[-1]['twin_id']}")
        stock = opening_stock(rows, current_user, apply=True)
        record["opening_stock"] = stock
        record["reversal"] = reversal_list(db, skus, stock["commit"]["summary"].get("batch_id"))
    except (Exception, SystemExit) as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["reversal"] = reversal_list(db, skus)
        print(f"STOPPED after {len(created)} product(s) returned: {record['error']}")
        print("REVERSAL LIST (partial run -- the sku fence will refuse a re-run until these are gone):")
        print(json.dumps(record["reversal"], indent=1, default=str))
        raise
    return record


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--input", default=DEFAULT_INPUT, help="large_11.json (must hash to INPUT_SHA256)")
    ap.add_argument("--apply", action="store_true", help="WRITE (default: read-only dry-run)")
    ap.add_argument("--actor", help="username or user_id recorded as the actor (required with --apply)")
    ap.add_argument("--out", help="write the run record here (default with --apply: next to --input)")
    ap.add_argument("--owner-ack", action="store_true",
                    help="the OWNER POINTS were put to the owner and answered (required with --apply)")
    a = ap.parse_args(argv)
    check_owner_points(a.apply, a.owner_ack)
    if a.apply and not a.out:
        a.out = os.path.join(os.path.dirname(a.input), f"apply_{utc_naive():%Y%m%dT%H%M%SZ}.json")

    rows = load_input(a.input)
    check_clock()
    db = connect_prod()
    check_store(db)
    current_user = resolve_actor(db, a.actor, a.apply)
    product_repo = ProductRepository(db["products"])
    variant_repo = CatalogVariantRepository(db["catalog_variants"])
    audit_repo = AuditRepository(db["audit_logs"])
    print(f"ACTOR  user_id={current_user['user_id']} roles={current_user['roles']}  MODE {'APPLY' if a.apply else 'DRY-RUN'}")

    problems = row_fences(db, rows, product_repo)
    if problems:
        print("REFUSED:")
        for p in problems:
            print("  -", p)
        sys.exit(EXIT_FENCE)
    print("FENCES OK")
    if a.apply and in_sync_window(datetime.now(timezone.utc)):
        sys.exit("inside a live-sync window (00:30-01:30 / 08:30-09:30 IST) -- run outside it. Nothing done.")

    for r in rows:
        pv = r.pop("_preview")
        print(f"PLAN   {r['large_sku']}\n"
              f"       spine: name={pv.get('name')!r} size={pv.get('size')} identity_key={pv.get('identity_key')} "
              f"mrp={pv.get('mrp')} hsn={pv.get('hsn_code')} gst={pv.get('gst_rate')} variant_of={r['parent']['product_id']}\n"
              f"       twin:  ecom.variant_of.twin_id={r['parent']['pim_product_id']} clean, no gid\n"
              f"       row:   parent_product_id={r['parent']['pim_product_id']} option_size=Large mrp={pv.get('mrp')} "
              f"-> variant {r['shopify_large_variant']['variant_id']} / {r['shopify_large_variant']['inventory_item_id']}\n"
              f"       stock: +{r['opening_stock']['qty']} at BV-PUN-01")
    if not a.apply:
        print("DRY-RUN OK -- nothing written. Re-run with --apply --actor <username> to create.")
        return 0

    record: Dict[str, Any] = {"actor": current_user["user_id"], "started_at_utc": utc_naive().isoformat(),
                              "input_sha256": INPUT_SHA256}
    try:
        apply_run(db, rows, current_user, product_repo=product_repo, variant_repo=variant_repo,
                  audit_repo=audit_repo, record=record)
    finally:
        record["finished_at_utc"] = utc_naive().isoformat()
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=1, default=str, ensure_ascii=False)
        print("WROTE", a.out)
    summary = record["opening_stock"]["commit"]["summary"]
    print("REVERSAL LIST:")
    print(json.dumps(record["reversal"], indent=1, default=str))
    print(f"APPLY OK  {len(record['created'])} products, {summary['units_added']} units, "
          f"batch {summary['batch_id']}. Parents left CLEAN; nothing pushed to Shopify.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
