"""Shopify push -- the durable first-publish claim (audit SF-02).

productCreate is not idempotent. If Shopify makes the product but its answer
is lost (a read timeout, a 5xx, a dropped connection) -- or IMS cannot save
the new id -- a blind second create makes a second listing. So a first
publish works like this:

  1. CLAIM. Before productCreate the row gets ecom.create_claim
     {token, at, state: SENDING} by compare-and-set; a second press (or a
     second worker) that finds a claim never sends a create of its own.
  2. IDENTITY. The create carries the metafield ims.ims_product_id = the IMS
     product id, so a listing can be proved to be THIS product's.
  3. RESOLVE BEFORE REPEAT. A create whose answer was lost marks the claim
     UNCERTAIN and is NEVER re-sent (transport raises ShopifyOutcomeUnknown).
     The next press asks Shopify first: products created since the claim
     carrying this product's identity -> ADOPT that listing (store its id,
     carry on as an update). Not found, and the claim is older than SETTLE
     (long enough for Shopify's search to show a product it made) -> only
     then is a create sent again.
  4. A created product whose id IMS could not save keeps its claim: the next
     press finds it the same way and adopts it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from agents.nexus_providers import _as_shopify_gid

from ..catalog_ecom import update_ecom
from .transport import _graphql, _now

CLAIM = "create_claim"
IDENTITY_KEY = "ims_product_id"  # metafield ims.ims_product_id
SETTLE = timedelta(minutes=15)

CREATE_UNCERTAIN = "CREATE_UNCERTAIN"
CREATE_IN_PROGRESS = "CREATE_IN_PROGRESS"
CREATE_ID_NOT_SAVED = "CREATE_ID_NOT_SAVED"

_FIND_CREATED = """
query imsFindCreated($q: String!) {
  products(first: 50, query: $q, sortKey: CREATED_AT, reverse: true) {
    nodes { id identity: metafield(namespace: "ims", key: "ims_product_id") { value } }
  }
}
"""


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
    repeat and settles on its own after SETTLE."""
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


async def find_created(db, pid: str, claim: Dict[str, Any]) -> List[str]:
    """Shopify products created since the claim that carry this product's
    identity metafield, OLDEST first. Raises on a failed read (the caller
    then cannot tell, and must not create)."""
    since = (_utc(claim.get("at")) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = await _graphql(db, _FIND_CREATED, {"q": f"created_at:>='{since}'"})
    if body.get("errors"):
        raise ValueError(f"graphql errors: {str(body['errors'])[:200]}")
    nodes = (((body.get("data") or {}).get("products") or {}).get("nodes")) or []
    hits = [
        _as_shopify_gid(n.get("id"), "Product")
        for n in nodes
        if isinstance(n, dict) and n.get("id")
        and str(((n.get("identity") or {}).get("value")) or "") == str(pid)
    ]
    return list(reversed(hits))


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


async def resolve_held(db, pid: str, held: Dict[str, Any]) -> Tuple[str, Any]:
    """A claim already stands. Returns
      ("adopted", (gid, copies))   -- the listing exists on Shopify; use it
      ("send", claim)              -- settled and not on Shopify: create
      ("refuse", (code, message))  -- in flight, or cannot tell yet."""
    if held.get("state") == "SENDING" and _age(held) < SETTLE:
        return "refuse", (
            CREATE_IN_PROGRESS,
            "another press is creating this product on Shopify right now -- "
            "nothing was sent; wait a few minutes and press again",
        )
    try:
        found = await find_created(db, pid, held)
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
    if _age(held) >= SETTLE:
        mine = take_over(db, pid, held)
        if mine is not None:
            return "send", mine
        return "refuse", (CREATE_IN_PROGRESS, "another press took this product over -- press again")
    return "refuse", (
        CREATE_UNCERTAIN,
        "an earlier send of this product did not get Shopify's answer, so IMS "
        "cannot tell yet whether it was created. Nothing was sent again; press "
        "again in about 15 minutes and IMS will look for it on Shopify first",
    )
