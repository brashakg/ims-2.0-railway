"""Atomic, fail-closed approval of the unpaid exposure a CREDIT tender opens.

One account document serializes reservations across workers/stores. Reservations
remain counted until the payment is visible, or its order token is fenced off.
Expiry alone never frees money: recovery first invalidates the exact token the
old worker needs to write. No transaction or process-local lock is required.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from uuid import uuid4

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError


def is_walkin(customer_id) -> bool:
    value = str(customer_id or "").strip().lower()
    return not value or value in {"walkin", "walk-in"} or value.startswith("walkin-")


def _money(value) -> float:
    value = float(value or 0)
    if not isfinite(value) or value < 0:
        raise ValueError("Unverifiable credit amount")
    return round(value, 2)


def _owed(order) -> float:
    from .delivery_gate import order_balance_due

    due = order_balance_due(order)
    if due is None:
        raise HTTPException(503, "Outstanding credit could not be verified. Please retry.")
    return _money(due)


def _snapshot(order):
    # Every field used to approve the exposure must still describe this sale.
    keys = ("payments", "grand_total", "balance_due", "amount_paid", "status",
            "customer_id", "customer_phone", "store_id", "credit_fence",
            # PR #1264: a refund-advance claim landing mid-payment voids the write.
            "advance_refund_id")
    return {key: order[key] if key in order else {"$exists": False} for key in keys}


def _exposures(orders, customer_id, customer, order_id):
    if is_walkin(customer_id):
        query = {"order_id": order_id}
    else:
        matches = [{"customer_id": customer_id}]
        phone = customer.get("phone") or customer.get("mobile")
        if phone:
            matches.append({"customer_phone": phone})
        query = {"$or": matches}
    query["status"] = {"$ne": "CANCELLED"}
    result = {}
    # A raw cursor has no repository's 500-row cap or fail-soft empty fallback.
    for doc in orders.find(query):
        if any(str(p.get("method", "")).upper() == "CREDIT" for p in doc.get("payments") or []):
            result[doc["order_id"]] = _owed(doc)
    return result


def outstanding_credit(orders, customer_id, customer) -> float:
    """Preview the same unpaid and in-flight exposure as an approval."""
    try:
        if is_walkin(customer_id):
            return 0.0  # anonymous shoppers have a per-sale ceiling
        amounts = _exposures(orders, customer_id, customer, None)
        state = orders.database["credit_reservations"].find_one({"_id": f"customer:{customer_id}"}) or {}
        for entry in state.get("reservations", []):
            # An approval will fence stale requests before reusing capacity.
            # The preview stays read-only and permits that recovery attempt;
            # the server always rechecks actual debt before recording anything.
            if datetime.now(timezone.utc).timestamp() - entry["created_at"] > 120:
                continue
            pending = orders.find_one({"order_id": entry["order_id"]})
            if pending and pending.get("credit_fence") == entry["token"]:
                key = entry["order_id"]
                amounts[key] = max(amounts.get(key, 0), _money(entry["amount"]))
        return round(sum(amounts.values()), 2)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Outstanding credit could not be verified. Please retry.") from exc


def _recover(orders, reservations):
    keep = []
    now = datetime.now(timezone.utc).timestamp()
    for entry in reservations:
        order = orders.find_one({"order_id": entry["order_id"]})
        if not order or order.get("credit_fence") != entry["token"]:
            continue  # the old writer can no longer satisfy its order CAS
        if now - entry["created_at"] > 120:
            fenced = orders.update_one(
                {"order_id": entry["order_id"], "credit_fence": entry["token"]},
                {"$unset": {"credit_fence": ""}},
            )
            if fenced.matched_count:
                continue
            # A changed token also fences this reservation. A failed read raises.
            fresh = orders.find_one({"order_id": entry["order_id"]})
            if not fresh or fresh.get("credit_fence") != entry["token"]:
                continue
        keep.append(entry)
    return keep


def record_credit(repo, order, payment, customer_repo, *, handover=False) -> bool:
    """Reserve capacity, then append CREDIT with the approved order snapshot.

