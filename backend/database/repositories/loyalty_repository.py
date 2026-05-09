"""
IMS 2.0 - Loyalty Repository
=============================
Customer loyalty / points engine repositories.

Two collections:
  loyalty_accounts        -- one doc per customer (running balance + tier)
  loyalty_transactions    -- immutable ledger (EARN / REDEEM / EXPIRE / ADJUST)
  loyalty_settings        -- single-doc rule config

Fail-soft: every helper returns a sensible default on any DB error so a
loyalty failure can NEVER block POS.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from .base_repository import BaseRepository


# ============================================================================
# Account repo
# ============================================================================


class LoyaltyAccountRepository(BaseRepository):
    """One row per customer."""

    @property
    def entity_name(self) -> str:
        return "LoyaltyAccount"

    @property
    def id_field(self) -> str:
        return "customer_id"

    def find_or_create(self, customer_id: str) -> Dict[str, Any]:
        """Return the account, creating an empty BRONZE row on first hit."""
        existing = self.find_by_id(customer_id)
        if existing is not None:
            return existing
        now = datetime.now()
        seed = {
            "customer_id": customer_id,
            "_id": customer_id,
            "balance_points": 0,
            "tier": "BRONZE",
            "lifetime_earned": 0,
            "lifetime_redeemed": 0,
            "last_activity_at": now,
            "created_at": now,
            "updated_at": now,
        }
        try:
            self.collection.insert_one(seed)
        except Exception:
            # Concurrency / duplicate key -- re-read.
            again = self.find_by_id(customer_id)
            if again is not None:
                return again
        return seed

    def adjust_balance(
        self,
        customer_id: str,
        delta_points: int,
        delta_lifetime_earned: int = 0,
        delta_lifetime_redeemed: int = 0,
        new_tier: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Increment balance + lifetime counters atomically. Pass `new_tier`
        to bump the tier in the same write (computed by the caller off the
        post-write lifetime_earned).
        """
        try:
            inc: Dict[str, int] = {}
            if delta_points:
                inc["balance_points"] = int(delta_points)
            if delta_lifetime_earned:
                inc["lifetime_earned"] = int(delta_lifetime_earned)
            if delta_lifetime_redeemed:
                inc["lifetime_redeemed"] = int(delta_lifetime_redeemed)

            set_block: Dict[str, Any] = {
                "last_activity_at": datetime.now(),
                "updated_at": datetime.now(),
            }
            if new_tier:
                set_block["tier"] = new_tier

            update: Dict[str, Any] = {"$set": set_block}
            if inc:
                update["$inc"] = inc

            self.collection.update_one(
                {"customer_id": customer_id}, update,
            )
            return self.find_by_id(customer_id)
        except Exception:
            return self.find_by_id(customer_id)


# ============================================================================
# Ledger repo
# ============================================================================


