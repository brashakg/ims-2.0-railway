"""F49 - Family/household loyalty points wallet (service layer).

A household groups up to ``loyalty.pool_max_members`` (E2 policy, default 7)
customers into one shared loyalty-points pool. The pool BALANCE lives in a
money_guard FAMILY_WALLET account doc (one doc per household in the
``family_wallets`` collection, keyed on household_id) -- every credit/debit
goes through money_guard's guarded find_one_and_update (the floor lives IN the
filter), never read-modify-write. Pool redemption is OTP-gated via the
existing ``reminder_rail.send_pool_redemption_otp`` /
``verify_pool_redemption_otp`` slice (sha256-hashed code, atomic single-doc
consume) -- the OTP machinery is NOT re-implemented here. The router mints the
redeemed value as a store-credit voucher via the canonical
``vouchers.mint_voucher`` helper so it is spendable through the existing
voucher flow (NO orders.py / POS change).

UNIT NOTE: loyalty points are POINTS, not paise/rupees. The FAMILY_WALLET
money_guard account is registered with ``integer=True`` and stores whole
POINTS as its amount unit (``balance_points``). Do NOT convert to rupees in
this module; the voucher minted at redemption carries the rupee conversion
(loyalty ``redeem_rupee_per_point``).

Membership invariants:
- A customer belongs to at most ONE ACTIVE household. Enforced by a pre-check
  query on ``member_customer_ids`` plus a best-effort partial UNIQUE multikey
  index on member_customer_ids (status=ACTIVE only). RACE CAVEAT: standalone
  Mongo (no multi-doc transactions) -- two concurrent enrolments of the same
  customer into two DIFFERENT households can both pass the pre-check; the
  unique index is the backstop (the second insert/push raises and maps to
  409). Where the index cannot be created the pre-check alone holds and the
  worst case is a duplicate membership that the index would have refused --
  documented, accepted (mirrors the petty-cash open_float guard pattern).
- The max-member cap is enforced IN the find_one_and_update FILTER
  ($expr $size < max), so two concurrent adds at capacity-1 produce exactly
  ONE winner -- the loser maps to 409 household_full.
- The primary member (member_customer_ids[0] == primary_customer_id) can
  never be removed; the $pull filter excludes the primary.

Chain-wide BY OWNER DECISION: household lookup + pool redemption are NOT
store-scoped (mirrors chain-wide customer-lookup + voucher-redeem). The
household records its CREATING store_id for provenance only.

Result envelope (petty-cash style; business failures never raise):
  {"ok": True, ...}  |  {"ok": False, "http": <status>, "error": "<code>"}

No emoji (Windows cp1252). Fail-soft on absent DB (http 503).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from . import money_guard as mg

logger = logging.getLogger(__name__)

HOUSEHOLDS_COLLECTION = "households"
WALLETS_COLLECTION = "family_wallets"
ACCOUNT_TYPE = "FAMILY_WALLET"

STATUS_ACTIVE = "ACTIVE"
STATUS_DISSOLVED = "DISSOLVED"

DEFAULT_MAX_MEMBERS = 7  # code fallback; E2 loyalty.pool_max_members overrides


def _now_iso() -> str:
    """IST-naive ISO timestamp (matches money_guard / petty_cash convention)."""
    try:
        from ..utils.ist import now_ist_naive

        return now_ist_naive().isoformat()
    except Exception:  # noqa: BLE001
        from datetime import datetime

        return datetime.now().isoformat()


def _coll(db, name: str):
    """Resolve a collection from a DB handle. None when no DB (fail-soft)."""
    if db is None:
        return None
    getter = getattr(db, "get_collection", None)
    if callable(getter):
        try:
            return getter(name)
        except Exception:  # noqa: BLE001
            return None
    try:
        return db[name]
    except Exception:  # noqa: BLE001
        return None


def _households(db):
    return _coll(db, HOUSEHOLDS_COLLECTION)


def _actor_id(actor: Optional[Dict[str, Any]]) -> Optional[str]:
    if isinstance(actor, dict):
        return actor.get("user_id") or actor.get("id")
    return str(actor) if actor else None


def _public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not doc:
        return None
    out = dict(doc)
    out.pop("_id", None)
    return out


def ensure_indexes(db) -> None:
    """Idempotent index creation. The partial UNIQUE multikey index on
    member_customer_ids (ACTIVE households only) is the structural backstop for
    one-household-per-customer; unique household_id keys both collections.
    Best-effort; never raises (a fake/old Mongo without partial-index support
    still works via the pre-checks)."""
    hh = _households(db)
    if hh is not None:
        try:
            hh.create_index("household_id", unique=True)
        except Exception:  # noqa: BLE001
            logger.debug("[FAMWALLET] household_id index skipped", exc_info=True)
        try:
            # Multikey UNIQUE on the array elements: no customer may appear in
            # two ACTIVE household docs. Partial so a DISSOLVED household never
            # blocks a re-enrolment.
            hh.create_index(
                "member_customer_ids",
                unique=True,
                partialFilterExpression={"status": STATUS_ACTIVE},
            )
        except Exception:  # noqa: BLE001
            logger.debug("[FAMWALLET] membership unique index skipped", exc_info=True)
    fw = _coll(db, WALLETS_COLLECTION)
    if fw is not None:
        try:
            fw.create_index("household_id", unique=True)
        except Exception:  # noqa: BLE001
            logger.debug("[FAMWALLET] wallet index skipped", exc_info=True)


# ---------------------------------------------------------------------------
# Audit (append-only via AuditRepository; fail-soft, never blocks the move)
# ---------------------------------------------------------------------------


def _audit(
    db,
    action: str,
    household_id: str,
    *,
    actor: Optional[str],
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    if db is None:
        return
    try:
        coll = db.get_collection("audit_logs")
        if coll is None:
            return
        from database.repositories.audit_repository import AuditRepository

        AuditRepository(coll).create(
            {
                "log_id": f"AUD-{uuid.uuid4().hex[:12]}",
                "action": action,
                "entity_type": "household",
                "entity_id": household_id,
                "user_id": actor,
                "actor": actor,
                "source": "FAMWALLET",
                "before_state": None,
                "after_state": detail or {},
                "severity": "INFO",
                "timestamp": _now_iso(),
            }
        )
    except Exception:  # noqa: BLE001
        logger.debug("[FAMWALLET] audit write skipped", exc_info=True)


def _is_dup_key(exc: Exception) -> bool:
    try:
        from pymongo.errors import DuplicateKeyError

        if isinstance(exc, DuplicateKeyError):
            return True
    except Exception:  # noqa: BLE001
        pass
    return "E11000" in str(exc) or "duplicate key" in str(exc).lower()


# ---------------------------------------------------------------------------
# Household lifecycle
# ---------------------------------------------------------------------------


def create_household(
    db,
    *,
    primary_customer_id: str,
    actor: Dict[str, Any],
    store_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a household with the primary customer as member[0].

    Validates the primary exists and is not already in an ACTIVE household
    (pre-check; the partial unique index is the race backstop -- a duplicate
    insert maps to 409, never a second membership)."""
    hh = _households(db)
    if hh is None:
        return {"ok": False, "http": 503, "error": "no_db"}
    primary_customer_id = (primary_customer_id or "").strip()
    if not primary_customer_id:
        return {"ok": False, "http": 422, "error": "invalid_customer"}

    customers = _coll(db, "customers")
    try:
        cust = (
            customers.find_one({"customer_id": primary_customer_id})
            if customers is not None
            else None
        )
    except Exception:  # noqa: BLE001
        cust = None
    if not cust:
        return {"ok": False, "http": 404, "error": "customer_not_found"}

    try:
        existing = hh.find_one(
            {"member_customer_ids": primary_customer_id, "status": STATUS_ACTIVE}
        )
    except Exception:  # noqa: BLE001
        existing = None
    if existing:
        return {
            "ok": False,
            "http": 409,
            "error": "already_in_household",
            "household_id": existing.get("household_id"),
        }

    hid = f"HH-{uuid.uuid4().hex[:12].upper()}"
    now = _now_iso()
    doc = {
        "_id": hid,
        "household_id": hid,
        "primary_customer_id": primary_customer_id,
        "member_customer_ids": [primary_customer_id],
        "store_id": store_id,  # creating store (provenance); lookup is CHAIN-WIDE
        "status": STATUS_ACTIVE,
        "created_by": _actor_id(actor),
        "created_at": now,
        "updated_at": now,
    }
    try:
        hh.insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        if _is_dup_key(exc):
            # Racing enrolment of the same customer (unique index backstop).
            return {"ok": False, "http": 409, "error": "already_in_household"}
        logger.warning("[FAMWALLET] create_household failed: %s", exc)
        return {"ok": False, "http": 503, "error": "write_failed"}

    _audit(
        db,
        "family_wallet.household_create",
        hid,
        actor=_actor_id(actor),
        detail={"primary_customer_id": primary_customer_id, "store_id": store_id},
    )
    return {"ok": True, "household": _public(doc)}


