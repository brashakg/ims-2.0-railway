"""
IMS 2.0 - Online order Rx FLAG-and-HOLD (clinical compliance for online sales)
==============================================================================
Owner decision (2026-06-30): a SPECTACLE / prescription-lens line sold ONLINE
(Shopify own-store, ONDC) must never be auto-dispensed without a valid, customer-
matching, non-expired prescription -- BUT a paid online sale is NEVER refused.
The order is always booked; if its Rx is MISSING / NOT-FOUND / EXPIRED, or its
line powers are out of clinical range, we FLAG it (rx_pending) + HOLD it
(fulfillment_hold) + raise ONE follow-up TASK so the fulfilling store collects /
verifies the prescription before dispensing.

This is the online counterpart of the POS gate
(routers/orders.py::_validate_order_line_rx). It reuses the SAME canonical pieces
rather than re-deriving any of them:

  * api.services.rx_validation.is_rx_required_line -- which line types REQUIRE an
    Rx (spectacle / Rx lens) vs are EXEMPT (frame / sunglass / ready-reader /
    CONTACT lens). Contacts NEVER flag.
  * api.services.rx_validation._validate_rx_value / _validate_axis -- the clinical
    power range + 0.25-diopter grid + whole-axis / cyl-requires-axis rules.
  * routers/prescriptions._rx_validity -- expiry computation (prescription_date /
    test_date + validity_months), so "expired" means the same thing everywhere.

The difference from POS: POS RAISES (422 -> the cashier fixes it before billing).
Online NEVER raises -- it records the problem on the order doc + a task.

The follow-up TASK reuses the canonical `tasks`-collection insert pattern that
routers/transfers.py::_create_receive_mismatch_task uses (the same collection the
Tasks module + escalation engine read), so an Rx-pending online order surfaces in
exactly the same worklist a receive-discrepancy does.

ASCII only (Windows cp1252) -- no emoji / unicode in any string here.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Hold reason codes stamped onto the order doc + the follow-up task.
HOLD_MISSING = "RX_MISSING"      # required Rx line carried no prescription link
HOLD_NOT_FOUND = "RX_NOT_FOUND"  # linked prescription id did not resolve
HOLD_MISMATCH = "RX_CUSTOMER_MISMATCH"  # Rx belongs to a different customer
HOLD_EXPIRED = "RX_EXPIRED"      # prescription is past validity
HOLD_POWER = "RX_POWER_OUT_OF_RANGE"  # a line power failed clinical validation


def _line_get(line: Dict[str, Any], *keys, default=None):
    """First present value among `keys` on a line dict (tolerant of the several
    field spellings the online payloads carry)."""
    for k in keys:
        if k in line and line[k] not in (None, ""):
            return line[k]
    return default


def _line_label(line: Dict[str, Any]) -> str:
    """Human label for a line in a hold reason / task body."""
    return str(
        _line_get(
            line,
            "product_name",
            "name",
            "title",
            "sku",
            "product_id",
            default="item",
        )
    )


def validate_line_powers(line: Dict[str, Any]) -> List[str]:
    """Run the CANONICAL clinical power validation on one line's sph/cyl/add/axis.

    Returns a list of human-readable problem strings (EMPTY when the powers are
    fine or absent). NEVER raises -- the online contract is flag-not-reject, so a
    bad power is recorded, not thrown. Reuses the exact validators the POS / Rx
    paths use (api.services.rx_validation), so "out of range" means the same
    thing online as it does at the counter.
    """
    from .rx_validation import _validate_axis, _validate_rx_value

    problems: List[str] = []
    sph = _line_get(line, "sph")
    cyl = _line_get(line, "cyl")
    add = _line_get(line, "add")
    axis = _line_get(line, "axis")

    # Only validate values that are actually present -- an online frame line, or a
    # lens line without captured powers, simply has nothing to check here.
    for value, field in ((sph, "sph"), (cyl, "cyl"), (add, "add")):
        if value in (None, ""):
            continue
        try:
            _validate_rx_value(str(value), field)
        except ValueError as exc:
            problems.append(str(exc))
    # AXIS is range/whole-checked, and mandatory when cyl is non-zero.
    if axis not in (None, "") or (cyl not in (None, "")):
        try:
            _validate_axis(axis, cyl=cyl)
        except ValueError as exc:
            problems.append(str(exc))
    return problems


def find_valid_prescription_id(
    db, customer_id: Optional[str], line: Dict[str, Any]
) -> Optional[str]:
    """Best-effort: does THIS line carry / can we find a valid, customer-matching,
    non-expired prescription? Returns the prescription_id when one is valid, else
    None (the caller then flags the order).

    Resolution order:
      1. an explicit prescription_id on the line -> verify it (customer + expiry).
      2. otherwise look up the customer's prescriptions and accept the most-recent
         non-expired one.

    Fail-SOFT: no DB / no repo / any error -> None (treated as "no valid Rx", so
    the order is flagged for staff to collect it -- we never block the sale and
    never crash ingestion).
    """
    rx_repo = None
    try:
        from ..dependencies import get_prescription_repository

        rx_repo = get_prescription_repository()
    except Exception:  # noqa: BLE001
        rx_repo = None
    if rx_repo is None:
        return None

    # Expiry helper (shared with POS / clinical so "expired" is consistent).
    try:
        from ..routers.prescriptions import _rx_validity
    except Exception:  # noqa: BLE001
        _rx_validity = None  # type: ignore[assignment]

    def _is_match_and_valid(rx: Dict[str, Any]) -> bool:
        if not rx:
            return False
        rx_cust = rx.get("customer_id") or rx.get("customerId")
        if customer_id and rx_cust and str(rx_cust) != str(customer_id):
            return False
        if _rx_validity is not None:
            try:
                _expiry, is_valid = _rx_validity(rx)
            except Exception:  # noqa: BLE001
                is_valid = None
            if is_valid is False:
                return False
        return True

    # 1. explicit link on the line.
    explicit = _line_get(line, "prescription_id", "prescriptionId", "rx_id")
    if explicit:
        explicit = str(explicit).strip()
        try:
            rx = rx_repo.find_by_id(explicit)
        except Exception:  # noqa: BLE001
            rx = None
        if rx and _is_match_and_valid(rx):
            return explicit
        return None  # the linked Rx is bad (not found / wrong customer / expired)

    # 2. fall back to the customer's prescriptions.
    if not customer_id:
        return None
    try:
        rxs = rx_repo.find_by_customer(str(customer_id)) or []
    except Exception:  # noqa: BLE001
        rxs = []
    for rx in rxs:
        if _is_match_and_valid(rx):
            return rx.get("prescription_id") or rx.get("prescriptionId") or rx.get("id")
    return None


def evaluate_rx_hold(
    db,
    items: List[Dict[str, Any]],
    customer_id: Optional[str],
) -> Dict[str, Any]:
    """Decide whether an online order must be held pending an Rx, and why.

    For EVERY line:
      * power-validate (range / 0.25 step / axis) -- always, even on exempt lines,
        because an out-of-range power is a data error staff must catch.
      * if the line REQUIRES an Rx (spectacle / Rx lens; contacts + frames are
        EXEMPT), confirm a valid customer-matching non-expired prescription
        exists; if not, the order is held.

    Returns:
      {
        "rx_pending": bool,         # True -> flag + hold + task
        "reasons": [str, ...],      # short reason codes (deduped, ordered)
        "lines": [                  # per problem line (for the task body)
            {"label": str, "issues": [str, ...]}, ...
        ],
        "detail": str,              # one-line human summary for rx_hold_reason
      }
    NEVER raises.
    """
    from .rx_validation import is_rx_required_line

    reasons: List[str] = []
    problem_lines: List[Dict[str, Any]] = []

    for line in items or []:
        if not isinstance(line, dict):
            continue
        label = _line_label(line)
        issues: List[str] = []

        # (a) power validation runs on EVERY line (exempt or not).
        power_problems = validate_line_powers(line)
        if power_problems:
            if HOLD_POWER not in reasons:
                reasons.append(HOLD_POWER)
            issues.extend(power_problems)

        # (b) Rx-required check only for spectacle / Rx-lens lines.
        item_type = _line_get(line, "item_type", "itemType")
        category = _line_get(line, "category")
        if is_rx_required_line(item_type, category):
            rx_id = find_valid_prescription_id(db, customer_id, line)
            if not rx_id:
                explicit = _line_get(line, "prescription_id", "prescriptionId", "rx_id")
                if explicit:
                    code = HOLD_NOT_FOUND
                    issues.append(
                        f"linked prescription '{explicit}' is missing / "
                        f"not for this customer / expired"
                    )
                else:
                    code = HOLD_MISSING
                    issues.append("no prescription on file for this customer")
                if code not in reasons:
                    reasons.append(code)

        if issues:
            problem_lines.append({"label": label, "issues": issues})

    rx_pending = bool(reasons)
    detail = ""
    if rx_pending:
        bits = [
            f"{pl['label']}: {'; '.join(pl['issues'])}" for pl in problem_lines
        ]
        detail = " | ".join(bits)[:1000]
    return {
        "rx_pending": rx_pending,
        "reasons": reasons,
        "lines": problem_lines,
        "detail": detail,
    }


def raise_rx_hold_task(
    db,
    *,
    order_id: str,
    order_ref: str,
    store_id: Optional[str],
    channel: str,
    evaluation: Dict[str, Any],
) -> Optional[str]:
    """Insert ONE follow-up task for an Rx-pending online order, store-scoped to
    the fulfilling store. Mirrors transfers._create_receive_mismatch_task (same
    canonical `tasks` collection the Tasks module + escalation engine read).

    IDEMPOTENT: guards on (order_id, task_type) so re-ingesting the same online
    order never raises a second task. Fail-soft: no DB / any error -> None, never
    blocks the order that is already booked.
    """
    if db is None:
        return None
    try:
        tasks = db.get_collection("tasks")
    except Exception:  # noqa: BLE001
        tasks = None
    if tasks is None:
        return None

    task_type = "online_rx_hold"
    # Idempotency: a task for this order already exists -> do nothing.
    try:
        existing = tasks.find_one({"order_id": order_id, "task_type": task_type})
        if existing:
            return existing.get("task_id")
    except Exception:  # noqa: BLE001
        # If the lookup fails we still try to insert; the order-doc rx_pending
        # flag is the primary signal, so a rare double task is acceptable but the
        # caller-side flag guard usually prevents even reaching here on a replay.
        pass

    lines = evaluation.get("lines") or []
    line_bits = [
        f"- {pl['label']}: {'; '.join(pl['issues'])}" for pl in lines
    ]
    line_text = "\n".join(line_bits) if line_bits else "(see order Rx fields)"
    task_id = f"TSK-{uuid.uuid4().hex[:10].upper()}"
    try:
        tasks.insert_one(
            {
                "task_id": task_id,
                "task_type": task_type,
                "title": f"Collect prescription before dispensing online order {order_ref}",
                "description": (
                    f"Online ({channel}) order {order_ref} contains a prescription "
                    f"lens that is NOT backed by a valid, customer-matching, "
                    f"non-expired prescription. The paid order was booked, but it "
                    f"must NOT be dispensed until the Rx is collected / verified.\n"
                    f"Lines needing attention:\n{line_text}"
                ),
                "status": "PENDING",
                "priority": "P2",
                "store_id": store_id,
                "source": "ONLINE_RX_HOLD",
                "channel": channel,
                "order_id": order_id,
                "order_ref": order_ref,
                "rx_hold_reasons": evaluation.get("reasons") or [],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "created_by": "system:online_rx_hold",
            }
        )
        return task_id
    except Exception as exc:  # noqa: BLE001 - task is a side-channel, fail-soft
        logger.warning(
            "[ONLINE_RX_HOLD] follow-up task create skipped for %s: %s",
            order_id,
            exc,
        )
        return None
