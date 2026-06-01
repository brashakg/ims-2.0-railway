"""
IMS 2.0 - Catalog Variant Repository  (BVI Phase 1 foundation)
==============================================================
The variant IDENTITY + Shopify-mapping tier for the "Online Store" module.

A `catalog_variants` row is a product variant (color/size) keyed by `sku`,
carrying its Shopify GIDs + the two-barcode model. It deliberately stores NO
quantity: physical on-hand is the serialized `stock_units` collection, and the
online quantity pushed to Shopify is a DERIVED conservative slice (see
services/stock_allocation + BVI_MERGE_PLAN.md A.2). Keeping a count here would
re-introduce the BVI `VariantLocation` split-brain that the bridge avoids.

Idempotent join keys (never `_id`): `sku` (primary) | `store_barcode` ->
`stock_units.barcode` (physical) | `shopify_variant_id` (Shopify side).

This is Phase-1 FOUNDATION: the PIM editor / Shopify push that WRITE through
this repo land in later phases. Only the identity round-trip lives here now.
"""
from __future__ import annotations

from typing import Dict, List, Optional
from datetime import datetime
import uuid

from .base_repository import BaseRepository


class CatalogVariantRepository(BaseRepository):
    """Repository for the `catalog_variants` collection."""

    @property
    def entity_name(self) -> str:
        return "CatalogVariant"

    @property
    def id_field(self) -> str:
        return "variant_id"

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_by_sku(self, sku: str) -> Optional[Dict]:
        """Return the variant whose `sku` matches, else None.

        `sku` is the primary identity; this is the hot lookup (stock join,
        Shopify push resolution). Fail-soft via base_repository.find_one.
        """
        if not sku:
            return None
        return self.find_one({"sku": sku})

    def list_by_parent(self, parent_product_id: str) -> List[Dict]:
        """All variants of a parent PIM product (catalog_products id), ordered
        by sku for a stable PDP option ordering. Empty list when none / no DB."""
        if not parent_product_id:
            return []
        return self.find_many(
            {"parent_product_id": parent_product_id}, sort=[("sku", 1)]
        )

    # ------------------------------------------------------------------
    # Write (idempotent upsert keyed on sku)
    # ------------------------------------------------------------------

    def upsert(self, variant: Dict) -> Optional[Dict]:
        """Insert-or-update a variant keyed on `sku` (NOT Mongo `_id`).

        Idempotent: calling twice with the same `sku` updates the existing row
        in place rather than minting a duplicate. Returns the stored doc.

        Uses an explicit find-then-insert/update (the 2-arg update_one /
        insert_one signatures) so it works against BOTH real pymongo and the
        in-memory MockCollection used in no-DB / test mode -- MockCollection has
        no `upsert=` kwarg and won't insert a missing doc on update_one.
        """
        if not variant or not variant.get("sku"):
            # `sku` is the identity; refuse a row without it rather than mint a
            # null-keyed orphan that would later collide on the unique index.
            return None

        sku = variant["sku"]
        now = datetime.now()
        existing = self.find_one({"sku": sku})

        if existing is not None:
            # Update in place. Preserve the original identity + created_at; let
            # the caller's fields win for everything else. _clean_id keeps the
            # returned doc JSON-serialisable.
            patch = {k: v for k, v in variant.items() if k not in ("variant_id", "_id", "created_at")}
            patch["updated_at"] = now
            try:
                self.collection.update_one({"sku": sku}, {"$set": patch})
            except Exception as e:  # noqa: BLE001
                print(f"Error upserting {self.entity_name} {sku}: {e}")
                return None
            merged = dict(existing)
            merged.update(patch)
            return self._clean_id(merged)

        # Insert a fresh row. base_repository.create() assigns variant_id (if
        # absent), _id, and created_at/updated_at.
        doc = dict(variant)
        if self.id_field not in doc:
            doc[self.id_field] = str(uuid.uuid4())
        return self.create(doc)
