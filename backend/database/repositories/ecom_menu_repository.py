"""
IMS 2.0 - E-commerce Menu Repository  (BVI Phase 3 -- FLAGSHIP #2)
=================================================================
Data access for the `ecom_menus` collection: BVI's Shopify navigation Menus +
the mega-menu, folded into IMS Mongo (BVI_MERGE_PLAN.md A.1 / Phase 3).

PUSH-DARK: this repo STORES + EDITS menus inside IMS only. No Shopify network
writes happen in Phase 3 (the GraphQL `menuUpdate` push is Phase 5). Every write
here sets `locally_modified=True` so the Phase-5 push queue knows the row is
dirty.

A menu doc carries an EMBEDDED, recursive item tree in `items`: each MenuItem
node has its own `children` array of the same shape, so an N-level mega-menu is
ONE document (Mongo-natural; BVI's relational MenuItem table with a parentId
self-relation flattens into this nested array). The tree helpers
(add/move/remove a node + renumber sibling positions) walk that embedded tree.

Idempotent join keys (never Mongo `_id`): `handle` (menu slug, primary) |
`shopify_menu_id` (Shopify side). `get_by_handle` is the re-import key. Item
nodes are addressed by their own `id` (a uuid), NEVER by Mongo `_id`.

Fail-soft throughout (mirrors base_repository + ecom_collection_repository): any
error returns a safe empty value and prints, never raises to the caller. All
writes go through the 2-arg insert_one / update_one signatures so they work
against BOTH real pymongo and the in-memory MockCollection used in no-DB / test
mode.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from datetime import datetime
import uuid

from .base_repository import BaseRepository


# The presentation / linkage fields a MenuItem node may carry, beyond the
# structural ones (id / parent_id / position / children). Used to normalize an
# inbound node so a well-formed doc is always stored.
_ITEM_FIELDS = (
    "title",
    "item_type",
    "url",
    "resource_id",
    "tags_filter",
    "shopify_item_id",
    "icon_url",
    "banner_url",
    "badge_text",
    "badge_color",
    "pinned_to_top",
)


class EcomMenuRepository(BaseRepository):
    """Repository for the `ecom_menus` collection."""

    @property
    def entity_name(self) -> str:
        return "EcomMenu"

    @property
    def id_field(self) -> str:
        return "menu_id"

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_by_id(self, menu_id: str) -> Optional[Dict]:
        """Return the menu by its internal id, else None. Fail-soft."""
        if not menu_id:
            return None
        return self.find_one({self.id_field: menu_id})

    def get_by_handle(self, handle: str) -> Optional[Dict]:
        """Return the menu whose slug `handle` matches, else None.

        `handle` is the unique slug + the idempotent re-import key. Fail-soft
        via base_repository.find_one.
        """
        if not handle:
            return None
        return self.find_one({"handle": handle})

    def list(
        self,
        active: Optional[bool] = None,
        is_default: Optional[bool] = None,
        skip: int = 0,
        limit: int = 200,
    ) -> List[Dict]:
        """List menus, optionally filtered.

        Filters are AND-combined and each is applied only when provided (None =
        "don't filter on this"). Ordered by handle for stability. Empty list
        when none / no DB.
        """
        query: Dict = {}
        if active is not None:
            query["active"] = active
        if is_default is not None:
            query["is_default"] = is_default
        return self.find_many(query, sort=[("handle", 1)], skip=skip, limit=limit)

    # ------------------------------------------------------------------
    # Create / update / delete (whole menu, incl. the items tree)
    # ------------------------------------------------------------------

    def create(self, data: Dict) -> Optional[Dict]:
        """Create a menu. Requires `handle` (the unique slug); refuses a row
        without it rather than mint a null-keyed orphan that would later collide
        on the unique-sparse handle index. Defaults are applied for the flags so
        a minimal payload yields a well-formed doc.

        Any inbound `items` tree is normalized + renumbered (ids minted, parent
        links + sibling positions fixed) so the stored tree is always consistent.

        PUSH-DARK: new menus are born `locally_modified=True` (nothing has been
        pushed to Shopify yet).
        """
        if not data or not data.get("handle"):
            return None

        doc = dict(data)
        doc.setdefault(self.id_field, str(uuid.uuid4()))
        doc.setdefault("is_default", False)
        doc.setdefault("active", True)
        # Normalize/renumber the embedded tree (empty for a fresh menu).
        doc["items"] = self._normalize_tree(doc.get("items") or [], parent_id=None)
        # Dirty from birth -- not yet synced to Shopify.
        doc.setdefault("locally_modified", True)
        # base_repository.create assigns _id + created_at/updated_at and inserts.
        return super().create(doc)

    def update(self, menu_id: str, data: Dict) -> bool:
        """Patch a menu by id (title / active / is_default / the full items tree).
        Identity/immutable fields are stripped so a caller can't overwrite the id
        or created_at. If `items` is supplied it REPLACES the whole tree (and is
        normalized + renumbered first). Any update marks the row dirty
        (`locally_modified=True`) for the Phase-5 push queue unless the caller
        explicitly set it. Returns True on a real change.
        """
        if not menu_id or not data:
            return False
        patch = {
            k: v
            for k, v in data.items()
            if k not in (self.id_field, "_id", "created_at", "created_by")
        }
        if not patch:
            return False
        # A whole-tree replacement is normalized + renumbered before persist.
        if "items" in patch:
            patch["items"] = self._normalize_tree(patch.get("items") or [], parent_id=None)
        patch.setdefault("locally_modified", True)
        return super().update(menu_id, patch)

    def delete(self, menu_id: str) -> bool:
        """Hard-delete a menu by id. Fail-soft via base_repository."""
        if not menu_id:
            return False
        return super().delete(menu_id)

    # ------------------------------------------------------------------
    # Item-node helpers (operate on the embedded recursive `items` tree)
    # ------------------------------------------------------------------

    def add_item(
        self,
        menu_id: str,
        item: Dict,
        parent_id: Optional[str] = None,
        position: Optional[int] = None,
    ) -> Optional[Dict]:
        """Add a MenuItem node under `parent_id` (None = top level), at `position`
        among its new siblings (None = append at the end). A fresh `id` is minted
        for the node (any caller-supplied id is ignored to keep ids server-owned).
        Sibling positions are renumbered. Returns the updated menu doc, or None on
        failure / unknown menu / unknown parent.
        """
        if not menu_id or not isinstance(item, dict):
            return None
        menu = self.find_one({self.id_field: menu_id})
        if menu is None:
            return None
        items = list(menu.get("items") or [])

        node = self._new_node(item, parent_id=parent_id)

        if parent_id is None:
            siblings = items
        else:
            parent = self._find_node(items, parent_id)
            if parent is None:
                return None  # unknown parent -> caller error, not a silent top-add
            parent.setdefault("children", [])
            siblings = parent["children"]

        self._insert_at(siblings, node, position)
        # Renumber the whole tree so positions + parent links are consistent.
        items = self._normalize_tree(items, parent_id=None)
        return self._save_items(menu_id, items)

    def move_item(
        self,
        menu_id: str,
        item_id: str,
        new_parent_id: Optional[str] = None,
        position: Optional[int] = None,
    ) -> Optional[Dict]:
        """Move an existing node to a new parent (None = top level) + position.
        Guards against moving a node under itself or one of its own descendants
        (which would orphan a subtree). Renumbers afterwards. Returns the updated
        menu doc, or None on failure / unknown menu / unknown node / unknown
        new parent / illegal cycle.
        """
        if not menu_id or not item_id:
            return None
        menu = self.find_one({self.id_field: menu_id})
        if menu is None:
            return None
        items = list(menu.get("items") or [])

        # The node must exist before we detach it.
        node = self._find_node(items, item_id)
        if node is None:
            return None
        # Cycle guard: can't reparent under self or a descendant.
        if new_parent_id is not None and (
            new_parent_id == item_id or self._is_descendant(node, new_parent_id)
        ):
            return None
        # New parent (if any) must exist.
        if new_parent_id is not None and self._find_node(items, new_parent_id) is None:
            return None

        detached = self._detach_node(items, item_id)
        if detached is None:
            return None
        detached["parent_id"] = new_parent_id

        if new_parent_id is None:
            siblings = items
        else:
            parent = self._find_node(items, new_parent_id)
            # Parent existence was checked above; re-find post-detach.
            if parent is None:
                return None
            parent.setdefault("children", [])
            siblings = parent["children"]

        self._insert_at(siblings, detached, position)
        items = self._normalize_tree(items, parent_id=None)
        return self._save_items(menu_id, items)

    def remove_item(self, menu_id: str, item_id: str) -> Optional[Dict]:
        """Remove a node (and its whole subtree) from the menu. Idempotent: a
        node that isn't present is a no-op success. Renumbers the remaining
        siblings. Returns the updated menu doc, or None on failure / unknown menu.
        """
        if not menu_id or not item_id:
            return None
        menu = self.find_one({self.id_field: menu_id})
        if menu is None:
            return None
        items = list(menu.get("items") or [])
        self._detach_node(items, item_id)  # no-op if absent (idempotent)
        items = self._normalize_tree(items, parent_id=None)
        return self._save_items(menu_id, items)

    def update_item(self, menu_id: str, item_id: str, fields: Dict) -> Optional[Dict]:
        """Patch the presentation/linkage fields of a single node in place
        (title / item_type / url / badges / icon / pinned_to_top / ...). Structural
        fields (id / parent_id / position / children) are NOT patchable here --
        use move_item for re-parenting. Returns the updated menu doc, or None on
        failure / unknown menu / unknown node.
        """
        if not menu_id or not item_id or not isinstance(fields, dict):
            return None
        menu = self.find_one({self.id_field: menu_id})
        if menu is None:
            return None
        items = list(menu.get("items") or [])
        node = self._find_node(items, item_id)
        if node is None:
            return None
        for k in _ITEM_FIELDS:
            if k in fields:
                node[k] = fields[k]
        items = self._normalize_tree(items, parent_id=None)
        return self._save_items(menu_id, items)

    # ------------------------------------------------------------------
    # Internal tree mechanics (pure; operate on in-memory lists/dicts)
    # ------------------------------------------------------------------

    def _new_node(self, item: Dict, parent_id: Optional[str]) -> Dict:
        """Build a normalized node from an inbound dict: server-minted id, the
        carried presentation fields, an empty children list. Any nested
        `children` supplied on the inbound item is normalized recursively so a
        caller can add a whole subtree in one call."""
        node: Dict = {"id": str(uuid.uuid4()), "parent_id": parent_id}
        for k in _ITEM_FIELDS:
            if k in item:
                node[k] = item[k]
        node.setdefault("title", item.get("title", ""))
        node.setdefault("pinned_to_top", bool(item.get("pinned_to_top", False)))
        # Recurse into a supplied subtree (each child gets a fresh id + this
        # node's id as parent; positions fixed by the later _normalize_tree pass).
        kids = item.get("children") or []
        node["children"] = [self._new_node(k, parent_id=node["id"]) for k in kids]
        return node

    def _normalize_tree(self, items: List[Dict], parent_id: Optional[str]) -> List[Dict]:
        """Return a normalized COPY of an item list: every node gets an `id`
        (minted if missing), the correct `parent_id`, a 0-based `position` among
        its siblings, a normalized `pinned_to_top` bool, and a recursively
        normalized `children` list. This is the single place positions + parent
        links are made consistent (called after every structural mutation and on
        whole-tree create/update)."""
        normalized: List[Dict] = []
        for pos, raw in enumerate(items or []):
            if not isinstance(raw, dict):
                continue
            node = dict(raw)
            node["id"] = node.get("id") or str(uuid.uuid4())
            node["parent_id"] = parent_id
            node["position"] = pos
            node["pinned_to_top"] = bool(node.get("pinned_to_top", False))
            node["children"] = self._normalize_tree(
                node.get("children") or [], parent_id=node["id"]
            )
            normalized.append(node)
        return normalized

    def _find_node(self, items: List[Dict], item_id: str) -> Optional[Dict]:
        """Depth-first search for the node with `id == item_id`. Returns the live
        dict (so callers can mutate it in place), or None."""
        for node in items or []:
            if node.get("id") == item_id:
                return node
            found = self._find_node(node.get("children") or [], item_id)
            if found is not None:
                return found
        return None

    def _detach_node(self, items: List[Dict], item_id: str) -> Optional[Dict]:
        """Remove the node with `id == item_id` from wherever it lives in the
        tree and return it (with its subtree intact), or None if not found.
        Mutates `items` in place."""
        for i, node in enumerate(items or []):
            if node.get("id") == item_id:
                return items.pop(i)
            detached = self._detach_node(node.get("children") or [], item_id)
            if detached is not None:
                return detached
        return None

    @staticmethod
    def _insert_at(siblings: List[Dict], node: Dict, position: Optional[int]) -> None:
        """Insert `node` into `siblings` at `position` (clamped to [0, len]); a
        None or out-of-range position appends. Positions are fixed afterwards by
        _normalize_tree, so this only governs ordering."""
        if position is None:
            siblings.append(node)
            return
        try:
            pos = int(position)
        except (TypeError, ValueError):
            siblings.append(node)
            return
        pos = max(0, min(pos, len(siblings)))
        siblings.insert(pos, node)

    def _is_descendant(self, node: Dict, candidate_id: str) -> bool:
        """True if `candidate_id` is somewhere in `node`'s subtree (so reparenting
        node under candidate would create a cycle)."""
        return self._find_node(node.get("children") or [], candidate_id) is not None

    def _save_items(self, menu_id: str, items: List[Dict]) -> Optional[Dict]:
        """Persist a rewritten items tree + mark dirty. Returns the merged doc
        (JSON-safe) or None on error. Mirrors ecom_collection_repository._save_products."""
        try:
            self.collection.update_one(
                {self.id_field: menu_id},
                {
                    "$set": {
                        "items": items,
                        "locally_modified": True,
                        "updated_at": datetime.now(),
                    }
                },
            )
        except Exception as e:  # noqa: BLE001 -- fail-soft
            print(f"Error updating {self.entity_name} items {menu_id}: {e}")
            return None
        return self.find_one({self.id_field: menu_id})

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @staticmethod
    def count_nodes(items: List[Dict]) -> int:
        """Total number of nodes in an items tree (top-level + all descendants).
        Pure helper used by the router summary + tests."""
        total = 0
        for node in items or []:
            total += 1
            total += EcomMenuRepository.count_nodes(node.get("children") or [])
        return total
