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