Anonymous sales use the chain default per order: strangers do not share an AR
account. Named customers include all their existing credit orders and phone-
matched legacy orders. The entire unpaid balance becomes AR, regardless of the
amount written on the promise row. Repeating CREDIT cannot double-count a bill.
"""
    orders = repo.collection
    token = uuid4().hex
    order_id = order["order_id"]
    account_id = None
    guards = None
    try:
        from ..routers.customers import effective_credit_limit

        if order.get("status") == "CANCELLED":
            raise HTTPException(400, "Cannot approve credit on a cancelled order.")
        customer_id = order.get("customer_id")
        customer = None
        if not is_walkin(customer_id):
            if customer_repo is None:
                raise ValueError("Customer account unavailable")
            customer = customer_repo.find_by_id(customer_id)
            if not customer:
                raise ValueError("Customer account unavailable")
        limit = effective_credit_limit(customer, strict=True)
        cash = sum(float(p.get("amount") or 0) for p in order.get("payments") or []
                   if str(p.get("method", "")).upper() != "CREDIT")
        if not isfinite(cash):
            raise ValueError("Existing cash tenders could not be verified")
        # add_payment recomputes the ladder from tenders. A stale lower stored
        # balance must not approve less exposure than that write will create.
        exposure = _owed(order) if handover else max(
            _owed(order), _money(max(0, _money(order.get("grand_total")) - cash)))
        account_id = (f"sale:{order_id}" if is_walkin(customer_id)
                      else f"customer:{customer_id}")
        guards = orders.database["credit_reservations"]
        # The built-in unique _id index is the cross-worker serialization key.
        try:
            guards.update_one({"_id": account_id}, {"$setOnInsert": {
                "version": 0, "reservations": [],
            }}, upsert=True)
        except DuplicateKeyError:
            pass
        claim = orders.update_one(
            {"order_id": order_id, **_snapshot(order)},
            {"$set": {"credit_fence": token}},
        )
        if not claim.matched_count:
            raise HTTPException(409, "The order changed while checking credit. Please retry.")
        approved_order = {**order, "credit_fence": token}
        for _ in range(12):
            state = guards.find_one({"_id": account_id})
            reservations = _recover(orders, state["reservations"])
            exposure_by_order = _exposures(orders, customer_id, customer or {}, order_id)
            for entry in reservations:
                key = entry["order_id"]
                exposure_by_order[key] = max(exposure_by_order.get(key, 0), entry["amount"])
            exposure_by_order[order_id] = max(exposure_by_order.get(order_id, 0), exposure)
            proposed = round(sum(exposure_by_order.values()), 2)
            if proposed > limit:
                raise HTTPException(400, f"Credit limit exceeded: limit Rs {limit:.2f}; "
                                    f"unpaid credit including this sale Rs {proposed:.2f}. "
                                    "Collect payment or ask an Admin to review the limit.")
            reservation = {"order_id": order_id, "token": token, "amount": exposure,
                           "created_at": datetime.now(timezone.utc).timestamp()}
            saved = guards.update_one(
                {"_id": account_id, "version": state["version"]},
                {"$set": {"reservations": reservations + [reservation]}, "$inc": {"version": 1}},
            )
            if saved.matched_count:
                break
        else:
            raise HTTPException(409, "Another credit sale is being recorded. Please retry.")

        if handover:
            # Handover approves the existing receivable; it collects no money.
            # Preserve legacy paid/balance figures even when the old cash legs
            # were imported without an itemised payments array.
            if any(p.get("idempotency_key") == payment["idempotency_key"]
                   for p in order.get("payments") or []):
                return True
            result = orders.update_one(
                {"order_id": order_id, **_snapshot(approved_order)},
                {"$push": {"payments": payment}, "$set": {
                    "credit_sale": True, "payment_status": "CREDIT", "bill_type": "CREDIT",
                }},
            )
            recorded = bool(result.matched_count)
        else:
            recorded = repo.add_payment(order_id, payment, expected_order=approved_order)
        if recorded:
            return True
        # A lost write reply is successful only if this exact payment persisted.
        fresh = orders.find_one({"order_id": order_id})
        if fresh and any(p.get("payment_id") == payment["payment_id"] for p in fresh.get("payments") or []):
            return True
        raise HTTPException(409, "The order changed while recording credit. Please retry.")
    except HTTPException:
        raise
    except Exception as exc:
        # Both writers may have committed before the connection lost its reply.
        try:
            fresh = orders.find_one({"order_id": order_id})
            if fresh and any(p.get("payment_id") == payment["payment_id"]
                             for p in fresh.get("payments") or []):
                return True
        except Exception:
            pass
        raise HTTPException(503, "Credit could not be verified or recorded. Please retry.") from exc
    finally:
        # Invalidate BEFORE freeing capacity, even after an uncertain write.
        # If either write fails, retain the reservation for fenced recovery.
        try:
            orders.update_one({"order_id": order_id, "credit_fence": token},
                              {"$unset": {"credit_fence": ""}})
            if guards is not None and account_id:
                guards.update_one({"_id": account_id}, {
                    "$pull": {"reservations": {"token": token}}, "$inc": {"version": 1},
                })
        except Exception:
            pass


def record_handover_credit(order, current_user, db):
    """Called only by authorised handover/dispatch actions, never a preview."""
    if db is None:
        raise HTTPException(503, "Credit could not be verified or recorded. Please retry.")
    from database.repositories.order_repository import OrderRepository
    from database.repositories.customer_repository import CustomerRepository

    payment = {
        "payment_id": uuid4().hex, "method": "CREDIT", "amount": _owed(order),
        "reference": "Approved unpaid handover", "received_by": current_user.get("user_id"),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "idempotency_key": f"credit-handover:{order['order_id']}",
    }
    return record_credit(OrderRepository(db["orders"]), order, payment,
                         CustomerRepository(db["customers"]), handover=True)
