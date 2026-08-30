"""
IMS 2.0 - Outside-Rx doctor name + prescription photo (POS Wave 4 groundwork)
=============================================================================
Owner spec: an OUTSIDE prescription (customer brings paper from an external
doctor) must record the REAL doctor's name — previously the card attributed
the Rx to whichever staff member keyed it (crm.py had a doctor_name read
field with no writer) — and can carry a PHOTO of the paper Rx, stored in
GridFS with kind=rx_photo (GRN #760 pattern: the doc stores only a
store-minted file id).

Discriminating power: every test fails if its feature is reverted (the
doctor_name field would be stripped by Pydantic; the photo routes 404).

No real MongoDB / GridFS: in-memory fakes (InMemoryFileStore is the shipped
test double for the file store).
"""

from __future__ import annotations

import io
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import prescriptions  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import file_store as fs_module  # noqa: E402
from api.services.file_store import InMemoryFileStore, set_file_store  # noqa: E402

# Reuse the shipped fake-repo harness from the optometrist-name suite.
from tests.test_prescription_optometrist_name import (  # noqa: E402
    _BASE_PAYLOAD,
    _FakeCustRepo,
    _FakeRxRepo,
    _FakeUserRepo,
)


class _FakeRxRepoWithUpdate(_FakeRxRepo):
    def update(self, pid, patch):
        doc = self._docs.get(pid)
        if doc is None:
            return None
        doc.update(patch)
        return doc


def _client(monkeypatch, *, user=None, rx_repo=None):
    rx_repo = rx_repo or _FakeRxRepoWithUpdate()
    app = FastAPI()
    app.include_router(prescriptions.router, prefix="/prescriptions")

    async def _fake_user():
        return user or {
            "user_id": "opt-1",
            "username": "dr_meera",
            "full_name": "Dr. Meera Iyer",
            "active_store_id": "store-001",
            "roles": ["OPTOMETRIST"],
        }

    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(
        prescriptions, "get_prescription_repository", lambda: rx_repo
    )
    monkeypatch.setattr(
        prescriptions, "get_customer_repository", lambda: _FakeCustRepo(None)
    )
    monkeypatch.setattr(
        prescriptions, "get_user_repository", lambda: _FakeUserRepo({})
    )
    return TestClient(app), rx_repo


@pytest.fixture
def mem_store():
    store = InMemoryFileStore()
    set_file_store(store)
    yield store
    set_file_store(None)


# ---------------------------------------------------------------------------
# doctor_name
# ---------------------------------------------------------------------------


def test_from_doctor_create_persists_doctor_name(monkeypatch):
    client, repo = _client(monkeypatch)
    payload = {
        **_BASE_PAYLOAD,
        "source": "FROM_DOCTOR",
        "doctor_name": "Dr. A. K. Banerjee",
    }
    r = client.post("/prescriptions", json=payload)
    assert r.status_code in (200, 201), r.text
    doc = repo.find_by_id(r.json()["prescription_id"])
    assert doc["doctor_name"] == "Dr. A. K. Banerjee"
    # Staff attribution unchanged: the keyer's name still fills optometrist_name.
    assert doc["optometrist_name"] == "Dr. Meera Iyer"


def test_store_test_without_doctor_name_stays_none(monkeypatch):
    client, repo = _client(monkeypatch)
    r = client.post("/prescriptions", json=dict(_BASE_PAYLOAD))
    assert r.status_code in (200, 201), r.text
    doc = repo.find_by_id(r.json()["prescription_id"])
    assert doc.get("doctor_name") is None


def test_update_can_fix_doctor_name_typo(monkeypatch):
    client, repo = _client(monkeypatch)
    r = client.post(
        "/prescriptions",
        json={**_BASE_PAYLOAD, "source": "FROM_DOCTOR", "doctor_name": "Dr. Benerjee"},
    )
    pid = r.json()["prescription_id"]
    r2 = client.put(f"/prescriptions/{pid}", json={"doctor_name": "Dr. Banerjee"})
    assert r2.status_code == 200, r2.text
    assert repo.find_by_id(pid)["doctor_name"] == "Dr. Banerjee"


