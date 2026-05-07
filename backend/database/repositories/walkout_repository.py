"""
IMS 2.0 — Walkout Repository (Module i, Phase 1)
=================================================
Data access for the Walkouts collection. Lifts the Pune-incentive
Walkouts Log out of Excel into IMS 2.0.

Design notes (from docs/PUNE_INCENTIVE_BUILD_PLAN.md):
  - id: WO-{STORE3}-{YYYY}-{6HEX} (e.g. WO-PNE-2026-A1B2C3)
  - store_id stamped from session.active_store_id; READ-ONLY on append
  - mobile is 10-digit, unique-ish per store — used by Phase 2 to spot
    repeat walkouts on the same mobile within N days
  - followups + result + soft-delete metadata initialized empty here;
    populated by Phase 3
"""
from datetime import datetime
from typing import Dict, List, Optional
import uuid

from .base_repository import BaseRepository


class WalkoutRepository(BaseRepository):
    """Repository for Walkouts operations (Pune Incentive Module i)."""

    @property
    def entity_name(self) -> str:
        return "Walkout"

    @property
    def id_field(self) -> str:
        return "walkout_id"

    # ------------------------------------------------------------------
    # ID generation
    # ------------------------------------------------------------------
    def _generate_walkout_id(self, store_id: str) -> str:
        """
        Build WO-{STORE3}-{YYYY}-{6HEX}.

          'BV-PNE-01'  -> 'PNE'
          'WO-DHN-01'  -> 'DHN'
          'BV-BOK-01'  -> 'BOK'
          unset / weird -> 'XXX'

        Mirrors the order-number generator's defensive shape so a
        pathological store_id can't produce empty slots like
        'WO--2026-...' or 'WO-BV-2026-...' (the legacy double-prefix
        bug from order IDs).
        """
        raw = (store_id or "").strip().upper()
        # Drop the chain prefix (BV / WO / BVO) so prefix focuses on
        # the store part.
        parts = [p for p in raw.split("-") if p and p not in ("BV", "WO", "BVO")]
        if parts:
            code = parts[0][:3]
        else:
            code = "XXX"
        # Sanitize to alnum only; fallback if empty.
        code = "".join(c for c in code if c.isalnum()) or "XXX"
        year = datetime.utcnow().year
        suffix = uuid.uuid4().hex[:6].upper()
        return f"WO-{code}-{year}-{suffix}"

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    def create_walkout(self, data: Dict) -> Optional[Dict]:
        """
        Create a walkout row. The router fills in semantic fields; this
        method stamps `walkout_id`, `created_at`, `updated_at`, `_id`,
        and ensures embedded follow-ups + soft-delete metadata are
        present (initialized empty).

        Returns the stored doc on success, None on insertion failure.
        """
        now = datetime.now()
        walkout_id = self._generate_walkout_id(data.get("store_id", ""))

        doc = {
            **data,
            "walkout_id": walkout_id,
            "_id": walkout_id,
            # Embedded sub-records (Phase 3+)
            "followups": data.get("followups", []),
            "result": data.get("result"),
            "result_set_at": None,
            "result_set_by": None,
            "converted_order_id": None,
            # Soft-delete metadata
            "deleted_at": None,
            "deleted_by": None,
            "delete_reason": None,
            # Timestamps
            "created_at": now,
            "updated_at": now,
        }

        try:
            self.collection.insert_one(doc)
        except Exception as e:
            # Don't swallow the audit trail of failures — caller may want it.
            print(f"[WALKOUT] insert failed: {e}")
            return None
        return doc

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------
    def find_by_walkout_id(self, walkout_id: str) -> Optional[Dict]:
        """Get one walkout by its id, excluding soft-deleted rows."""
        try:
            return self.collection.find_one(
                {"walkout_id": walkout_id, "deleted_at": None}
            )
        except Exception:
            return None

    def find_by_mobile_recent(self, mobile: str, days: int = 30) -> Optional[Dict]:
        """
        Stub for Phase 2 — return the most recent walkout for a given
        mobile within the last N days. Phase 1 callers don't need this
        yet (the dedupe-by-mobile logic for walk-in counters lives in
        Phase 4); returning None keeps the contract honest until then.
        """
        return None

    # ------------------------------------------------------------------
    # Phase 2 — list / update / soft-delete
    # ------------------------------------------------------------------
    def _build_list_filter(
        self,
        store_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        sales_person_id: Optional[str] = None,
        primary_walkout_reason: Optional[str] = None,
        result: Optional[str] = None,
    ) -> Dict:
        """Compose a Mongo filter dict from the supported list filters.

        Always excludes soft-deleted rows (deleted_at=None). date_from
        / date_to are inclusive on `date_str` (YYYY-MM-DD) so date math
        works with ISO sort order.
        """
        f: Dict = {"deleted_at": None}
        if store_id:
            f["store_id"] = store_id
        if sales_person_id:
            f["sales_person_id"] = sales_person_id
        if primary_walkout_reason:
            f["primary_walkout_reason"] = primary_walkout_reason
        if result is not None:
            # Allow filtering for "no result yet" via the literal string "none"
            f["result"] = None if result.lower() == "none" else result
        if date_from or date_to:
            date_filter: Dict = {}
            if date_from:
                date_filter["$gte"] = date_from
            if date_to:
                date_filter["$lte"] = date_to
            f["date_str"] = date_filter
        return f

    def list_walkouts(
        self,
        *,
        store_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        sales_person_id: Optional[str] = None,
        primary_walkout_reason: Optional[str] = None,
        result: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[Dict]:
        """List walkouts ordered newest-first by date_str, then created_at."""
        f = self._build_list_filter(
            store_id=store_id,
            date_from=date_from,
            date_to=date_to,
            sales_person_id=sales_person_id,
            primary_walkout_reason=primary_walkout_reason,
            result=result,
        )
        try:
            cursor = self.collection.find(f)
            try:
                cursor = cursor.sort([("date_str", -1), ("created_at", -1)])
            except Exception:
                pass
            try:
                cursor = cursor.skip(int(skip or 0))
            except Exception:
                pass
            try:
                cursor = cursor.limit(int(limit or 50))
            except Exception:
                pass
            return list(cursor)
        except Exception as e:
            print(f"[WALKOUT] list failed: {e}")
            return []

    def count_walkouts(
        self,
        *,
        store_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        sales_person_id: Optional[str] = None,
        primary_walkout_reason: Optional[str] = None,
        result: Optional[str] = None,
    ) -> int:
        f = self._build_list_filter(
            store_id=store_id,
            date_from=date_from,
            date_to=date_to,
            sales_person_id=sales_person_id,
            primary_walkout_reason=primary_walkout_reason,
            result=result,
        )
        try:
            return int(self.collection.count_documents(f))
        except Exception:
            # Fall back to scanning the iterable (FakeCollection in tests).
            try:
                return sum(1 for _ in self.collection.find(f))
            except Exception:
                return 0

    def update_walkout(
        self, walkout_id: str, diff: Dict, updated_by: Optional[str] = None
    ) -> Optional[Dict]:
        """Apply a partial update to a non-deleted walkout. Stamps
        `updated_at`/`updated_by`. Returns the post-update doc or None."""
        if not diff:
            return self.find_by_walkout_id(walkout_id)
        update_doc = dict(diff)
        update_doc["updated_at"] = datetime.now()
        if updated_by is not None:
            update_doc["updated_by"] = updated_by
        try:
            self.collection.update_one(
                {"walkout_id": walkout_id, "deleted_at": None},
                {"$set": update_doc},
            )
        except Exception as e:
            print(f"[WALKOUT] update failed: {e}")
            return None
        return self.find_by_walkout_id(walkout_id)

    def soft_delete_walkout(
        self,
        walkout_id: str,
        deleted_by: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> bool:
        """Mark a walkout as soft-deleted. Returns True if the row was
        active prior to this call, False if it was missing or already
        deleted."""
        existing = self.find_by_walkout_id(walkout_id)
        if not existing:
            return False
        try:
            self.collection.update_one(
                {"walkout_id": walkout_id},
                {
                    "$set": {
                        "deleted_at": datetime.now(),
                        "deleted_by": deleted_by,
                        "delete_reason": reason,
                        "updated_at": datetime.now(),
                        "updated_by": deleted_by,
                    }
                },
            )
            return True
        except Exception as e:
            print(f"[WALKOUT] soft-delete failed: {e}")
            return False
