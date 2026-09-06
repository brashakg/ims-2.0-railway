"""Shopify push -- tags

The tag pass (sync audit gap #4, owner 2026-09-06): IMS manages ONLY the
tags it wrote, so a tag a human added in the Shopify admin survives a push.

OWNERSHIP. ``ecom.shopify_tags_sent`` on the twin is the exact list IMS last
put on the Shopify product (written by writeback._writeback_product after a
clean pass). On an UPDATE the product write carries NO ``tags`` field at all
(productUpdate would REPLACE the whole list -- the wipe); instead the pass
diffs what IMS wants now (product_input.ims_product_tags) against that
ledger: ``tagsAdd`` what is new, ``tagsRemove`` what IMS dropped, and only
that. Tags on Shopify that IMS never sent are ``unmanaged`` and never
touched. A product that went live before the ledger existed (the 36
connector-uploaded Ray-Ban Meta products, the six IMS pushed) is ADOPTED on
its first pass: every tag already there is treated as unmanaged, IMS's tags
are added, nothing is removed, and the ledger is recorded. The CREATE keeps
sending the full list on the input (nothing to diff against yet).

ORDER is add -> remove, and a failed step stops the pass without writing the
ledger, so the next press diffs against the truth: what was added is already
on Shopify (a re-add is skipped), what failed to come off is retried. Every
Shopify tag comparison is lower-cased (Shopify matches tags case-
insensitively; ims_product_tags lower-cases what IMS sends).

Fail-soft side channel: a tag error is reported on the result with a stable
``code`` (TAGS_NOT_SYNCED) and a plain ``error`` line, never flips the push's
ok and never withholds the publish -- a listing with a stale filter tag beats
an invisible one. No emoji (Windows cp1252).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .queries import _TAGS_ADD, _TAGS_REMOVE
from .transport import _graphql, _user_errors
from .writeback import _writeback_product

TAGS_SENT_FIELD = "shopify_tags_sent"  # under ecom.*
TAGS_CODE = "TAGS_NOT_SYNCED"


def sent_tags(product: Dict[str, Any]) -> Optional[List[str]]:
    """The ownership ledger ecom.shopify_tags_sent, lower-cased; None when the
    product has never been through the pass (adoption due). Pure."""
    rows = (product.get("ecom") or {}).get(TAGS_SENT_FIELD)
    if not isinstance(rows, list):
        return None
    return _norm(rows)


def _norm(tags: Optional[List[Any]]) -> List[str]:
    out: List[str] = []
    for t in tags or []:
        token = str(t or "").strip().lower()
        if token and token not in out:
            out.append(token)
    return out


def plan_product_tags(
    product: Dict[str, Any],
    ims_tags: List[str],
    shopify_tags: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """PURE diff of the tags IMS wants against the tags IMS owns. ``shopify_tags``
    is the product's current Shopify tag list (off the create/update
    response); None means UNKNOWN (the dark plan), in which case the ledger
    alone decides and nothing is filtered by what is already there.

    Returns {add: [tag], remove: [tag], unmanaged: n | None (unknown),
    adopt: bool (no ledger yet -- nothing is removed), create: bool (the full
    list rides the productCreate input; nothing to diff)}."""
    ims = _norm(ims_tags)
    current = None if shopify_tags is None else _norm(shopify_tags)
    create = not (product.get("ecom") or {}).get("shopify_product_id")
    sent = sent_tags(product)
    if create:
        return {"add": ims, "remove": [], "unmanaged": 0, "adopt": False, "create": True}
    adopt = sent is None
    owned = [] if adopt else sent
    add = [t for t in ims if t not in owned and (current is None or t not in current)]
    remove = [t for t in owned if t not in ims and (current is None or t in current)]
    # Unmanaged = on Shopify, not wanted by IMS, and NOT IMS's to remove.
    unmanaged = (
        None
        if current is None
        else len([t for t in current if t not in ims and t not in owned])
    )
    return {"add": add, "remove": remove, "unmanaged": unmanaged, "adopt": adopt, "create": False}


async def sync_product_tags(
    db,
    product: Dict[str, Any],
    product_gid: str,
    ims_tags: List[str],
    shopify_tags: Optional[List[Any]],
) -> Dict[str, Any]:
    """LIVE-only (the caller has passed the gates and written the product):
    make the IMS-owned tags on the Shopify product match ``ims_tags`` per the
    ownership rule above, then record the ledger. On a CREATE the input
    already carried the list, so only the ledger is written.
    Fail-soft summary, never raises: {added, removed, unmanaged, adopted,
    error?, code?}."""
    pid = product.get("id") or product.get("product_id")
    plan = plan_product_tags(product, ims_tags, shopify_tags)
    summary: Dict[str, Any] = {
        "added": len(plan["add"]) if plan["create"] else 0,
        "removed": 0,
        "unmanaged": plan["unmanaged"],
        "adopted": plan["adopt"],
    }
    if not plan["create"]:
        for key, counter, mutation, field in (
            ("add", "added", _TAGS_ADD, "tagsAdd"),
            ("remove", "removed", _TAGS_REMOVE, "tagsRemove"),
        ):
            if not plan[key]:
                continue
            try:
                body = await _graphql(db, mutation, {"id": product_gid, "tags": plan[key]})
                err = _user_errors(body, field)
            except Exception as exc:  # noqa: BLE001 -- fail-soft side channel
                err = str(exc)
            if err:
                summary["code"] = TAGS_CODE
                summary["error"] = (
                    "tags not synced (%s): %s -- the listing keeps its current "
                    "tags; press again" % (field, err)
                )
                return summary
            summary[counter] = len(plan[key])
    if pid:
        _writeback_product(db, pid, product_gid, tags_sent=_norm(ims_tags))
    return summary
