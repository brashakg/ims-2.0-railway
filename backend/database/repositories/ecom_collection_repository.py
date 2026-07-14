"""
IMS 2.0 - E-commerce Collection Repository  (BVI Phase 2 -- FLAGSHIP #1)
=======================================================================
Data access for the `ecom_collections` collection: BVI's Shopify Custom/Smart
Collections, folded into IMS Mongo (BVI_MERGE_PLAN.md A.1 / Phase 2).

PUSH-DARK: this repo STORES + EDITS collections inside IMS only. No Shopify
network writes happen in Phase 2 (the GraphQL push is Phase 5). Every write here
sets `locally_modified=True` so the Phase-5 push queue knows the row is dirty.

A collection is CUSTOM (manual, ordered SKU list in the embedded `products`
array) or SMART (membership computed from `rules` -- see services/ecom_smart_rules).
Manual-membership methods (add/remove/reorder) operate on the embedded array;
they are Mongo-natural and avoid a separate join collection.

Idempotent join keys (never Mongo `_id`): `handle` (storefront slug, primary) |
`shopify_collection_id` (Shopify side). `get_by_handle` is the re-import key.

Fail-soft throughout (mirrors base_repository + catalog_variant_repository): any
error returns a safe empty value and prints, never raises to the caller. All
writes use the 2-arg insert_one / update_one signatures so they work against BOTH
real pymongo and the in-memory MockCollection used in no-DB / test mode.
"""
from __future__ import annotations

from typing import Dict, List, Optional
from datetime import datetime
import uuid

from .base_repository import BaseRepository


