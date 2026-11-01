"""Existing event consumers keep the same instant/day when new writers add offsets."""
from datetime import datetime, timedelta, timezone

import pytest

from api.services import attendance_engine, clinical_abuse, conversion_analytics
from api.services import lab_routing, serial_tracking, task_sla, task_triggers
from api.utils import event_time

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
WEST = timezone(timedelta(hours=-4))
INSTANT = datetime(2026, 3, 31, 20, tzinfo=UTC)
FACES = [INSTANT.replace(tzinfo=None).isoformat(), INSTANT.isoformat(),
         INSTANT.astimezone(IST).isoformat(), INSTANT.astimezone(WEST).isoformat()]


@pytest.mark.parametrize("face", FACES)
def test_sla_and_conversion_use_same_instant_across_month_boundary(face):
    now = INSTANT + timedelta(hours=25)
    canonical = {"status": "OPEN", "priority": "P1", "created_at": FACES[0]}
    assert task_sla.should_escalate({**canonical, "created_at": face}, now=now) == task_sla.should_escalate(canonical, now=now)
    assert conversion_analytics._parse_dt(face) == INSTANT.replace(tzinfo=None)


@pytest.mark.parametrize("face", FACES)
def test_lab_elapsed_time_handles_old_naive_and_new_offsets(face):
    assert lab_routing._dwell_ms(face, INSTANT + timedelta(seconds=60)) == 60000
    assert lab_routing._dwell_ms(face, INSTANT.replace(tzinfo=None) + timedelta(seconds=60)) == 60000
    assert lab_routing._dwell_ms(face, INSTANT - timedelta(seconds=1)) == 0


@pytest.mark.parametrize("face", FACES)
def test_serial_sold_event_preserves_warranty_day_and_explicit_expiry(face):
    result = serial_tracking.compute_warranty({"sold_at": face, "warranty_months": 1}, now=INSTANT)
    assert result["expiry_date"].startswith("2026-04-30")
    # A manually supplied expiry remains its own calendar date.
    explicit = serial_tracking.compute_warranty({"warranty_expiry_date": "2026-04-01T01:00:00+05:30"}, now=INSTANT)
    assert explicit["days_remaining"] == 1


def test_clinical_manual_date_and_auto_test_event_are_not_conflated():
    face = INSTANT.astimezone(IST).isoformat()
    auto = {"source": "TESTED_AT_STORE", "eye_test_id": "E1", "test_date": face}
    assert clinical_abuse.rx_date(auto) == INSTANT.replace(tzinfo=None)
    assert clinical_abuse.rx_date({"prescription_date": face}) == datetime(2026, 4, 1, 1, 30)


def test_task_closure_accepts_mixed_legacy_bson_and_negative_offset_event():
    assert task_triggers.is_suspicious_closure({"status": "COMPLETED", "created_at": INSTANT.replace(tzinfo=None),
        "completed_at": (INSTANT + timedelta(seconds=10)).astimezone(WEST).isoformat()})


@pytest.mark.parametrize("start", ["2026-04-01T10:00:00", "2026-04-01T10:00:00+05:30", "2026-04-01T04:30:00Z"])
@pytest.mark.parametrize("end", ["2026-04-01T18:00:00", "2026-04-01T18:00:00+05:30", "2026-04-01T08:30:00-04:00"])
def test_attendance_mixed_legacy_ist_and_explicit_instants_preserve_worked_hours(start, end):
    assert attendance_engine.classify_half_day(check_in=start, check_out=end, min_hours=8)["hours_worked"] == 8
    assert attendance_engine.compute_late_mark(start, "10:00", 0)["is_late"] is False


