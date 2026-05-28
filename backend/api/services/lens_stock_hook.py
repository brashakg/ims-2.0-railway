"""
IMS 2.0 - Lens Stock Hook (Branch B' sub-PR 4)
==============================================
Glue between order/workshop business events and the atomic
reserve/commit/release endpoints landed in B'1 (lens_stock router).

B'1 exposed atomic CAS endpoints for lens stock movements. NO IMS code
called them yet -- this module bridges that gap by wiring three hooks
into existing flows:

  reserve_for_order_item(...)         -> POS create_order  (BLOCKING)
  commit_for_workshop_dispatch(...)   -> Workshop lens MOUNTED (fail-soft)
  release_for_cancel(...)             -> Order cancel        (fail-soft)

Failure semantics per the spec:
  reserve  -- a 409 (insufficient stock) is BLOCKING. The POS surfaces it
              to the user so they can pick a different cell. Other
              backend failures (mongo blip, transient 500) MUST NOT crash
              order create: we log + record `lens_reserve_failed=True` on
              the returned per-item record; revenue is not blocked.
  commit   -- fail-soft. Workshop staff already moved the physical lens;
              a 409 here is logged + reconciled later.
  release  -- fail-soft. Order cancellation must always succeed; if the
              cell can't be released (e.g. already committed), log + move
              on.

Identity extraction:
  The hook accepts an order_item dict and pulls the lens cell coordinates
  (lens_line_id, sph, cyl, add) from one of:
    - explicit top-level keys on the order_item ("lens_line_id", "sph",
      "cyl", "add") -- the canonical shape once the FE in B'2 sends them
    - the legacy `lens_options` dict on the order_item (back-compat for
      orders that pre-date the Power Grid rebuild)
  If neither path yields a complete cell key, the hook is a no-op
  (returns None). Non-lens items (item_type != "LENS") are also no-ops.

Idempotency:
  reserve uses source_id = f"{order_id}#{line_index}". Before calling
  reserve_cell, the hook checks lens_stock_audit for a prior `reserve`
  row with that source_id -- if present, returns the cached result
  instead of double-reserving. The commit hook applies the same dedup
  with source_id = order_id + "#" + line_index + "#commit".

This module owns NO Mongo schema; it is a thin orchestration layer
over the lens_stock router functions.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Identity extraction
# ---------------------------------------------------------------------------


_LENS_ITEM_TYPES = {"LENS", "OPTICAL_LENS", "CONTACT_LENS_RX"}


def _is_lens_item(order_item: Dict[str, Any]) -> bool:
    """True when this order line is a lens we should attempt to track.

    Falls back to the legacy POS prefix `lens-` on product_id (used by the
    POS lens configurator) when item_type isn't explicitly LENS."""
    it = str(order_item.get("item_type") or "").upper()
    if it in _LENS_ITEM_TYPES:
        return True
    pid = str(order_item.get("product_id") or "")
    if pid.startswith(("lens-", "lens-sug-")):
        return True
    return False


