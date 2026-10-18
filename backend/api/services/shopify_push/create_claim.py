"""Shopify push -- the durable first-publish claim (audit SF-02).

productCreate is not idempotent. If Shopify makes the product but its answer
is lost (a read timeout, a 5xx, a dropped connection) -- or IMS cannot save
the new id -- a blind second create makes a second listing. So a first
publish works like this:

  1. CLAIM. Before productCreate the row gets ecom.create_claim
     {token, at, state: SENDING} by compare-and-set; a second press (or a
     second worker) that finds a claim never sends a create of its own.
  2. IDENTITY. The create carries the metafield ims.ims_product_id = the IMS
     product id. Its metafield DEFINITION has Shopify's uniqueValues
     capability (ensure_identity_definition, run before the first create):
     Shopify itself then refuses a second product with the same id, and
     productByIdentifier(customId) reads it back DIRECTLY -- no search lag.
  3. RESOLVE BEFORE REPEAT. A create whose answer was lost marks the claim
     UNCERTAIN and is NEVER re-sent (transport raises ShopifyOutcomeUnknown).
     A later press asks Shopify first and ADOPTS the listing it finds (stores
     its id, carries on as an update). The direct read is the answer when the
     definition is in place; otherwise (or if that read fails) the products
     created around the claim are searched, OLDEST first, every page.
     Not found: with the direct read, a create goes out once the claim is past
     IN_FLIGHT; with only the search, once it is past SETTLE (long enough for
     Shopify's search to show a product it made). A SENDING claim younger
     than IN_FLIGHT is another press mid-create: refused, nothing looked up.
  4. A created product whose id IMS could not save keeps its claim: the next
     press finds it the same way and adopts it.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from agents.nexus_providers import _as_shopify_gid

from ..catalog_ecom import update_ecom
from .transport import _graphql, _now

logger = logging.getLogger(__name__)

CLAIM = "create_claim"
IDENTITY_KEY = "ims_product_id"  # metafield ims.ims_product_id
# The longest a create call can take (connect retries + backoff included):
# a SENDING claim younger than this is another press mid-create.
IN_FLIGHT = timedelta(minutes=5)
# Long enough for Shopify's product SEARCH to show a product it made.
SETTLE = timedelta(minutes=15)

CREATE_UNCERTAIN = "CREATE_UNCERTAIN"
CREATE_IN_PROGRESS = "CREATE_IN_PROGRESS"
CREATE_ID_NOT_SAVED = "CREATE_ID_NOT_SAVED"

# The fallback search: every product created in the claim's window, OLDEST
# first, 250 a page, every page (a 450-product publish puts hundreds in it).
_FIND_CREATED = """
query imsFindCreated($q: String!, $after: String) {
  products(first: 250, after: $after, query: $q, sortKey: CREATED_AT) {
    nodes { id identity: metafield(namespace: "ims", key: "ims_product_id") { value } }
    pageInfo { hasNextPage endCursor }
  }
}
"""
_WINDOW_BEFORE = timedelta(minutes=5)
_WINDOW_AFTER = timedelta(minutes=10)
_MAX_PAGES = 40

# The direct read (needs the uniqueValues definition).
_BY_IDENTITY = """
query imsProductByIdentity($value: String!) {
  productByIdentifier(identifier: {customId: {namespace: "ims", key: "ims_product_id", value: $value}}) {
    id
  }
}
"""

_IDENTITY_DEFINITION = """
query imsIdentityDefinition {
  metafieldDefinitions(first: 1, ownerType: PRODUCT, namespace: "ims", key: "ims_product_id") {
    nodes { id metafieldsCount capabilities { uniqueValues { enabled } } }
  }
}
"""
_IDENTITY_DEFINITION_CREATE = """
mutation imsIdentityDefinitionCreate($definition: MetafieldDefinitionInput!) {
  metafieldDefinitionCreate(definition: $definition) {
    createdDefinition { id capabilities { uniqueValues { enabled } } }
    userErrors { field message code }
  }
}
"""
_IDENTITY_DEFINITION_UPDATE = """
mutation imsIdentityDefinitionUpdate($definition: MetafieldDefinitionUpdateInput!) {
  metafieldDefinitionUpdate(definition: $definition) {
    updatedDefinition { id capabilities { uniqueValues { enabled } } }
    userErrors { field message code }
  }
}
"""
_IDENTITY_VALUES = """
query imsIdentityValues($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on Product { id title identity: metafield(namespace: "ims", key: "ims_product_id") { value } }
  }
}
"""

# Per process: once the definition is confirmed it stays confirmed; a failed
# check is retried at most every _ENSURE_RETRY so a 450-product publish does
# not pay for it 450 times.
_identity: Dict[str, Any] = {"ready": False, "retry_at": None}
_ENSURE_RETRY = timedelta(minutes=10)


def identity_metafield(pid: str) -> Dict[str, Any]:
    return {
        "namespace": "ims",
        "key": IDENTITY_KEY,
        "type": "single_line_text_field",
        "value": str(pid),
    }


def _utc(at: Any) -> datetime:
    if isinstance(at, str):
        at = datetime.fromisoformat(at)
    if not isinstance(at, datetime):
        return datetime.min.replace(tzinfo=timezone.utc)
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _age(claim: Dict[str, Any]) -> timedelta:
    return _now() - _utc(claim.get("at"))


def take_claim(db, pid: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """("send", claim) -- this press may create (claim None: no stored row to
    protect, the legacy path); ("held", claim) -- another claim stands;
    ("has_id", None) -- the row got its Shopify id meanwhile."""
    seen: Dict[str, Any] = {}
    mine = {"token": uuid.uuid4().hex, "at": _now(), "state": "SENDING"}

    def _take(ecom: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        seen.clear()
        if ecom.get("shopify_product_id"):
            seen["has_id"] = True
            return None
        if isinstance(ecom.get(CLAIM), dict):
            seen["held"] = ecom[CLAIM]
            return None
        return {**ecom, CLAIM: mine}

    if update_ecom(db, pid, _take) is not None:
        return "send", mine
    if seen.get("has_id"):
        return "has_id", None
    if seen.get("held"):
        return "held", seen["held"]
    if db["catalog_products"].find_one({"id": pid}) is None:
        return "send", None  # no stored row: nothing to claim
    # The row kept changing under the claim write: treat it as in flight.
    return "held", {"state": "SENDING", "at": _now()}


def take_over(db, pid: str, held: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Replace a settled claim with this press's own -- only while it is still
    exactly `held` (one winner when two presses race)."""
    mine = {"token": uuid.uuid4().hex, "at": _now(), "state": "SENDING"}

    def _swap(ecom: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if ecom.get("shopify_product_id") or ecom.get(CLAIM) != held:
            return None
        return {**ecom, CLAIM: mine}

    return mine if update_ecom(db, pid, _swap) is not None else None


def settle_claim(db, pid: str, claim: Optional[Dict[str, Any]], *, uncertain: bool) -> None:
    """The create's outcome is known-failed (drop the claim) or unknown (mark
    it UNCERTAIN so no press repeats it before looking). Only this press's own
    claim is touched. Fail-soft: a claim left SENDING still blocks a blind
    repeat and is looked up once it is past IN_FLIGHT."""
    if not claim:
        return

    def _settle(ecom: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        held = ecom.get(CLAIM)
        if not isinstance(held, dict) or held.get("token") != claim["token"]:
            return None
        if uncertain:
            ecom[CLAIM] = {**held, "state": "UNCERTAIN"}
        else:
            ecom.pop(CLAIM, None)
        return ecom

    try:
        update_ecom(db, pid, _settle)
    except Exception:  # noqa: BLE001 -- see docstring
        pass


# ---------------------------------------------------------------------------
# The identity definition (uniqueValues) -- ensured before any first create
# ---------------------------------------------------------------------------


def _errors(body: Dict[str, Any]) -> None:
    if body.get("errors"):
        raise ValueError(f"graphql errors: {str(body['errors'])[:200]}")


def _user_error_text(field_obj: Dict[str, Any]) -> str:
    return "; ".join(str(e.get("message")) for e in (field_obj or {}).get("userErrors") or [])


def _unique_on(node: Optional[Dict[str, Any]]) -> bool:
    caps = (node or {}).get("capabilities") or {}
    return bool((caps.get("uniqueValues") or {}).get("enabled"))


async def _identity_holders(db) -> Dict[str, List[str]]:
    """{ims_product_id value: [Shopify product titles]} over every product
    IMS holds a Shopify id for -- the values already out there."""
    gids = sorted({
        _as_shopify_gid((d.get("ecom") or {}).get("shopify_product_id"), "Product")
        for d in db["catalog_products"].find({}, {"_id": 0, "id": 1, "ecom.shopify_product_id": 1})
        if (d.get("ecom") or {}).get("shopify_product_id")
    })
    holders: Dict[str, List[str]] = {}
    for i in range(0, len(gids), 100):
        body = await _graphql(db, _IDENTITY_VALUES, {"ids": gids[i : i + 100]})
        _errors(body)
        for n in (body.get("data") or {}).get("nodes") or []:
            value = ((n or {}).get("identity") or {}).get("value")
            if value:
                holders.setdefault(str(value), []).append(str(n.get("title") or n.get("id")))
    return holders


async def ensure_identity_definition(db, *, apply: bool = True) -> Dict[str, Any]:
    """Make sure the PRODUCT metafield definition ims.ims_product_id exists
    with the uniqueValues capability. Idempotent: reads first, writes only
    what is missing. `apply=False` reports what it WOULD do and writes
    nothing. Returns {ready, action, message}. Raises on a failed read.

      exists              definition there, unique on        -> nothing to do
      would_enable /      definition there, unique off       -> metafieldDefinitionUpdate
        enabled
      would_create /      no definition: first reads the ims_product_id value
        created             of every product IMS has on Shopify -- any value
                            held by two products blocks it -- then
                            metafieldDefinitionCreate
      blocked_duplicates  two Shopify products carry one IMS id: a human
                          deletes the extra copy first
      *_refused           Shopify's userErrors, word for word
    """
    if _identity["ready"]:
        return {"ready": True, "action": "exists", "message": "already confirmed"}
    body = await _graphql(db, _IDENTITY_DEFINITION, {})
    _errors(body)
    nodes = (((body.get("data") or {}).get("metafieldDefinitions") or {}).get("nodes")) or []
    if nodes and _unique_on(nodes[0]):
        _identity["ready"] = True
        return {"ready": True, "action": "exists", "message": "definition in place, unique values on"}
    if nodes:
        count = nodes[0].get("metafieldsCount")
        if not apply:
            return {
                "ready": False,
                "action": "would_enable",
                "message": f"definition exists without unique values ({count} values) -- "
                "would switch unique values on",
            }
        body = await _graphql(db, _IDENTITY_DEFINITION_UPDATE, {"definition": {
            "namespace": "ims",
            "key": IDENTITY_KEY,
            "ownerType": "PRODUCT",
            "capabilities": {"uniqueValues": {"enabled": True}},
        }})
        _errors(body)
        out = (body.get("data") or {}).get("metafieldDefinitionUpdate") or {}
        if _unique_on(out.get("updatedDefinition")):
            _identity["ready"] = True
            return {"ready": True, "action": "enabled", "message": "unique values switched on"}
        return {
            "ready": False,
            "action": "enable_refused",
            "message": _user_error_text(out) or "Shopify did not switch unique values on",
        }
    holders = await _identity_holders(db)
    dupes = {v: t for v, t in holders.items() if len(t) > 1}
    if dupes:
        return {
            "ready": False,
            "action": "blocked_duplicates",
            "duplicates": dupes,
            "message": "these IMS products are on Shopify more than once -- delete the extra "
            "copies in Shopify admin first: "
            + "; ".join(f"{v}: {', '.join(t)}" for v, t in sorted(dupes.items())),
        }
    if not apply:
        return {
            "ready": False,
            "action": "would_create",
            "message": f"no definition yet; {len(holders)} live products already carry a "
            "value (all different) -- would create it with unique values",
        }
    body = await _graphql(db, _IDENTITY_DEFINITION_CREATE, {"definition": {
        "name": "IMS product id",
        "description": "The IMS catalogue id of this product. Set by IMS; do not edit.",
        "namespace": "ims",
        "key": IDENTITY_KEY,
        "type": "single_line_text_field",
        "ownerType": "PRODUCT",
        "capabilities": {"uniqueValues": {"enabled": True}},
    }})
    _errors(body)
    out = (body.get("data") or {}).get("metafieldDefinitionCreate") or {}
    if _unique_on(out.get("createdDefinition")):
        _identity["ready"] = True
        return {"ready": True, "action": "created", "message": "definition created with unique values"}
    return {
        "ready": False,
        "action": "create_refused",
        "message": _user_error_text(out) or "Shopify did not create the definition",
    }


async def prepare_identity(db) -> None:
    """The push's call before any first create: ensure the definition, at
    most once per _ENSURE_RETRY while it is not in place. Never raises -- a
    first publish still goes out without it (the claim and the window search
    still guard it) and the reason is logged."""
    if _identity["ready"]:
        return
    retry_at = _identity.get("retry_at")
    if retry_at and _now() < retry_at:
        return
    try:
        result = await ensure_identity_definition(db, apply=True)
    except Exception as exc:  # noqa: BLE001 -- see docstring
        result = {"ready": False, "message": str(exc)}
    if not result.get("ready"):
        _identity["retry_at"] = _now() + _ENSURE_RETRY
        logger.warning("[SHOPIFY_PUSH] identity definition not in place: %s", result.get("message"))


# ---------------------------------------------------------------------------
# Looking on Shopify before any repeat
# ---------------------------------------------------------------------------


async def _search_window(db, pid: str, claim: Dict[str, Any]) -> List[str]:
    at = _utc(claim.get("at"))
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    q = (
        f"created_at:>='{(at - _WINDOW_BEFORE).strftime(fmt)}' "
        f"AND created_at:<='{(at + _WINDOW_AFTER).strftime(fmt)}'"
    )
    hits: List[str] = []
    after: Optional[str] = None
    for _ in range(_MAX_PAGES):
        body = await _graphql(db, _FIND_CREATED, {"q": q, "after": after})
        _errors(body)
        page = (body.get("data") or {}).get("products") or {}
        hits += [
            _as_shopify_gid(n.get("id"), "Product")
            for n in page.get("nodes") or []
            if isinstance(n, dict) and n.get("id")
            and str(((n.get("identity") or {}).get("value")) or "") == str(pid)
        ]
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return hits
        after = info.get("endCursor")
        if not after:
            raise ValueError("a search page said more follow but gave no cursor")
    raise ValueError("more products were created around this send than IMS reads -- cannot tell")


async def find_created(db, pid: str, claim: Dict[str, Any]) -> Tuple[List[str], bool]:
    """(Shopify products carrying this product's identity, OLDEST first;
    definitive). Definitive = the direct read answered (the unique
    definition is in place), so "none" really means none; otherwise the
    window search, whose "none" may only be search lag. Raises when it
    cannot look at all (the caller must then not create)."""
    if _identity["ready"]:
        try:
            body = await _graphql(db, _BY_IDENTITY, {"value": str(pid)})
            _errors(body)
            node = (body.get("data") or {}).get("productByIdentifier")
            found = [_as_shopify_gid(node["id"], "Product")] if node and node.get("id") else []
            return found, True
        except Exception as exc:  # noqa: BLE001 -- fall back to the search
            logger.warning("[SHOPIFY_PUSH] identity read failed for %s, searching: %s", pid, exc)
    return await _search_window(db, pid, claim), False


def adopt(db, pid: str, gid: str) -> bool:
    """Store the found listing's id and drop the claim -- unless the row got
    an id meanwhile. The dirty flag is left alone: the adopting press goes on
    as an update and clears it only for what it sends."""

    def _adopt(ecom: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if ecom.get("shopify_product_id"):
            return None
        ecom["shopify_product_id"] = gid
        ecom.pop(CLAIM, None)
        return ecom

    return update_ecom(db, pid, _adopt) is not None


async def adopt_if_on_shopify(db, pid: str) -> bool:
    """After Shopify REFUSED a create: is this product already there? With
    the unique definition in place a second create of a listed product is
    refused (the IMS id is taken) -- the direct read finds that listing and
    links it, so the press carries on as an update instead of failing on
    every press. Only the direct read decides (definitive); never raises."""
    if not _identity["ready"]:
        return False
    try:
        found, definitive = await find_created(db, pid, {"at": _now()})
    except Exception:  # noqa: BLE001 -- the refusal stands
        return False
    return bool(found and definitive and adopt(db, pid, found[0]))


async def resolve_held(db, pid: str, held: Dict[str, Any]) -> Tuple[str, Any]:
    """A claim already stands. Returns
      ("adopted", (gid, copies))   -- the listing exists on Shopify; use it
      ("send", claim)              -- settled and not on Shopify: create
      ("refuse", (code, message))  -- in flight, or cannot tell yet."""
    age = _age(held)
    if held.get("state") == "SENDING" and age < IN_FLIGHT:
        return "refuse", (
            CREATE_IN_PROGRESS,
            "another press is creating this product on Shopify right now -- "
            "nothing was sent; wait a few minutes and press again",
        )
    try:
        found, definitive = await find_created(db, pid, held)
    except Exception as exc:  # noqa: BLE001 -- cannot look -> cannot create
        return "refuse", (
            CREATE_UNCERTAIN,
            "an earlier send of this product may have created it on Shopify, and "
            f"IMS could not check ({exc}). Nothing was sent; press again later",
        )
    if found:
        if adopt(db, pid, found[0]):
            return "adopted", (found[0], len(found))
        return "refuse", (CREATE_IN_PROGRESS, "the product was linked to Shopify meanwhile -- press again")
    if age >= SETTLE or (definitive and age >= IN_FLIGHT):
        mine = take_over(db, pid, held)
        if mine is not None:
            return "send", mine
        return "refuse", (CREATE_IN_PROGRESS, "another press took this product over -- press again")
    wait = "a few minutes" if definitive else "about 15 minutes"
    return "refuse", (
        CREATE_UNCERTAIN,
        "an earlier send of this product did not get Shopify's answer, so IMS "
        "cannot tell yet whether it was created. Nothing was sent again; press "
        f"again in {wait} and IMS will look for it on Shopify first",
    )
