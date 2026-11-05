"""
Codex audit L3-18 + L2-CLIN-F01: two read doors that skipped the per-object
store rule (and, for the Rx print, the clinical role gate) their canonical
siblings enforce.

L3-18      POST /finance/einvoice/{order_id} returned another shop's cached
           IRN / QR / ack to a store-scoped ACCOUNTANT whose GET /orders/{id}
           for the same order is 403.
L2-CLIN-F01 GET /clinical/prescriptions/{id}/print served the full Rx card to
           INVESTOR (403 on GET /prescriptions/{id}) and to a foreign shop.
           Sibling redo doors (/redo, /redos) had the same per-object hole.

Bare app + dependency override + fake repos: no Mongo, no GSP/IRP call (the
order already carries an IRN, so generate_irn short-circuits to SKIPPED).
"""

from __future__ import annotations

import importlib
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import clinical  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

einvoice_router = importlib.import_module("api.routers.finance.einvoice")

STORE_A, STORE_B = "store-A", "store-B"


def _user(roles, store_ids, active=None):
    return {
        "user_id": "u1",
        "username": "tester",
        "full_name": "Test User",
        "roles": roles,
        "activeRole": roles[0],
        "store_ids": store_ids,
        "active_store_id": active if active is not None else (store_ids or [None])[0],
    }


# ============================================================================
# L3-18 -- e-invoice door
# ============================================================================

_ORDER_B = {
    "id": "ord-B1",
    "store_id": STORE_B,
    "irn": "IRN-CACHED-SECRET",
    "ack_no": "ACK-999",
    "ack_date": "2026-11-01",
    "einvoice_signed_qr": "QR-SECRET",
}


class _FakeColl:
    def __init__(self, doc):
        self._doc = doc

    def find_one(self, *_a, **_k):
        return dict(self._doc) if self._doc else None


class _FakeDb:
    def get_collection(self, name):
        return _FakeColl(_ORDER_B if name == "orders" else None)


def _einv_client(monkeypatch, user):
    monkeypatch.setattr(einvoice_router, "_get_db", lambda: _FakeDb())
    app = FastAPI()
    app.include_router(einvoice_router.router, prefix="/finance")

    async def _fake():
        return user

    app.dependency_overrides[get_current_user] = _fake
    return TestClient(app)


class TestEinvoiceStoreScope:
    def test_foreign_store_accountant_gets_no_cached_irn(self, monkeypatch):
        c = _einv_client(monkeypatch, _user(["ACCOUNTANT"], [STORE_A]))
        r = c.post("/finance/einvoice/ord-B1")
        assert r.status_code == 403
        assert "IRN-CACHED-SECRET" not in r.text
        assert "QR-SECRET" not in r.text

    def test_same_store_accountant_still_reads_cached_irn(self, monkeypatch):
        c = _einv_client(monkeypatch, _user(["ACCOUNTANT"], [STORE_B]))
        r = c.post("/finance/einvoice/ord-B1")
        assert r.status_code == 200
        assert r.json()["irn"] == "IRN-CACHED-SECRET"

    def test_admin_reads_any_store(self, monkeypatch):
        c = _einv_client(monkeypatch, _user(["ADMIN"], [STORE_A]))
        r = c.post("/finance/einvoice/ord-B1")
        assert r.status_code == 200
        assert r.json()["signed_qr"] == "QR-SECRET"


# ============================================================================
# L2-CLIN-F01 -- Rx print + sibling redo doors
# ============================================================================

_RX_B = {
    "prescription_id": "rx-B1",
    "id": "rx-B1",
    "store_id": STORE_B,
    "patient_name": "AUDIT PATIENT",
    "right_eye": {"sphere": "-7.25", "cylinder": "-1.50", "axis": 90},
    "left_eye": {"sphere": "-6.75", "cylinder": "-1.25", "axis": 85},
    "redos": [{"redo_id": "r1", "reason": "SECRET-REDO-REASON"}],
}


class _FakeRxRepo:
    def __init__(self):
        self.updated = []

    def find_by_id(self, rx_id):
        return dict(_RX_B) if rx_id == "rx-B1" else None

    def update(self, rx_id, fields):
        self.updated.append((rx_id, fields))
        return True


def _rx_client(monkeypatch, user, repo=None):
    repo = repo or _FakeRxRepo()
    monkeypatch.setattr(clinical, "get_prescription_repository", lambda: repo)
    monkeypatch.setattr(clinical, "get_store_repository", lambda: None)
    app = FastAPI()
    app.include_router(clinical.router, prefix="/clinical")

    async def _fake():
        return user

    app.dependency_overrides[get_current_user] = _fake
    return TestClient(app)


