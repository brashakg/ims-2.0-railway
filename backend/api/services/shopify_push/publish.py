"""Shopify push -- publish

Sales-channel publish -- the door that makes an ACTIVE product
visible: the Online Store publication-id resolver, publishablePublish,
and `push_mode_status_resolved` (the gate read plus that one lookup).
"""

from __future__ import annotations

from typing import Any, Dict, Optional
import os

from agents.nexus_providers import _as_shopify_gid

from ._shared import logger, push_mode_status
from .transport import (
    PUBLISH_SCOPE_MISSING,
    _PUBLISH_SCOPE_MISSING_MSG,
    _graphql,
    _is_access_denied,
    _user_errors,
)
from .queries import (
    _ONLINE_STORE_PUBLICATION_NAME,
    _PUBLICATIONS_QUERY,
    _PUBLISHABLE_PUBLISH,
    _publication_id_cache,
)

async def push_mode_status_resolved(db) -> Dict[str, Any]:
    """push_mode_status PLUS the one `publications` lookup the pre-press tile
    needs in order to be TRUE.

    push_mode_status is a pure read, so the best it can report is the pinned id
    or whatever THIS process happened to cache -- and the backend runs four
    uvicorn workers (backend/Dockerfile). After every deploy all four caches are
    empty, so the tile said "NOT resolved -- presses will publish nothing" about
    a shop whose very next press publishes fine, and then flapped green/red
    depending on which worker answered. A red tile the owner learns to ignore is
    worse than no tile at all: it is the only warning before the door this whole
    change opens.

    So when the gates are LIVE and nothing is known yet, ASK Shopify -- once per
    process, because the answer lands in the same cache the push path reads.
    Fail-soft: an unreachable lookup leaves the honest `unresolved` (and DARK
    never calls out at all -- there are no credentials to call with).
    """
    status = push_mode_status(db)
    if status["is_live"] and status["online_store_publication_source"] == "unresolved":
        gid = await _resolve_online_store_publication_id(db)
        if gid:
            status["online_store_publication_id"] = gid
            status["online_store_publication_source"] = "looked_up"
    return status



# ---------------------------------------------------------------------------
# Sales-channel publish -- the door that makes an ACTIVE product visible
# ---------------------------------------------------------------------------


async def _resolve_online_store_publication_id(db) -> Optional[str]:
    """The Online Store publication gid: the pinned env value when set, else a
    one-time `publications` lookup (cached per process). Fail-soft -> None (the
    publish step then reports why instead of raising)."""
    pinned = (os.getenv("SHOPIFY_ONLINE_STORE_PUBLICATION_ID") or "").strip()
    if pinned:
        return _as_shopify_gid(pinned, "Publication")
    cached = _publication_id_cache.get(_ONLINE_STORE_PUBLICATION_NAME)
    if cached:
        return cached
    try:
        body = await _graphql(db, _PUBLICATIONS_QUERY, {})
    except Exception as e:  # noqa: BLE001 -- fail-soft side channel
        logger.warning("[SHOPIFY_PUSH] publication lookup failed: %s", e)
        return None
    nodes = ((body.get("data") or {}).get("publications") or {}).get("nodes") or []
    for n in nodes:
        if str((n or {}).get("name") or "").strip().lower() == (
            _ONLINE_STORE_PUBLICATION_NAME.lower()
        ):
            gid = n.get("id")
            if gid:
                _publication_id_cache[_ONLINE_STORE_PUBLICATION_NAME] = gid
                return gid
    return None


async def _publish_to_online_store(db, product_gid: str) -> Dict[str, Any]:
    """LIVE-only: publish ONE product to the Online Store sales channel so an
    ACTIVE product is actually visible on the storefront. Fail-SOFT side channel
    (reported, never flips the push's ok). Called on EVERY product push whose
    payload is ACTIVE, create or update -- publishablePublish is idempotent, so
    re-publishing an already-published product is a no-op. The caller withholds
    it unless the price and the photograph are both provably right."""
    pub_id = await _resolve_online_store_publication_id(db)
    if not pub_id:
        return {
            "published": False,
            "error": (
                "Online Store publication id not resolved (set "
                "SHOPIFY_ONLINE_STORE_PUBLICATION_ID or grant read_publications)"
            ),
        }
    try:
        body = await _graphql(
            db,
            _PUBLISHABLE_PUBLISH,
            {"id": product_gid, "input": [{"publicationId": pub_id}]},
        )
    except Exception as e:  # noqa: BLE001 -- fail-soft side channel
        return {"published": False, "publication_id": pub_id, "error": str(e)}
    err = _user_errors(body, "publishablePublish")
    if err:
        out: Dict[str, Any] = {"published": False, "publication_id": pub_id, "error": err}
        if _is_access_denied(body):
            # The product write BEFORE this step succeeded (its gid is already
            # written back), so the next press UPDATES. Say what to fix in
            # plain words; the raw vendor text stays on `error` for the audit.
            out["code"] = PUBLISH_SCOPE_MISSING
            out["message"] = _PUBLISH_SCOPE_MISSING_MSG
        return out
    return {"published": True, "publication_id": pub_id}

