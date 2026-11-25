"""
IMS 2.0 — Handoff Repository
=============================

Stores file-handoff documents (uploaded image/PDF assigned to one or
more recipients with a 3-30 day TTL). The Mongo TTL index on
`expires_at` auto-deletes documents server-side; the orphan-file
sweep (NEXUS hourly tick) removes the corresponding GridFS blobs.

Per-recipient state lives nested in `recipients[]` so a single row
captures the full multi-recipient handoff. Reshare creates a NEW row
with `parent_handoff_id` set + the same `expires_at` (anchored to the
original upload date — TTL never resets, per user direction).
"""
from typing import List, Optional, Dict
from datetime import datetime, timezone

from .base_repository import BaseRepository

# Audit R23 / decision D47: a reshare child is inserted as "pending" and only
# becomes a usable copy once its parent is proven alive (commit_reshare) and the
# child is published. Every read below hides pending rows, so an unfinished
# forward can never be opened, listed or forwarded onward.
FORWARD_PENDING = "pending"
_NOT_PENDING = {"forward_state": {"$ne": FORWARD_PENDING}}


class HandoffRepository(BaseRepository):
    """Repository for handoff documents."""

    @property
    def entity_name(self) -> str:
        return "Handoff"

    @property
    def id_field(self) -> str:
        return "handoff_id"

    def find_by_id(self, id: str) -> Optional[Dict]:
        """Like the base lookup, but an unfinished (pending) forward does not
        exist for any route: open, file, respond, dismiss, reshare, revoke."""
        try:
            return self._clean_id(
                self.collection.find_one({self.id_field: id, **_NOT_PENDING})
            )
        except Exception as e:
            print(f"Error finding {self.entity_name}: {e}")
            return None

    # ------------------------------------------------------------------
    # Inbox / outbox queries
    # ------------------------------------------------------------------

    def find_inbox_for_user(
        self, user_id: str, handoff_type: Optional[str] = None
    ) -> List[Dict]:
        """Return non-expired handoffs where the user is one of the
        recipients AND has not yet responded / dismissed (visibility
        rules applied at the router layer).

        When ``handoff_type`` is supplied the query is narrowed to that
        discriminator (F50: CLINICAL_RX). When omitted the legacy
        file-handoff behaviour is preserved exactly -- the general inbox
        sees CLINICAL_RX docs too only if no filter is passed, but the
        router for the general inbox does not pass one; the dedicated
        clinical inbox always does."""
        if not user_id:
            return []
        # Mongo's `$elemMatch` on the recipients array — match any
        # recipient sub-doc with this user_id. Result still contains
        # all recipient sub-docs; the router filters per-user view.
        # A read error is RAISED (the route answers 503), never a false empty inbox.
        query: Dict = {"recipients.user_id": user_id, **_NOT_PENDING}
        if handoff_type is not None:
            query["handoff_type"] = handoff_type
        return self._find_many_strict(query)

    # ------------------------------------------------------------------
    # F50 — clinical->retail handover (CLINICAL_RX discriminator)
    # ------------------------------------------------------------------

    def find_active_clinical_for_test(
        self, eye_test_id: str, now: Optional[datetime] = None
    ) -> Optional[Dict]:
        """Return a NON-EXPIRED CLINICAL_RX handoff already minted for this
        eye_test_id, if any. Powers the send-to-floor idempotency guard so a
        retried / double-clicked send returns the existing handoff instead of
        firing a second batch of notifications."""
        if not eye_test_id:
            return None
        if now is None:
            now = datetime.now(timezone.utc)
        try:
            return self.find_one(
                {
                    "handoff_type": "CLINICAL_RX",
                    "eye_test_id": eye_test_id,
                    "expires_at": {"$gt": now},
                }
            )
        except Exception:
            return None

    def acknowledge_clinical(
        self, handoff_id: str, user_id: str, now: Optional[datetime] = None
    ) -> bool:
        """Atomically claim the FIRST acknowledgement of a CLINICAL_RX handoff.

        Guarded single-document ``find_one_and_update`` (mirrors
        ``payout_snapshot.stamp_payroll_fed`` / ``vouchers.redeem_voucher_atomic``):
        the filter requires ``acknowledged_by`` to be unset/null, so under
        concurrency exactly one caller wins and stamps. Returns True only when
        THIS call did the stamping; False if it was already acknowledged (the
        router then returns 200 idempotently without overwriting the original
        ``acknowledged_at``)."""
        if not handoff_id or not user_id:
            return False
        if now is None:
            now = datetime.now(timezone.utc)
        try:
            from pymongo import ReturnDocument

            updated = self.collection.find_one_and_update(
                {
                    "handoff_id": handoff_id,
                    "handoff_type": "CLINICAL_RX",
                    "$or": [
                        {"acknowledged_by": {"$exists": False}},
                        {"acknowledged_by": None},
                    ],
                },
                {"$set": {"acknowledged_by": user_id, "acknowledged_at": now}},
                return_document=ReturnDocument.AFTER,
            )
            return updated is not None
        except Exception as e:  # noqa: BLE001
            print(f"[HANDOFF] acknowledge_clinical failed: {e}")
            return False

    def mark_served_clinical(
        self, handoff_id: str, user_id: str, now: Optional[datetime] = None
    ) -> bool:
        """Atomically mark a CLINICAL_RX handoff Served the FIRST time only.

        Guarded ``find_one_and_update`` on ``mark_served != True`` so a double
        mark-served can never double-count the conversion credit. Returns True
        only when THIS call flipped it; False if it was already served (router
        returns 409)."""
        if not handoff_id or not user_id:
            return False
        if now is None:
            now = datetime.now(timezone.utc)
        try:
            from pymongo import ReturnDocument

            updated = self.collection.find_one_and_update(
                {
                    "handoff_id": handoff_id,
                    "handoff_type": "CLINICAL_RX",
                    "mark_served": {"$ne": True},
                },
                {
                    "$set": {
                        "mark_served": True,
                        "served_by": user_id,
                        "served_at": now,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
            return updated is not None
        except Exception as e:  # noqa: BLE001
            print(f"[HANDOFF] mark_served_clinical failed: {e}")
            return False

    def find_sent_by_user(self, user_id: str) -> List[Dict]:
        if not user_id:
            return []
        return self._find_many_strict({"uploader_id": user_id, **_NOT_PENDING})

    def _find_many_strict(self, query: Dict) -> List[Dict]:
        """Newest 200 rows; raises on a read error (BaseRepository.find_many
        swallows it into an empty list)."""
        cursor = self.collection.find(query).sort([("created_at", -1)]).limit(200)
        return [self._clean_id(d) for d in cursor]

    def file_still_referenced(self, file_id: str) -> bool:
        """True when any handoff row still points at this blob (audit R23:
        a reshare shares its parent's file). Fails SAFE: on any read error
        the blob counts as still in use, so a revoke never deletes bytes it
        could not prove are unused."""
        try:
            return self.collection.count_documents({"file.file_id": file_id}, limit=1) > 0
        except Exception:
            return True

    def commit_reshare(self, parent_id: str) -> bool:
        """Prove the parent of a pending reshare child is still a live, usable
        handoff, as ONE write on the parent row. Revoke deletes that same row,
        so the two serialise: True means this commit landed before any revoke,
        whose reference check then runs after our (already inserted) child and
        keeps the bytes. False means the parent is gone. A driver error is
        raised: the caller cannot know, so it must not publish."""
        result = self.collection.update_one(
            {"handoff_id": parent_id, **_NOT_PENDING},
            {"$set": {"last_reshared_at": datetime.now(timezone.utc)}},
        )
        return result.matched_count > 0

    def publish_reshare(self, child_id: str) -> bool:
        """Make a committed pending child a usable copy. Raises on a driver
        error (the caller then reads back to learn the real state)."""
        result = self.collection.update_one(
            {"handoff_id": child_id, "forward_state": FORWARD_PENDING},
            {"$set": {"forward_state": "live"}},
        )
        return result.matched_count > 0

    def find_raw(self, handoff_id: str) -> Optional[Dict]:
        """Read a row whatever its forward_state. Raises on a read error."""
        return self._clean_id(self.collection.find_one({"handoff_id": handoff_id}))

    # ------------------------------------------------------------------
    # Recipient-level mutations
    # ------------------------------------------------------------------

    def update_recipient(
        self,
        handoff_id: str,
        user_id: str,
        updates: Dict,
    ) -> bool:
        """Patch a single recipient sub-doc. `updates` keys are the
        recipient field names (status, response, comment, dismissed,
        kept, snooze_until, responded_at)."""
        if not handoff_id or not user_id or not updates:
            return False
        try:
            set_block = {
                f"recipients.$[r].{k}": v for k, v in updates.items()
            }
            set_block["updated_at"] = datetime.now(timezone.utc)
            result = self.collection.update_one(
                {"handoff_id": handoff_id},
                {"$set": set_block},
                array_filters=[{"r.user_id": user_id}],
            )
            return result.modified_count > 0
        except Exception as e:
            print(f"Error updating recipient: {e}")
            return False

    # ------------------------------------------------------------------
    # TTL helpers (called by NEXUS sweep)
    # ------------------------------------------------------------------

    def find_expired(self, now: Optional[datetime] = None) -> List[Dict]:
        """Rows whose expires_at has passed but haven't been TTL-deleted
        yet (TTL has minute-granularity slop). Used by the orphan-file
        sweep to know which GridFS blobs to also delete."""
        if now is None:
            now = datetime.now(timezone.utc)
        try:
            return self.find_many({"expires_at": {"$lt": now}}, limit=500)
        except Exception:
            return []

    def ensure_ttl_index(self) -> None:
        """Create the Mongo TTL index on expires_at if not present.
        Idempotent — safe to call on every startup."""
        try:
            self.collection.create_index(
                "expires_at",
                expireAfterSeconds=0,
                name="ttl_expires_at",
            )
        except Exception as e:
            # FakeCollection (tests) or no-Mongo path falls through
            print(f"[HANDOFF] TTL index not created: {e}")