def add_member(
    db,
    household_id: str,
    customer_id: str,
    *,
    actor: Dict[str, Any],
    max_members: int = DEFAULT_MAX_MEMBERS,
) -> Dict[str, Any]:
    """Add a member via a SINGLE guarded find_one_and_update.

    The max-member cap is IN the filter ($expr $size < max_members), so two
    concurrent adds at capacity-1 produce exactly one winner. The $ne on
    member_customer_ids makes a duplicate add of the same customer a no-match
    (409 already_member). Cross-household double-enrolment is pre-checked
    (race caveat: the partial unique index is the backstop -- a racing $push
    that violates it raises and maps to 409)."""
    hh = _households(db)
    if hh is None:
        return {"ok": False, "http": 503, "error": "no_db"}
    customer_id = (customer_id or "").strip()
    if not customer_id:
        return {"ok": False, "http": 422, "error": "invalid_customer"}
    try:
        max_members = int(max_members)
    except (TypeError, ValueError):
        max_members = DEFAULT_MAX_MEMBERS
    if max_members < 1:
        max_members = 1

    customers = _coll(db, "customers")
    try:
        cust = (
            customers.find_one({"customer_id": customer_id})
            if customers is not None
            else None
        )
    except Exception:  # noqa: BLE001
        cust = None
    if not cust:
        return {"ok": False, "http": 404, "error": "customer_not_found"}

    # Unique membership across ALL households (pre-check + index backstop).
    try:
        elsewhere = hh.find_one(
            {"member_customer_ids": customer_id, "status": STATUS_ACTIVE}
        )
    except Exception:  # noqa: BLE001
        elsewhere = None
    if elsewhere:
        already_here = elsewhere.get("household_id") == household_id
        return {
            "ok": False,
            "http": 409,
            "error": "already_member" if already_here else "already_in_household",
            "household_id": elsewhere.get("household_id"),
        }

    filt = {
        "household_id": household_id,
        "status": STATUS_ACTIVE,
        "member_customer_ids": {"$ne": customer_id},
        # The cap lives IN the filter: concurrency-safe (one winner at 6/7).
        "$expr": {"$lt": [{"$size": "$member_customer_ids"}, max_members]},
    }
    update = {
        "$push": {"member_customer_ids": customer_id},
        "$set": {"updated_at": _now_iso()},
    }
    try:
        from pymongo import ReturnDocument

        after = hh.find_one_and_update(
            filt, update, return_document=ReturnDocument.AFTER
        )
    except Exception as exc:  # noqa: BLE001
        if _is_dup_key(exc):
            return {"ok": False, "http": 409, "error": "already_in_household"}
        logger.warning("[FAMWALLET] add_member failed: %s", exc)
        return {"ok": False, "http": 503, "error": "write_failed"}

    if after is None:
        # Disambiguate why the guarded write matched nothing (read-only).
        try:
            cur = hh.find_one({"household_id": household_id})
        except Exception:  # noqa: BLE001
            cur = None
        if not cur:
            return {"ok": False, "http": 404, "error": "household_not_found"}
        if cur.get("status") != STATUS_ACTIVE:
            return {"ok": False, "http": 409, "error": "household_inactive"}
        if customer_id in (cur.get("member_customer_ids") or []):
            return {"ok": False, "http": 409, "error": "already_member"}
        return {
            "ok": False,
            "http": 409,
            "error": "household_full",
            "max_members": max_members,
        }

    _audit(
        db,
        "family_wallet.member_add",
        household_id,
        actor=_actor_id(actor),
        detail={
            "customer_id": customer_id,
            "member_count": len(after.get("member_customer_ids") or []),
        },
    )
    return {"ok": True, "household": _public(after)}