class EcomCollectionRepository(BaseRepository):
    """Repository for the `ecom_collections` collection."""

    @property
    def entity_name(self) -> str:
        return "EcomCollection"

    @property
    def id_field(self) -> str:
        return "collection_id"

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_by_id(self, collection_id: str) -> Optional[Dict]:
        """Return the collection by its internal id, else None. Fail-soft."""
        if not collection_id:
            return None
        return self.find_one({self.id_field: collection_id})

    def get_by_handle(self, handle: str) -> Optional[Dict]:
        """Return the collection whose storefront `handle` matches, else None.

        `handle` is the unique slug + the idempotent re-import key. Fail-soft
        via base_repository.find_one.
        """
        if not handle:
            return None
        return self.find_one({"handle": handle})

    def list(
        self,
        published: Optional[bool] = None,
        collection_type: Optional[str] = None,
        category_anchor: Optional[str] = None,
        auto_source: Optional[str] = None,
        skip: int = 0,
        limit: int = 200,
    ) -> List[Dict]:
        """List collections, optionally filtered.

        Filters are AND-combined and each is applied only when provided (None =
        "don't filter on this"). Ordered by `sort_priority` ascending (lower =
        earlier in nav, matching the BVI convention) then handle for stability.
        Empty list when none / no DB.
        """
        query: Dict = {}
        if published is not None:
            query["published"] = published
        if collection_type:
            query["collection_type"] = collection_type
        if category_anchor:
            query["category_anchor"] = category_anchor
        if auto_source:
            query["auto_source"] = auto_source
        return self.find_many(
            query, sort=[("sort_priority", 1), ("handle", 1)], skip=skip, limit=limit
        )

    # ------------------------------------------------------------------
    # Create / update / delete
    # ------------------------------------------------------------------

    def create(self, data: Dict) -> Optional[Dict]:
        """Create a collection. Requires `handle` (the unique slug); refuses a
        row without it rather than mint a null-keyed orphan that would later
        collide on the unique-sparse handle index. Defaults are applied for the
        type/flags so a minimal payload yields a well-formed doc.

        PUSH-DARK: new collections are born `locally_modified=True` (nothing has
        been pushed to Shopify yet).
        """
        if not data or not data.get("handle"):
            return None

        doc = dict(data)
        doc.setdefault(self.id_field, str(uuid.uuid4()))
        doc.setdefault("collection_type", "CUSTOM")
        doc.setdefault("published", True)
        doc.setdefault("disjunctive", False)
        doc.setdefault("sort_priority", 100)
        # Manual membership array (empty for a fresh CUSTOM; unused for SMART).
        doc.setdefault("products", [])
        doc.setdefault("rules", [])
        doc.setdefault("products_count", len(doc.get("products") or []))
        # SUPERADMIN "block from online sale": when True, every product in this
        # collection is excluded from Shopify (never pushed; delisted if synced).
        # See api/services/online_block.py + routers/online_store_collections.py.
        doc.setdefault("online_sync_blocked", False)
        # Dirty from birth -- not yet synced to Shopify.
        doc.setdefault("locally_modified", True)
        # base_repository.create assigns _id + created_at/updated_at and inserts.
        return super().create(doc)

    def update(self, collection_id: str, data: Dict) -> bool:
        """Patch a collection by id. Identity/immutable fields are stripped so a
        caller can't overwrite the id or created_at. Any update marks the row
        dirty (`locally_modified=True`) for the Phase-5 push queue unless the
        caller explicitly set it. Returns True on a real change.
        """
        if not collection_id or not data:
            return False
        patch = {
            k: v
            for k, v in data.items()
            if k not in (self.id_field, "_id", "created_at", "created_by")
        }
        if not patch:
            return False
        patch.setdefault("locally_modified", True)
        # Keep products_count consistent if the manual list was replaced wholesale.
        if "products" in patch and isinstance(patch["products"], list):
            patch["products_count"] = len(patch["products"])
        return super().update(collection_id, patch)

    def delete(self, collection_id: str) -> bool:
        """Hard-delete a collection by id. Fail-soft via base_repository."""
        if not collection_id:
            return False
        return super().delete(collection_id)

    def set_block(
        self, collection_id: str, blocked: bool, user_id: Optional[str] = None
    ) -> Optional[Dict]:
        """SUPERADMIN "block from online sale" toggle: set online_sync_blocked +
        audit stamps directly.

        Deliberately does NOT flip `locally_modified` (the collection metadata
        itself did not change -- only IMS's internal online-block flag), so a
        block/unblock never queues a pointless collection re-push. Returns the
        updated doc, or None on failure / unknown collection. Fail-soft."""
        if not collection_id:
            return None
        coll = self.find_one({self.id_field: collection_id})
        if coll is None:
            return None
        now = datetime.now()
        patch: Dict = {"online_sync_blocked": bool(blocked)}
        if blocked:
            patch["online_sync_blocked_by"] = user_id
            patch["online_sync_blocked_at"] = now
        else:
            patch["online_sync_unblocked_by"] = user_id
            patch["online_sync_unblocked_at"] = now
        try:
            self.collection.update_one(
                {self.id_field: collection_id}, {"$set": patch}
            )
        except Exception as e:  # noqa: BLE001 -- fail-soft
            print(f"Error setting block on {self.entity_name} {collection_id}: {e}")
            return None
        return self.find_one({self.id_field: collection_id})

    # ------------------------------------------------------------------
    # Manual membership (embedded ordered `products` array)
    # ------------------------------------------------------------------

    def add_product(self, collection_id: str, sku: str, position: Optional[int] = None) -> Optional[Dict]:
        """Add a SKU to a CUSTOM collection's ordered membership, idempotently.

        If the SKU is already a member its position is updated (when `position`
        is given) rather than duplicated. When `position` is None the SKU is
        appended at the end (next position). Returns the updated collection doc,
        or None on failure / unknown collection.
        """
        if not collection_id or not sku:
            return None
        coll = self.find_one({self.id_field: collection_id})
        if coll is None:
            return None
        products = list(coll.get("products") or [])

        existing = next((p for p in products if p.get("sku") == sku), None)
        if existing is not None:
            if position is not None:
                existing["position"] = int(position)
        else:
            pos = (
                int(position)
                if position is not None
                else (max((int(p.get("position", 0)) for p in products), default=-1) + 1)
            )
            products.append({"sku": sku, "position": pos})

        return self._save_products(collection_id, products)

    def remove_product(self, collection_id: str, sku: str) -> Optional[Dict]:
        """Remove a SKU from a CUSTOM collection's membership. Idempotent (a SKU
        that isn't a member is a no-op success). Returns the updated doc, or None
        on failure / unknown collection."""
        if not collection_id or not sku:
            return None
        coll = self.find_one({self.id_field: collection_id})
        if coll is None:
            return None
        products = [p for p in (coll.get("products") or []) if p.get("sku") != sku]
        return self._save_products(collection_id, products)

    def reorder_products(self, collection_id: str, ordered_skus: List[str]) -> Optional[Dict]:
        """Re-position the membership to match `ordered_skus` (0-based positions
        in the given order). SKUs in the list that aren't current members are
        ignored; current members NOT in the list are appended after, preserving
        their prior relative order. Returns the updated doc, or None on failure.
        """
        if not collection_id or ordered_skus is None:
            return None
        coll = self.find_one({self.id_field: collection_id})
        if coll is None:
            return None
        current = {p.get("sku"): dict(p) for p in (coll.get("products") or [])}

        reordered: List[Dict] = []
        pos = 0
        for sku in ordered_skus:
            if sku in current:
                item = current.pop(sku)
                item["position"] = pos
                reordered.append(item)
                pos += 1
        # Append any leftover members (not named in ordered_skus) after, keeping
        # their existing relative order by prior position.
        for item in sorted(current.values(), key=lambda p: int(p.get("position", 0))):
            item["position"] = pos
            reordered.append(item)
            pos += 1

        return self._save_products(collection_id, reordered)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _save_products(self, collection_id: str, products: List[Dict]) -> Optional[Dict]:
        """Persist a rewritten membership array + sync products_count + mark dirty.
        Returns the merged doc (JSON-safe) or None on error."""
        try:
            self.collection.update_one(
                {self.id_field: collection_id},
                {
                    "$set": {
                        "products": products,
                        "products_count": len(products),
                        "locally_modified": True,
                        "updated_at": datetime.now(),
                    }
                },
            )
        except Exception as e:  # noqa: BLE001 -- fail-soft
            print(f"Error updating {self.entity_name} membership {collection_id}: {e}")
            return None
        return self.find_one({self.id_field: collection_id})
