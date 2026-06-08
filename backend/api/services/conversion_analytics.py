"""IMS 2.0 - F24 Optometrist -> retail conversion analytics (#24).

PURE, READ-ONLY analytics. This module READS ``eye_tests`` + ``orders`` to
attribute completed eye tests to retail orders. It NEVER creates, mutates, or
cancels an order, never touches POS, and never changes any payment / balance.

Conversion rule (LOCKED -- F24 packet + DECISIONS sec 3):
  A COMPLETED eye test "converts" iff an order from the SAME ``customer_id`` with
  ``status NOT IN [CANCELLED, DRAFT]`` was created within ``conversion_window_days``
  (default 7) of the test's ``completed_at``. When a customer has several completed
  tests before an order, attribution goes to the MOST RECENT completed test placed
  before the order's date (so the optometrist who last saw the patient gets credit).

Revenue is ROLE-GATED (DECISIONS sec 3, locked): when ``include_revenue`` is False
(OPTOMETRIST callers) every revenue figure is emitted as ``None`` -- present-but-null,
never the rupee amount and never ``0`` -- mirroring the cost_mask philosophy
(``services/cost_mask.py``): never send the browser a number the role may not see.

The join is done in Python (not a Mongo $lookup pipeline) for Railway-Mongo
compatibility + testability; data volume is hundreds of tests/store/month, not
millions (F24 packet "Why Python-side"). No emoji (Windows cp1252).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

# Orders in these states never count as a conversion (F24 packet + reports.py
# _orders_in_window mirror the same exclusion).
_NON_CONVERTING_STATUSES = {"CANCELLED", "DRAFT"}


def _empty() -> Dict:
    """Fail-soft envelope: missing DB / repos -> empty dashboard, never raises."""
    return {"store_summary": {}, "rows": []}


def _order_revenue(order: dict) -> float:
    """Billable amount of an order. Field-fallback chain mirrors reports.py
    ``_order_revenue`` so legacy docs (pre grand_total rename) don't zero out."""
    for k in ("grand_total", "final_amount", "total_amount", "total"):
        v = order.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


