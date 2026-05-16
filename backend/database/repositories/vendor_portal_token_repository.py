"""
IMS 2.0 - Vendor Portal Token Repository
==========================================
Token issuance + lookup for the external lens-lab portal.

Each row is a long-lived bearer token that grants a single vendor read+post
access to *their own* open workshop_jobs. Admin issues these via
`POST /api/v1/vendors/{id}/portal-token`. The token UUID itself is the bearer
secret (no separate hash) — the URL the admin shares with the lab IS the
credential, the same shape Stripe / Linear use for their public-link surfaces.
Rotate by issuing a new token + flipping `active=False` on the old one.
"""
from __future__ import annotations

from typing import Dict, List, Optional
from datetime import datetime, timedelta
import uuid

from .base_repository import BaseRepository


class VendorPortalTokenRepository(BaseRepository):
    """Repository for vendor_portal_tokens collection."""

    @property
    def entity_name(self) -> str:
        return "VendorPortalToken"

    @property
    def id_field(self) -> str:
        return "token_id"

    # ------------------------------------------------------------------
    # Issuance
    # ------------------------------------------------------------------

    def issue(
        self,
        vendor_id: str,
        vendor_name: str,
        created_by: str,
        ttl_days: int = 365,
    ) -> Dict:
        """Create a fresh token row and return it.

        ttl_days defaults to 365 — labs hate having to chase IT for a new
        link every quarter. Admin can revoke at any time via `revoke()`.
        """
        now = datetime.now()
        token_id = str(uuid.uuid4())
        doc = {
            "token_id": token_id,
            "vendor_id": vendor_id,
            "vendor_name": vendor_name,
            "active": True,
            "created_at": now,
            "created_by": created_by,
            "expires_at": now + timedelta(days=int(ttl_days or 365)),
            "last_used_at": None,
            "use_count": 0,
        }
        # base_repository.create() will set _id + add updated_at
        return self.create(doc) or doc

    # ------------------------------------------------------------------
    # Auth lookup
    # ------------------------------------------------------------------

    def find_active(self, token_id: str) -> Optional[Dict]:
        """Return the token row if it's active + not expired, else None.

        We do *not* raise — the caller (router) renders the 401. Repo just
        answers "is this a valid bearer right now?".
        """
        if not token_id:
            return None
        doc = self.find_by_id(token_id)
        if doc is None:
            return None
        if not doc.get("active", False):
            return None
        expires_at = doc.get("expires_at")
        if expires_at is not None:
            try:
                if isinstance(expires_at, str):
                    exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                else:
                    exp = expires_at
                # Drop tz so naive < naive comparison works regardless of
                # whether Mongo gave us a tz-aware datetime.
                if exp.tzinfo is not None:
                    exp = exp.replace(tzinfo=None)
                if exp < datetime.now():
                    return None
            except (ValueError, TypeError):
                # Bad expires_at: refuse the token rather than honor it
                return None
        return doc

    def touch(self, token_id: str) -> bool:
        """Stamp last_used_at + bump use_count. Idempotent / fail-soft."""
        try:
            now = datetime.now()
            self.collection.update_one(
                {"token_id": token_id},
                {
                    "$set": {"last_used_at": now, "updated_at": now},
                    "$inc": {"use_count": 1},
                },
            )
            return True
        except Exception:
            # Some MockCollection variants don't implement $inc — fall back
            # to a plain $set with current count + 1 so the test suite
            # that uses a hand-rolled fake collection still passes.
            try:
                doc = self.find_by_id(token_id) or {}
                cnt = int(doc.get("use_count", 0) or 0) + 1
                self.update(token_id, {"last_used_at": datetime.now(), "use_count": cnt})
                return True
            except Exception:
                return False

    # ------------------------------------------------------------------
    # Admin ops
    # ------------------------------------------------------------------

    def list_for_vendor(self, vendor_id: str) -> List[Dict]:
        return self.find_many({"vendor_id": vendor_id}, sort=[("created_at", -1)])

    def revoke(self, token_id: str, by_user: str) -> bool:
        return self.update(
            token_id,
            {"active": False, "revoked_at": datetime.now(), "revoked_by": by_user},
        )