def _extract_cell_key(
    order_item: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Pull (lens_line_id, sph, cyl, add) off an order_item.

    Looks first at explicit top-level keys (canonical, set by the FE in
    B'2), falls back to the legacy `lens_options` dict for back-compat.
    Returns None when lens_line_id is missing OR sph is missing (those two
    are required to address a cell). cyl defaults to 0.0 and add to None
    (single-vision) when omitted -- the lens_stock router validates that
    the add-nullness matches the parent line's has_add.
    """
    lens_line_id = order_item.get("lens_line_id")
    sph = order_item.get("sph")
    cyl = order_item.get("cyl")
    add = order_item.get("add")

    if lens_line_id is None or sph is None:
        opts = order_item.get("lens_options")
        if isinstance(opts, dict):
            lens_line_id = lens_line_id or opts.get("lens_line_id")
            if sph is None:
                sph = opts.get("sph")
            if cyl is None:
                cyl = opts.get("cyl")
            if add is None:
                add = opts.get("add")

    if not lens_line_id or sph is None:
        return None

    try:
        sph_f = float(sph)
    except (TypeError, ValueError):
        logger.warning(
            "[LENS_HOOK] sph %r not numeric for line %s; skipping",
            sph,
            lens_line_id,
        )
        return None
    try:
        cyl_f = 0.0 if cyl is None else float(cyl)
    except (TypeError, ValueError):
        cyl_f = 0.0
    add_f: Optional[float]
    if add is None:
        add_f = None
    else:
        try:
            add_f = float(add)
        except (TypeError, ValueError):
            add_f = None

    return {
        "lens_line_id": str(lens_line_id),
        "sph": sph_f,
        "cyl": cyl_f,
        "add": add_f,
    }


def _qty_for(order_item: Dict[str, Any]) -> int:
    """Quantity to reserve for this line. Defaults to 1 when unset/invalid
    so a malformed quantity field never zeros out the reservation."""
    raw = order_item.get("quantity")
    try:
        q = int(raw) if raw is not None else 1
    except (TypeError, ValueError):
        q = 1
    if q < 1:
        q = 1
    return q


# ---------------------------------------------------------------------------
# DB handle + idempotency
# ---------------------------------------------------------------------------


def _get_audit_collection() -> Optional[Any]:
    """Return the lens_stock_audit collection, or None when unavailable.
    Used for the idempotency lookup -- if the collection is None we skip
    the dedup check (fail-open on dedup: better to risk a double-reserve
    + 409 than to block the whole order create on a mongo blip).

    Routes the lookup through the lens_stock router's own _get_db helper
    so tests can monkey-patch a single seam (the stock router) and have
    the hook see the same FakeMongo."""
    try:
        from ..routers import lens_stock as stock_router

        db = stock_router._get_db()  # noqa: SLF001 -- intentional shared seam
        if db is None:
            return None
        return db.get_collection("lens_stock_audit")
    except Exception:  # noqa: BLE001
        return None


def _already_acted(
    source_id: str, action: str, line_stock_id_hint: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Return a prior audit row matching (source_id, action) so the hook
    can short-circuit a retry. Idempotency key is `source_id`; action
    discriminates reserve vs commit (a single order_item can carry both).

    `line_stock_id_hint` is unused by the lookup itself but logged so we
    can correlate during debugging.
    """
    _ = line_stock_id_hint
    coll = _get_audit_collection()
    if coll is None:
        return None
    try:
        return coll.find_one({"source_id": source_id, "action": action})
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[LENS_HOOK] idempotency lookup failed for %s/%s: %s",
            source_id,
            action,
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Reserve (POS Step 6 -- BLOCKING)
# ---------------------------------------------------------------------------


async def reserve_for_order_item(
    *,
    order_item: Dict[str, Any],
    order_id: str,
    line_index: int,
    store_id: str,
    user: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Soft-reserve the lens cell for one order line. Idempotent on
    (order_id, line_index).

    Returns:
      None -- non-lens item OR cell coordinates missing OR fail-soft skip.
      dict -- {"status": "reserved" | "already_reserved" | "failed",
               "lens_line_id", "sph", "cyl", "add", "qty",
               "cell"?, "error"?} on success / soft-failure.

    Raises:
      HTTPException 409 -- insufficient stock. The POS surfaces this to the
                           user; do NOT swallow.
      HTTPException 4xx (other) -- when the lens_stock_router returns a
                           clearly-our-fault response (404 cell-not-found
                           is converted to a 409 here so POS gets a single
                           consistent code).
    Other exceptions are LOGGED and converted to a soft-failure record so
    POS order create never crashes on a transient B'1 endpoint issue.
    """
    if not _is_lens_item(order_item):
        return None

    cell = _extract_cell_key(order_item)
    if cell is None:
        return None

    qty = _qty_for(order_item)
    source_id = "{oid}#{idx}".format(oid=order_id, idx=line_index)

    # Idempotency: if a `reserve` row for this source_id already exists,
    # the prior call already succeeded -- treat as cached success.
    prior = _already_acted(source_id, "reserve")
    if prior is not None:
        logger.info(
            "[LENS_HOOK] reserve already done for %s (line_stock=%s)",
            source_id,
            prior.get("line_stock_id"),
        )
        return {
            "status": "already_reserved",
            "lens_line_id": cell["lens_line_id"],
            "sph": cell["sph"],
            "cyl": cell["cyl"],
            "add": cell["add"],
            "qty": qty,
            "line_stock_id": prior.get("line_stock_id"),
        }

    # Build the router payload. The router accepts a Pydantic model so
    # we import lazily to avoid circular imports at module load (the
    # router imports from this package's services indirectly via auth).
    try:
        from ..routers import lens_stock as stock_router
    except Exception as exc:  # noqa: BLE001
        logger.error("[LENS_HOOK] cannot import lens_stock router: %s", exc)
        return {
            "status": "failed",
            "lens_line_id": cell["lens_line_id"],
            "sph": cell["sph"],
            "cyl": cell["cyl"],
            "add": cell["add"],
            "qty": qty,
            "error": "lens_stock router unavailable",
        }

    payload = stock_router.ReserveCommitReleasePayload(
        store_id=store_id,
        sph=cell["sph"],
        cyl=cell["cyl"],
        add=cell["add"],
        qty=qty,
        source_type="POS",
        source_id=source_id,
        notes="POS order create reserve",
    )

    try:
        # The router's reserve_cell is async; we await it directly.
        # The router uses sync pymongo internally so the await is just
        # a coroutine wrapper, but FastAPI request handlers can naturally
        # await this from their own async context.
        result = await stock_router.reserve_cell(cell["lens_line_id"], payload, user)
    except HTTPException as exc:
        if exc.status_code in (404, 409):
            # 404 (cell never seeded) is upgraded to 409 so POS sees a
            # single "insufficient stock for SPH X CYL Y" message instead
            # of distinguishing missing vs empty.
            detail = exc.detail or "Stock cell not available"
            if exc.status_code == 404:
                detail = (
                    "Lens stock cell not configured for SPH {sph} CYL {cyl}"
                    " (lens_line={line}). Set on_hand via Power Grid "
                    "before selling.".format(
                        sph=cell["sph"],
                        cyl=cell["cyl"],
                        line=cell["lens_line_id"],
                    )
                )
            raise HTTPException(status_code=409, detail=detail)
        # Other HTTP errors propagate (5xx surfaces as a generic POS
        # failure; SUPERADMIN can investigate).
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[LENS_HOOK] reserve unexpected error for %s: %s",
            source_id,
            exc,
            exc_info=True,
        )
        return {
            "status": "failed",
            "lens_line_id": cell["lens_line_id"],
            "sph": cell["sph"],
            "cyl": cell["cyl"],
            "add": cell["add"],
            "qty": qty,
            "error": str(exc),
        }

    return {
        "status": "reserved",
        "lens_line_id": cell["lens_line_id"],
        "sph": cell["sph"],
        "cyl": cell["cyl"],
        "add": cell["add"],
        "qty": qty,
        "cell": (result or {}).get("cell"),
        "line_stock_id": ((result or {}).get("cell") or {}).get("line_stock_id"),
    }


# ---------------------------------------------------------------------------
# Commit (Workshop lens MOUNTED -- fail-soft)
# ---------------------------------------------------------------------------


async def commit_for_workshop_dispatch(
    *,
    order_item: Dict[str, Any],
    order_id: str,
    line_index: int,
    store_id: str,
    user: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Hard-commit the reserved lens cell when the workshop mounts the
    physical lens into the frame.

    Idempotent on (order_id, line_index, "commit"). Returns None when the
    item is not a lens or has no cell key. Fail-soft: any exception is
    logged + None is returned (the workshop has already produced the lens;
    blocking the dispatch on a stock-side hiccup is worse than logging it).
    """
    if not _is_lens_item(order_item):
        return None

    cell = _extract_cell_key(order_item)
    if cell is None:
        return None

    qty = _qty_for(order_item)
    source_id = "{oid}#{idx}#commit".format(oid=order_id, idx=line_index)

    prior = _already_acted(source_id, "commit")
    if prior is not None:
        return {
            "status": "already_committed",
            "lens_line_id": cell["lens_line_id"],
            "sph": cell["sph"],
            "cyl": cell["cyl"],
            "add": cell["add"],
            "qty": qty,
        }

    try:
        from ..routers import lens_stock as stock_router
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LENS_HOOK] commit: cannot import lens_stock router: %s", exc)
        return None

    payload = stock_router.ReserveCommitReleasePayload(
        store_id=store_id,
        sph=cell["sph"],
        cyl=cell["cyl"],
        add=cell["add"],
        qty=qty,
        source_type="WORKSHOP",
        source_id=source_id,
        notes="Workshop dispatch commit",
    )

    try:
        result = await stock_router.commit_cell(cell["lens_line_id"], payload, user)
    except HTTPException as exc:
        # 409 here means reserved < qty -- a real data inconsistency we
        # cannot resolve from inside the workshop callback. Log + move on.
        logger.warning(
            "[LENS_HOOK] commit %s status=%s detail=%s",
            source_id,
            exc.status_code,
            exc.detail,
        )
        return {
            "status": "failed",
            "lens_line_id": cell["lens_line_id"],
            "sph": cell["sph"],
            "cyl": cell["cyl"],
            "add": cell["add"],
            "qty": qty,
            "error": str(exc.detail),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[LENS_HOOK] commit unexpected error for %s: %s",
            source_id,
            exc,
        )
        return {
            "status": "failed",
            "lens_line_id": cell["lens_line_id"],
            "sph": cell["sph"],
            "cyl": cell["cyl"],
            "add": cell["add"],
            "qty": qty,
            "error": str(exc),
        }

    return {
        "status": "committed",
        "lens_line_id": cell["lens_line_id"],
        "sph": cell["sph"],
        "cyl": cell["cyl"],
        "add": cell["add"],
        "qty": qty,
        "cell": (result or {}).get("cell"),
    }


# ---------------------------------------------------------------------------
# Release (Order cancel -- fail-soft)
# ---------------------------------------------------------------------------


async def release_for_cancel(
    *,
    order_item: Dict[str, Any],
    order_id: str,
    line_index: int,
    store_id: str,
    user: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Release the reserved cell back to AVAILABLE on order cancel.

    Idempotent on (order_id, line_index, "release"). Fail-soft -- a 409
    here means the cell was already committed (workshop ran ahead of
    cancel) or the cell does not exist; either way we don't block the
    cancel. Skips when no `reserve` row exists for this line (nothing
    was ever reserved -- e.g. lens_reserve_failed=true at order create).
    """
    if not _is_lens_item(order_item):
        return None

    cell = _extract_cell_key(order_item)
    if cell is None:
        return None

    qty = _qty_for(order_item)
    reserve_source_id = "{oid}#{idx}".format(oid=order_id, idx=line_index)
    release_source_id = "{oid}#{idx}#release".format(oid=order_id, idx=line_index)

    # If we already released, no-op.
    prior_rel = _already_acted(release_source_id, "release")
    if prior_rel is not None:
        return {
            "status": "already_released",
            "lens_line_id": cell["lens_line_id"],
            "sph": cell["sph"],
            "cyl": cell["cyl"],
            "add": cell["add"],
            "qty": qty,
        }

    # If reserve never happened for this line, skip (nothing to release).
    prior_res = _already_acted(reserve_source_id, "reserve")
    if prior_res is None:
        logger.info(
            "[LENS_HOOK] release skipped for %s -- no prior reserve",
            reserve_source_id,
        )
        return {
            "status": "no_reservation",
            "lens_line_id": cell["lens_line_id"],
            "sph": cell["sph"],
            "cyl": cell["cyl"],
            "add": cell["add"],
            "qty": qty,
        }

    # If a commit row already exists, the lens was already cut -- there
    # are no reserved units to release. Skip.
    commit_source_id = "{oid}#{idx}#commit".format(oid=order_id, idx=line_index)
    prior_com = _already_acted(commit_source_id, "commit")
    if prior_com is not None:
        return {
            "status": "already_committed",
            "lens_line_id": cell["lens_line_id"],
            "sph": cell["sph"],
            "cyl": cell["cyl"],
            "add": cell["add"],
            "qty": qty,
        }

    try:
        from ..routers import lens_stock as stock_router
    except Exception as exc:  # noqa: BLE001
        logger.warning("[LENS_HOOK] release: cannot import lens_stock router: %s", exc)
        return None

    payload = stock_router.ReserveCommitReleasePayload(
        store_id=store_id,
        sph=cell["sph"],
        cyl=cell["cyl"],
        add=cell["add"],
        qty=qty,
        source_type="ORDER_CANCEL",
        source_id=release_source_id,
        notes="Order cancel release",
    )

    try:
        result = await stock_router.release_cell(cell["lens_line_id"], payload, user)
    except HTTPException as exc:
        logger.warning(
            "[LENS_HOOK] release %s status=%s detail=%s",
            release_source_id,
            exc.status_code,
            exc.detail,
        )
        return {
            "status": "failed",
            "lens_line_id": cell["lens_line_id"],
            "sph": cell["sph"],
            "cyl": cell["cyl"],
            "add": cell["add"],
            "qty": qty,
            "error": str(exc.detail),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[LENS_HOOK] release unexpected error for %s: %s",
            release_source_id,
            exc,
        )
        return {
            "status": "failed",
            "lens_line_id": cell["lens_line_id"],
            "sph": cell["sph"],
            "cyl": cell["cyl"],
            "add": cell["add"],
            "qty": qty,
            "error": str(exc),
        }

    return {
        "status": "released",
        "lens_line_id": cell["lens_line_id"],
        "sph": cell["sph"],
        "cyl": cell["cyl"],
        "add": cell["add"],
        "qty": qty,
        "cell": (result or {}).get("cell"),
    }


# ---------------------------------------------------------------------------
# Bulk helpers used by the routers
# ---------------------------------------------------------------------------


async def reserve_all_lens_items(
    *,
    items: List[Dict[str, Any]],
    order_id: str,
    store_id: str,
    user: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Reserve every lens item on an order. Stops at the first 409
    (insufficient stock) -- HTTPException propagates so the POS surfaces a
    single clear message. The caller is responsible for rolling back any
    cells reserved by earlier successful calls (release them on the failed
    order path). Returns a list of per-line reservation records.
    """
    records: List[Dict[str, Any]] = []
    for idx, item in enumerate(items):
        rec = await reserve_for_order_item(
            order_item=item,
            order_id=order_id,
            line_index=idx,
            store_id=store_id,
            user=user,
        )
        if rec is not None:
            records.append({"line_index": idx, **rec})
    return records


async def release_all_lens_items(
    *,
    items: List[Dict[str, Any]],
    order_id: str,
    store_id: str,
    user: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Release every lens item on a cancelled order. Fail-soft for each
    line. Returns per-line release records."""
    records: List[Dict[str, Any]] = []
    for idx, item in enumerate(items):
        rec = await release_for_cancel(
            order_item=item,
            order_id=order_id,
            line_index=idx,
            store_id=store_id,
            user=user,
        )
        if rec is not None:
            records.append({"line_index": idx, **rec})
    return records


async def commit_all_lens_items(
    *,
    items: List[Dict[str, Any]],
    order_id: str,
    store_id: str,
    user: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Commit every lens item on a dispatched order. Fail-soft for each
    line."""
    records: List[Dict[str, Any]] = []
    for idx, item in enumerate(items):
        rec = await commit_for_workshop_dispatch(
            order_item=item,
            order_id=order_id,
            line_index=idx,
            store_id=store_id,
            user=user,
        )
        if rec is not None:
            records.append({"line_index": idx, **rec})
    return records
