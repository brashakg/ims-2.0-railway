"""
IMS 2.0 - Clinical abuse / fraud-signal detection
=================================================
Two layers of tests:

  1. Pure detector logic (``api.services.clinical_abuse``) -- each threshold is
     pinned: it triggers above the bound, stays silent below it, and respects
     the minimum-sample guard. No DB, no app.
  2. Endpoint role gating (``GET /clinical/abuse-detection``) -- the bare-app +
     get_current_user-override pattern from ``test_expenses_gating.py``:
     SALES_STAFF / OPTOMETRIST get 403; STORE_MANAGER does not.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import clinical_abuse as ab  # noqa: E402
from api.routers import clinical  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# ============================================================================
# 1a. EXCESSIVE REDOS PER OPTOMETRIST
# ============================================================================


class TestRedoSeverity:
    def test_triggers_warning_above_threshold(self):
        # 2 / 10 = 20% which is >= 15% WARN but < 30% CRITICAL.
        assert ab.redo_severity(2, 10) == "warning"

    def test_triggers_critical_at_high_rate(self):
        # 4 / 10 = 40% which is >= 30% CRITICAL.
        assert ab.redo_severity(4, 10) == "critical"

    def test_silent_below_threshold(self):
        # 1 / 10 = 10% which is below the 15% WARN bound.
        assert ab.redo_severity(1, 10) is None

    def test_silent_at_zero_redos(self):
        assert ab.redo_severity(0, 20) is None

    def test_respects_min_sample(self):
        # 2 / 4 = 50% rate but only 4 tests (< REDO_MIN_SAMPLE) -> no alert.
        assert ab.redo_severity(2, 4) is None

    def test_zero_tests_is_silent(self):
        assert ab.redo_severity(0, 0) is None

    def test_exact_warn_boundary_triggers(self):
        # Exactly 15% (3/20) should trip WARNING (>= comparison).
        assert ab.redo_severity(3, 20) == "warning"

    def test_redo_rate_percent(self):
        assert ab.redo_rate_percent(2, 10) == 20.0
        assert ab.redo_rate_percent(0, 0) == 0.0


# ============================================================================
# 1b. OUT-OF-RANGE / SUSPICIOUS Rx VALUES
# ============================================================================


class TestOutOfRangeEye:
    def test_sph_at_bound_is_out_of_range(self):
        assert ab.is_eye_out_of_range({"sph": "20.00", "cyl": "0", "axis": 90}) is True

    def test_sph_beyond_bound(self):
        assert ab.is_eye_out_of_range({"sph": "-21.00"}) is True

    def test_cyl_beyond_bound(self):
        assert ab.is_eye_out_of_range({"cyl": "-6.50"}) is True

    def test_axis_out_of_range(self):
        assert ab.is_eye_out_of_range({"axis": 200}) is True
        assert ab.is_eye_out_of_range({"axis": 0}) is True

    def test_add_out_of_band(self):
        # An ADD of 0.50 is below the +0.75 minimum (and non-zero) -> suspicious.
        assert ab.is_eye_out_of_range({"add": "0.50"}) is True
        assert ab.is_eye_out_of_range({"add": "4.00"}) is True

    def test_normal_rx_is_clean(self):
        assert (
            ab.is_eye_out_of_range(
                {"sph": "-1.25", "cyl": "-0.50", "axis": 90, "add": "2.00"}
            )
            is False
        )

    def test_zero_add_is_not_flagged(self):
        # add == 0 means "no add", not an out-of-band value.
        assert ab.is_eye_out_of_range({"sph": "-1.00", "add": "0"}) is False

    def test_blank_or_junk_is_clean(self):
        assert ab.is_eye_out_of_range({}) is False
        assert ab.is_eye_out_of_range({"sph": "", "cyl": None}) is False
        assert ab.is_eye_out_of_range({"sph": "abc"}) is False
        assert ab.is_eye_out_of_range("not a dict") is False

    def test_is_rx_out_of_range_either_eye(self):
        rx = {
            "right_eye": {"sph": "-1.00"},
            "left_eye": {"sph": "21.00"},
        }
        assert ab.is_rx_out_of_range(rx) is True
        clean = {"right_eye": {"sph": "-1.00"}, "left_eye": {"sph": "+2.00"}}
        assert ab.is_rx_out_of_range(clean) is False


class TestOutOfRangeSeverity:
    def test_triggers_warning(self):
        # 2 / 10 = 20% (>= 20% WARN), and 2 >= MIN_COUNT.
        assert ab.out_of_range_severity(2, 10) == "warning"

    def test_triggers_critical(self):
        # 4 / 10 = 40% (>= 40% CRITICAL).
        assert ab.out_of_range_severity(4, 10) == "critical"

    def test_silent_below_rate(self):
        # 2 / 20 = 10% -> below 20% WARN.
        assert ab.out_of_range_severity(2, 20) is None

    def test_respects_min_count(self):
        # 1 hit in 5 tests = 20% rate but only 1 absolute hit (< MIN_COUNT=2).
        assert ab.out_of_range_severity(1, 5) is None

    def test_respects_min_sample(self):
        # 2 / 4 -> only 4 tests, below OUT_OF_RANGE_MIN_SAMPLE.
        assert ab.out_of_range_severity(2, 4) is None


# ============================================================================
# 1c. REPEAT TESTS FOR ONE PATIENT IN A SHORT WINDOW
# ============================================================================


class TestRepeatWindow:
    def test_three_in_window_triggers_warning(self):
        base = datetime(2026, 5, 1, 10, 0)
        dates = [base, base + timedelta(days=2), base + timedelta(days=4)]
        n = ab.max_tests_in_window(dates, ab.REPEAT_WINDOW_DAYS)
        assert n == 3
        assert ab.repeat_severity(n) == "warning"

    def test_five_in_window_triggers_critical(self):
        base = datetime(2026, 5, 1)
        dates = [base + timedelta(days=i) for i in range(5)]
        n = ab.max_tests_in_window(dates, ab.REPEAT_WINDOW_DAYS)
        assert n == 5
        assert ab.repeat_severity(n) == "critical"

    def test_spread_out_tests_are_silent(self):
        # 3 tests but each 10 days apart -> never 3 inside a 7-day window.
        base = datetime(2026, 1, 1)
        dates = [base, base + timedelta(days=10), base + timedelta(days=20)]
        n = ab.max_tests_in_window(dates, ab.REPEAT_WINDOW_DAYS)
        assert n == 1
        assert ab.repeat_severity(n) is None

    def test_two_visits_is_silent(self):
        base = datetime(2026, 5, 1)
        dates = [base, base + timedelta(days=1)]
        n = ab.max_tests_in_window(dates, ab.REPEAT_WINDOW_DAYS)
        assert n == 2
        assert ab.repeat_severity(n) is None

    def test_empty_and_single(self):
        assert ab.max_tests_in_window([], 7) == 0
        assert ab.max_tests_in_window([datetime(2026, 5, 1)], 7) == 1


# ============================================================================
# 1d. RAPID / IMPLAUSIBLY-FAST ENTRIES
# ============================================================================


class TestRapidBurst:
    def test_detects_tight_burst_as_critical(self):
        # 5 tests over 8 minutes -> 2.0 min avg gap -> CRITICAL (<= 3 min).
        base = datetime(2026, 5, 1, 10, 0)
        dates = [base + timedelta(minutes=2 * i) for i in range(5)]
        burst = ab.find_rapid_burst(dates)
        assert burst is not None
        assert burst["count"] == 5
        assert ab.rapid_severity(burst["avg_gap_minutes"]) == "critical"

    def test_moderate_pace_is_warning(self):
        # 5 tests, each 2.25 min apart -> 9 min span -> avg gap 2.25 -> critical.
        # Make a clearly-warning case: 5 tests spanning 9 min but with a gap
        # above the 3-min critical line is impossible at min_tests=5/10min, so
        # assert the warning branch directly via rapid_severity.
        assert ab.rapid_severity(5.0) == "warning"
        assert ab.rapid_severity(3.0) == "critical"

    def test_too_few_tests_no_burst(self):
        base = datetime(2026, 5, 1, 10, 0)
        dates = [base, base + timedelta(minutes=1), base + timedelta(minutes=2)]
        assert ab.find_rapid_burst(dates) is None

    def test_slow_pace_no_burst(self):
        # 5 tests but spread 30 min apart -> never 5 inside a 10-min window.
        base = datetime(2026, 5, 1, 9, 0)
        dates = [base + timedelta(minutes=30 * i) for i in range(5)]
        assert ab.find_rapid_burst(dates) is None

    def test_burst_within_longer_day(self):
        # A clean morning (spread out) then a 6-test burst in 5 minutes.
        morning = [datetime(2026, 5, 1, 9, 0) + timedelta(minutes=20 * i) for i in range(3)]
        burst_block = [datetime(2026, 5, 1, 14, 0) + timedelta(minutes=i) for i in range(6)]
        burst = ab.find_rapid_burst(morning + burst_block)
        assert burst is not None
        assert burst["count"] == 6


# ============================================================================
# 1e. DATE PARSING HELPERS
# ============================================================================


class TestDateHelpers:
    def test_parse_datetime_passthrough(self):
        dt = datetime(2026, 5, 1, 12, 0)
        assert ab.parse_dt(dt) == dt

    def test_parse_iso_string(self):
        assert ab.parse_dt("2026-05-01T12:00:00") == datetime(2026, 5, 1, 12, 0)

    def test_parse_iso_with_z(self):
        assert ab.parse_dt("2026-05-01T12:00:00Z") == datetime(2026, 5, 1, 12, 0)

    def test_parse_junk_is_none(self):
        assert ab.parse_dt("not-a-date") is None
        assert ab.parse_dt("") is None
        assert ab.parse_dt(None) is None

    def test_rx_date_prefers_prescription_date(self):
        rx = {
            "prescription_date": "2026-05-01T00:00:00",
            "test_date": "2026-04-01T00:00:00",
            "created_at": datetime(2026, 3, 1),
        }
        assert ab.rx_date(rx) == datetime(2026, 5, 1)

    def test_rx_date_falls_back(self):
        rx = {"created_at": datetime(2026, 3, 1, 8, 30)}
        assert ab.rx_date(rx) == datetime(2026, 3, 1, 8, 30)

    def test_rx_date_none_when_absent(self):
        assert ab.rx_date({}) is None


# ============================================================================
# 1f. ALERT ASSEMBLY (router pure helper)
# ============================================================================


class TestBuildAbuseAlerts:
    def _rx(self, **kw):
        base = {
            "prescription_id": kw.get("prescription_id", "rx"),
            "optometrist_id": kw.get("optometrist_id", "opt-1"),
            "optometrist_name": kw.get("optometrist_name", "Dr. A"),
            "customer_id": kw.get("customer_id", "cust-1"),
            "patient_name": kw.get("patient_name", "Pat"),
            "test_date": kw.get("test_date", "2026-05-01T10:00:00"),
            "right_eye": kw.get("right_eye", {"sph": "-1.00"}),
            "left_eye": kw.get("left_eye", {"sph": "-1.00"}),
        }
        base.update({k: v for k, v in kw.items() if k in base})
        return base

    def test_empty_input_no_alerts(self):
        assert clinical._build_abuse_alerts([], datetime(2026, 5, 1)) == []

    def test_high_redo_optometrist_flagged(self):
        # 10 tests for opt-1, 4 with a redo stamped -> 40% -> critical redo alert.
        rxs = []
        for i in range(10):
            r = self._rx(
                prescription_id=f"rx{i}",
                customer_id=f"c{i}",  # distinct patients to avoid repeat alert
                test_date=f"2026-05-0{(i % 9) + 1}T10:0{i}:00",
            )
            if i < 4:
                r["redo_count"] = 1
            rxs.append(r)
        alerts = clinical._build_abuse_alerts(rxs, datetime(2026, 5, 10))
        redo = [a for a in alerts if a["type"] == "high-redo-rate" and a["id"].startswith("redo-")]
        assert len(redo) == 1
        assert redo[0]["severity"] == "critical"
        assert redo[0]["optometristId"] == "opt-1"
        assert redo[0]["redoRate"] == 40.0

    def test_repeat_patient_flagged(self):
        # 3 tests for the SAME customer within a week -> exact-copy warning.
        rxs = [
            self._rx(prescription_id="a", test_date="2026-05-01T10:00:00"),
            self._rx(prescription_id="b", test_date="2026-05-03T10:00:00"),
            self._rx(prescription_id="c", test_date="2026-05-05T10:00:00"),
        ]
        alerts = clinical._build_abuse_alerts(rxs, datetime(2026, 5, 6))
        repeat = [a for a in alerts if a["type"] == "exact-copy"]
        assert len(repeat) == 1
        assert repeat[0]["severity"] == "warning"

    def test_out_of_range_flagged(self):
        # 10 tests, 2 with an out-of-range eye -> 20% -> WARNING. Distinct
        # patients & spread dates so no repeat/redo alert interferes.
        rxs = []
        for i in range(10):
            eye = {"sph": "21.00"} if i < 2 else {"sph": "-1.00"}
            rxs.append(
                self._rx(
                    prescription_id=f"rx{i}",
                    customer_id=f"c{i}",
                    test_date=f"2026-05-{(i * 3) + 1:02d}T10:00:00",
                    right_eye=eye,
                )
            )
        alerts = clinical._build_abuse_alerts(rxs, datetime(2026, 5, 31))
        oor = [a for a in alerts if a["id"].startswith("oor-")]
        assert len(oor) == 1
        assert oor[0]["severity"] == "warning"

    def test_out_of_range_critical_at_high_rate(self):
        # 5 tests, 2 out-of-range -> 40% -> CRITICAL.
        rxs = []
        for i in range(5):
            eye = {"sph": "21.00"} if i < 2 else {"sph": "-1.00"}
            rxs.append(
                self._rx(
                    prescription_id=f"rx{i}",
                    customer_id=f"c{i}",
                    test_date=f"2026-05-{(i * 5) + 1:02d}T10:00:00",
                    right_eye=eye,
                )
            )
        alerts = clinical._build_abuse_alerts(rxs, datetime(2026, 5, 30))
        oor = [a for a in alerts if a["id"].startswith("oor-")]
        assert len(oor) == 1
        assert oor[0]["severity"] == "critical"

    def test_clean_data_no_alerts(self):
        rxs = [
            self._rx(
                prescription_id=f"rx{i}",
                customer_id=f"c{i}",
                test_date=f"2026-05-{(i * 5) + 1:02d}T10:00:00",
            )
            for i in range(6)
        ]
        assert clinical._build_abuse_alerts(rxs, datetime(2026, 5, 30)) == []

    def test_alerts_match_frontend_shape(self):
        rxs = []
        for i in range(10):
            r = self._rx(prescription_id=f"rx{i}", customer_id=f"c{i}")
            if i < 4:
                r["redo_count"] = 1
            rxs.append(r)
        alerts = clinical._build_abuse_alerts(rxs, datetime(2026, 5, 10))
        assert alerts
        required = {"id", "type", "severity", "optometristName", "optometristId", "details", "timestamp"}
        for a in alerts:
            assert required.issubset(a.keys())
            assert a["type"] in ("high-redo-rate", "exact-copy", "suspicious-speed")
            assert a["severity"] in ("warning", "critical")


# ============================================================================
# 1g. OPTOMETRIST NAME RESOLUTION (backlog #4 -- no bare UUIDs on the card)
# ============================================================================


class TestOptometristNameResolution:
    """The owner saw 'Optometrist: 97d2a24c-...' -- when the Rx only stored an
    optometrist_id (or stuffed a UUID into the name field), the alert must show
    the resolved human name from the name_map."""

    _UUID = "97d2a24c-e5d8-4a1b-9c3d-0011223344ff"

    def _rxs_with_redos(self, opto_id, opto_name=None, n=10, redos=4):
        rxs = []
        for i in range(n):
            r = {
                "prescription_id": f"rx{i}",
                "optometrist_id": opto_id,
                "customer_id": f"c{i}",
                "test_date": f"2026-05-{(i % 28) + 1:02d}T10:0{i % 9}:00",
                "right_eye": {"sph": "-1.00"},
                "left_eye": {"sph": "-1.00"},
            }
            if opto_name is not None:
                r["optometrist_name"] = opto_name
            if i < redos:
                r["redo_count"] = 1
            rxs.append(r)
        return rxs

    def test_id_only_rx_resolves_to_name(self):
        # Rx rows carry ONLY a UUID optometrist_id (no name stored).
        rxs = self._rxs_with_redos(self._UUID, opto_name=None)
        name_map = {self._UUID: "Dr. Asha Verma"}
        alerts = clinical._build_abuse_alerts(rxs, datetime(2026, 5, 30), name_map)
        redo = [a for a in alerts if a["id"].startswith("redo-")]
        assert redo
        assert redo[0]["optometristName"] == "Dr. Asha Verma"

    def test_uuid_stuffed_into_name_field_is_overridden(self):
        # Backend bug variant: optometrist_name was populated with the UUID.
        rxs = self._rxs_with_redos(self._UUID, opto_name=self._UUID)
        name_map = {self._UUID: "Dr. Asha Verma"}
        alerts = clinical._build_abuse_alerts(rxs, datetime(2026, 5, 30), name_map)
        redo = [a for a in alerts if a["id"].startswith("redo-")]
        assert redo
        assert redo[0]["optometristName"] == "Dr. Asha Verma"

    def test_no_map_falls_back_to_id_not_crash(self):
        # Absent map -> previous behaviour (id shown), never a crash.
        rxs = self._rxs_with_redos(self._UUID, opto_name=None)
        alerts = clinical._build_abuse_alerts(rxs, datetime(2026, 5, 30))
        redo = [a for a in alerts if a["id"].startswith("redo-")]
        assert redo
        assert redo[0]["optometristName"] == self._UUID

    def test_real_name_kept_when_no_map_entry(self):
        # A genuine stored name (not id-shaped) is preserved even if not mapped.
        rxs = self._rxs_with_redos("opt-1", opto_name="Dr. Real Name")
        alerts = clinical._build_abuse_alerts(rxs, datetime(2026, 5, 30), {})
        redo = [a for a in alerts if a["id"].startswith("redo-")]
        assert redo
        assert redo[0]["optometristName"] == "Dr. Real Name"

    def test_looks_like_id_helper(self):
        assert clinical._looks_like_id(self._UUID) is True
        assert clinical._looks_like_id("97d2a24ce5d84a1b9c3d0011223344ff") is True
        assert clinical._looks_like_id("Dr. Asha Verma") is False
        assert clinical._looks_like_id("Asha") is False
        assert clinical._looks_like_id("") is False


# ============================================================================
# 2. ENDPOINT ROLE GATING
# ============================================================================


def _client_as(roles):
    app = FastAPI()
    app.include_router(clinical.router, prefix="/clinical")

    async def _fake_user():
        return {
            "user_id": "u1",
            "full_name": "Test User",
            "active_store_id": "store-001",
            "roles": roles,
        }

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


class TestAbuseEndpointGating:
    def test_sales_staff_blocked(self):
        client = _client_as(["SALES_STAFF"])
        resp = client.get("/clinical/abuse-detection", params={"store_id": "store-001"})
        assert resp.status_code == 403

    def test_optometrist_blocked(self):
        # The optometrists being measured must NOT see their own scorecard.
        client = _client_as(["OPTOMETRIST"])
        resp = client.get("/clinical/abuse-detection", params={"store_id": "store-001"})
        assert resp.status_code == 403

    def test_cashier_blocked(self):
        client = _client_as(["CASHIER"])
        resp = client.get("/clinical/abuse-detection", params={"store_id": "store-001"})
        assert resp.status_code == 403

    def test_store_manager_allowed(self):
        client = _client_as(["STORE_MANAGER"])
        resp = client.get("/clinical/abuse-detection", params={"store_id": "store-001"})
        assert resp.status_code != 403

    def test_area_manager_allowed(self):
        client = _client_as(["AREA_MANAGER"])
        resp = client.get("/clinical/abuse-detection", params={"store_id": "store-001"})
        assert resp.status_code != 403

    def test_admin_allowed(self):
        client = _client_as(["ADMIN"])
        resp = client.get("/clinical/abuse-detection", params={"store_id": "store-001"})
        assert resp.status_code != 403

    def test_superadmin_allowed(self):
        client = _client_as(["SUPERADMIN"])
        resp = client.get("/clinical/abuse-detection", params={"store_id": "store-001"})
        assert resp.status_code != 403

    def test_returns_envelope_shape(self):
        # With no DB the endpoint fail-softs to an empty (valid) envelope.
        client = _client_as(["STORE_MANAGER"])
        resp = client.get("/clinical/abuse-detection", params={"store_id": "store-001"})
        assert resp.status_code == 200
        body = resp.json()
        assert "alerts" in body
        assert "generated_at" in body
        assert isinstance(body["alerts"], list)
