"""
IMS 2.0 - Product Image Repository  (BVI Phase 4 -- FLAGSHIP #3)
===============================================================
Data access for the `product_images` collection: BVI's ProductImage +
VariantImage tables AND the design-team work queue that wraps them, folded into
IMS Mongo (BVI_MERGE_PLAN.md A.1 / Phase 4).

PUSH-DARK: this repo STORES + EDITS image records + drives their
RAW->EDITED->APPROVED design lifecycle inside IMS only. No Shopify network write
happens in Phase 4 (the GraphQL image push that fills `shopify_image_id` is
Phase 5).

One row = one image of a product (variant_id=null) or of a specific variant
(variant_id set) -- BVI's two image tables merged + discriminated by variant_id.

THE LIFECYCLE STATE MACHINE (enforced here in set_status):
    QUEUED ------> IN_PROGRESS ------> REVIEW ------> APPROVED
                       ^                  |
                       |                  +---------> REJECTED
                       +--------------------------------+
    REJECTED ----> IN_PROGRESS   (re-work a rejected image)
A transition not in this graph is refused (returns None) so the router can 409.
attach_edited is the REVIEW on-ramp: it records the designer's edited_url and
moves IN_PROGRESS -> REVIEW in one step.

Idempotent keys (never `_id`): image_id (primary) | product_id | variant_id.

Fail-soft throughout (mirrors base_repository + ecom_menu_repository): any error
returns a safe empty value and prints, never raises to the caller. Writes go
through the 2-arg update_one signature so they work against BOTH real pymongo and
the in-memory MockCollection used in no-DB / test mode.
"""
from __future__ import annotations

from typing import Dict, List, Optional
from datetime import datetime
import uuid

from .base_repository import BaseRepository


# Valid design-lifecycle states (mirrors PRODUCT_IMAGE_SCHEMA.status enum).
VALID_STATUSES = {"QUEUED", "IN_PROGRESS", "REVIEW", "APPROVED", "REJECTED"}

# Image kinds (mirrors PRODUCT_IMAGE_SCHEMA.kind enum).
VALID_KINDS = {"RAW", "EDITED", "FINAL"}

# Image sources (mirrors PRODUCT_IMAGE_SCHEMA.source enum).
VALID_SOURCES = {"UPLOAD", "SHOPIFY", "SCRAPE", "AI"}

# The allowed status-transition graph. A move is legal iff the target is in the
# set keyed by the current status. APPROVED is terminal (no outgoing edges);
# REJECTED can only go back to IN_PROGRESS (re-work). This is the single source
# of truth for both the repository guard and the router's 409.
VALID_TRANSITIONS: Dict[str, set] = {
    "QUEUED": {"IN_PROGRESS"},
    "IN_PROGRESS": {"REVIEW"},
    "REVIEW": {"APPROVED", "REJECTED"},
    "REJECTED": {"IN_PROGRESS"},
    "APPROVED": set(),  # terminal
}

# Presentation/linkage fields a caller may patch via update() (everything that
# is NOT identity, lifecycle-controlled, or a server-owned timestamp). Lifecycle
# moves go through assign/set_status/attach_edited, not update().
_PATCHABLE_FIELDS = (
    "url",
    "kind",
    "source",
    "position",
    "alt_text",
    "design_notes",
    "variant_id",
    "submitted_by",
    "shopify_image_id",
)


def is_valid_transition(current: Optional[str], target: str) -> bool:
    """Pure predicate: may an image move from `current` status to `target`?

    A None/absent current is treated as QUEUED (a freshly-minted row). An
    unknown target is always illegal. Used by both set_status and the router so
    the 409 and the guard agree."""
    if target not in VALID_STATUSES:
        return False
    cur = current or "QUEUED"
    return target in VALID_TRANSITIONS.get(cur, set())


