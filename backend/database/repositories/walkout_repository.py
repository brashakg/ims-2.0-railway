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

    # ------------------------------------------------------------------
    # Phase 3 — embedded follow-ups + result
    # ------------------------------------------------------------------
    def append_followup(
        self, walkout_id: str, followup: Dict
    ) -> Optional[Dict]:
        """Append a new follow-up sub-doc to the walkouts.followups array.

        The router enforces the round 1/2 / "no round 3" business rule
        before calling this. Returns the post-update doc or None.
        """
        try:
            self.collection.update_one(
                {"walkout_id": walkout_id, "deleted_at": None},
                {
                    "$push": {"followups": followup},
                    "$set": {"updated_at": datetime.now()},
                },
            )
        except Exception as e:
            print(f"[WALKOUT] append_followup failed: {e}")
            return None
        return self.find_by_walkout_id(walkout_id)

    def update_followup(
        self,
        walkout_id: str,
        round_num: int,
        patch: Dict,
        updated_by: Optional[str] = None,
    ) -> Optional[Dict]:
        """Update fields on the follow-up at the given round number."""
        existing = self.find_by_walkout_id(walkout_id)
        if not existing:
            return None
        followups = list(existing.get("followups") or [])
        idx = next(
            (i for i, fu in enumerate(followups) if fu.get("round") == round_num),
            None,
        )
        if idx is None:
            return None
        followups[idx] = {**followups[idx], **patch}
        try:
            self.collection.update_one(
                {"walkout_id": walkout_id, "deleted_at": None},
                {
                    "$set": {
                        "followups": followups,
                        "updated_at": datetime.now(),
                        "updated_by": updated_by,
                    }
                },
            )
        except Exception as e:
            print(f"[WALKOUT] update_followup failed: {e}")
            return None
        return self.find_by_walkout_id(walkout_id)

    def approve_followup(
        self,
        walkout_id: str,
        round_num: int,
        *,
        approver_user_id: str,
        approver_name: Optional[str],
        decision: str,
        manager_note: Optional[str] = None,
    ) -> Optional[Dict]:
        """Stamp the approval/rejection decision on a DONE follow-up.

        Anti-fake-closure: a salesperson who marked a follow-up DONE
        leaves it in PENDING_APPROVAL until a manager flips it via
        this path. The router enforces the role-gate; this method only
        does the atomic write + return.

        `decision` must be 'APPROVED' or 'REJECTED'. Returns the
        post-update walkout doc or None on failure / missing walkout /
        missing round.
        """
        existing = self.find_by_walkout_id(walkout_id)
        if not existing:
            return None
        followups = list(existing.get("followups") or [])
        idx = next(
            (i for i, fu in enumerate(followups) if fu.get("round") == round_num),
            None,
        )
        if idx is None:
            return None
        now = datetime.now()
        followups[idx] = {
            **followups[idx],
            "approval_required": True,
            "approval_status": decision,
            "approved_by_user_id": approver_user_id,
            "approved_by_name": approver_name,
            "approved_at": now,
            "manager_note": manager_note,
        }
        try:
            self.collection.update_one(
                {"walkout_id": walkout_id, "deleted_at": None},
                {
                    "$set": {
                        "followups": followups,
                        "updated_at": now,
                        "updated_by": approver_user_id,
                    }
                },
            )
        except Exception as e:
            print(f"[WALKOUT] approve_followup failed: {e}")
            return None
        return self.find_by_walkout_id(walkout_id)

    def set_result(
        self,
        walkout_id: str,
        result: str,
        converted_order_id: Optional[str],
        set_by: str,
    ) -> Optional[Dict]:
        """Stamp the outcome (DUE / NEGATIVE / CONVERTED). Includes the
        order id when CONVERTED. Other branches don't touch
        converted_order_id (pass None)."""
        update_doc = {
            "result": result,
            "result_set_at": datetime.now(),
            "result_set_by": set_by,
            "updated_at": datetime.now(),
            "updated_by": set_by,
        }
        if result == "CONVERTED":
            update_doc["converted_order_id"] = converted_order_id
        else:
            update_doc["converted_order_id"] = None
        try:
            self.collection.update_one(
                {"walkout_id": walkout_id, "deleted_at": None},
                {"$set": update_doc},
            )
        except Exception as e:
            print(f"[WALKOUT] set_result failed: {e}")
            return None
        return self.find_by_walkout_id(walkout_id)

    def list_followups_due_today(
        self, today_str: str, store_id: Optional[str] = None
    ) -> List[Dict]:
        """Return one dict per pending FU scheduled for today_str.

        Each row is a flat shape suitable for the FU-due-today widget:
        walkout_id, customer_name, mobile, sales_person_id,
        sales_person_name, round, scheduled_date, scheduled_time,
        mode, supervisor_id, supervisor_name, status, notes.
        """
        f: Dict = {"deleted_at": None}
        if store_id:
            f["store_id"] = store_id
        try:
            walkouts = list(self.collection.find(f))
        except Exception:
            return []
        out = []
        for w in walkouts:
            for fu in (w.get("followups") or []):
                if fu.get("status") != "PENDING":
                    continue
                scheduled = fu.get("scheduled_date")
                if isinstance(scheduled, datetime):
                    scheduled_str = scheduled.date().isoformat()
                else:
                    scheduled_str = str(scheduled)[:10] if scheduled else ""
                if scheduled_str != today_str:
                    continue
                out.append({
                    "walkout_id": w.get("walkout_id"),
                    "store_id": w.get("store_id"),
                    "customer_id": w.get("customer_id"),
                    "customer_name": w.get("customer_name"),
                    "mobile": w.get("mobile"),
                    "sales_person_id": w.get("sales_person_id"),
                    "sales_person_name": w.get("sales_person_name"),
                    **fu,
                    "scheduled_date": scheduled_str,
                })
        return out

    def list_overdue_followups(self, today_str: str) -> List[Dict]:
        """Return PENDING follow-ups whose scheduled_date < today and
        which haven't been escalated yet (escalation_task_id is None).
        Used by the escalate-overdue cron."""
        try:
            walkouts = list(self.collection.find({"deleted_at": None}))
        except Exception:
            return []
        out = []
        for w in walkouts:
            for fu in (w.get("followups") or []):
                if fu.get("status") != "PENDING":
                    continue
                if fu.get("escalation_task_id"):
                    continue
                scheduled = fu.get("scheduled_date")
                if isinstance(scheduled, datetime):
                    scheduled_str = scheduled.date().isoformat()
                else:
                    scheduled_str = str(scheduled)[:10] if scheduled else ""
                if not scheduled_str or scheduled_str >= today_str:
                    continue
                out.append({
                    "walkout_id": w.get("walkout_id"),
                    "store_id": w.get("store_id"),
                    "customer_id": w.get("customer_id"),
                    "customer_name": w.get("customer_name"),
                    "mobile": w.get("mobile"),
                    "sales_person_id": w.get("sales_person_id"),
                    "sales_person_name": w.get("sales_person_name"),
                    **fu,
                    "scheduled_date": scheduled_str,
                })
        return out

    def stamp_followup_escalation(
        self, walkout_id: str, round_num: int, task_id: str
    ) -> bool:
        """Record that a task was created to chase down an overdue FU."""
        existing = self.find_by_walkout_id(walkout_id)
        if not existing:
            return False
        followups = list(existing.get("followups") or [])
        idx = next(
            (i for i, fu in enumerate(followups) if fu.get("round") == round_num),
            None,
        )
        if idx is None:
            return False
        followups[idx] = {
            **followups[idx],
            "escalation_task_id": task_id,
            "status": "ESCALATED",
        }
        try:
            self.collection.update_one(
                {"walkout_id": walkout_id},
                {
                    "$set": {
                        "followups": followups,
                        "updated_at": datetime.now(),
                    }
                },
            )
            return True
        except Exception as e:
            print(f"[WALKOUT] stamp_followup_escalation failed: {e}")
            return False

    # ------------------------------------------------------------------
    # F45 — reason-driven policy suggestion (D3) + 50/50 credit (D2) +
    # POS compliance cohort (D5)
    # ------------------------------------------------------------------
    def update_policy_suggestion(
        self, walkout_id: str, suggestion: Dict
    ) -> Optional[Dict]:
        """Stamp the computed reason-driven policy_suggestion dict onto a
        walkout. Additive single-doc $set; returns the post-update doc."""
        try:
            self.collection.update_one(
                {"walkout_id": walkout_id, "deleted_at": None},
                {
                    "$set": {
                        "policy_suggestion": suggestion,
                        "updated_at": datetime.now(),
                    }
                },
            )
        except Exception as e:
            print(f"[WALKOUT] update_policy_suggestion failed: {e}")
            return None
        return self.find_by_walkout_id(walkout_id)

    def set_sale_credits(
        self, walkout_id: str, credits: List[Dict]
    ) -> Optional[Dict]:
        """Write the embedded sale_credits array onto a walkout doc.

        This is the single-document mirror of the flat walkout_sale_credits
        collection (which the SC engine queries). Called AFTER the flat-
        collection upserts succeed so the embedded array is never ahead of
        the canonical flat rows. Single-doc $set; returns the post-update doc.
        """
        try:
            self.collection.update_one(
                {"walkout_id": walkout_id, "deleted_at": None},
                {
                    "$set": {
                        "sale_credits": credits,
                        "updated_at": datetime.now(),
                    }
                },
            )
        except Exception as e:
            print(f"[WALKOUT] set_sale_credits failed: {e}")
            return None
        return self.find_by_walkout_id(walkout_id)

    def list_open_for_staff(
        self, store_id: str, sales_person_id: str
    ) -> List[Dict]:
        """Return non-deleted walkouts for a (store, salesperson) pair that
        have NO result yet (result is None) -- the POS soft-block compliance
        cohort (D5). Pure read, fail-soft -> []."""
        f: Dict = {
            "deleted_at": None,
            "store_id": store_id,
            "sales_person_id": sales_person_id,
            "result": None,
        }
        try:
            return list(self.collection.find(f))
        except Exception:
            return []
