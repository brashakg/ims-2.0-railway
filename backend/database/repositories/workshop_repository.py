"""
IMS 2.0 - Workshop Repository
==============================
Workshop job data access operations
"""
from typing import List, Optional, Dict, Any
from datetime import datetime, date
from api.utils.ist import ist_today
from .base_repository import BaseRepository


class WorkshopJobRepository(BaseRepository):
    """Repository for Workshop Job operations"""

    @property
    def entity_name(self) -> str:
        return "WorkshopJob"

    @property
    def id_field(self) -> str:
        return "job_id"

    def find_by_number(self, job_number: str) -> Optional[Dict]:
        return self.find_one({"job_number": job_number})

    def find_by_order(self, order_id: str) -> List[Dict]:
        return self.find_many({"order_id": order_id})

    def find_by_store(self, store_id: str, status: str = None) -> List[Dict]:
        # BUG-061: limit=0 -> full-store rollup (workshop dashboard KPIs sum/count
        # ALL jobs by status). The default 100 cap understated the counts. The
        # paginated workshop list endpoints use their own find_many(skip/limit).
        filter_dict = {"store_id": store_id}
        if status:
            filter_dict["status"] = status
        return self.find_many(filter_dict, sort=[("created_at", -1)], limit=0)

    def find_pending(self, store_id: str = None) -> List[Dict]:
        filter_dict = {"status": {"$in": ["PENDING", "IN_PROGRESS"]}}
        if store_id:
            filter_dict["store_id"] = store_id
        return self.find_many(filter_dict, sort=[("expected_date", 1)])

    def find_ready(self, store_id: str = None) -> List[Dict]:
        filter_dict = {"status": "READY"}
        if store_id:
            filter_dict["store_id"] = store_id
        return self.find_many(filter_dict, sort=[("completed_at", -1)])

    def find_overdue(self, store_id: str = None) -> List[Dict]:
        # expected_date is stored as a date-only ISO string (e.g. "2026-05-30")
        # by create_job / update_job. We compare date-only vs date-only so that a
        # job due TODAY is NOT flagged as overdue until the day rolls over.
        # Using datetime.now().isoformat() (which includes time) would cause a
        # same-day date string ("2026-05-30") to compare LESS-THAN the datetime
        # string ("2026-05-30T14:00:00") and incorrectly mark today's jobs overdue.
        # IST (TZ-P3): expected_date is a BUSINESS date; the server runs UTC, so
        # datetime.now() between 00:00-05:30 IST is still on the PREVIOUS day and
        # would under-flag jobs that are already overdue in India.
        today_str = ist_today().isoformat()  # "2026-05-30"
        filter_dict = {
            "status": {"$in": ["PENDING", "IN_PROGRESS"]},
            "expected_date": {"$lt": today_str},
        }
        if store_id:
            filter_dict["store_id"] = store_id
        return self.find_many(filter_dict, sort=[("expected_date", 1)])

    def update_status(
        self,
        job_id: str,
        status: str,
        by_user: str = None,
        notes: str = None,
        picked_up_by_name: str = None,
        picked_up_by_phone: str = None,
    ) -> bool:
        now = datetime.now()
        update: Dict[str, Any] = {"status": status, "status_updated_at": now}
        if by_user:
            update["status_updated_by"] = by_user
        if notes:
            update["status_notes"] = notes
        if status == "COMPLETED":
            update["completed_at"] = now
        if status == "DELIVERED":
            update["delivered_at"] = now
            # Pickup record: WHO collected the job (customer / relative /
            # driver). Optional -- never gates the delivery, purely a record.
            if picked_up_by_name:
                update["picked_up_by_name"] = picked_up_by_name
            if picked_up_by_phone:
                update["picked_up_by_phone"] = picked_up_by_phone
        # Append this transition to status_history so every step (and the
        # DELIVERED pickup record) is preserved -- mirrors the qc_history
        # read-append-write pattern. Fail-soft: a missing/unreadable job doc
        # never blocks the status write itself.
        try:
            existing = self.find_by_id(job_id)
        except Exception:
            existing = None
        if isinstance(existing, dict):
            entry: Dict[str, Any] = {"status": status, "at": now.isoformat()}
            if by_user:
                entry["by_user"] = by_user
            if notes:
                entry["notes"] = notes
            if status == "DELIVERED":
                if picked_up_by_name:
                    entry["picked_up_by_name"] = picked_up_by_name
                if picked_up_by_phone:
                    entry["picked_up_by_phone"] = picked_up_by_phone
            update["status_history"] = list(
                existing.get("status_history") or []
            ) + [entry]
        return self.update(job_id, update)

    def assign_technician(self, job_id: str, technician_id: str) -> bool:
        return self.update(
            job_id,
            {
                "technician_id": technician_id,
                "assigned_at": datetime.now(),
            },
        )

    def add_qc_result(
        self,
        job_id: str,
        passed: bool,
        notes: str,
        by_user: str,
        checklist_items: Optional[List[Dict]] = None,
        waived: bool = False,
        waive_reason: Optional[str] = None,
    ) -> bool:
        """Record a QC result and transition the job status via update_status so
        that all timestamp fields (status_updated_at, delivered_at, ...) are
        stamped consistently.

        Bug fixed: the previous implementation directly wrote ``status`` in the
        same update dict as ``qc_passed``. That bypassed ``update_status`` which
        stamps ``status_updated_at`` and (for COMPLETED) ``completed_at``. The
        KPI endpoint's ``delivered_today`` count and ``avg_turnaround_days``
        both rely on those fields being present, so the old code produced
        incorrect zero/None KPIs for jobs that had gone through QC.
        """
        now = datetime.now()
        target_status = "READY" if (passed or waived) else "QC_FAILED"

        qc_update: Dict[str, Any] = {
            "qc_passed": passed,
            "qc_notes": notes,
            "qc_by": by_user,
            "qc_at": now,
            "qc_waived": waived,
        }
        if waived and waive_reason:
            qc_update["qc_waive_reason"] = waive_reason
        if checklist_items is not None:
            qc_update["qc_checklist"] = checklist_items
        # Append to qc_history so every QC attempt is preserved
        existing = self.find_by_id(job_id)
        if existing is not None:
            history = list(existing.get("qc_history") or [])
            history.append(
                {
                    "passed": passed,
                    "waived": waived,
                    "notes": notes,
                    "by_user": by_user,
                    "at": now.isoformat(),
                    "checklist": checklist_items or [],
                }
            )
            qc_update["qc_history"] = history

        # Write QC fields first, then transition status (separate update so
        # status_updated_at reflects the transition timestamp accurately).
        ok = self.update(job_id, qc_update)
        if not ok:
            return False
        return self.update_status(job_id, target_status, by_user)

    def get_technician_workload(self, store_id: str) -> List[Dict]:
        pipeline = [
            {
                "$match": {
                    "store_id": store_id,
                    "status": {"$in": ["PENDING", "IN_PROGRESS"]},
                }
            },
            {
                "$group": {
                    "_id": "$technician_id",
                    "job_count": {"$sum": 1},
                    "oldest_job": {"$min": "$created_at"},
                }
            },
            {"$sort": {"job_count": -1}},
        ]
        return self.aggregate(pipeline)
