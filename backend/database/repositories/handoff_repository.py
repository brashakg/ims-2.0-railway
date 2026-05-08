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


class HandoffRepository(BaseRepository):
    """Repository for handoff documents."""

    @property
    def entity_name(self) -> str:
        return "Handoff"

    @property
    def id_field(self) -> str:
        return "handoff_id"

    # ------------------------------------------------------------------
    # Inbox / outbox queries
    # ------------------------------------------------------------------

    def find_inbox_for_user(self, user_id: str) -> List[Dict]:
        """Return non-expired handoffs where the user is one of the
        recipients AND has not yet responded / dismissed (visibility
        rules applied at the router layer)."""
        if not user_id:
            return []
        # Mongo's `$elemMatch` on the recipients array — match any
        # recipient sub-doc with this user_id. Result still contains
        # all recipient sub-docs; the router filters per-user view.
        try:
            return self.find_many(
                {"recipients.user_id": user_id},
                sort=[("created_at", -1)],
                limit=200,
            )
        except Exception:
            return []

    def find_sent_by_user(self, user_id: str) -> List[Dict]:
        if not user_id:
            return []
        try:
            return self.find_many(
                {"uploader_id": user_id},
                sort=[("created_at", -1)],
                limit=200,
            )
        except Exception:
            return []

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
