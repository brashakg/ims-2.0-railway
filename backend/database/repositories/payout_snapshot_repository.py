"""
IMS 2.0 — Payout Snapshot Repository (Pune Incentive Module iii)
=================================================================
One row per (store, year, month) lock. Multiple DRAFTs allowed; only
ONE LOCKED snapshot per (store, year, month) — enforced at the DB
level by a unique partial index in connection.py::ensure_indexes:

    db.payout_snapshots.createIndex(
      { store_id: 1, year: 1, month: 1 },
      { unique: true, partialFilterExpression: { status: "LOCKED" } }
    )

Lifecycle: DRAFT → LOCKED → PAID (no transitions backwards).
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional
import uuid

from .base_repository import BaseRepository


class PayoutSnapshotRepository(BaseRepository):
    """Repository for the payout_snapshots collection."""

    @property
    def entity_name(self) -> str:
        return "PayoutSnapshot"

    @property
    def id_field(self) -> str:
        return "snapshot_id"

    @staticmethod
    def _store_code(store_id: str) -> str:
        raw = (store_id or "").strip().upper()
        parts = [p for p in raw.split("-") if p and p not in ("BV", "WO", "BVO")]
        code = (parts[0][:3] if parts else "XXX") or "XXX"
        return "".join(c for c in code if c.isalnum()) or "XXX"

    def _generate_snapshot_id(
        self, store_id: str, year: int, month: int, status: str
    ) -> str:
        # LOCKED snapshots get the canonical PAY-{STORE3}-{YYYY-MM} id
        # so the human refs in audit logs are stable. DRAFTs get a
        # unique suffix because multiple DRAFTs may coexist.
        base = f"PAY-{self._store_code(store_id)}-{year:04d}-{month:02d}"
        if status == "LOCKED":
            return base
        return f"{base}-DRAFT-{uuid.uuid4().hex[:6].upper()}"

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    def create_snapshot(self, data: Dict, *, status: str = "LOCKED") -> Optional[Dict]:
        """Insert a new snapshot. Caller must provide store_id, year,
        month, plus the full computed envelope. Raises on
        DuplicateKeyError so the router can map to 409 — that's the
        DB-level enforcement of "only one LOCKED per month"."""
        now = datetime.now()
        snapshot_id = self._generate_snapshot_id(
            data.get("store_id", ""),
            int(data.get("year") or 0),
            int(data.get("month") or 0),
            status,
        )
        doc = {
            **data,
            "snapshot_id": snapshot_id,
            "_id": snapshot_id,
            "status": status,
            "locked_at": now if status == "LOCKED" else None,
            "locked_by": data.get("locked_by") if status == "LOCKED" else None,
            "paid_at": None,
            "paid_by": None,
            "created_at": now,
            "updated_at": now,
        }
        self.collection.insert_one(doc)
        return doc

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def find_by_id(self, snapshot_id: str) -> Optional[Dict]:
        try:
            return self.collection.find_one({"snapshot_id": snapshot_id})
        except Exception:
            return None

    def find_locked(
        self, store_id: str, year: int, month: int
    ) -> Optional[Dict]:
        try:
            return self.collection.find_one({
                "store_id": store_id, "year": year, "month": month,
                "status": {"$in": ["LOCKED", "PAID"]},
            })
        except Exception:
            return None

    def list_for_store_year(
        self, store_id: str, year: int
    ) -> List[Dict]:
        try:
            cursor = self.collection.find({
                "store_id": store_id, "year": year,
            })
            try:
                cursor = cursor.sort([("month", -1)])
            except Exception:
                pass
            return list(cursor)
        except Exception:
            return []

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------
    def mark_paid(self, snapshot_id: str, paid_by: str) -> Optional[Dict]:
        existing = self.find_by_id(snapshot_id)
        if not existing:
            return None
        if existing.get("status") != "LOCKED":
            return None
        try:
            self.collection.update_one(
                {"snapshot_id": snapshot_id},
                {
                    "$set": {
                        "status": "PAID",
                        "paid_at": datetime.now(),
                        "paid_by": paid_by,
                        "updated_at": datetime.now(),
                    }
                },
            )
        except Exception as e:
            print(f"[PAYOUT] mark_paid failed: {e}")
            return None
        return self.find_by_id(snapshot_id)
