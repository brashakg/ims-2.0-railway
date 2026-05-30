"""
IMS 2.0 -- upload-bill returns 503 when file storage is unavailable (Bug 3 / P2)
=================================================================================
Before the fix, POST /expenses/{id}/upload-bill returned HTTP 200 with body
{"persisted": false} when GridFS was unavailable -- callers checking only the
status code silently believed the upload succeeded.

After the fix it raises HTTPException(503) consistent with the sibling
download-bill path and the handoffs upload pattern.

The test wires a minimal FastAPI app, stubs get_file_store() -> None,
and confirms the endpoint now returns 503 with the expected detail.
"""

from __future__ import annotations

import io
import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import expenses  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_user():
    return {
        "user_id": "u1",
        "full_name": "Test User",
        "active_store_id": "store-001",
        "roles": ["ADMIN"],
    }


def _fake_expense_repo(expense_doc: dict):
    """Stub repository that returns the given doc on find_by_id and accepts update."""
    repo = MagicMock()
    repo.find_by_id.return_value = expense_doc
    repo.update.return_value = True
    return repo


def _client_with_store(expense_doc: dict, store_available: bool):
    """Build a TestClient with full dependency overrides.

    store_available=False -> get_file_store() returns None (storage down).
    """
    app = FastAPI()
    app.include_router(expenses.router, prefix="/expenses")

    async def _user():
        return _fake_user()

    app.dependency_overrides[get_current_user] = _user

    # Patch get_expense_repository and get_file_store at module level so
    # the endpoint picks them up.
    repo = _fake_expense_repo(expense_doc)

    with (
        patch.object(expenses, "get_expense_repository", return_value=repo),
        patch.object(
            expenses,
            "get_file_store",
            return_value=(None if not store_available else MagicMock()),
        ),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        yield client, repo


# ---------------------------------------------------------------------------
# Test: storage unavailable -> 503
# ---------------------------------------------------------------------------


class TestUploadBill503WhenStorageDown:
    _EXPENSE = {
        "expense_id": "exp-001",
        "store_id": "store-001",
        "employee_id": "u1",
        "bill_sha256": None,
        "duplicate_bill": False,
        "duplicate_of": None,
    }

    def _post_upload(self, client):
        content = b"fake-pdf-bytes"
        return client.post(
            "/expenses/exp-001/upload-bill",
            files={"file": ("receipt.pdf", io.BytesIO(content), "application/pdf")},
        )

    def test_storage_down_returns_503(self):
        for client, _repo in _client_with_store(self._EXPENSE, store_available=False):
            resp = self._post_upload(client)
            assert resp.status_code == 503, (
                f"Expected 503 when storage unavailable, got {resp.status_code}: "
                f"{resp.text}"
            )

    def test_storage_down_detail_message(self):
        for client, _repo in _client_with_store(self._EXPENSE, store_available=False):
            resp = self._post_upload(client)
            body = resp.json()
            assert "detail" in body
            assert "unavailable" in body["detail"].lower()

    def test_storage_down_not_200(self):
        """Guard regression: the old code returned 200 -- make sure it never does."""
        for client, _repo in _client_with_store(self._EXPENSE, store_available=False):
            resp = self._post_upload(client)
            assert resp.status_code != 200, (
                "upload-bill must NOT return 200 when storage is unavailable"
            )

    def test_storage_down_fingerprint_still_persisted(self):
        """SHA-256 fingerprint is written to the expense doc even when 503.

        This preserves the anti-fraud duplicate-detection for a later successful
        re-upload.
        """
        for client, repo in _client_with_store(self._EXPENSE, store_available=False):
            self._post_upload(client)
            # update() should have been called with bill_sha256 even on 503 path
            if repo.update.called:
                args = repo.update.call_args
                update_payload = args[0][1] if args[0] else args[1].get("update", {})
                assert "bill_sha256" in update_payload, (
                    "SHA-256 fingerprint must be persisted even when storage is down"
                )


# ---------------------------------------------------------------------------
# Test: genuine success path is unchanged
# ---------------------------------------------------------------------------


class TestUploadBillSuccessPathUnchanged:
    _EXPENSE = {
        "expense_id": "exp-002",
        "store_id": "store-001",
        "employee_id": "u1",
        "bill_sha256": None,
        "duplicate_bill": False,
        "duplicate_of": None,
    }

    def test_successful_upload_returns_200(self):
        content = b"fake-pdf-bytes"
        for client, _repo in _client_with_store(self._EXPENSE, store_available=True):
            # Patch store.put to return a file id
            with patch(
                "api.routers.expenses.get_file_store"
            ) as mock_store_fn:
                mock_store = MagicMock()
                mock_store.put.return_value = "file-id-123"
                mock_store_fn.return_value = mock_store

                # Re-create repo stub inside this scope
                repo2 = _fake_expense_repo(self._EXPENSE)
                with patch.object(
                    expenses, "get_expense_repository", return_value=repo2
                ):
                    app2 = FastAPI()
                    app2.include_router(expenses.router, prefix="/expenses")

                    async def _user2():
                        return {
                            "user_id": "u1",
                            "roles": ["ADMIN"],
                            "active_store_id": "store-001",
                        }

                    app2.dependency_overrides[get_current_user] = _user2
                    c2 = TestClient(app2, raise_server_exceptions=False)
                    resp = c2.post(
                        "/expenses/exp-002/upload-bill",
                        files={
                            "file": (
                                "receipt.pdf",
                                io.BytesIO(content),
                                "application/pdf",
                            )
                        },
                    )
                    assert resp.status_code == 200
                    body = resp.json()
                    assert body.get("persisted") is True