PRINT = "/clinical/prescriptions/rx-B1/print"
REDOS = "/clinical/prescriptions/rx-B1/redos"
REDO = "/clinical/prescriptions/rx-B1/redo"


class TestRxPrintScope:
    def test_investor_denied_print(self, monkeypatch):
        c = _rx_client(monkeypatch, _user(["INVESTOR"], [STORE_B]))
        r = c.get(PRINT)
        assert r.status_code == 403
        assert "AUDIT PATIENT" not in r.text

    def test_foreign_shop_optometrist_gets_404_no_content(self, monkeypatch):
        c = _rx_client(monkeypatch, _user(["OPTOMETRIST"], [STORE_A]))
        r = c.get(PRINT)
        assert r.status_code == 404
        assert "AUDIT PATIENT" not in r.text

    def test_area_manager_outside_reach_404(self, monkeypatch):
        c = _rx_client(monkeypatch, _user(["AREA_MANAGER"], [STORE_A]))
        assert c.get(PRINT).status_code == 404

    def test_same_shop_optometrist_prints(self, monkeypatch):
        c = _rx_client(monkeypatch, _user(["OPTOMETRIST"], [STORE_B]))
        r = c.get(PRINT)
        assert r.status_code == 200
        assert "AUDIT PATIENT" in r.text

    def test_area_manager_own_stores_prints(self, monkeypatch):
        c = _rx_client(monkeypatch, _user(["AREA_MANAGER"], [STORE_A, STORE_B]))
        assert c.get(PRINT).status_code == 200

    def test_admin_prints_any_shop(self, monkeypatch):
        c = _rx_client(monkeypatch, _user(["ADMIN"], [STORE_A]))
        assert c.get(PRINT).status_code == 200


class TestRedoSiblingScope:
    def test_investor_denied_redo_history(self, monkeypatch):
        c = _rx_client(monkeypatch, _user(["INVESTOR"], [STORE_B]))
        r = c.get(REDOS)
        assert r.status_code == 403
        assert "SECRET-REDO-REASON" not in r.text

    def test_foreign_shop_redo_history_404(self, monkeypatch):
        c = _rx_client(monkeypatch, _user(["OPTOMETRIST"], [STORE_A]))
        r = c.get(REDOS)
        assert r.status_code == 404
        assert "SECRET-REDO-REASON" not in r.text

    def test_same_shop_redo_history(self, monkeypatch):
        c = _rx_client(monkeypatch, _user(["OPTOMETRIST"], [STORE_B]))
        r = c.get(REDOS)
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_foreign_shop_cannot_record_redo(self, monkeypatch):
        repo = _FakeRxRepo()
        c = _rx_client(monkeypatch, _user(["STORE_MANAGER"], [STORE_A]), repo)
        r = c.post(REDO, json={"reason": "lens remake"})
        assert r.status_code == 404
        assert repo.updated == []

    def test_same_shop_records_redo(self, monkeypatch):
        repo = _FakeRxRepo()
        c = _rx_client(monkeypatch, _user(["STORE_MANAGER"], [STORE_B]), repo)
        r = c.post(REDO, json={"reason": "lens remake"})
        assert r.status_code == 200
        assert len(repo.updated) == 1


# ============================================================================
# Codex review of #1378, issue 2 -- unattributed e-invoice records (no shop)
# ============================================================================
# validate_store_access treats a missing store_id as "use the caller's active
# store" and returns, so a legacy order / invoice with no shop handed its
# cached IRN / QR / ack to any accountant -- even one assigned no shop. Only
# ADMIN / SUPERADMIN may open an unattributed record (can_access_store_scoped).

_IRN_FIELDS = {
    "irn": "IRN-LEGACY-SECRET",
    "ack_no": "ACK-LEGACY",
    "ack_date": "2026-11-01",
    "einvoice_signed_qr": "QR-LEGACY-SECRET",
}


class _OneCollDb:
    """Serves `doc` only from `coll_name`; the other collection is empty, so
    the invoices fallback is exercised when coll_name == 'invoices'."""

    def __init__(self, coll_name, doc):
        self._name, self._doc = coll_name, doc

    def get_collection(self, name):
        return _FakeColl(self._doc if name == self._name else None)


def _einv_client_db(monkeypatch, user, db):
    monkeypatch.setattr(einvoice_router, "_get_db", lambda: db)
    app = FastAPI()
    app.include_router(einvoice_router.router, prefix="/finance")

    async def _fake():
        return user

    app.dependency_overrides[get_current_user] = _fake
    return TestClient(app)


