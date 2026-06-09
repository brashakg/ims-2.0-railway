"""
IMS 2.0 — Walk-In Counter Repository (Module i, Phase 4)
=========================================================
Per-store-per-day walk-in tally used by the walkouts dashboard's
conversion math (Module ii consumes this via P5's conversion-feed).

Design notes (docs/PUNE_INCENTIVE_BUILD_PLAN.md):
  - one doc per (store_id, date_str), id = "{store_id}_{date_str}"
  - pos_auto_count: bumped by orders.py on order intake, dedup'd
    by mobile within the same day (same customer's second order
    same day doesn't double-count)
  - manual_topup: store-floor "browse-and-leave" additions, with
    audited log entries
  - per_staff: how many of the day's walk-ins each salesperson got
  - mobiles_seen: hidden field used as a dedup set (lifted out of
    the dashboard response shape so it doesn't leak)
"""
from datetime import datetime, date as date_type
from typing import Dict, List, Optional

from .base_repository import BaseRepository


class WalkInCounterRepository(BaseRepository):
    """Repository for the walk_in_counters collection."""

    @property
    def entity_name(self) -> str:
        return "WalkInCounter"

    @property
    def id_field(self) -> str:
        return "_id"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _doc_id(store_id: str, date_str: str) -> str:
        return f"{store_id}_{date_str}"

    @staticmethod
    def _today_str() -> str:
        return date_type.today().isoformat()

    def _get_or_init(self, store_id: str, date_str: str) -> Dict:
        """Fetch the doc for this (store, date), creating an empty
        skeleton if it doesn't exist yet. The skeleton is committed
        to the DB so subsequent updates are simple $inc / $set ops."""
        doc_id = self._doc_id(store_id, date_str)
        try:
            existing = self.collection.find_one({"_id": doc_id})
        except Exception:
            existing = None
        if existing:
            return existing
        skeleton = {
            "_id": doc_id,
            "store_id": store_id,
            "date_str": date_str,
            "pos_auto_count": 0,
            "manual_topup": 0,
            "manual_log": [],
            "total": 0,
            "per_staff": {},
            "mobiles_seen": [],
            "updated_at": datetime.now(),
        }
        try:
            self.collection.insert_one(skeleton)
        except Exception as e:
            print(f"[WALKIN] init failed: {e}")
        return skeleton

    # ------------------------------------------------------------------
    # Auto-increment (POS hook)
    # ------------------------------------------------------------------
    def auto_increment(
        self,
        *,
        store_id: str,
        sales_person_id: str,
        mobile: Optional[str] = None,
        date_str: Optional[str] = None,
    ) -> Dict:
        """Bump the counter for a customer interaction. Deduplicates
        per (mobile, day) — second order of the day for the same mobile
        is a no-op on the count (but still tracked under per_staff if
        the mobile hasn't been attributed to that staff yet)."""
        if not store_id:
            return {"deduped": True, "reason": "no_store_id"}
        date_str = date_str or self._today_str()
        doc = self._get_or_init(store_id, date_str)

        mobiles = list(doc.get("mobiles_seen") or [])
        already_seen = bool(mobile) and mobile in mobiles

        per_staff = dict(doc.get("per_staff") or {})
        new_pos_auto = int(doc.get("pos_auto_count") or 0)
        if not already_seen:
            new_pos_auto += 1
            if mobile:
                mobiles.append(mobile)
            if sales_person_id:
                per_staff[sales_person_id] = int(per_staff.get(sales_person_id, 0)) + 1

        new_total = new_pos_auto + int(doc.get("manual_topup") or 0)

        try:
            self.collection.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "pos_auto_count": new_pos_auto,
                        "mobiles_seen": mobiles,
                        "per_staff": per_staff,
                        "total": new_total,
                        "updated_at": datetime.now(),
                    }
                },
            )
        except Exception as e:
            print(f"[WALKIN] auto_increment update failed: {e}")
        return {
            "store_id": store_id,
            "date_str": date_str,
            "deduped": already_seen,
            "pos_auto_count": new_pos_auto,
            "total": new_total,
        }

    # ------------------------------------------------------------------
    # Manual top-up
    # ------------------------------------------------------------------
    def manual_topup(
        self,
        *,
        store_id: str,
        added_by: str,
        delta: int,
        reason: str,
        sales_person_id: Optional[str] = None,
        date_str: Optional[str] = None,
    ) -> Dict:
        """Add `delta` to the day's counter (e.g. a customer who only
        browsed and never made it to a POS interaction). Always recorded
        in manual_log even if `delta == 0`."""
        if delta == 0:
            return {"updated": False, "reason": "delta_zero"}
        date_str = date_str or self._today_str()
        doc = self._get_or_init(store_id, date_str)

        manual_log = list(doc.get("manual_log") or [])
        manual_log.append({
            "added_by": added_by,
            "added_at": datetime.now(),
            "delta": delta,
            "reason": reason,
            "sales_person_id": sales_person_id,
        })
        new_topup = int(doc.get("manual_topup") or 0) + int(delta)
        per_staff = dict(doc.get("per_staff") or {})
        if sales_person_id:
            per_staff[sales_person_id] = int(per_staff.get(sales_person_id, 0)) + int(delta)
        new_total = int(doc.get("pos_auto_count") or 0) + new_topup

        try:
            self.collection.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "manual_topup": new_topup,
                        "manual_log": manual_log,
                        "per_staff": per_staff,
                        "total": new_total,
                        "updated_at": datetime.now(),
                    }
                },
            )
        except Exception as e:
            print(f"[WALKIN] manual_topup update failed: {e}")
        return {
            "store_id": store_id,
            "date_str": date_str,
            "manual_topup": new_topup,
            "total": new_total,
            "log_entry": manual_log[-1],
        }

    # ------------------------------------------------------------------
    # Per-staff manual entry (N3 footfall)
    # ------------------------------------------------------------------
    @staticmethod
    def compute_entry_status(per_staff: Dict, expected_staff: List[str]) -> str:
        """Derive the day's footfall capture status.

        N3 explicit status enum (replaces the implicit 'integer 0 = nothing
        logged' colour-as-meaning from Excel):
          PENDING  -- no staff has a walk-in entry yet
          PARTIAL  -- some expected staff have entries, at least one is missing
          COMPLETE -- every expected staff has an entry (or there are no
                      expected staff but at least one entry exists)
        """
        per_staff = per_staff or {}
        entered = {sp for sp in per_staff.keys() if sp}
        if not entered:
            return "PENDING"
        expected = {s for s in (expected_staff or []) if s}
        if not expected:
            # No roster to compare against; any entry means the store engaged.
            return "COMPLETE"
        missing = expected - entered
        return "COMPLETE" if not missing else "PARTIAL"

    def set_per_staff(
        self,
        *,
        store_id: str,
        staff_id: str,
        walk_ins: int,
        updated_by: str,
        date_str: Optional[str] = None,
        reason: Optional[str] = None,
        expected_staff: Optional[List[str]] = None,
    ) -> Dict:
        """Set (not increment) the day's walk-in count for ONE staff member.

        This is the N3 manager attribution layer. It overwrites
        ``per_staff[staff_id]`` to ``walk_ins`` (0 is a valid value -- a staff
        with no customers today is real data, not missing data), appends an
        append-only ``per_staff_log`` audit entry, recomputes ``entry_status``,
        and leaves ``pos_auto_count`` / ``manual_topup`` / ``total`` untouched
        (the POS auto-floor and the per-staff attribution are independent).

        Single-document write (standalone Mongo -- no transactions). Reads then
        writes the one doc via ``find_one_and_update`` so the recomputed
        per_staff / log / status land atomically on that one document.
        """
        date_str = date_str or self._today_str()
        doc = self._get_or_init(store_id, date_str)

        per_staff = dict(doc.get("per_staff") or {})
        old_val = int(per_staff.get(staff_id, 0)) if staff_id in per_staff else None
        new_val = max(0, int(walk_ins))
        per_staff[staff_id] = new_val

        log = list(doc.get("per_staff_log") or [])
        log.append(
            {
                "staff_id": staff_id,
                "old_val": old_val,
                "new_val": new_val,
                "updated_by": updated_by,
                "updated_at": datetime.now(),
                "reason": reason,
            }
        )
        status = self.compute_entry_status(per_staff, expected_staff or [])

        updated = None
        try:
            updated = self.collection.find_one_and_update(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "per_staff": per_staff,
                        "per_staff_log": log,
                        "entry_status": status,
                        "updated_at": datetime.now(),
                    }
                },
                return_document=True,
            )
        except Exception as e:
            print(f"[WALKIN] set_per_staff update failed: {e}")
        if not updated:
            # Fall back to the in-memory shape so the caller still gets the
            # committed values (the $set above is what we wrote).
            updated = dict(doc)
            updated["per_staff"] = per_staff
            updated["per_staff_log"] = log
            updated["entry_status"] = status
        return {k: v for k, v in updated.items() if k not in ("_id", "mobiles_seen")}

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def get_today(self, store_id: str, date_str: Optional[str] = None) -> Dict:
        date_str = date_str or self._today_str()
        try:
            doc = self.collection.find_one(
                {"_id": self._doc_id(store_id, date_str)}
            )
        except Exception:
            doc = None
        if not doc:
            return {
                "store_id": store_id,
                "date_str": date_str,
                "pos_auto_count": 0,
                "manual_topup": 0,
                "total": 0,
                "per_staff": {},
            }
        # Strip dedup field from the response — internal-only.
        out = {k: v for k, v in doc.items() if k not in ("_id", "mobiles_seen")}
        return out

    def get_mtd(self, store_id: str, year: int, month: int) -> Dict:
        """Sum every doc for (store_id, YYYY-MM-*) into a single
        envelope. Builds per_staff totals as the union of daily
        per_staff dicts."""
        prefix = f"{year:04d}-{month:02d}-"
        try:
            docs = list(self.collection.find({"store_id": store_id}))
        except Exception:
            docs = []
        docs = [d for d in docs if str(d.get("date_str", "")).startswith(prefix)]
        total_pos = 0
        total_manual = 0
        per_staff: Dict[str, int] = {}
        for d in docs:
            total_pos += int(d.get("pos_auto_count") or 0)
            total_manual += int(d.get("manual_topup") or 0)
            for sp, n in (d.get("per_staff") or {}).items():
                per_staff[sp] = int(per_staff.get(sp, 0)) + int(n)
        return {
            "store_id": store_id,
            "year": year,
            "month": month,
            "pos_auto_count": total_pos,
            "manual_topup": total_manual,
            "total": total_pos + total_manual,
            "per_staff": per_staff,
            "days_with_data": len(docs),
        }
