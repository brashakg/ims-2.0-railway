"""
IMS 2.0 — Incentive Settings Repository (Pune Incentive Modules ii + iii)
==========================================================================
One doc per store. Read-heavy; writes are SUPERADMIN-only and audit-logged
through the router. Holds eligibility bands, the visufit gate, growth
targets / base rates / discount multipliers (Module iii consumes those
in the next phase).
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from .base_repository import BaseRepository


# Defaults used when no settings doc has been seeded yet.
DEFAULT_ELIGIBILITY_BANDS = [
    {"min": 0, "max": 70, "value": 0.0},
    {"min": 70, "max": 80, "value": 0.6},
    {"min": 80, "max": 95, "value": 0.8},
    {"min": 95, "max": 1000, "value": 1.0},
]
DEFAULT_VISUFIT_GATE_THRESHOLD = 0.90
DEFAULT_VISUFIT_GATE_ENABLED = True


class IncentiveSettingsRepository(BaseRepository):
    """Repository for the incentive_settings collection."""

    @property
    def entity_name(self) -> str:
        return "IncentiveSettings"

    @property
    def id_field(self) -> str:
        return "store_id"

    def get_for_store(self, store_id: str) -> Dict:
        """Return the store's settings doc, or a defaults envelope if
        none exists yet (read-only stub for Phase 1)."""
        try:
            doc = self.collection.find_one({"store_id": store_id})
        except Exception:
            doc = None
        if not doc:
            return self._defaults(store_id)
        # Backfill any missing keys with defaults so the router never
        # crashes on a partial seed.
        defaults = self._defaults(store_id)
        merged = {**defaults, **doc}
        return merged

    def _defaults(self, store_id: str) -> Dict:
        return {
            "store_id": store_id,
            "staff_weightages": {},
            "eligibility_bands": list(DEFAULT_ELIGIBILITY_BANDS),
            "growth_targets": {"L1": 0.20, "L2": 0.25, "L3": 0.30},
            "base_rates": {"L1": 0.01, "L2": 0.0125, "L3": 0.015},
            "discount_kill_threshold": 0.15,
            "discount_multipliers": [
                {"max_pct": 0.10, "multiplier": 1.5},
                {"max_pct": 0.11, "multiplier": 1.4},
                {"max_pct": 0.12, "multiplier": 1.3},
                {"max_pct": 0.13, "multiplier": 1.2},
                {"max_pct": 0.14, "multiplier": 1.1},
                {"max_pct": 0.15, "multiplier": 1.0},
            ],
            "visufit_gate_threshold": DEFAULT_VISUFIT_GATE_THRESHOLD,
            "visufit_gate_enabled": DEFAULT_VISUFIT_GATE_ENABLED,
            "supervisor_bonuses": [],
            "updated_at": None,
            "updated_by": None,
        }

    def update_eligibility_bands(
        self,
        store_id: str,
        bands: list,
        updated_by: str,
    ) -> Dict:
        """Replace eligibility_bands. Snapshot semantics live on
        points_log rows — historical rows keep their snapshot, so this
        write doesn't mutate past computations."""
        existing = self.collection.find_one({"store_id": store_id})
        now = datetime.now()
        if existing:
            self.collection.update_one(
                {"store_id": store_id},
                {
                    "$set": {
                        "eligibility_bands": bands,
                        "updated_at": now,
                        "updated_by": updated_by,
                    }
                },
            )
        else:
            doc = self._defaults(store_id)
            doc["eligibility_bands"] = bands
            doc["updated_at"] = now
            doc["updated_by"] = updated_by
            doc["_id"] = store_id
            self.collection.insert_one(doc)
        return self.get_for_store(store_id)

    def update_visufit_gate(
        self,
        store_id: str,
        threshold: Optional[float],
        enabled: Optional[bool],
        updated_by: str,
    ) -> Dict:
        """Toggle / re-tune the visufit gate."""
        patch: Dict = {"updated_at": datetime.now(), "updated_by": updated_by}
        if threshold is not None:
            patch["visufit_gate_threshold"] = threshold
        if enabled is not None:
            patch["visufit_gate_enabled"] = enabled
        existing = self.collection.find_one({"store_id": store_id})
        if existing:
            self.collection.update_one(
                {"store_id": store_id}, {"$set": patch}
            )
        else:
            doc = self._defaults(store_id)
            doc.update(patch)
            doc["_id"] = store_id
            self.collection.insert_one(doc)
        return self.get_for_store(store_id)