_COLLS = ["orders", "invoices"]
_NO_SHOP_VARIANTS = ["missing", None, ""]


def _legacy_doc(variant):
    doc = {"id": "legacy-1", **_IRN_FIELDS}
    if variant != "missing":
        doc["store_id"] = variant
    return doc


class TestEinvoiceUnattributed:
    @pytest.mark.parametrize("coll", _COLLS)
    @pytest.mark.parametrize("variant", _NO_SHOP_VARIANTS)
    @pytest.mark.parametrize(
        "store_ids", [[STORE_A], [STORE_B], []], ids=["shopA", "shopB", "noshop"]
    )
    def test_accountant_denied_unattributed(self, monkeypatch, coll, variant, store_ids):
        user = _user(["ACCOUNTANT"], store_ids)
        c = _einv_client_db(monkeypatch, user, _OneCollDb(coll, _legacy_doc(variant)))
        r = c.post("/finance/einvoice/legacy-1")
        assert r.status_code == 403, r.text
        assert "IRN-LEGACY-SECRET" not in r.text
        assert "QR-LEGACY-SECRET" not in r.text

    @pytest.mark.parametrize("coll", _COLLS)
    @pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
    def test_hq_reads_unattributed(self, monkeypatch, coll, role):
        c = _einv_client_db(
            monkeypatch, _user([role], []), _OneCollDb(coll, _legacy_doc("missing"))
        )
        r = c.post("/finance/einvoice/legacy-1")
        assert r.status_code == 200, r.text
        assert r.json()["irn"] == "IRN-LEGACY-SECRET"

    def test_invoices_fallback_same_shop_accountant(self, monkeypatch):
        doc = {"id": "inv-B1", "store_id": STORE_B, **_IRN_FIELDS}
        c = _einv_client_db(
            monkeypatch,
            _user(["ACCOUNTANT"], [STORE_A, STORE_B]),
            _OneCollDb("invoices", doc),
        )
        r = c.post("/finance/einvoice/inv-B1")
        assert r.status_code == 200, r.text
        assert r.json()["signed_qr"] == "QR-LEGACY-SECRET"

    def test_invoices_fallback_foreign_and_noshop_accountant(self, monkeypatch):
        doc = {"id": "inv-B1", "store_id": STORE_B, **_IRN_FIELDS}
        for store_ids in ([STORE_A], []):
            c = _einv_client_db(
                monkeypatch, _user(["ACCOUNTANT"], store_ids), _OneCollDb("invoices", doc)
            )
            r = c.post("/finance/einvoice/inv-B1")
            assert r.status_code == 403, r.text
            assert "IRN-LEGACY-SECRET" not in r.text


# ============================================================================
# Codex review of #1378, issue 1 -- prescription PHOTO read + replace doors
# ============================================================================
# GET /prescriptions/{id}/photo used bare get_current_user (no clinical role
# gate, no store check); POST replaced another shop's photo and deleted its
# blob. Both now apply require_rx_read / the Rx store guard BEFORE touching the
# file store.

import io  # noqa: E402

from api.routers import prescriptions as rx_router  # noqa: E402
from api.services.file_store import InMemoryFileStore, set_file_store  # noqa: E402

_OLD_PNG = b"\x89PNG\r\n\x1a\n" + b"OLD-PHOTO" * 8
_NEW_PNG = b"\x89PNG\r\n\x1a\n" + b"NEW-PHOTO" * 8


class _PhotoRxRepo:
    def __init__(self, docs):
        self.docs = {d["prescription_id"]: dict(d) for d in docs}
        self.updated = []

    def find_by_id(self, pid):
        d = self.docs.get(pid)
        return dict(d) if d else None

    def update(self, pid, fields):
        self.updated.append((pid, fields))
        self.docs[pid].update(fields)
        return self.docs[pid]


@pytest.fixture
def photo_env(monkeypatch):
    store = InMemoryFileStore()
    set_file_store(store)
    docs = []
    for pid, sid in (("rx-photo-B", STORE_B), ("rx-photo-none", None)):
        fid = store.put(
            content=_OLD_PNG,
            filename="old.png",
            mime_type="image/png",
            metadata={"kind": "rx_photo", "prescription_id": pid},
        )
        doc = {"prescription_id": pid, "rx_photo_file_id": fid}
        if sid:
            doc["store_id"] = sid
        docs.append(doc)
    repo = _PhotoRxRepo(docs)
    monkeypatch.setattr(rx_router, "get_prescription_repository", lambda: repo)
    yield store, repo
    set_file_store(None)


