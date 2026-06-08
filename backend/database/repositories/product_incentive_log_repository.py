"""
IMS 2.0 - Product-Incentive (Kicker) Log Repository (SC)
=========================================================
Append-only rupee-incentive log for qualifying product sales (e.g. ZEISS
PAL), SPIFFs (category="SPIFF"), and clawbacks (negative incentive_amount).
Rolls up monthly into the payout snapshot + the payroll feed.

Idempotency: a unique partial index on (order_id, sku) WHERE order_id is a
string blocks a double-log of the same POS line. Manual entries (order_id
null) are not constrained -- a manager can log multiple manual kickers.
See backend/database/connection.py::ensure_indexes.

No emoji (Windows cp1252).
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional
import uuid

from .base_repository import BaseRepository


class ProductIncentiveLogRepository(BaseRepository):
    """Repository for the product_incentive_log collection."""

    @property
    def entity_name(self) -> str:
        return "ProductIncentiveLog"

    @property
    def id_field(self) -> str:
        return "entry_id"

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    def log_entry(self, data: Dict) -> Optional[Dict]:
        """Insert a kicker entry. The router validates + assembles `data`;
        this stamps entry_id + timestamps + soft-delete metadata.

        Raises DuplicateKeyError (mapped to 409 by the router) when the
        unique (order_id, sku) partial index rejects a repeat POS line.
        """
        now = datetime.now()
        entry_id = uuid.uuid4().hex
        doc = {
            **data,
            "entry_id": entry_id,
            "_id": entry_id,
            "deleted_at": None,
            "deleted_by": None,
            "deleted_reason": None,
            "created_at": now,
            "updated_at": now,
        }
        self.collection.insert_one(doc)  # may raise DuplicateKeyError
        return doc

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def list_for_ym(
        self, store_id: str, ym: str, staff_id: Optional[str] = None
    ) -> List[Dict]:
        """All active kicker entries for a store-month (optionally one staff)."""
        query: Dict = {"store_id": store_id, "ym": ym, "deleted_at": None}
        if staff_id:
            query["staff_id"] = staff_id
        try:
            cursor = self.collection.find(query)
            try:
                cursor = cursor.sort([("date_str", -1)])
            except Exception:
                pass
            return list(cursor)
        except Exception:
            return []

    def staff_total_for_ym(self, store_id: str, ym: str) -> Dict[str, float]:
        """{staff_id: signed_rupee_total} for the store-month. Sums the signed
        incentive_amount so a clawback (negative entry) reduces the total."""
        out: Dict[str, float] = {}
        for e in self.list_for_ym(store_id, ym):
            sid = e.get("staff_id")
            if not sid:
                continue
            out[sid] = round(out.get(sid, 0.0) + float(e.get("incentive_amount") or 0.0), 2)
        return out

    def find_by_entry_id(self, entry_id: str) -> Optional[Dict]:
        try:
            return self.collection.find_one(
                {"entry_id": entry_id, "deleted_at": None}
            )
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Soft-delete (a clawback is preferred for paid lines, but a wrongly
    # entered row can be removed by a manager with reason + audit).
    # ------------------------------------------------------------------
    def soft_delete(self, entry_id: str, deleted_by: str, reason: str) -> bool:
        existing = self.find_by_entry_id(entry_id)
        if not existing:
            return False
        try:
            self.collection.update_one(
                {"entry_id": entry_id},
                {
                    "$set": {
                        "deleted_at": datetime.now(),
                        "deleted_by": deleted_by,
                        "deleted_reason": reason,
                        "updated_at": datetime.now(),
                    }
                },
            )
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[KICKER] soft_delete failed: {e}")
            return False