def remove_member(
    db, household_id: str, customer_id: str, *, actor: Dict[str, Any]
) -> Dict[str, Any]:
    """Remove a NON-PRIMARY member via a single guarded $pull (the filter
    excludes the primary, so the primary is structurally irremovable)."""
    hh = _households(db)
    if hh is None:
        return {"ok": False, "http": 503, "error": "no_db"}

    filt = {
        "household_id": household_id,
        "status": STATUS_ACTIVE,
        "member_customer_ids": customer_id,
        "primary_customer_id": {"$ne": customer_id},
    }
    update = {
        "$pull": {"member_customer_ids": customer_id},
        "$set": {"updated_at": _now_iso()},
    }
    try:
        from pymongo import ReturnDocument

        after = hh.find_one_and_update(
            filt, update, return_document=ReturnDocument.AFTER
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[FAMWALLET] remove_member failed: %s", exc)
        return {"ok": False, "http": 503, "error": "write_failed"}

    if after is None:
        try:
            cur = hh.find_one({"household_id": household_id})
        except Exception:  # noqa: BLE001
            cur = None
        if not cur:
            return {"ok": False, "http": 404, "error": "household_not_found"}
        if cur.get("primary_customer_id") == customer_id:
            return {"ok": False, "http": 409, "error": "primary_irremovable"}
        if cur.get("status") != STATUS_ACTIVE:
            return {"ok": False, "http": 409, "error": "household_inactive"}
        return {"ok": False, "http": 404, "error": "not_a_member"}

    _audit(
        db,
        "family_wallet.member_remove",
        household_id,
        actor=_actor_id(actor),
        detail={"customer_id": customer_id},
    )
    return {"ok": True, "household": _public(after)}


# ---------------------------------------------------------------------------
# Reads (CHAIN-WIDE by owner decision; fail-soft)
# ---------------------------------------------------------------------------


def get_household(db, household_id: str) -> Optional[Dict[str, Any]]:
    hh = _households(db)
    if hh is None:
        return None
    try:
        return _public(hh.find_one({"household_id": household_id}))
    except Exception:  # noqa: BLE001
        return None


def get_household_by_customer(db, customer_id: str) -> Optional[Dict[str, Any]]:
    """The ACTIVE household containing customer_id -- chain-wide lookup."""
    hh = _households(db)
    if hh is None:
        return None
    try:
        return _public(
            hh.find_one({"member_customer_ids": customer_id, "status": STATUS_ACTIVE})
        )
    except Exception:  # noqa: BLE001
        return None


def pool_balance(db, household_id: str) -> int:
    """Current pool balance in POINTS (0 when the wallet was never funded)."""
    try:
        return int(mg.get_balance(db, ACCOUNT_TYPE, household_id).get("balance") or 0)
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------------------
# Pool earn / redeem (THIN facade over money_guard; points are the unit)
# ---------------------------------------------------------------------------


def _ensure_wallet(db, household_id: str) -> bool:
    """Lazily create the household's FAMILY_WALLET account doc (balance floor 0)
    on first earn. Idempotent: a racing duplicate insert is swallowed (the
    unique household_id index / _id is the guard)."""
    fw = _coll(db, WALLETS_COLLECTION)
    if fw is None:
        return False
    try:
        if fw.find_one({"household_id": household_id}):
            return True
    except Exception:  # noqa: BLE001
        return False
    now = _now_iso()
    try:
        fw.insert_one(
            {
                "_id": household_id,
                "household_id": household_id,
                "balance_points": 0,
                "status": STATUS_ACTIVE,
                "money_ledger": [],
                "created_at": now,
                "updated_at": now,
            }
        )
    except Exception as exc:  # noqa: BLE001
        if not _is_dup_key(exc):
            logger.warning("[FAMWALLET] wallet create failed: %s", exc)
            return False
    return True


def _loyalty_txn(
    db,
    *,
    txn_id: str,
    household_id: str,
    customer_id: Optional[str],
    ttype: str,
    points: int,
    order_id: Optional[str],
    reason: str,
    actor: Optional[str],
) -> None:
    """Append one loyalty-txn audit row mirroring loyalty.py's ledger shape
    (txn_id/customer_id/type/points/order_id/reason/created_by/created_at)
    plus household_id. Fail-soft -- the money already moved via money_guard."""
    coll = _coll(db, "loyalty_transactions")
    if coll is None:
        return
    try:
        coll.insert_one(
            {
                "txn_id": txn_id,
                "household_id": household_id,
                "customer_id": customer_id,
                "type": ttype,
                "points": int(points),
                "rupee_value": None,  # points-unit pool; rupee conversion at voucher mint
                "order_id": order_id,
                "reason": reason,
                "expires_at": None,
                "created_by": actor,
                "created_at": _now_iso(),
            }
        )
    except Exception:  # noqa: BLE001
        logger.debug("[FAMWALLET] loyalty txn row skipped", exc_info=True)


def pool_earn(
    db,
    household_id: str,
    points: Any,
    *,
    actor: Dict[str, Any],
    source_order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Credit whole POINTS to the household pool: a single guarded
    money_guard.credit on the FAMILY_WALLET account (created lazily, floor 0).
    Idempotent per source_order_id (a retried earn for the same order returns
    duplicate=True and does NOT double-credit)."""
    if _households(db) is None:
        return {"ok": False, "http": 503, "error": "no_db"}
    try:
        pts = int(points)
    except (TypeError, ValueError):
        return {"ok": False, "http": 422, "error": "invalid_points"}
    if pts <= 0:
        return {"ok": False, "http": 422, "error": "invalid_points"}

    household = get_household(db, household_id)
    if not household:
        return {"ok": False, "http": 404, "error": "household_not_found"}
    if household.get("status") != STATUS_ACTIVE:
        return {"ok": False, "http": 409, "error": "household_inactive"}

    if not _ensure_wallet(db, household_id):
        return {"ok": False, "http": 503, "error": "wallet_unavailable"}

    res = mg.credit(
        db,
        ACCOUNT_TYPE,
        household_id,
        pts,
        reason="pool_earn",
        actor=_actor_id(actor),
        ref=source_order_id,
        store_id=household.get("store_id"),
        idempotency_key=f"poolearn:{source_order_id}" if source_order_id else None,
    )
    if not res.ok:
        code = {"unavailable": 503, "no_atomic": 503, "not_found": 404}.get(
            res.reason or "", 409
        )
        return {"ok": False, "http": code, "error": res.reason or "credit_failed"}
    if res.reason == "duplicate":
        return {
            "ok": True,
            "balance": int(res.balance),
            "txn_id": res.txn_id,
            "duplicate": True,
        }

    _loyalty_txn(
        db,
        txn_id=res.txn_id,
        household_id=household_id,
        customer_id=None,
        ttype="POOL_EARN",
        points=pts,
        order_id=source_order_id,
        reason=(
            f"Pool earn on order {source_order_id}" if source_order_id else "Pool earn"
        ),
        actor=_actor_id(actor),
    )
    _audit(
        db,
        "family_wallet.pool_earn",
        household_id,
        actor=_actor_id(actor),
        detail={
            "points": pts,
            "order_id": source_order_id,
            "balance_after": int(res.balance),
        },
    )
    return {
        "ok": True,
        "balance": int(res.balance),
        "txn_id": res.txn_id,
        "duplicate": False,
    }


def pool_redeem(
    db,
    household_id: str,
    points: Any,
    *,
    redeeming_customer_id: str,
    actor: Dict[str, Any],
    otp_id: Optional[str] = None,
    otp_code: Optional[str] = None,
    require_otp: bool = True,
) -> Dict[str, Any]:
    """OTP-gated pool debit.

    Order of operations:
      1. membership check -- the redeeming customer MUST be a member (403);
      2. advisory balance check (cheap 409 BEFORE the OTP is consumed, so an
         obviously-insufficient redeem does not burn a verified OTP);
      3. OTP verify via the rail's atomic consume-once verify (households AND
         amount must match what the OTP was issued for);
      4. the authoritative guarded money_guard.debit -- the floor
         (balance_points >= points) is IN the filter, so two concurrent
         redeems near the floor produce exactly one winner. The debit is
         idempotent on the consumed otp_id (a retry cannot double-debit).
    The rupee-value voucher mint happens in the ROUTER (vouchers.mint_voucher)
    after this returns ok."""
    if _households(db) is None:
        return {"ok": False, "http": 503, "error": "no_db"}
    try:
        pts = int(points)
    except (TypeError, ValueError):
        return {"ok": False, "http": 422, "error": "invalid_points"}
    if pts <= 0:
        return {"ok": False, "http": 422, "error": "invalid_points"}

    household = get_household(db, household_id)
    if not household:
        return {"ok": False, "http": 404, "error": "household_not_found"}
    if household.get("status") != STATUS_ACTIVE:
        return {"ok": False, "http": 409, "error": "household_inactive"}
    if redeeming_customer_id not in (household.get("member_customer_ids") or []):
        return {"ok": False, "http": 403, "error": "not_a_member"}

    # Advisory pre-check (the authoritative floor is in the debit filter).
    if pool_balance(db, household_id) < pts:
        return {"ok": False, "http": 409, "error": "insufficient_balance"}

    if require_otp:
        if not otp_id or not otp_code:
            return {"ok": False, "http": 400, "error": "otp_required"}
        try:
            from .reminder_rail import verify_pool_redemption_otp

            ver = verify_pool_redemption_otp(db, otp_id=otp_id, code=otp_code)
        except Exception:  # noqa: BLE001
            logger.warning("[FAMWALLET] OTP verify errored", exc_info=True)
            return {"ok": False, "http": 503, "error": "otp_unavailable"}
        if not ver.get("ok"):
            return {
                "ok": False,
                "http": 403,
                "error": f"otp_{ver.get('reason') or 'rejected'}",
            }
        if ver.get("household_id") != household_id:
            return {"ok": False, "http": 403, "error": "otp_household_mismatch"}
        try:
            authorized = int(float(ver.get("amount") or 0))
        except (TypeError, ValueError):
            authorized = 0
        if authorized != pts:
            return {"ok": False, "http": 403, "error": "otp_amount_mismatch"}

    res = mg.debit(
        db,
        ACCOUNT_TYPE,
        household_id,
        pts,
        reason="pool_redeem",
        actor=_actor_id(actor),
        ref=otp_id,
        store_id=household.get("store_id"),
        # One consumed OTP -> at most one debit, even on a network retry.
        idempotency_key=f"poolredeem:{otp_id}" if otp_id else None,
    )
    if not res.ok:
        code = {
            "insufficient": 409,
            "not_found": 409,
            "inactive": 409,
            "unavailable": 503,
            "no_atomic": 503,
            "invalid_amount": 422,
        }.get(res.reason or "", 409)
        err = (
            "insufficient_balance"
            if res.reason in ("insufficient", "not_found")
            else (res.reason or "debit_failed")
        )
        return {"ok": False, "http": code, "error": err}
    if res.reason == "duplicate":
        return {
            "ok": True,
            "balance": int(res.balance),
            "txn_id": res.txn_id,
            "points": pts,
            "duplicate": True,
        }

    _loyalty_txn(
        db,
        txn_id=res.txn_id,
        household_id=household_id,
        customer_id=redeeming_customer_id,
        ttype="POOL_REDEEM",
        points=pts,
        order_id=None,
        reason="Family pool redemption",
        actor=_actor_id(actor),
    )
    _audit(
        db,
        "family_wallet.pool_redeem",
        household_id,
        actor=_actor_id(actor),
        detail={
            "points": pts,
            "redeeming_customer_id": redeeming_customer_id,
            "otp_id": otp_id,
            "balance_after": int(res.balance),
        },
    )
    return {
        "ok": True,
        "balance": int(res.balance),
        "txn_id": res.txn_id,
        "points": pts,
        "duplicate": False,
    }
