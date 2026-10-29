"""catalog_products `ecom` sub-doc: the edit revision + the one safe write.

AUDIT SF-04 (owner-approved 18 Oct 2026). `ecom.locally_modified` is the
Shopify push queue. A push reads the product (its payload snapshot), talks
to Shopify, then clears the flag -- and a catalogue edit saved in between
used to be wiped off the queue with it (the newer title / price / photo never
went out). Two rules close that:

  1. EDIT REVISION. Every writer that queues a product (sets the flag True)
     also stamps a fresh `ecom.edit_rev` (`dirty_marks`). The push clears the
     flag only when the stored revision still equals the one in its snapshot,
     so an edit made after the snapshot stays queued for the next run.
  2. COMPARE-AND-SET. A read-merge-write of the whole `ecom` object
     (`update_ecom`) lands only if `ecom` is unchanged since the read, and is
     retried on a fresh read otherwise -- so two writers can never silently
     drop each other's sibling fields (a Shopify id, a claim, a status).

No imports from the push package: product_master, the catalog router and the
online services all use this without a cycle.
"""

from __future__ import annotations

import copy
import logging
import uuid
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

EDIT_REV = "edit_rev"


def new_edit_rev() -> str:
    return uuid.uuid4().hex


def dirty_marks() -> Dict[str, Any]:
    """The ecom keys a QUEUEING write sets: the flag plus a fresh revision.
    Dotted form for a `$set` patch: {f"ecom.{k}": v for k, v in ...}."""
    return {"locally_modified": True, EDIT_REV: new_edit_rev()}


def update_ecom(
    db,
    product_id: str,
    mutate: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
    *,
    attempts: int = 5,
) -> Optional[Dict[str, Any]]:
    """update_ecom_in over db["catalog_products"]."""
    return update_ecom_in(db["catalog_products"], product_id, mutate, attempts=attempts)


def update_ecom_in(
    coll,
    product_id: str,
    mutate: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
    *,
    attempts: int = 5,
) -> Optional[Dict[str, Any]]:
    """Compare-and-set the product's `ecom`: `mutate` gets a private copy of
    the stored ecom ({} when absent) and returns the new ecom, or None to
    write nothing. The write's filter carries the ecom that was read, so it
    lands only if no other writer changed ecom since; on a miss it re-reads
    and re-applies `mutate`. Returns the ecom written, else None (no doc,
    `mutate` declined, or every attempt lost the race -- logged). Raises
    only what the database raises."""
    for _ in range(attempts):
        doc = coll.find_one({"id": product_id})
        if doc is None:
            return None
        old = doc.get("ecom")
        new = mutate(copy.deepcopy(old) if isinstance(old, dict) else {})
        if new is None:
            return None
        res = coll.update_one({"id": product_id, "ecom": old}, {"$set": {"ecom": new}})
        hit = getattr(res, "matched_count", None)
        if hit is None:
            hit = getattr(res, "modified_count", 0)
        if hit:
            return new
    logger.warning("[ECOM] %s: ecom kept changing under the write -- not written", product_id)
    return None


def unqueue_in(coll, product_id: str, sent_rev: Optional[str]) -> bool:
    """Take a product OFF the push queue (clear `locally_modified`) -- only
    while it still carries `sent_rev`, the edit revision the caller read
    (rule 1): an edit saved after that read stays queued. Returns True when
    the flag was cleared. Raises only what the database raises."""

    def _clear(ecom: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not ecom.get("locally_modified") or ecom.get(EDIT_REV) != sent_rev:
            return None
        ecom["locally_modified"] = False
        return ecom

    return update_ecom_in(coll, product_id, _clear) is not None