class ProductImageRepository(BaseRepository):
    """Repository for the `product_images` collection."""

    @property
    def entity_name(self) -> str:
        return "ProductImage"

    @property
    def id_field(self) -> str:
        return "image_id"

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_by_id(self, image_id: str) -> Optional[Dict]:
        """Return the image by its internal id, else None. Fail-soft."""
        if not image_id:
            return None
        return self.find_one({self.id_field: image_id})

    def list(
        self,
        status: Optional[str] = None,
        product_id: Optional[str] = None,
        variant_id: Optional[str] = None,
        assigned_to: Optional[str] = None,
        kind: Optional[str] = None,
        skip: int = 0,
        limit: int = 200,
    ) -> List[Dict]:
        """List images (the design QUEUE), optionally filtered.

        Filters are AND-combined and each applied only when provided (None =
        "don't filter on this"). Ordered by (product_id, position) so a product's
        gallery comes back in display order. Empty list when none / no DB.
        """
        query: Dict = {}
        if status is not None:
            query["status"] = status
        if product_id is not None:
            query["product_id"] = product_id
        if variant_id is not None:
            query["variant_id"] = variant_id
        if assigned_to is not None:
            query["assigned_to"] = assigned_to
        if kind is not None:
            query["kind"] = kind
        return self.find_many(
            query, sort=[("product_id", 1), ("position", 1)], skip=skip, limit=limit
        )

    def count_by_status(self, status: str) -> int:
        """How many images are in a given lifecycle status (queue badges)."""
        if not status:
            return 0
        return self.count({"status": status})

    # ------------------------------------------------------------------
    # Create / update / delete
    # ------------------------------------------------------------------

    def create(self, data: Dict) -> Optional[Dict]:
        """Register (queue) an image. Requires `product_id` + `url`; refuses a
        row without either rather than mint a useless orphan.

        Defaults: a fresh image enters the queue as kind=RAW, status=QUEUED,
        source=UPLOAD, position=0, variant_id=None, shopify_image_id=None. The
        caller's values win where supplied. The image_id is server-minted.
        """
        if not data or not data.get("product_id") or not data.get("url"):
            return None

        doc = dict(data)
        doc.setdefault(self.id_field, str(uuid.uuid4()))
        doc.setdefault("kind", "RAW")
        doc.setdefault("status", "QUEUED")
        doc.setdefault("source", "UPLOAD")
        doc.setdefault("position", 0)
        doc.setdefault("variant_id", None)
        doc.setdefault("edited_url", None)
        doc.setdefault("alt_text", None)
        doc.setdefault("design_notes", None)
        doc.setdefault("assigned_to", None)
        doc.setdefault("reviewed_by", None)
        doc.setdefault("approved_at", None)
        # PUSH-DARK: never pushed to Shopify yet.
        doc.setdefault("shopify_image_id", None)
        # base_repository.create assigns _id + created_at/updated_at and inserts.
        return super().create(doc)

    def update(self, image_id: str, data: Dict) -> bool:
        """Patch an image's presentation/linkage fields (url / kind / source /
        position / alt_text / design_notes / variant_id / shopify_image_id).

        Identity, lifecycle-controlled fields (status / assigned_to / edited_url /
        reviewed_by / approved_at) and server timestamps are stripped -- those
        move only through assign / set_status / attach_edited so the state machine
        can't be bypassed by a blind PUT. Returns True on a real change.
        """
        if not image_id or not data:
            return False
        patch = {k: v for k, v in data.items() if k in _PATCHABLE_FIELDS}
        if not patch:
            return False
        return super().update(image_id, patch)

    def delete(self, image_id: str) -> bool:
        """Hard-delete an image by id. Fail-soft via base_repository."""
        if not image_id:
            return False
        return super().delete(image_id)

    # ------------------------------------------------------------------
    # Lifecycle helpers (the design queue state machine)
    # ------------------------------------------------------------------

    def assign(self, image_id: str, user_id: Optional[str]) -> Optional[Dict]:
        """Assign the image to a DESIGN_MANAGER (`user_id`). Does NOT itself
        change status (a manager can pick up a QUEUED item before starting work);
        the move to IN_PROGRESS is an explicit set_status. Passing user_id=None
        unassigns. Returns the updated doc, or None on unknown image / no DB.
        """
        if not image_id:
            return None
        if self.get_by_id(image_id) is None:
            return None
        return self._patch(image_id, {"assigned_to": user_id})

    def set_status(
        self, image_id: str, status: str, by: Optional[str] = None
    ) -> Optional[Dict]:
        """Transition the image to `status`, enforcing the valid-transition graph.

        Returns the updated doc on success. Returns None on: unknown image, no DB,
        an unknown target status, or an ILLEGAL transition (the router maps the
        last two to a 409). The same-state no-op (status already == target) is
        also refused as illegal so a caller can't, e.g., re-APPROVE.

        Side effects: moving to APPROVED stamps `approved_at` + `reviewed_by=by`;
        moving to REJECTED stamps `reviewed_by=by`. (The chained audit row for an
        approval is written by the ROUTER, which owns the audit repo + user, not
        here -- the repository stays a pure data layer.)
        """
        if not image_id or not status:
            return None
        target = status.strip().upper()
        existing = self.get_by_id(image_id)
        if existing is None:
            return None
        current = existing.get("status") or "QUEUED"
        if not is_valid_transition(current, target):
            # Unknown target OR illegal edge OR same-state no-op -> caller 409s.
            return None

        patch: Dict = {"status": target}
        now = datetime.now()
        if target == "APPROVED":
            patch["approved_at"] = now
            patch["reviewed_by"] = by
        elif target == "REJECTED":
            patch["reviewed_by"] = by
        return self._patch(image_id, patch)

    def attach_edited(
        self, image_id: str, edited_url: str, by: Optional[str] = None
    ) -> Optional[Dict]:
        """Attach the designer's EDITED asset (`edited_url`) and move the image
        IN_PROGRESS -> REVIEW in one step (the review on-ramp).

        Requires the image to currently be IN_PROGRESS (the only state from which
        REVIEW is reachable); from any other state this returns None so the router
        can 409 -- you can't submit for review work that wasn't started. Also
        bumps `kind` to EDITED (the asset is now a designer output) and records
        `submitted_by=by` when provided. Returns the updated doc, or None on
        unknown image / no DB / illegal state / empty edited_url.
        """
        if not image_id or not edited_url:
            return None
        existing = self.get_by_id(image_id)
        if existing is None:
            return None
        current = existing.get("status") or "QUEUED"
        if not is_valid_transition(current, "REVIEW"):
            # Only IN_PROGRESS -> REVIEW is legal; anything else 409s.
            return None
        patch: Dict = {
            "edited_url": edited_url,
            "status": "REVIEW",
            "kind": "EDITED",
        }
        if by is not None:
            patch["submitted_by"] = by
        return self._patch(image_id, patch)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _patch(self, image_id: str, fields: Dict) -> Optional[Dict]:
        """Apply a $set patch + bump updated_at, then return the merged doc
        (JSON-safe) or None on error. Uses the 2-arg update_one so it works on
        both real pymongo and MockCollection. Mirrors ecom_menu_repo._save_items.
        """
        try:
            patch = dict(fields)
            patch["updated_at"] = datetime.now()
            self.collection.update_one({self.id_field: image_id}, {"$set": patch})
        except Exception as e:  # noqa: BLE001 -- fail-soft
            print(f"Error updating {self.entity_name} {image_id}: {e}")
            return None
        return self.get_by_id(image_id)