def _photo_client(user):
    app = FastAPI()
    app.include_router(rx_router.router, prefix="/prescriptions")

    async def _fake():
        return user

    app.dependency_overrides[get_current_user] = _fake
    return TestClient(app)


def _replace(c, pid):
    return c.post(
        f"/prescriptions/{pid}/photo",
        files={"file": ("new.png", io.BytesIO(_NEW_PNG), "image/png")},
    )


class TestRxPhotoRead:
    @pytest.mark.parametrize("role", ["INVESTOR", "ACCOUNTANT", "CASHIER"])
    def test_non_clinical_role_denied(self, photo_env, role):
        r = _photo_client(_user([role], [STORE_B])).get("/prescriptions/rx-photo-B/photo")
        assert r.status_code == 403
        assert _OLD_PNG not in r.content

    def test_foreign_shop_optometrist_404(self, photo_env):
        r = _photo_client(_user(["OPTOMETRIST"], [STORE_A])).get(
            "/prescriptions/rx-photo-B/photo"
        )
        assert r.status_code == 404
        assert _OLD_PNG not in r.content

    def test_area_manager_outside_reach_404(self, photo_env):
        r = _photo_client(_user(["AREA_MANAGER"], [STORE_A])).get(
            "/prescriptions/rx-photo-B/photo"
        )
        assert r.status_code == 404

    def test_unattributed_hidden_from_store_role(self, photo_env):
        r = _photo_client(_user(["OPTOMETRIST"], [STORE_B])).get(
            "/prescriptions/rx-photo-none/photo"
        )
        assert r.status_code == 404
        assert _OLD_PNG not in r.content

    @pytest.mark.parametrize(
        "roles,stores",
        [
            (["OPTOMETRIST"], [STORE_B]),
            (["SALES_STAFF"], [STORE_B]),
            (["AREA_MANAGER"], [STORE_A, STORE_B]),
            (["ADMIN"], [STORE_A]),
        ],
    )
    def test_authorized_reads_photo(self, photo_env, roles, stores):
        r = _photo_client(_user(roles, stores)).get("/prescriptions/rx-photo-B/photo")
        assert r.status_code == 200
        assert r.content == _OLD_PNG

    def test_admin_reads_unattributed_photo(self, photo_env):
        r = _photo_client(_user(["ADMIN"], [])).get("/prescriptions/rx-photo-none/photo")
        assert r.status_code == 200
        assert r.content == _OLD_PNG


class TestRxPhotoReplace:
    @pytest.mark.parametrize(
        "pid,roles,stores",
        [
            ("rx-photo-B", ["OPTOMETRIST"], [STORE_A]),
            ("rx-photo-B", ["SALES_CASHIER"], [STORE_A]),
            ("rx-photo-none", ["STORE_MANAGER"], [STORE_B]),
        ],
    )
    def test_out_of_scope_replace_leaves_blob_untouched(
        self, photo_env, pid, roles, stores
    ):
        store, repo = photo_env
        before_ids = set(store._files)
        old_fid = repo.docs[pid]["rx_photo_file_id"]
        r = _replace(_photo_client(_user(roles, stores)), pid)
        assert r.status_code == 404, r.text
        assert repo.updated == []
        assert repo.docs[pid]["rx_photo_file_id"] == old_fid
        assert set(store._files) == before_ids  # no new blob, old not deleted
        assert store.get(old_fid, require_kind="rx_photo")[0] == _OLD_PNG

    def test_same_shop_replaces(self, photo_env):
        store, repo = photo_env
        old_fid = repo.docs["rx-photo-B"]["rx_photo_file_id"]
        r = _replace(_photo_client(_user(["SALES_CASHIER"], [STORE_B])), "rx-photo-B")
        assert r.status_code == 201, r.text
        new_fid = repo.docs["rx-photo-B"]["rx_photo_file_id"]
        assert new_fid != old_fid
        assert store.get(old_fid, require_kind="rx_photo") is None
        assert store.get(new_fid, require_kind="rx_photo")[0] == _NEW_PNG

    def test_admin_replaces_unattributed(self, photo_env):
        _, repo = photo_env
        r = _replace(_photo_client(_user(["ADMIN"], [])), "rx-photo-none")
        assert r.status_code == 201, r.text
        assert len(repo.updated) == 1

    def test_policy_get_photo_row_matches_rx_read_roles(self):
        from api.services import rbac_policy as rbac

        entry = rbac.policy_for("GET", "/api/v1/prescriptions/RX-1/photo")
        assert entry is not None
        assert set(entry["allowed"]) == set(rx_router._RX_READ_ROLES)