def test_ist_money_helpers_keep_calendar_day_and_fallback_explicit(monkeypatch):
    from api.services import family_wallet, money_guard, petty_cash_service, petty_cash_settlement_service
    monkeypatch.setattr(event_time, "utc_now", lambda: INSTANT)
    monkeypatch.setattr(money_guard, "_ist_now", lambda: datetime(2026, 4, 1, 1, 30))
    for service in (family_wallet, money_guard, petty_cash_service, petty_cash_settlement_service):
        assert service._now_iso() == "2026-04-01T01:30:00+05:30"
    monkeypatch.setattr(money_guard, "_ist_now", None)
    assert money_guard._now_iso() == "2026-04-01T01:30:00+05:30"


@pytest.mark.parametrize("clock_face", [INSTANT, INSTANT.astimezone(IST), INSTANT.astimezone(WEST)])
@pytest.mark.parametrize("writer", ["release", "repository"])
def test_order_status_bson_and_history_share_one_utc_instant(monkeypatch, clock_face, writer):
    import mongomock
    from api.routers.orders import release
    from database.repositories import order_repository

    # The clock is explicitly UTC regardless of host timezone. The offset face
    # checks the normalization before it is used as Mongo's naive UTC value.
    class ForbiddenLocalClock:
        @staticmethod
        def now(*_args, **_kwargs):
            raise AssertionError("the paired writer must not sample host local time")

    module = release if writer == "release" else order_repository
    monkeypatch.setattr(module, "utc_now", lambda: clock_face.astimezone(UTC))
    monkeypatch.setattr(module, "datetime", ForbiddenLocalClock)
    collection = mongomock.MongoClient().test.orders
    collection.insert_one({"order_id": "O1", "status": "READY", "grand_total": 1234, "amount_paid": 1234})
    repo = order_repository.OrderRepository(collection)
    if writer == "release":
        assert release._claim_order_status(repo, "O1", "DELIVERED", required_status="READY", user_id="U1")
        assert not release._claim_order_status(repo, "O1", "CANCELLED", required_status="READY", user_id="U2")
    else:
        assert repo.update_status("O1", "DELIVERED", "U1")
    row = collection.find_one({"order_id": "O1"})
    assert row["status_updated_at"] == row["delivered_at"] == INSTANT.replace(tzinfo=None)
    assert row["status_history"] == [{"status": "DELIVERED", "timestamp": INSTANT.isoformat(), "changed_by": "U1"}]
    assert row["grand_total"] == row["amount_paid"] == 1234


@pytest.mark.parametrize("supplied", [False, True])
def test_fitting_confirmation_does_not_move_calendar_defaults_at_local_midnight(monkeypatch, supplied):
    import asyncio
    from types import SimpleNamespace
    from api.routers.workshop import jobs
    from api.routers.workshop.models import FittingDetailsUpdate

    saved = {}
    def update(_key, patch):
        saved.update(patch)
        return True
    repo = SimpleNamespace(find_by_id=lambda _key: {"job_id": "J1", "store_id": "S1"}, update=update)
    monkeypatch.setattr(jobs, "get_workshop_repository", lambda: repo)
    monkeypatch.setattr(jobs, "_assert_job_store_access", lambda *_args: None)
    monkeypatch.setattr(jobs, "utc_now_iso", lambda: INSTANT.isoformat())
    details = {"confirmed_by_sales": True, "expected_lens_receive_date": "2026-04-05"}
    if supplied:
        details.update(order_date="2026-03-29", order_time="18:07")
    from freezegun import freeze_time
    # 2026-04-01 01:30 IST: the defaults are the IST calendar day and clock.
    with freeze_time("2026-03-31T20:00:00Z", tick=True):
        asyncio.run(jobs.update_fitting_details("J1", FittingDetailsUpdate(fitting_details=details),
            {"user_id": "U1", "username": "Sales"}))
    fd = saved["fitting_details"]
    assert fd["order_date"] == ("2026-03-29" if supplied else "2026-04-01")
    assert fd["order_time"] == ("18:07" if supplied else "01:30")
    assert fd["expected_lens_receive_date"] == "2026-04-05"
    assert fd["confirmed_at"] == INSTANT.isoformat()