class LoyaltyTransactionRepository(BaseRepository):
    """Immutable ledger of every loyalty mutation."""

    @property
    def entity_name(self) -> str:
        return "LoyaltyTransaction"

    @property
    def id_field(self) -> str:
        return "txn_id"

    def find_for_customer(
        self,
        customer_id: str,
        limit: int = 20,
        skip: int = 0,
        type_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return ledger rows for one customer, newest-first."""
        flt: Dict[str, Any] = {"customer_id": customer_id}
        if type_filter:
            flt["type"] = type_filter
        try:
            cursor = self.collection.find(flt)
            cursor = cursor.sort([("created_at", -1)])
            if skip:
                cursor = cursor.skip(int(skip))
            if limit:
                cursor = cursor.limit(int(limit))
            return list(cursor)
        except Exception:
            return []

    def count_for_customer(
        self, customer_id: str, type_filter: Optional[str] = None,
    ) -> int:
        flt: Dict[str, Any] = {"customer_id": customer_id}
        if type_filter:
            flt["type"] = type_filter
        try:
            return self.collection.count_documents(flt)
        except Exception:
            return 0

    def has_earn_for_order(self, customer_id: str, order_id: str) -> bool:
        """Idempotency guard: same (customer, order) -> only one EARN row."""
        if not order_id:
            return False
        try:
            return self.collection.find_one({
                "customer_id": customer_id,
                "order_id": order_id,
                "type": "EARN",
            }) is not None
        except Exception:
            return False

    def find_expired_unprocessed(self, now: datetime) -> List[Dict[str, Any]]:
        """All EARN rows whose expires_at <= now AND that haven't already
        been swept (we mark with `expired: True` on the EARN row after
        writing the offsetting EXPIRE row).
        """
        try:
            cursor = self.collection.find({
                "type": "EARN",
                "expires_at": {"$lte": now, "$ne": None},
                "expired": {"$ne": True},
            })
            return list(cursor)
        except Exception:
            return []

    def mark_expired(self, txn_id: str) -> bool:
        try:
            self.collection.update_one(
                {"txn_id": txn_id},
                {"$set": {"expired": True, "expired_at": datetime.now()}},
            )
            return True
        except Exception:
            return False


# ============================================================================
# Settings repo  (single doc)
# ============================================================================


# Loyalty rule defaults. Centralised so the engine + the frontend share
# one source of truth.
DEFAULT_SETTINGS: Dict[str, Any] = {
    "enabled": True,
    "points_per_rupee": 0.01,        # 1 point per 100 rupees
    "category_multipliers": {
        "FRAME": 1.0, "FRAMES": 1.0,
        "LENS": 1.5, "LENSES": 1.5, "RX_LENSES": 1.5,
        "SUNGLASS": 0.5, "SUNGLASSES": 0.5,
        "CONTACT_LENS": 1.0, "CONTACT_LENSES": 1.0,
        "WATCH": 0.0,
        "ACCESSORY": 0.0, "ACCESSORIES": 0.0,
    },
    "min_order_for_earn": 0.0,
    "expiry_days": 365,
    "redeem_rupee_per_point": 1.0,
    "min_redeem_points": 100,
    "max_redeem_pct_of_order": 50.0,
    "tier_thresholds": {
        "SILVER": 1000,
        "GOLD": 5000,
        "PLATINUM": 25000,
    },
    "tier_multipliers": {
        "BRONZE": 1.0,
        "SILVER": 1.1,
        "GOLD": 1.25,
        "PLATINUM": 1.5,
    },
}


class LoyaltySettingsRepository(BaseRepository):
    """Wraps the single-doc settings collection."""

    @property
    def entity_name(self) -> str:
        return "LoyaltySettings"

    @property
    def id_field(self) -> str:
        return "_id"

    SINGLETON_ID = "loyalty_settings"

    def get(self) -> Dict[str, Any]:
        """Always returns a complete settings dict — defaults fill any gap."""
        try:
            doc = self.collection.find_one({"_id": self.SINGLETON_ID})
        except Exception:
            doc = None
        merged: Dict[str, Any] = {}
        for k, v in DEFAULT_SETTINGS.items():
            if isinstance(v, dict):
                merged[k] = dict(v)
            else:
                merged[k] = v
        if doc:
            for k, v in doc.items():
                if k in ("_id",):
                    continue
                if isinstance(v, dict) and isinstance(merged.get(k), dict):
                    merged[k] = {**merged[k], **v}
                else:
                    merged[k] = v
        return merged

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """Merge `patch` into the singleton row. Unknown keys are kept so a
        future schema add doesn't drop user-supplied tuning."""
        try:
            existing = self.collection.find_one({"_id": self.SINGLETON_ID}) or {}
            merged: Dict[str, Any] = {**existing, **patch}
            merged["_id"] = self.SINGLETON_ID
            merged["updated_at"] = datetime.now()
            # upsert
            self.collection.update_one(
                {"_id": self.SINGLETON_ID},
                {"$set": merged},
                # `upsert=True` may not exist on every fake collection in
                # tests — fall back to insert when no row yet.
            )
            still = self.collection.find_one({"_id": self.SINGLETON_ID})
            if still is None:
                self.collection.insert_one(merged)
        except Exception:
            pass
        return self.get()
