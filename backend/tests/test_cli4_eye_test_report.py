"""
IMS 2.0 - CLI-4: Eye-test clinical report endpoint regression tests
====================================================================

Before: GET /reports/clinical/eye-tests was hard-coded to return
        {"data": [], "total": 0} regardless of real data.

After: queries EyeTestRepository.get_store_tests_in_range and aggregates
       by optometrist.

These tests run without a real database (pure monkeypatching).
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")

from api.routers import reports  # noqa: E402


# ---------------------------------------------------------------------------
# Fake EyeTestRepository
# ---------------------------------------------------------------------------


class FakeEyeTestRepo:
    """Stand-in for EyeTestRepository that stores test docs in memory."""

    def __init__(self, tests: list):
        self._tests = tests

    def get_store_tests_in_range(
        self,
        store_id: str,
        from_date: str | None = None,
        to_date: str | None = None,
        status: str | None = None,
        skip: int = 0,
        limit: int = 200,
    ) -> list:
        results = self._tests
        if store_id:
            results = [t for t in results if t.get("store_id") == store_id]
        if from_date:
            results = [t for t in results if t.get("test_date", "") >= from_date]
        if to_date:
            results = [t for t in results if t.get("test_date", "") <= to_date]
        if status:
            results = [t for t in results if t.get("status") == status]
        return results[skip : skip + limit] if limit else results[skip:]


# Fake user token
FAKE_USER = {"sub": "user_1", "active_store_id": "store_a", "roles": ["ADMIN"]}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eye_test_report_returns_data(monkeypatch):
    """The endpoint returns real test rows (not hard-coded empty)."""
    tests = [
        {
            "store_id": "store_a",
            "test_date": "2026-06-01",
            "status": "COMPLETED",
            "optometrist_id": "opt_1",
            "optometrist_name": "Dr. Sharma",
        },
        {
            "store_id": "store_a",
            "test_date": "2026-06-02",
            "status": "COMPLETED",
            "optometrist_id": "opt_1",
            "optometrist_name": "Dr. Sharma",
        },
    ]
    repo = FakeEyeTestRepo(tests)
    monkeypatch.setattr(reports, "get_eye_test_repository", lambda: repo)

    result = await reports.eye_test_report(
        store_id="store_a",
        from_date=date(2026, 6, 1),
        to_date=date(2026, 6, 30),
        current_user=FAKE_USER,
    )

    assert result["total"] == 2, f"Expected total=2, got {result['total']}"
    assert len(result["data"]) == 2
    assert len(result["by_optometrist"]) == 1
    assert result["by_optometrist"][0]["test_count"] == 2
    assert result["by_optometrist"][0]["optometrist_name"] == "Dr. Sharma"


@pytest.mark.asyncio
async def test_eye_test_report_empty_when_no_data(monkeypatch):
    """Returns honest empty state when there are no tests in the range."""
    monkeypatch.setattr(reports, "get_eye_test_repository", lambda: FakeEyeTestRepo([]))

    result = await reports.eye_test_report(
        store_id="store_a",
        from_date=date(2026, 1, 1),
        to_date=date(2026, 1, 31),
        current_user=FAKE_USER,
    )

    assert result["total"] == 0
    assert result["data"] == []
    assert result["by_optometrist"] == []


@pytest.mark.asyncio
async def test_eye_test_report_no_repo_returns_empty(monkeypatch):
    """Fail-soft: when the DB is unreachable (repo=None) returns empty."""
    monkeypatch.setattr(reports, "get_eye_test_repository", lambda: None)

    result = await reports.eye_test_report(
        store_id="store_a",
        from_date=date(2026, 6, 1),
        to_date=date(2026, 6, 30),
        current_user=FAKE_USER,
    )

    assert result["total"] == 0
    assert result["data"] == []


@pytest.mark.asyncio
async def test_eye_test_report_aggregates_multiple_optometrists(monkeypatch):
    """By-optometrist grouping works across multiple optometrists."""
    tests = [
        {
            "store_id": "s1",
            "test_date": "2026-06-05",
            "status": "COMPLETED",
            "optometrist_id": "opt_1",
            "optometrist_name": "Dr. A",
        },
        {
            "store_id": "s1",
            "test_date": "2026-06-05",
            "status": "COMPLETED",
            "optometrist_id": "opt_2",
            "optometrist_name": "Dr. B",
        },
        {
            "store_id": "s1",
            "test_date": "2026-06-05",
            "status": "COMPLETED",
            "optometrist_id": "opt_1",
            "optometrist_name": "Dr. A",
        },
    ]
    monkeypatch.setattr(reports, "get_eye_test_repository", lambda: FakeEyeTestRepo(tests))

    result = await reports.eye_test_report(
        store_id="s1",
        from_date=date(2026, 6, 1),
        to_date=date(2026, 6, 30),
        current_user=FAKE_USER,
    )

    assert result["total"] == 3
    by_id = {r["optometrist_id"]: r["test_count"] for r in result["by_optometrist"]}
    assert by_id["opt_1"] == 2
    assert by_id["opt_2"] == 1


@pytest.mark.asyncio
async def test_eye_test_report_uses_current_user_store(monkeypatch):
    """When store_id query param is absent, falls back to current_user's store."""
    tests = [
        {
            "store_id": "user_store",
            "test_date": "2026-06-05",
            "status": "COMPLETED",
            "optometrist_id": "opt_x",
        },
    ]
    monkeypatch.setattr(reports, "get_eye_test_repository", lambda: FakeEyeTestRepo(tests))

    user = {**FAKE_USER, "active_store_id": "user_store"}
    result = await reports.eye_test_report(
        store_id=None,  # no explicit store_id
        from_date=date(2026, 6, 1),
        to_date=date(2026, 6, 30),
        current_user=user,
    )

    assert result["total"] == 1
