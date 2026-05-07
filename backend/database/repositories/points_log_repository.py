"""
IMS 2.0 — Points Log Repository (Module ii, Daily Points + MTD Scoring)
========================================================================
Append-only points log with controlled soft-delete. The DB-level
"refuse second save" enforcement lives on a unique partial index over
(store_id, date_str, staff_id) where deleted_at is null — see
backend/database/connection.py::ensure_indexes.

Design notes (docs/PUNE_INCENTIVE_BUILD_PLAN.md §Module ii):
  - log_id format: PL-{STORE3}-{YYYY-MM-DD}-{STAFF}-{6HEX}
  - eligibility + eligibility_thresholds_used are SNAPSHOTTED at
    write-time (settings change later doesn't rewrite history)
  - visufit_gate_applied + visufit_usage_pct_mtd are stamped on the
    row so the gate's effect is visible from the row itself
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

from .base_repository import BaseRepository


class PointsLogRepository(BaseRepository):
    """Repository for the points_log collection."""

    @property
    def entity_name(self) -> str:
        return "PointsLog"

    @property
    def id_field(self) -> str:
        return "log_id"

    # ------------------------------------------------------------------
    # ID generation
    # ------------------------------------------------------------------
    @staticmethod
    def _store_code(store_id: str) -> str:
        raw = (store_id or "").strip().upper()
        parts = [p for p in raw.split("-") if p and p not in ("BV", "WO", "BVO")]
        code = (parts[0][:3] if parts else "XXX") or "XXX"
        return "".join(c for c in code if c.isalnum()) or "XXX"

    @staticmethod
    def _staff_code(staff_id: str) -> str:
        raw = (staff_id or "").strip().upper()
        # Strip "USER-" prefix if present
        if raw.startswith("USER-"):
            raw = raw[5:]
        return "".join(c for c in raw if c.isalnum())[:6] or "XXXX"

    def _generate_log_id(self, store_id: str, date_str: str, staff_id: str) -> str:
        return (
            f"PL-{self._store_code(store_id)}-{date_str}-"
            f"{self._staff_code(staff_id)}-{uuid.uuid4().hex[:6].upper()}"
        )

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    def create_points_log(self, data: Dict) -> Optional[Dict]:
        """Insert a new row. The router validates + assembles `data`;
        this method stamps log_id + timestamps + soft-delete metadata.

        Raises if the unique partial index rejects the write
        (DuplicateKeyError) so the router can return 409.
        """
        now = datetime.now()
        log_id = self._generate_log_id(
            data.get("store_id", ""),
            data.get("date_str", ""),
            data.get("staff_id", ""),
        )
        doc = {
            **data,
            "log_id": log_id,
            "_id": log_id,
            "deleted_at": None,
            "deleted_by": None,
            "delete_reason": None,
            "created_at": now,
            "updated_at": now,
        }
        self.collection.insert_one(doc)  # may raise DuplicateKeyError
        return doc

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def find_by_log_id(self, log_id: str) -> Optional[Dict]:
        try:
            return self.collection.find_one(
                {"log_id": log_id, "deleted_at": None}
            )
        except Exception:
            return None

    def find_by_date_and_staff(
        self, store_id: str, date_str: str, staff_id: str
    ) -> Optional[Dict]:
        try:
            return self.collection.find_one({
                "store_id": store_id,
                "date_str": date_str,
                "staff_id": staff_id,
                "deleted_at": None,
            })
        except Exception:
            return None

    def list_by_date(
        self, store_id: str, date_str: str
    ) -> List[Dict]:
        try:
            cursor = self.collection.find({
                "store_id": store_id,
                "date_str": date_str,
                "deleted_at": None,
            })
            try:
                cursor = cursor.sort([("staff_id", 1)])
            except Exception:
                pass
            return list(cursor)
        except Exception:
            return []

    def list_by_staff_range(
        self,
        store_id: str,
        staff_id: str,
        date_from: str,
        date_to: str,
    ) -> List[Dict]:
        try:
            cursor = self.collection.find({
                "store_id": store_id,
                "staff_id": staff_id,
                "deleted_at": None,
                "date_str": {"$gte": date_from, "$lte": date_to},
            })
            try:
                cursor = cursor.sort([("date_str", -1)])
            except Exception:
                pass
            return list(cursor)
        except Exception:
            return []

    def list_for_mtd(
        self, store_id: str, date_from: str, date_to: str
    ) -> List[Dict]:
        try:
            cursor = self.collection.find({
                "store_id": store_id,
                "deleted_at": None,
                "date_str": {"$gte": date_from, "$lte": date_to},
            })
            return list(cursor)
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Soft-delete
    # ------------------------------------------------------------------
    def soft_delete(
        self, log_id: str, deleted_by: str, reason: str
    ) -> bool:
        existing = self.find_by_log_id(log_id)
        if not existing:
            return False
        try:
            self.collection.update_one(
                {"log_id": log_id},
                {
                    "$set": {
                        "deleted_at": datetime.now(),
                        "deleted_by": deleted_by,
                        "delete_reason": reason,
                        "updated_at": datetime.now(),
                    }
                },
            )
            return True
        except Exception as e:
            print(f"[POINTS] soft_delete failed: {e}")
            return False