def _parse_dt(value) -> Optional[datetime]:
    """Coerce a stored timestamp (datetime or ISO string) to a naive datetime.

    ``eye_tests.completed_at`` is stored as ``datetime.now().isoformat()`` (a
    string); ``orders.created_at`` is a Mongo datetime. Both must compare against
    the conversion window, so normalise to naive datetime. Returns None on junk.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Tolerate a trailing 'Z' (UTC designator) that fromisoformat rejects
        # on older Pythons.
        if s.endswith("Z"):
            s = s[:-1]
        try:
            dt = datetime.fromisoformat(s)
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except ValueError:
            # Date-only fallback (YYYY-MM-DD).
            try:
                return datetime.fromisoformat(s[:10])
            except ValueError:
                return None
    return None


def get_conversion_dashboard(
    eye_test_repo,
    order_repo,
    *,
    store_ids: List[str],
    from_date: date,
    to_date: date,
    conversion_window_days: int = 7,
    include_revenue: bool = False,
    optometrist_id_filter: Optional[str] = None,
) -> Dict:
    """Compute the optometrist -> retail conversion dashboard for a store scope.

    Args:
      eye_test_repo: repository over ``eye_tests`` (needs ``find_many``).
      order_repo: repository over ``orders`` (needs ``find_many``).
      store_ids: stores in scope (OPTOMETRIST/STORE_MANAGER -> one; HQ -> many).
      from_date / to_date: inclusive test_date window (ISO date).
      conversion_window_days: order-after-test window (default 7).
      include_revenue: False for OPTOMETRIST -> revenue fields emitted as None.
      optometrist_id_filter: when set, only this optometrist's rows are returned
        (OPTOMETRIST self-scope; enforced server-side, not trusted from the query).

    Returns the ``{"store_summary": {...}, "rows": [...]}`` envelope documented in
    the F24 packet. Fail-soft: missing repo -> ``_empty()``.
    """
    if eye_test_repo is None or order_repo is None:
        return _empty()
    if not store_ids:
        return _empty()

    window = timedelta(days=int(conversion_window_days))

    # 1. Pull COMPLETED eye tests for the store scope + [from_date, to_date].
    #    test_date is a lexicographic ISO YYYY-MM-DD string (see
    #    clinical_repository.get_store_tests_in_range), so a string range == date
    #    range. limit=0 => all matching (no silent truncation, BUG-061 pattern).
    test_filter: Dict = {
        "store_id": {"$in": list(store_ids)},
        "status": "COMPLETED",
        "test_date": {
            "$gte": from_date.isoformat(),
            "$lte": to_date.isoformat(),
        },
    }
    if optometrist_id_filter:
        test_filter["optometrist_id"] = optometrist_id_filter
    try:
        tests = eye_test_repo.find_many(test_filter, limit=0) or []
    except Exception:  # noqa: BLE001 - read path must never raise
        tests = []

    # 2. Distinct customer ids that are attributable (have a linked customer).
    customer_ids = sorted(
        {
            t.get("customer_id")
            for t in tests
            if t.get("customer_id")
        }
    )

    # 3. Pull candidate orders for those customers. Widen the lower bound by the
    #    window so an order placed just after a late-period test is still caught;
    #    exclude CANCELLED / DRAFT at the source.
    orders_by_customer: Dict[str, List[dict]] = {}
    if customer_ids:
        start_dt = datetime.combine(from_date, datetime.min.time())
        end_dt = datetime.combine(to_date, datetime.max.time()) + window
        order_filter: Dict = {
            "customer_id": {"$in": customer_ids},
            "created_at": {"$gte": start_dt, "$lte": end_dt},
            "status": {"$nin": list(_NON_CONVERTING_STATUSES)},
        }
        try:
            orders = order_repo.find_many(order_filter, limit=0) or []
        except Exception:  # noqa: BLE001
            orders = []
        for o in orders:
            cid = o.get("customer_id")
            created = _parse_dt(o.get("created_at"))
            if not cid or created is None:
                continue
            # Belt-and-braces: re-check status in Python in case a fake/loose
            # repo ignored the $nin filter.
            if str(o.get("status", "")).upper() in _NON_CONVERTING_STATUSES:
                continue
            orders_by_customer.setdefault(cid, []).append(
                {"created_at": created, "revenue": _order_revenue(o),
                 "order_number": o.get("order_number") or o.get("order_id")}
            )
        # Earliest order first per customer, for deterministic "first order in
        # window" selection.
        for lst in orders_by_customer.values():
            lst.sort(key=lambda x: x["created_at"])

    # 4. Attribution. For each customer, walk their orders; for each order find
    #    the MOST RECENT completed test (by completed_at, across ALL optometrists)
    #    that is on-or-before the order date AND within the window. That test's
    #    optometrist gets the conversion credit. An order can credit at most one
    #    test; a test can be credited by at most its first qualifying order.
    tests_by_customer: Dict[str, List[dict]] = {}
    for t in tests:
        cid = t.get("customer_id")
        if not cid:
            continue
        completed = _parse_dt(t.get("completed_at")) or _parse_dt(
            t.get("test_date")
        )
        if completed is None:
            continue
        tests_by_customer.setdefault(cid, []).append(
            {
                "test_id": t.get("test_id"),
                "optometrist_id": t.get("optometrist_id"),
                "completed_at": completed,
            }
        )

    # converted test_id -> {order_number, revenue, days}
    converted_tests: Dict[str, dict] = {}
    for cid, ords in orders_by_customer.items():
        cust_tests = sorted(
            tests_by_customer.get(cid, []),
            key=lambda x: x["completed_at"],
        )
        if not cust_tests:
            continue
        used_test_ids = set()
        for order in ords:
            o_dt = order["created_at"]
            # Candidate tests: completed on-or-before the order and within window,
            # not already credited. Pick the MOST RECENT (max completed_at).
            best_id: Optional[str] = None
            best_dt: Optional[datetime] = None
            for ct in cust_tests:
                tid = ct["test_id"]
                if tid in used_test_ids:
                    continue
                completed_at = ct["completed_at"]
                delta = o_dt - completed_at
                if timedelta(0) <= delta <= window:
                    if best_dt is None or completed_at > best_dt:
                        best_id = tid
                        best_dt = completed_at
            if best_id is None or best_dt is None:
                continue
            used_test_ids.add(best_id)
            converted_tests[best_id] = {
                "order_number": order["order_number"],
                "revenue": order["revenue"],
                "days": max(0, (o_dt - best_dt).days),
            }

    # 5. Aggregate per optometrist.
    rows_acc: Dict[str, dict] = {}
    for t in tests:
        oid = t.get("optometrist_id") or "UNKNOWN"
        row = rows_acc.setdefault(
            oid,
            {
                "optometrist_id": oid,
                "optometrist_name": t.get("optometrist_name") or oid,
                "tests_completed": 0,
                "converted_count": 0,
                "unattributed_tests": 0,
                "_days": [],
                "_revenue": 0.0,
                "_orders": [],
            },
        )
        row["tests_completed"] += 1
        # Prefer a real name over the id fallback if a later test carries one.
        if t.get("optometrist_name") and row["optometrist_name"] == row["optometrist_id"]:
            row["optometrist_name"] = t.get("optometrist_name")
        if not t.get("customer_id"):
            row["unattributed_tests"] += 1
            continue
        hit = converted_tests.get(t.get("test_id"))
        if hit:
            row["converted_count"] += 1
            row["_days"].append(hit["days"])
            row["_revenue"] += hit["revenue"]
            row["_orders"].append(
                {
                    "order_number": hit["order_number"],
                    "amount": hit["revenue"] if include_revenue else None,
                    "days_after_test": hit["days"],
                }
            )

    rows: List[dict] = []
    for row in rows_acc.values():
        tc = row["tests_completed"]
        cc = row["converted_count"]
        days = row.pop("_days")
        revenue = row.pop("_revenue")
        orders_list = row.pop("_orders")
        rate = round(cc / tc * 100, 1) if tc else 0.0
        avg_days = round(sum(days) / len(days), 1) if days else None
        out = {
            "optometrist_id": row["optometrist_id"],
            "optometrist_name": row["optometrist_name"],
            "tests_completed": tc,
            "converted_count": cc,
            "conversion_rate_pct": rate,
            "avg_days_to_order": avg_days,
            "unattributed_tests": row["unattributed_tests"],
            # Revenue is role-gated: None (present-but-null) when not permitted.
            "revenue_attributed": round(revenue, 2) if include_revenue else None,
            "avg_order_value": (
                round(revenue / cc, 2) if (include_revenue and cc) else None
            ),
            "orders": orders_list,
        }
        rows.append(out)

    rows.sort(key=lambda r: (-r["converted_count"], -r["tests_completed"]))

    # 6. Store summary.
    total_tests = sum(r["tests_completed"] for r in rows)
    total_converted = sum(r["converted_count"] for r in rows)
    total_unattributed = sum(r["unattributed_tests"] for r in rows)
    total_revenue = sum(
        (r["revenue_attributed"] or 0.0) for r in rows
    ) if include_revenue else None
    summary = {
        "tests_completed": total_tests,
        "converted": total_converted,
        "conversion_rate_pct": (
            round(total_converted / total_tests * 100, 1) if total_tests else 0.0
        ),
        "unattributed_tests": total_unattributed,
        "revenue_attributed": (
            round(total_revenue, 2) if include_revenue else None
        ),
    }

    return {"store_summary": summary, "rows": rows}