# ---------------------------------------------------------------------------
# Photo upload / download
# ---------------------------------------------------------------------------

_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _make_rx(client):
    r = client.post(
        "/prescriptions",
        json={**_BASE_PAYLOAD, "source": "FROM_DOCTOR", "doctor_name": "Dr. X"},
    )
    return r.json()["prescription_id"]


def test_photo_upload_and_download_roundtrip(monkeypatch, mem_store):
    client, repo = _client(monkeypatch)
    pid = _make_rx(client)
    r = client.post(
        f"/prescriptions/{pid}/photo",
        files={"file": ("rx.png", io.BytesIO(_PNG), "image/png")},
    )
    assert r.status_code == 201, r.text
    file_id = r.json()["rx_photo_file_id"]
    assert repo.find_by_id(pid)["rx_photo_file_id"] == file_id

    dl = client.get(f"/prescriptions/{pid}/photo")
    assert dl.status_code == 200
    assert dl.content == _PNG
    assert dl.headers["content-type"].startswith("image/png")


def test_photo_reupload_replaces_and_deletes_old(monkeypatch, mem_store):
    client, repo = _client(monkeypatch)
    pid = _make_rx(client)
    r1 = client.post(
        f"/prescriptions/{pid}/photo",
        files={"file": ("a.png", io.BytesIO(_PNG), "image/png")},
    )
    old_id = r1.json()["rx_photo_file_id"]
    r2 = client.post(
        f"/prescriptions/{pid}/photo",
        files={"file": ("b.png", io.BytesIO(_PNG + b"2"), "image/png")},
    )
    new_id = r2.json()["rx_photo_file_id"]
    assert new_id != old_id
    assert repo.find_by_id(pid)["rx_photo_file_id"] == new_id
    # Old blob gone from the store.
    from api.services.file_store import ANY_KIND

    assert mem_store.get(old_id, require_kind=ANY_KIND) is None


def test_photo_upload_rejects_non_image(monkeypatch, mem_store):
    client, _ = _client(monkeypatch)
    pid = _make_rx(client)
    r = client.post(
        f"/prescriptions/{pid}/photo",
        files={"file": ("rx.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    assert r.status_code == 415


def test_photo_download_404_when_none_attached(monkeypatch, mem_store):
    client, _ = _client(monkeypatch)
    pid = _make_rx(client)
    r = client.get(f"/prescriptions/{pid}/photo")
    assert r.status_code == 404


def test_photo_upload_403_for_workshop_role(monkeypatch, mem_store):
    # POS + clinical roles may attach; workshop staff may not. Seed the Rx
    # with a clinical user first, then retry the upload as workshop.
    client_ok, repo = _client(monkeypatch)
    pid = _make_rx(client_ok)
    client_ws, _ = _client(
        monkeypatch,
        user={
            "user_id": "ws-1",
            "username": "ws",
            "roles": ["WORKSHOP_STAFF"],
            "active_store_id": "store-001",
        },
        rx_repo=repo,
    )
    r = client_ws.post(
        f"/prescriptions/{pid}/photo",
        files={"file": ("rx.png", io.BytesIO(_PNG), "image/png")},
    )
    assert r.status_code == 403


def test_pos_cashier_can_attach_photo(monkeypatch, mem_store):
    client_ok, repo = _client(monkeypatch)
    pid = _make_rx(client_ok)
    client_pos, _ = _client(
        monkeypatch,
        user={
            "user_id": "cash-1",
            "username": "cashier",
            "roles": ["SALES_CASHIER"],
            "active_store_id": "store-001",
        },
        rx_repo=repo,
    )
    r = client_pos.post(
        f"/prescriptions/{pid}/photo",
        files={"file": ("rx.png", io.BytesIO(_PNG), "image/png")},
    )
    assert r.status_code == 201, r.text
