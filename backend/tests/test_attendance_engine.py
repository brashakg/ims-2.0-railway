"""
IMS 2.0 - HR Attendance Engine tests
====================================
Pure-logic coverage of backend/api/services/attendance_engine.py (no DB, no
network) plus a few HTTP-level guard tests via TestClient.

Covers (per spec):
  - late-mark calc vs grace window
  - geo enforcement in / out of radius (and role exemption)
  - week-off swap approval forbids self-approval
  - LWP day computation

Also asserts the product-owner decisions:
  - NO OVERTIME anywhere (engine exposes no overtime symbol)
  - record/report only (engine helpers are pure; they never import payroll)
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import attendance_engine as ae  # noqa: E402


# ============================================================================
# LATE-MARK CALC vs GRACE
# ============================================================================


class TestLateMark:
    def test_on_time_within_grace_is_not_late(self):
        # 10:00 shift, 15m grace, checked in 10:10 -> on time.
        r = ae.compute_late_mark("2026-05-01T10:10:00", "10:00", 15)
        assert r["is_late"] is False
        assert r["late_minutes"] == 0

    def test_exactly_at_grace_boundary_is_not_late(self):
        # 10:00 + 15m grace = 10:15 boundary; a check-in AT 10:15 is on time.
        r = ae.compute_late_mark("2026-05-01T10:15:00", "10:00", 15)
        assert r["is_late"] is False
        assert r["late_minutes"] == 0

    def test_one_minute_past_grace_is_late(self):
        # 10:16 is past the 10:15 boundary -> late, measured from 10:00 start.
        r = ae.compute_late_mark("2026-05-01T10:16:00", "10:00", 15)
        assert r["is_late"] is True
        assert r["late_minutes"] == 16

    def test_late_minutes_measured_from_shift_start_not_grace(self):
        r = ae.compute_late_mark("2026-05-01T10:20:00", "10:00", 15)
        assert r["is_late"] is True
        assert r["late_minutes"] == 20  # from 10:00, not from 10:15

    def test_zero_grace_late_immediately(self):
        r = ae.compute_late_mark("2026-05-01T09:01:00", "09:00", 0)
        assert r["is_late"] is True
        assert r["late_minutes"] == 1

    def test_early_check_in_never_late(self):
        r = ae.compute_late_mark("2026-05-01T09:45:00", "10:00", 0)
        assert r["is_late"] is False
        assert r["late_minutes"] == 0

    def test_no_shift_means_never_late(self):
        # Can't judge lateness without a configured shift start.
        r = ae.compute_late_mark("2026-05-01T23:59:00", None, 15)
        assert r["is_late"] is False
        assert r["late_minutes"] == 0

    def test_accepts_datetime_object(self):
        r = ae.compute_late_mark(datetime(2026, 5, 1, 10, 30), "10:00", 15)
        assert r["is_late"] is True
        assert r["late_minutes"] == 30

    def test_malformed_inputs_fail_soft(self):
        assert ae.compute_late_mark("not-a-time", "10:00", 15)["is_late"] is False
        assert ae.compute_late_mark("2026-05-01T10:30:00", "25:99", 15)["is_late"] is False
        assert ae.compute_late_mark(None, "10:00", 15)["is_late"] is False


# ============================================================================
# GEO ENFORCEMENT  (in / out of radius + role exemption)
# ============================================================================

# Two points ~150m apart, and ~600m apart, near a store anchor.
STORE_LAT, STORE_LNG = 23.6700, 86.1500


class TestGeofence:
    def test_role_1_3_exempt(self):
        for role in ("SUPERADMIN", "ADMIN", "AREA_MANAGER"):
            r = ae.evaluate_geofence(
                roles=[role],
                user_lat=None,
                user_lng=None,
                store_lat=STORE_LAT,
                store_lng=STORE_LNG,
            )
            assert r["exempt"] is True
            assert r["allowed"] is True
            assert r["reason"] == "EXEMPT_ROLE"

    def test_inside_radius_allowed(self):
        # ~11m north (0.0001 deg lat ~ 11.1m) -> well within 500m.
        r = ae.evaluate_geofence(
            roles=["SALES_STAFF"],
            user_lat=STORE_LAT + 0.0001,
            user_lng=STORE_LNG,
            store_lat=STORE_LAT,
            store_lng=STORE_LNG,
            radius_m=500,
        )
        assert r["allowed"] is True
        assert r["reason"] == "WITHIN_RADIUS"
        assert r["distance_m"] is not None and r["distance_m"] < 500

    def test_outside_radius_blocked(self):
        # ~0.01 deg lat north ~ 1.1km -> outside a 500m fence.
        r = ae.evaluate_geofence(
            roles=["STORE_MANAGER"],
            user_lat=STORE_LAT + 0.01,
            user_lng=STORE_LNG,
            store_lat=STORE_LAT,
            store_lng=STORE_LNG,
            radius_m=500,
        )
        assert r["allowed"] is False
        assert r["reason"] == "OUTSIDE_RADIUS"
        assert r["distance_m"] > 500

    def test_fenced_role_without_location_blocked(self):
        r = ae.evaluate_geofence(
            roles=["CASHIER"],
            user_lat=None,
            user_lng=None,
            store_lat=STORE_LAT,
            store_lng=STORE_LNG,
        )
        assert r["allowed"] is False
        assert r["reason"] == "LOCATION_REQUIRED"

    def test_no_store_coords_fail_soft_allow(self):
        # Cannot fence without store coords -> allow (mirrors login behaviour).
        r = ae.evaluate_geofence(
            roles=["SALES_STAFF"],
            user_lat=STORE_LAT,
            user_lng=STORE_LNG,
            store_lat=None,
            store_lng=None,
        )
        assert r["allowed"] is True
        assert r["reason"] == "NO_STORE_COORDS"

    def test_default_radius_applied_when_none(self):
        r = ae.evaluate_geofence(
            roles=["SALES_STAFF"],
            user_lat=STORE_LAT,
            user_lng=STORE_LNG,
            store_lat=STORE_LAT,
            store_lng=STORE_LNG,
            radius_m=None,
        )
        assert r["radius_m"] == ae.DEFAULT_GEOFENCE_RADIUS_M == 500

    def test_haversine_known_distance(self):
        # 0.0001 deg of latitude is ~11.1m anywhere on Earth.
        d = ae.haversine_distance_m(STORE_LAT, STORE_LNG, STORE_LAT + 0.0001, STORE_LNG)
        assert 10 < d < 13


# ============================================================================
# WEEK-OFF SWAP  -- self-approval forbidden
# ============================================================================


class TestSwapApproval:
    def test_manager_can_approve_others_request(self):
        d = ae.can_approve_swap(
            approver_id="mgr-1",
            approver_roles=["STORE_MANAGER"],
            requested_by="staff-9",
            swap_status="PENDING",
        )
        assert d["allowed"] is True
        assert d["reason"] == "OK"

    def test_self_approval_forbidden_even_for_manager(self):
        # A manager who filed their OWN request still cannot self-approve.
        d = ae.can_approve_swap(
            approver_id="mgr-1",
            approver_roles=["STORE_MANAGER"],
            requested_by="mgr-1",
            swap_status="PENDING",
        )
        assert d["allowed"] is False
        assert d["reason"] == "SELF_APPROVAL"

    def test_non_manager_cannot_approve(self):
        d = ae.can_approve_swap(
            approver_id="staff-2",
            approver_roles=["SALES_STAFF"],
            requested_by="staff-9",
            swap_status="PENDING",
        )
        assert d["allowed"] is False
        assert d["reason"] == "INSUFFICIENT_ROLE"

    def test_already_decided_request_rejected(self):
        d = ae.can_approve_swap(
            approver_id="mgr-1",
            approver_roles=["ADMIN"],
            requested_by="staff-9",
            swap_status="APPROVED",
        )
        assert d["allowed"] is False
        assert d["reason"] == "NOT_PENDING"

    def test_superadmin_can_approve(self):
        d = ae.can_approve_swap(
            approver_id="ceo",
            approver_roles=["SUPERADMIN"],
            requested_by="staff-9",
            swap_status="PENDING",
        )
        assert d["allowed"] is True


# ============================================================================
# LWP DAY COMPUTATION
# ============================================================================


class TestLwp:
    def test_absent_and_marked_lwp_count(self):
        records = [
            {"date": "2026-05-01", "status": "ABSENT"},
            {"date": "2026-05-02", "status": "LWP"},
            {"date": "2026-05-03", "status": "PRESENT"},
        ]
        r = ae.compute_lwp_days(records=records)
        assert r["lwp_days"] == 2.0
        assert r["absent_days"] == 1
        assert r["marked_lwp_days"] == 1

    def test_paid_leave_and_week_off_not_lwp(self):
        records = [
            {"date": "2026-05-01", "status": "LEAVE"},      # paid leave -> not LWP
            {"date": "2026-05-02", "status": "WEEK_OFF"},   # off -> not LWP
            {"date": "2026-05-03", "status": "HOLIDAY"},    # holiday -> not LWP
            {"date": "2026-05-04", "status": "PRESENT"},
        ]
        r = ae.compute_lwp_days(records=records)
        assert r["lwp_days"] == 0.0

    def test_half_day_counts_as_half(self):
        records = [{"date": "2026-05-05", "status": "HALF_DAY"}]
        r = ae.compute_lwp_days(records=records, half_day_as_half=True)
        assert r["lwp_days"] == 0.5
        assert r["half_days"] == 1

    def test_half_day_can_be_disabled(self):
        records = [{"date": "2026-05-05", "status": "HALF_DAY"}]
        r = ae.compute_lwp_days(records=records, half_day_as_half=False)
        assert r["lwp_days"] == 0.0

    def test_approved_unpaid_leave_expands_across_range(self):
        leaves = [
            {
                "from_date": "2026-05-10",
                "to_date": "2026-05-12",
                "leave_type": "UNPAID",
                "status": "APPROVED",
            }
        ]
        r = ae.compute_lwp_days(records=[], approved_unpaid_leaves=leaves)
        assert r["unpaid_leave_days"] == 3
        assert r["lwp_days"] == 3.0  # 10, 11, 12

    def test_unapproved_or_paid_leave_ignored(self):
        leaves = [
            {"from_date": "2026-05-10", "to_date": "2026-05-10",
             "leave_type": "UNPAID", "status": "PENDING"},   # not approved
            {"from_date": "2026-05-11", "to_date": "2026-05-11",
             "leave_type": "CASUAL", "status": "APPROVED"},  # paid type
        ]
        r = ae.compute_lwp_days(records=[], approved_unpaid_leaves=leaves)
        assert r["lwp_days"] == 0.0

    def test_overlapping_absent_and_leave_counted_once(self):
        # Day 10 is ABSENT in attendance AND inside an approved unpaid leave.
        records = [{"date": "2026-05-10", "status": "ABSENT"}]
        leaves = [
            {"from_date": "2026-05-10", "to_date": "2026-05-11",
             "leave_type": "UNPAID", "status": "APPROVED"}
        ]
        r = ae.compute_lwp_days(records=records, approved_unpaid_leaves=leaves)
        # Day 10 once + day 11 = 2 unique unpaid days (not 3).
        assert r["lwp_days"] == 2.0

    def test_full_lwp_overrides_half_day_same_date(self):
        # If a date is both HALF_DAY and an absent/LWP record, it shouldn't add
        # an extra 0.5 on top of the full day.
        records = [
            {"date": "2026-05-07", "status": "HALF_DAY"},
            {"date": "2026-05-07", "status": "ABSENT"},
        ]
        r = ae.compute_lwp_days(records=records, half_day_as_half=True)
        assert r["lwp_days"] == 1.0  # one full unpaid day, no extra half

    def test_empty_inputs(self):
        r = ae.compute_lwp_days(records=[])
        assert r["lwp_days"] == 0.0


# ============================================================================
# PRODUCT-OWNER GUARDS
# ============================================================================


class TestNoOvertime:
    def test_engine_exposes_no_overtime_symbol(self):
        names = [n for n in dir(ae) if "overtime" in n.lower() or n.lower() == "ot"]
        assert names == [], f"engine leaked overtime symbols: {names}"

    def test_engine_does_not_import_payroll(self):
        # Record/report only: the pure engine must not IMPORT or call the payroll
        # engine. (The word "payroll" appears in the module docstring explaining
        # this contract, so we scan import statements specifically, not prose.)
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(ae))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
                imported += [a.name for a in node.names]
        offenders = [m for m in imported if "payroll" in (m or "").lower()]
        assert offenders == [], f"attendance engine must not import payroll: {offenders}"


# ============================================================================
# HTTP-LEVEL GUARDS  (via TestClient -- auth + role gates)
# ============================================================================


class TestHttpGuards:
    def test_shifts_list_requires_auth(self, client):
        assert client.get("/api/v1/hr/shifts").status_code == 401

    def test_late_marks_requires_auth(self, client):
        assert client.get("/api/v1/hr/attendance/late-marks").status_code == 401

    def test_lwp_report_requires_auth(self, client):
        assert client.get("/api/v1/hr/reports/lwp?year=2026&month=5").status_code == 401

    def test_create_shift_forbidden_for_sales_staff(self, client, staff_headers):
        resp = client.post(
            "/api/v1/hr/shifts",
            headers=staff_headers,
            json={"name": "Morning", "start_time": "10:00", "end_time": "19:00"},
        )
        assert resp.status_code == 403

    def test_approve_swap_forbidden_for_sales_staff(self, client, staff_headers):
        resp = client.post(
            "/api/v1/hr/weekoff-swaps/some-id/approve", headers=staff_headers
        )
        assert resp.status_code == 403

    def test_create_shift_rejects_bad_time(self, client, auth_headers):
        resp = client.post(
            "/api/v1/hr/shifts",
            headers=auth_headers,
            json={"name": "Bad", "start_time": "99:99", "end_time": "19:00"},
        )
        assert resp.status_code == 422

    def test_weekoff_swap_same_date_rejected(self, client, auth_headers):
        resp = client.post(
            "/api/v1/hr/weekoff-swaps",
            headers=auth_headers,
            json={"from_date": "2026-05-10", "to_date": "2026-05-10"},
        )
        assert resp.status_code == 422

    def test_lwp_report_month_bounds(self, client, auth_headers):
        # month 13 is out of range -> 422
        resp = client.get(
            "/api/v1/hr/reports/lwp?year=2026&month=13", headers=auth_headers
        )
        assert resp.status_code == 422


class TestHalfDayClassification:
    """Pure classify_half_day rule (owner-requested settings-system half-day)."""

    def test_hours_below_min_is_half_day(self):
        # 09:00 -> 12:30 = 3.5h worked, threshold 4h.
        r = ae.classify_half_day(
            check_in="2026-05-01T09:00:00",
            check_out="2026-05-01T12:30:00",
            min_hours=4.0,
            late_after=None,
        )
        assert r["is_half_day"] is True
        assert r["reason"] == "HOURS_BELOW_MIN"
        assert r["hours_worked"] == 3.5

    def test_full_hours_is_full_day(self):
        # 09:00 -> 18:00 = 9h, threshold 4h.
        r = ae.classify_half_day(
            check_in="2026-05-01T09:00:00",
            check_out="2026-05-01T18:00:00",
            min_hours=4.0,
            late_after="13:00",
        )
        assert r["is_half_day"] is False
        assert r["reason"] == "FULL_DAY"
        assert r["hours_worked"] == 9.0

    def test_check_in_after_cutoff_is_half_day(self):
        # Arrived 14:00, cutoff 13:00 -> half-day on the late trigger alone.
        r = ae.classify_half_day(
            check_in="2026-05-01T14:00:00",
            check_out="2026-05-01T18:30:00",  # 4.5h -- would be a full day on hours
            min_hours=4.0,
            late_after="13:00",
        )
        assert r["is_half_day"] is True
        assert r["reason"] == "CHECK_IN_AFTER_CUTOFF"

    def test_late_trigger_fires_without_checkout(self):
        r = ae.classify_half_day(
            check_in="2026-05-01T13:30:00",
            check_out=None,
            min_hours=4.0,
            late_after="13:00",
        )
        assert r["is_half_day"] is True
        assert r["reason"] == "CHECK_IN_AFTER_CUTOFF"

    def test_on_time_no_checkout_is_full_day(self):
        # On time, not checked out yet -> can't judge hours -> not half-day (yet).
        r = ae.classify_half_day(
            check_in="2026-05-01T09:00:00",
            check_out=None,
            min_hours=4.0,
            late_after="13:00",
        )
        assert r["is_half_day"] is False
        assert r["reason"] == "FULL_DAY"

    def test_disabled_triggers_never_fire(self):
        # No min_hours + no late_after -> never half-day even on a 1h day.
        r = ae.classify_half_day(
            check_in="2026-05-01T09:00:00",
            check_out="2026-05-01T10:00:00",
            min_hours=None,
            late_after=None,
        )
        assert r["is_half_day"] is False

    def test_negative_span_does_not_false_flag(self):
        # check-out before check-in (bad data) -> guarded, not a half-day.
        r = ae.classify_half_day(
            check_in="2026-05-01T18:00:00",
            check_out="2026-05-01T09:00:00",
            min_hours=4.0,
            late_after=None,
        )
        assert r["is_half_day"] is False

    def test_garbage_inputs_do_not_raise(self):
        r = ae.classify_half_day(
            check_in="not-a-date",
            check_out="also-bad",
            min_hours="oops",
            late_after="25:99",
        )
        assert r["is_half_day"] is False
