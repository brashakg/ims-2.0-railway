"""
IMS 2.0 - CI source guard: there is NO single online location any more
=====================================================================
Owner ruling 2026-09-06: every physical shop is its own Shopify location
(stores.shopify_location_id, one reader: stores_util.physical_stores). The
#1125 pooled world had FOUR ways to name "the" location -- the
SHOPIFY_ONLINE_LOCATION_ID env pin, the `locations` picker
(pick_online_location / resolve_online_location_id), the storefront-registry
row + its per-process cache (stored_online_location_id /
_online_location_cache) and a per-variant target reader
(online_variant_targets_for_skus). Each was a second rule that could drift
from the store list; all four are deleted. This guard fails CI if any of them
is read again under backend/api or backend/agents.

Also dead, and guarded by name: the pooled writer the #1125 world kept
beside the per-store one (`shopify_set_inventory_available`,
`repush_oversell_risk` / `_shopify_online_location` behind the deleted
POST /online-store/repush-oversell), the second sku -> listing resolver
(`_products_for_skus`; `online_catalog.listings_for_skus` is the one) and
the second baseline writer (`zero_stock_ledger_entry`; push_skus_stock's
write-back is the one).

Pure-Python AST walk (no rg/grep binary needed), the
test_no_legacy_stock_collection.py pattern. ONE allowed read of the env
NAME, listed here so the list can only shrink:
  * shopify_push/__init__.py -- the one-line import-time WARNING that the
    variable is ignored (a warning, never a value that is used).
"""

from __future__ import annotations

import ast
import pathlib

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCAN_DIRS = ("api", "agents")

ENV_PIN = "SHOPIFY_ONLINE_LOCATION_ID"
DEAD_CALLS = {
    "pick_online_location",
    "resolve_online_location_id",
    "stored_online_location_id",
    "online_variant_targets_for_skus",
    "_online_store_name_hints",
    "_persist_location",
    # the pooled writer + its location reader (design 3.6)
    "shopify_set_inventory_available",
    "repush_oversell_risk",
    "_shopify_online_location",
    # second implementations of the listing resolver / baseline writer
    "_products_for_skus",
    "zero_stock_ledger_entry",
}
DEAD_NAMES = {"_online_location_cache"}
# Only shrinks. Relative to backend/.
ENV_PIN_ALLOWED = {
    "api/services/shopify_push/__init__.py",
}


def _offenders_in(path: pathlib.Path, rel: str) -> list:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (UnicodeDecodeError, SyntaxError):
        return []
    hits: list = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in DEAD_CALLS:
                hits.append(f"{rel}:{node.lineno} calls {name}()")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in DEAD_CALLS:
            hits.append(f"{rel}:{node.lineno} defines {node.name}()")
        elif isinstance(node, ast.Name) and node.id in DEAD_NAMES:
            hits.append(f"{rel}:{node.lineno} reads {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in DEAD_NAMES:
            hits.append(f"{rel}:{node.lineno} reads {node.attr}")
        elif (
            isinstance(node, ast.Constant)
            and node.value == ENV_PIN
            and rel not in ENV_PIN_ALLOWED
        ):
            hits.append(f"{rel}:{node.lineno} reads the {ENV_PIN} env pin")
    return hits


def test_no_single_online_location_reader_under_backend():
    """If this fails: a second 'which Shopify location' rule got back in.

    The location is per SHOP on the store record; read it through
    stores_util.physical_stores and write through
    shopify_push.inventory.push_skus_stock -- never a pinned env, a picker,
    a cached registry row or a per-variant location."""
    offenders: list = []
    scanned = 0
    for sub in SCAN_DIRS:
        base = BACKEND_ROOT / sub
        for py in base.rglob("*.py"):
            if "migrations" in py.parts:
                continue
            scanned += 1
            rel = py.relative_to(BACKEND_ROOT).as_posix()
            offenders.extend(_offenders_in(py, rel))
    assert scanned >= 100, (
        f"the scan only walked {scanned} file(s) under {SCAN_DIRS} -- the guard "
        f"is not covering the codebase, so its 'clean' verdict is meaningless"
    )
    assert not offenders, (
        "Single-online-location readers found (per-store locations, owner "
        "ruling 2026-09-06 -- the location lives on the store record):\n"
        + "\n".join(f"  - {o}" for o in offenders)
    )


def test_the_env_pin_allow_list_only_shrinks():
    """Every allowed file must still exist AND still carry the read it is
    excused for -- a stale entry is a hole in the guard."""
    for rel in sorted(ENV_PIN_ALLOWED):
        path = BACKEND_ROOT / rel
        assert path.exists(), rel
        assert ENV_PIN in path.read_text(encoding="utf-8"), (
            f"{rel} no longer reads {ENV_PIN} -- drop it from ENV_PIN_ALLOWED"
        )
