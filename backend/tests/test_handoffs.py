"""
IMS 2.0 — Handoffs router regression tests
============================================
14 cases covering the file-handoff feature:

  - Upload + role-gating of recipients
  - File-type + size validation
  - Inbox visibility rules (dismissed / kept / snoozed)
  - Response state transitions + idempotency
  - Reshare creates child doc with parent_handoff_id + same expires_at
  - Permission gates on view/download/revoke
  - Eligible-recipients endpoint filters by role
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Test fakes — array-filter-aware Mongo emulator
# ============================================================================


def _doc_matches(doc, filter_):
    if not filter_:
        return True
    for k, expected in filter_.items():
        # Dotted-path support for filters like "recipients.user_id" (used by
        # find_inbox_for_user). Mongo treats the path as "any element of
        # the array has matching field".
        if "." in k:
            head, tail = k.split(".", 1)
            arr = doc.get(head)
            if isinstance(arr, list):
                if not any(_doc_matches(item, {tail: expected}) for item in arr if isinstance(item, dict)):
                    return False
                continue
            return False

        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$gte" and not (actual is not None and actual >= op_val):
                    return False
                if op == "$lt" and not (actual is not None and actual < op_val):
                    return False
                if op == "$lte" and not (actual is not None and actual <= op_val):
                    return False
                if op == "$nin" and actual in op_val:
                    return False
                if op == "$in" and actual not in op_val:
                    return False
                if op == "$ne" and actual == op_val:
                    return False
                if op == "$exists" and bool(actual is not None) != bool(op_val):
                    return False
        else:
            if actual != expected:
                return False
    return True


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._sort = None
        self._skip = 0
        self._limit = None

    def sort(self, *args, **kwargs):
        return self

    def skip(self, n):
        self._skip = int(n or 0)
        return self

    def limit(self, n):
        self._limit = int(n or 0) or None
        return self

    def __iter__(self):
        out = list(self._docs)
        if self._skip:
            out = out[self._skip:]
        if self._limit:
            out = out[: self._limit]
        return iter(out)


class FakeCollection:
    """Supports the calls HandoffRepository / BaseRepository exercise,
    including update_one with array_filters (for nested recipient
    sub-doc patches)."""

    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id", doc.get("handoff_id"))})()

    def find_one(self, filter_=None, projection=None):
        for d in self.docs:
            if _doc_matches(d, filter_):
                return d
        return None

    def find(self, filter_=None, projection=None):
        return _FakeCursor(d for d in self.docs if _doc_matches(d, filter_))

    def count_documents(self, filter_=None):
        return sum(1 for d in self.docs if _doc_matches(d, filter_))

    def update_one(self, filter_, update, array_filters=None, upsert=False):
        for d in self.docs:
            if not _doc_matches(d, filter_):
                continue
            set_block = (update or {}).get("$set", {}) or {}
            for k, v in set_block.items():
                # array-filter syntax: "recipients.$[r].status"
                if ".$[" in k:
                    head, _, rest = k.partition(".$[")
                    filter_alias, _, tail = rest.partition("].")
                    arr = d.get(head)
                    if not isinstance(arr, list):
                        continue
                    # Find the array-filter dict whose key starts with
                    # "<alias>." — Mongo's syntax is `[{"r.user_id": "..."}]`,
                    # not `[{"r": {...}}]`.
                    af = next(
                        (
                            af for af in (array_filters or [])
                            if any(key.startswith(f"{filter_alias}.") for key in af.keys())
                        ),
                        None,
                    )
                    if af is None:
                        continue
                    inner_filter = {
                        key.split(".", 1)[1]: val
                        for key, val in af.items()
                        if key.startswith(f"{filter_alias}.")
                    }
                    for item in arr:
                        if isinstance(item, dict) and _doc_matches(item, inner_filter):
                            item[tail] = v
                else:
                    d[k] = v
            return type("R", (), {"modified_count": 1, "matched_count": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def delete_one(self, filter_):
        for i, d in enumerate(list(self.docs)):
            if _doc_matches(d, filter_):
                self.docs.pop(i)
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()

    def create_index(self, *args, **kwargs):
        return None  # no-op for fake


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections = {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]

    def __getattr__(self, name):
        if name in {"is_connected", "_collections"}:
            raise AttributeError(name)
        return self.get_collection(name)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def patched_handoffs(monkeypatch):
    """Wire the handoffs router + dependencies to fakes."""
    fake_db = FakeDB()

    # Real HandoffRepository on the fake collection
    from database.repositories.handoff_repository import HandoffRepository
    from database.repositories.user_repository import UserRepository
    handoff_repo = HandoffRepository(fake_db.get_collection("handoffs"))

    # User repo with seeded roster
    user_coll = fake_db.get_collection("users")
    for user_doc in [
        {"user_id": "u-uploader", "username": "uploader", "name": "Anil Uploader",
         "roles": ["SALES_STAFF"]},
        {"user_id": "u-store-mgr", "username": "smgr", "name": "Smita Manager",
         "roles": ["STORE_MANAGER"]},
        {"user_id": "u-acct", "username": "acct", "name": "Asha Accountant",
         "roles": ["ACCOUNTANT"]},
        {"user_id": "u-admin", "username": "admin", "name": "Akhil Admin",
         "roles": ["ADMIN"]},
        {"user_id": "u-super", "username": "super", "name": "Vishnu Super",
         "roles": ["SUPERADMIN"]},
        {"user_id": "u-cashier", "username": "cash", "name": "Chitra Cashier",
         "roles": ["CASHIER"]},  # Ineligible — not in the recipient role set
    ]:
        user_coll.insert_one(user_doc)
    user_repo = UserRepository(user_coll)

    # Patch the repo accessors used by the handoffs router
    from api import dependencies as deps_module
    monkeypatch.setattr(deps_module, "get_handoff_repository", lambda: handoff_repo)
    monkeypatch.setattr(deps_module, "get_user_repository", lambda: user_repo)
    from api.routers import handoffs as handoffs_module
    monkeypatch.setattr(handoffs_module, "get_handoff_repository", lambda: handoff_repo)
    monkeypatch.setattr(handoffs_module, "get_user_repository", lambda: user_repo)

    # Replace the file store with the in-memory implementation
    from api.services.file_store import set_file_store, InMemoryFileStore
    fake_store = InMemoryFileStore()
    set_file_store(fake_store)

    yield {
        "db": fake_db,
        "handoff_repo": handoff_repo,
        "user_repo": user_repo,
        "file_store": fake_store,
    }
    set_file_store(None)


def _token(user_id, name, roles):
    """Mint a JWT for a synthetic user."""
    from api.routers.auth import create_access_token
    return create_access_token({
        "user_id": user_id,
        "username": user_id,
        "name": name,
        "roles": list(roles),
        "active_role": list(roles)[0],
        "store_ids": ["BV-TEST-01"],
        "active_store_id": "BV-TEST-01",
    })


def _upload(client, token, *, recipients, validity_days=7, title="Test handoff",
            content=b"%PDF-1.4 fake content", filename="invoice.pdf",
            mime_type="application/pdf"):
    """Helper: POST /handoffs with the multipart payload."""
    return client.post(
        "/api/v1/handoffs",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, content, mime_type)},
        data={
            "title": title,
            "recipient_ids": json.dumps(list(recipients)),
            "validity_days": str(validity_days),
        },
    )


# ============================================================================
# Tests — uploads
# ============================================================================


def test_create_handoff_succeeds_with_eligible_recipients(client, patched_handoffs):
    token = _token("u-uploader", "Anil", ["SALES_STAFF"])
    r = _upload(client, token, recipients=["u-store-mgr", "u-acct"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["uploader_id"] == "u-uploader"
    assert len(body["recipients"]) == 2
    assert {r["user_id"] for r in body["recipients"]} == {"u-store-mgr", "u-acct"}
    assert all(r["status"] == "pending" for r in body["recipients"])
    assert body["file"]["mime_type"] == "application/pdf"
    assert body["validity_days"] == 7
    # File was actually persisted
    assert body["file"]["file_id"] in patched_handoffs["file_store"]._files


def test_create_handoff_rejects_disallowed_mime_type(client, patched_handoffs):
    token = _token("u-uploader", "Anil", ["SALES_STAFF"])
    r = _upload(
        client, token,
        recipients=["u-store-mgr"],
        content=b"<xml>nope</xml>",
        filename="invoice.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert r.status_code == 415


def test_create_handoff_rejects_oversize_file(client, patched_handoffs):
    token = _token("u-uploader", "Anil", ["SALES_STAFF"])
    huge = b"a" * (26 * 1024 * 1024)  # 26 MB > 25 MB cap
    r = _upload(client, token, recipients=["u-store-mgr"], content=huge)
    assert r.status_code == 413


def test_create_handoff_rejects_invalid_validity(client, patched_handoffs):
    token = _token("u-uploader", "Anil", ["SALES_STAFF"])
    r = _upload(client, token, recipients=["u-store-mgr"], validity_days=2)
    assert r.status_code == 400
    r = _upload(client, token, recipients=["u-store-mgr"], validity_days=45)
    assert r.status_code == 400


def test_create_handoff_filters_ineligible_recipients(client, patched_handoffs):
    """A SALES_STAFF (cashier) recipient is silently dropped — only the
    eligible role lands in the doc."""
    token = _token("u-uploader", "Anil", ["SALES_STAFF"])
    r = _upload(client, token, recipients=["u-cashier", "u-store-mgr"])
    assert r.status_code == 201
    body = r.json()
    assert {r["user_id"] for r in body["recipients"]} == {"u-store-mgr"}


def test_create_handoff_400s_when_all_recipients_ineligible(client, patched_handoffs):
    token = _token("u-uploader", "Anil", ["SALES_STAFF"])
    r = _upload(client, token, recipients=["u-cashier"])
    assert r.status_code == 400


# ============================================================================
# Tests — inbox visibility
# ============================================================================


def test_inbox_returns_pending_handoff_for_recipient(client, patched_handoffs):
    upload_token = _token("u-uploader", "Anil", ["SALES_STAFF"])
    _upload(client, upload_token, recipients=["u-store-mgr"])

    smgr_token = _token("u-store-mgr", "Smita", ["STORE_MANAGER"])
    r = client.get(
        "/api/v1/handoffs/inbox",
        headers={"Authorization": f"Bearer {smgr_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["handoffs"][0]["my_status"] == "pending"


def test_inbox_hides_dismissed_handoff(client, patched_handoffs):
    upload_token = _token("u-uploader", "Anil", ["SALES_STAFF"])
    upload_resp = _upload(client, upload_token, recipients=["u-store-mgr"])
    handoff_id = upload_resp.json()["handoff_id"]

    smgr_token = _token("u-store-mgr", "Smita", ["STORE_MANAGER"])
    r = client.post(
        f"/api/v1/handoffs/{handoff_id}/dismiss",
        headers={"Authorization": f"Bearer {smgr_token}"},
        json={"action": "dismiss"},
    )
    assert r.status_code == 200

    r2 = client.get(
        "/api/v1/handoffs/inbox",
        headers={"Authorization": f"Bearer {smgr_token}"},
    )
    assert r2.json()["total"] == 0


def test_inbox_keeps_kept_handoff_visible_after_response(client, patched_handoffs):
    upload_token = _token("u-uploader", "Anil", ["SALES_STAFF"])
    upload_resp = _upload(client, upload_token, recipients=["u-store-mgr"])
    hid = upload_resp.json()["handoff_id"]

    smgr_token = _token("u-store-mgr", "Smita", ["STORE_MANAGER"])
    # Respond, then keep
    client.post(f"/api/v1/handoffs/{hid}/respond",
                headers={"Authorization": f"Bearer {smgr_token}"},
                json={"response": "approved", "comment": "Looks good"})
    client.post(f"/api/v1/handoffs/{hid}/dismiss",
                headers={"Authorization": f"Bearer {smgr_token}"},
                json={"action": "keep"})

    r = client.get("/api/v1/handoffs/inbox",
                   headers={"Authorization": f"Bearer {smgr_token}"})
    assert r.json()["total"] == 1
    assert r.json()["handoffs"][0]["my_response"] == "approved"


def test_inbox_hides_snoozed_handoff_until_snooze_expires(client, patched_handoffs):
    upload_token = _token("u-uploader", "Anil", ["SALES_STAFF"])
    upload_resp = _upload(client, upload_token, recipients=["u-store-mgr"])
    hid = upload_resp.json()["handoff_id"]

    smgr_token = _token("u-store-mgr", "Smita", ["STORE_MANAGER"])
    # Snooze 60 minutes
    client.post(f"/api/v1/handoffs/{hid}/dismiss",
                headers={"Authorization": f"Bearer {smgr_token}"},
                json={"action": "snooze", "snooze_minutes": 60})

    r = client.get("/api/v1/handoffs/inbox",
                   headers={"Authorization": f"Bearer {smgr_token}"})
    assert r.json()["total"] == 0


# ============================================================================
# Tests — response state machine
# ============================================================================


def test_response_marks_recipient_responded(client, patched_handoffs):
    upload_token = _token("u-uploader", "Anil", ["SALES_STAFF"])
    upload_resp = _upload(client, upload_token, recipients=["u-acct"])
    hid = upload_resp.json()["handoff_id"]

    acct_token = _token("u-acct", "Asha", ["ACCOUNTANT"])
    r = client.post(
        f"/api/v1/handoffs/{hid}/respond",
        headers={"Authorization": f"Bearer {acct_token}"},
        json={"response": "approved", "comment": "Verified"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["my_status"] == "responded"
    assert body["my_response"] == "approved"
    assert body["my_comment"] == "Verified"


def test_response_409s_if_already_responded(client, patched_handoffs):
    upload_token = _token("u-uploader", "Anil", ["SALES_STAFF"])
    upload_resp = _upload(client, upload_token, recipients=["u-acct"])
    hid = upload_resp.json()["handoff_id"]

    acct_token = _token("u-acct", "Asha", ["ACCOUNTANT"])
    client.post(f"/api/v1/handoffs/{hid}/respond",
                headers={"Authorization": f"Bearer {acct_token}"},
                json={"response": "approved"})
    r2 = client.post(f"/api/v1/handoffs/{hid}/respond",
                     headers={"Authorization": f"Bearer {acct_token}"},
                     json={"response": "denied"})
    assert r2.status_code == 409


def test_response_403s_for_non_recipient(client, patched_handoffs):
    upload_token = _token("u-uploader", "Anil", ["SALES_STAFF"])
    upload_resp = _upload(client, upload_token, recipients=["u-acct"])
    hid = upload_resp.json()["handoff_id"]

    other_token = _token("u-store-mgr", "Smita", ["STORE_MANAGER"])
    r = client.post(f"/api/v1/handoffs/{hid}/respond",
                    headers={"Authorization": f"Bearer {other_token}"},
                    json={"response": "approved"})
    assert r.status_code == 403


# ============================================================================
# Tests — reshare
# ============================================================================


def test_reshare_creates_child_with_parent_link_and_same_expiry(client, patched_handoffs):
    upload_token = _token("u-uploader", "Anil", ["SALES_STAFF"])
    upload_resp = _upload(client, upload_token, recipients=["u-store-mgr"], validity_days=14)
    parent = upload_resp.json()
    parent_expires = parent["expires_at"]

    smgr_token = _token("u-store-mgr", "Smita", ["STORE_MANAGER"])
    r = client.post(
        f"/api/v1/handoffs/{parent['handoff_id']}/reshare",
        headers={"Authorization": f"Bearer {smgr_token}"},
        json={"recipient_user_ids": ["u-acct"], "comment": "FYI"},
    )
    assert r.status_code == 201
    child = r.json()
    assert child["parent_handoff_id"] == parent["handoff_id"]
    # Same expires_at — TTL anchored to original upload date
    assert child["expires_at"] == parent_expires
    # Parent recipient (the resharer) was marked 'reshared'
    repo = patched_handoffs["handoff_repo"]
    fresh_parent = repo.find_by_id(parent["handoff_id"])
    smgr_record = next(
        r for r in fresh_parent["recipients"] if r["user_id"] == "u-store-mgr"
    )
    assert smgr_record["status"] == "responded"
    assert smgr_record["response"] == "reshared"


# ============================================================================
# Tests — permissions + eligible recipients endpoint
# ============================================================================


def test_eligible_recipients_filters_by_role(client, patched_handoffs):
    token = _token("u-uploader", "Anil", ["SALES_STAFF"])
    r = client.get(
        "/api/v1/handoffs/eligible-recipients/list",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    out = r.json()
    user_ids = {row["user_id"] for row in out["recipients"]}
    # Cashier excluded (ineligible role)
    assert "u-cashier" not in user_ids
    # 4 eligible roles all present
    assert {"u-store-mgr", "u-acct", "u-admin", "u-super"}.issubset(user_ids)


def test_revoke_403s_for_non_uploader(client, patched_handoffs):
    upload_token = _token("u-uploader", "Anil", ["SALES_STAFF"])
    upload_resp = _upload(client, upload_token, recipients=["u-store-mgr"])
    hid = upload_resp.json()["handoff_id"]

    smgr_token = _token("u-store-mgr", "Smita", ["STORE_MANAGER"])
    r = client.delete(
        f"/api/v1/handoffs/{hid}",
        headers={"Authorization": f"Bearer {smgr_token}"},
    )
    assert r.status_code == 403


def test_revoke_succeeds_for_uploader_and_cleans_file(client, patched_handoffs):
    upload_token = _token("u-uploader", "Anil", ["SALES_STAFF"])
    upload_resp = _upload(client, upload_token, recipients=["u-store-mgr"])
    hid = upload_resp.json()["handoff_id"]
    file_id = upload_resp.json()["file"]["file_id"]
    fs = patched_handoffs["file_store"]
    assert file_id in fs._files

    r = client.delete(
        f"/api/v1/handoffs/{hid}",
        headers={"Authorization": f"Bearer {upload_token}"},
    )
    assert r.status_code == 200
    # GridFS blob also cleaned
    assert file_id not in fs._files


def test_download_file_streams_content_with_disposition(client, patched_handoffs):
    upload_token = _token("u-uploader", "Anil", ["SALES_STAFF"])
    upload_resp = _upload(client, upload_token, recipients=["u-store-mgr"],
                          content=b"%PDF-1.4 actual bytes",
                          filename="april-bill.pdf")
    hid = upload_resp.json()["handoff_id"]

    smgr_token = _token("u-store-mgr", "Smita", ["STORE_MANAGER"])
    r = client.get(
        f"/api/v1/handoffs/{hid}/file",
        headers={"Authorization": f"Bearer {smgr_token}"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert "april-bill.pdf" in r.headers["content-disposition"]
    assert b"%PDF-1.4 actual bytes" in r.content
