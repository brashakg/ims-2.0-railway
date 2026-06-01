"""
Tests for the BVI Phase 4 Image Design Queue module (FLAGSHIP #3).

Three layers, mirroring test_ecom_menus.py's style:
  1. ProductImageRepository round-trips via the in-memory MockCollection (no live
     Mongo): create (defaults + required-field refusal) / get / list filters /
     update (patchable-only) / delete / count_by_status.
  2. The RAW->EDITED->APPROVED design lifecycle state machine: assign,
     set_status valid + ILLEGAL transitions (the 409 source), the approve/reject
     field stamps, attach_edited -> REVIEW + its illegal-state refusal, and the
     pure is_valid_transition predicate.
  3. Router wiring over a TestClient with a monkeypatched DB + audit repo (no live
     Mongo): every images route is catalogued in rbac_policy.POLICY with the ecom
     role set, the literal action sub-paths resolve over the {image_id} param,
     check_access allow/deny, the live role gate (SALES_STAFF 403 + fail-soft list
     without DB), the full create->assign->start->edited->approve flow, an illegal
     transition is a 409, and APPROVE writes an audit_logs row.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_online_store_images.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from database.connection import MockCollection  # noqa: E402
from database.repositories.product_image_repository import (  # noqa: E402
    ProductImageRepository,
    is_valid_transition,
    VALID_TRANSITIONS,
)
from api.services import rbac_policy as rbac  # noqa: E402


# ===========================================================================
# Layer 1 -- repository CRUD round-trips (MockCollection, no live Mongo)
# ===========================================================================

@pytest.fixture
def repo():
    return ProductImageRepository(MockCollection("product_images"))


def test_create_then_get_roundtrip_with_defaults(repo):
    """A fresh image enters the queue as kind=RAW, status=QUEUED, source=UPLOAD,
    position 0, with null lifecycle fields + a null shopify_image_id (PUSH-DARK)."""
    created = repo.create({"product_id": "P1", "url": "http://x/raw.jpg"})
    assert created is not None
    assert created["image_id"]
    assert created["product_id"] == "P1"
    assert created["url"] == "http://x/raw.jpg"
    # Queue defaults.
    assert created["kind"] == "RAW"
    assert created["status"] == "QUEUED"
    assert created["source"] == "UPLOAD"
    assert created["position"] == 0
    assert created["variant_id"] is None
    assert created["edited_url"] is None
    assert created["assigned_to"] is None
    assert created["reviewed_by"] is None
    assert created["approved_at"] is None
    # PUSH-DARK: not pushed to Shopify yet.
    assert created["shopify_image_id"] is None
    assert "created_at" in created and "updated_at" in created

    fetched = repo.get_by_id(created["image_id"])
    assert fetched is not None
    assert fetched["image_id"] == created["image_id"]


def test_create_requires_product_id_and_url(repo):
    """product_id + url are the minimum; a row missing either is refused."""
    assert repo.create({"url": "http://x/y.jpg"}) is None        # no product_id
    assert repo.create({"product_id": "P1"}) is None             # no url
    assert repo.create({}) is None
    assert repo.count() == 0


def test_create_honours_caller_overrides(repo):
    """Caller-supplied kind/source/variant_id/position win over the defaults."""
    created = repo.create(
        {
            "product_id": "P1",
            "url": "http://x/v.jpg",
            "variant_id": "V9",
            "kind": "EDITED",
            "source": "SHOPIFY",
            "position": 3,
            "alt_text": "Front view",
        }
    )
    assert created["variant_id"] == "V9"
    assert created["kind"] == "EDITED"
    assert created["source"] == "SHOPIFY"
    assert created["position"] == 3
    assert created["alt_text"] == "Front view"


def test_get_missing_returns_none(repo):
    assert repo.get_by_id("nope") is None
    assert repo.get_by_id("") is None


def test_list_filters_by_status_product_variant_assignee_kind(repo):
    a = repo.create({"product_id": "P1", "url": "u1"})                       # QUEUED
    b = repo.create({"product_id": "P1", "url": "u2", "variant_id": "V1"})   # QUEUED
    c = repo.create({"product_id": "P2", "url": "u3"})                       # QUEUED
    # Move b to IN_PROGRESS + assign; leave a/c QUEUED.
    repo.set_status(b["image_id"], "IN_PROGRESS", by="d1")
    repo.assign(b["image_id"], "designer-1")

    # By product.
    assert {i["image_id"] for i in repo.list(product_id="P1")} == {
        a["image_id"], b["image_id"]
    }
    assert {i["image_id"] for i in repo.list(product_id="P2")} == {c["image_id"]}
    # By status.
    assert {i["image_id"] for i in repo.list(status="QUEUED")} == {
        a["image_id"], c["image_id"]
    }
    assert {i["image_id"] for i in repo.list(status="IN_PROGRESS")} == {b["image_id"]}
    # By variant.
    assert {i["image_id"] for i in repo.list(variant_id="V1")} == {b["image_id"]}
    # By assignee.
    assert {i["image_id"] for i in repo.list(assigned_to="designer-1")} == {
        b["image_id"]
    }
    # Unfiltered -> all three.
    assert len(repo.list()) == 3


def test_update_patches_only_presentation_fields(repo):
    created = repo.create({"product_id": "P1", "url": "u"})
    iid = created["image_id"]
    # Patchable fields change.
    assert repo.update(iid, {"alt_text": "hero", "position": 2, "kind": "FINAL"}) is True
    doc = repo.get_by_id(iid)
    assert doc["alt_text"] == "hero"
    assert doc["position"] == 2
    assert doc["kind"] == "FINAL"
    # Lifecycle-controlled fields are NOT patchable via update() -- a patch of only
    # those is a no-op False (can't bypass the state machine with a blind PUT).
    assert repo.update(iid, {"status": "APPROVED", "assigned_to": "x",
                             "approved_at": "now", "reviewed_by": "y"}) is False
    doc = repo.get_by_id(iid)
    assert doc["status"] == "QUEUED"        # unchanged
    assert doc["assigned_to"] is None       # unchanged


def test_delete_removes_image(repo):
    iid = repo.create({"product_id": "P1", "url": "u"})["image_id"]
    assert repo.delete(iid) is True
    assert repo.get_by_id(iid) is None
    # Deleting again is a no-op False (already gone).
    assert repo.delete(iid) is False


def test_count_by_status(repo):
    repo.create({"product_id": "P1", "url": "u1"})
    repo.create({"product_id": "P1", "url": "u2"})
    b = repo.create({"product_id": "P1", "url": "u3"})
    repo.set_status(b["image_id"], "IN_PROGRESS", by="d")
    assert repo.count_by_status("QUEUED") == 2
    assert repo.count_by_status("IN_PROGRESS") == 1
    assert repo.count_by_status("APPROVED") == 0
    assert repo.count_by_status("") == 0


# ===========================================================================
# Layer 2 -- the RAW->EDITED->APPROVED lifecycle state machine
# ===========================================================================

def test_is_valid_transition_predicate():
    """The pure predicate matches the documented graph (the 409 source of truth)."""
    assert is_valid_transition("QUEUED", "IN_PROGRESS") is True
    assert is_valid_transition("IN_PROGRESS", "REVIEW") is True
    assert is_valid_transition("REVIEW", "APPROVED") is True
    assert is_valid_transition("REVIEW", "REJECTED") is True
    assert is_valid_transition("REJECTED", "IN_PROGRESS") is True
    # None current is treated as QUEUED.
    assert is_valid_transition(None, "IN_PROGRESS") is True
    # Illegal edges.
    assert is_valid_transition("QUEUED", "REVIEW") is False
    assert is_valid_transition("QUEUED", "APPROVED") is False
    assert is_valid_transition("IN_PROGRESS", "APPROVED") is False
    assert is_valid_transition("APPROVED", "IN_PROGRESS") is False   # terminal
    assert is_valid_transition("REVIEW", "QUEUED") is False
    # Same-state is not a valid edge.
    assert is_valid_transition("QUEUED", "QUEUED") is False
    # Unknown target.
    assert is_valid_transition("QUEUED", "BOGUS") is False
    # APPROVED is terminal in the graph.
    assert VALID_TRANSITIONS["APPROVED"] == set()


def test_assign_sets_and_unsets_without_changing_status(repo):
    created = repo.create({"product_id": "P1", "url": "u"})
    iid = created["image_id"]
    out = repo.assign(iid, "designer-1")
    assert out is not None
    assert out["assigned_to"] == "designer-1"
    assert out["status"] == "QUEUED"      # assign does not start work
    # Unassign with None.
    out2 = repo.assign(iid, None)
    assert out2["assigned_to"] is None
    # Unknown image -> None.
    assert repo.assign("no-such", "x") is None


def test_full_happy_path_transitions(repo):
    """QUEUED -> IN_PROGRESS -> REVIEW -> APPROVED, with the approve stamps."""
    iid = repo.create({"product_id": "P1", "url": "u"})["image_id"]
    assert repo.set_status(iid, "IN_PROGRESS", by="d1")["status"] == "IN_PROGRESS"
    assert repo.set_status(iid, "REVIEW", by="d1")["status"] == "REVIEW"
    approved = repo.set_status(iid, "APPROVED", by="reviewer-9")
    assert approved["status"] == "APPROVED"
    # Approve stamps approved_at + reviewed_by.
    assert approved["approved_at"] is not None
    assert approved["reviewed_by"] == "reviewer-9"


def test_reject_then_rework(repo):
    """REVIEW -> REJECTED stamps reviewer; REJECTED -> IN_PROGRESS re-works."""
    iid = repo.create({"product_id": "P1", "url": "u"})["image_id"]
    repo.set_status(iid, "IN_PROGRESS", by="d1")
    repo.set_status(iid, "REVIEW", by="d1")
    rejected = repo.set_status(iid, "REJECTED", by="reviewer-9")
    assert rejected["status"] == "REJECTED"
    assert rejected["reviewed_by"] == "reviewer-9"
    # Re-work: REJECTED -> IN_PROGRESS is legal.
    assert repo.set_status(iid, "IN_PROGRESS", by="d1")["status"] == "IN_PROGRESS"


def test_illegal_transitions_return_none(repo):
    """An illegal move returns None (the router maps this to a 409) and does NOT
    mutate the stored status."""
    iid = repo.create({"product_id": "P1", "url": "u"})["image_id"]
    # QUEUED -> REVIEW / APPROVED / REJECTED are all illegal (must start first).
    assert repo.set_status(iid, "REVIEW", by="d") is None
    assert repo.set_status(iid, "APPROVED", by="d") is None
    assert repo.set_status(iid, "REJECTED", by="d") is None
    # Same-state no-op is refused.
    assert repo.set_status(iid, "QUEUED", by="d") is None
    # Unknown target -> None.
    assert repo.set_status(iid, "BOGUS", by="d") is None
    # The stored status is untouched after all the rejected moves.
    assert repo.get_by_id(iid)["status"] == "QUEUED"
    # Unknown image -> None.
    assert repo.set_status("no-such", "IN_PROGRESS", by="d") is None


def test_attach_edited_moves_to_review(repo):
    """attach_edited records edited_url, flips kind->EDITED, moves IN_PROGRESS ->
    REVIEW, and records submitted_by."""
    iid = repo.create({"product_id": "P1", "url": "raw"})["image_id"]
    repo.set_status(iid, "IN_PROGRESS", by="d1")
    out = repo.attach_edited(iid, "http://x/edited.jpg", by="designer-1")
    assert out is not None
    assert out["edited_url"] == "http://x/edited.jpg"
    assert out["status"] == "REVIEW"
    assert out["kind"] == "EDITED"
    assert out["submitted_by"] == "designer-1"


def test_attach_edited_illegal_from_non_in_progress(repo):
    """attach_edited requires IN_PROGRESS; from QUEUED it returns None (-> 409) and
    leaves the row unchanged."""
    iid = repo.create({"product_id": "P1", "url": "raw"})["image_id"]
    # Still QUEUED -> can't submit for review.
    assert repo.attach_edited(iid, "http://x/edited.jpg") is None
    doc = repo.get_by_id(iid)
    assert doc["status"] == "QUEUED"
    assert doc["edited_url"] is None
    # Empty edited_url is refused even when IN_PROGRESS.
    repo.set_status(iid, "IN_PROGRESS", by="d1")
    assert repo.attach_edited(iid, "") is None
    # Unknown image -> None.
    assert repo.attach_edited("no-such", "http://x/e.jpg") is None


# ===========================================================================
# Layer 3 -- router RBAC catalogue + route resolution + role gate + live flow
# ===========================================================================

_IMAGE_ROUTES = [
    ("GET", "/api/v1/online-store/images"),
    ("POST", "/api/v1/online-store/images"),
    ("GET", "/api/v1/online-store/images/{image_id}"),
    ("PUT", "/api/v1/online-store/images/{image_id}"),
    ("DELETE", "/api/v1/online-store/images/{image_id}"),
    ("POST", "/api/v1/online-store/images/{image_id}/assign"),
    ("POST", "/api/v1/online-store/images/{image_id}/status"),
    ("POST", "/api/v1/online-store/images/{image_id}/edited"),
]

_ECOM_SET = {"ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER", "SUPERADMIN"}


def test_every_image_route_catalogued_with_ecom_roles():
    for method, path in _IMAGE_ROUTES:
        entry = rbac.policy_for(method, path)
        assert entry is not None, f"{method} {path} not catalogued in rbac_policy"
        assert set(entry["allowed"]) == _ECOM_SET, f"{method} {path} -> {entry['allowed']}"


def test_action_subpaths_beat_image_id_param():
    """The literal .../{image_id}/assign|status|edited must resolve to their own
    routes -- not be shadowed by the bare .../{image_id} param route."""
    for action in ("assign", "status", "edited"):
        hit = rbac.policy_for("POST", f"/api/v1/online-store/images/IMG1/{action}")
        assert hit is not None
        assert hit["path"].endswith(f"/images/{{image_id}}/{action}"), action

    # A bare image id still resolves to the param routes (PUT + DELETE + GET).
    for method in ("GET", "PUT", "DELETE"):
        hit = rbac.policy_for(method, "/api/v1/online-store/images/IMG1")
        assert hit is not None and hit["path"].endswith("/images/{image_id}")


def test_check_access_allows_ecom_roles_denies_others():
    path = "/api/v1/online-store/images"
    for role in ("SUPERADMIN", "ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER"):
        assert rbac.check_access("POST", path, [role]) is True, role
    for role in ("SALES_STAFF", "CASHIER", "OPTOMETRIST", "WORKSHOP_STAFF", "ACCOUNTANT"):
        assert rbac.check_access("POST", path, [role]) is False, role


def test_live_role_gate_forbids_sales_staff(client, staff_headers):
    """SALES_STAFF is outside the ecom set -> 403 before the handler (no DB needed)."""
    r = client.get("/api/v1/online-store/images", headers=staff_headers)
    assert r.status_code == 403, r.text


def test_live_list_is_failsoft_without_db(client, auth_headers):
    """GET list returns 200 with a well-formed envelope even when no DB is
    connected (db_connected False -> empty list, never a 500)."""
    r = client.get("/api/v1/online-store/images", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "images" in body and "count" in body
    assert isinstance(body["images"], list)


# --- live flow over a monkeypatched DB + audit repo (no live Mongo) ----------

class _FakeConn:
    """Stand-in for the DatabaseConnection the image router's _get_db() expects:
    `.is_connected` True + `.db[name]` returns a shared MockCollection so all
    routes in one test hit the same in-memory store."""

    def __init__(self):
        self._colls = {}
        self.is_connected = True

    class _DB:
        def __init__(self, outer):
            self._outer = outer

        def __getitem__(self, name):
            return self._outer._colls.setdefault(name, MockCollection(name))

    @property
    def db(self):
        return _FakeConn._DB(self)


@pytest.fixture
def patched_db(monkeypatch):
    """Point dependencies.get_db at a fresh _FakeConn and get_audit_repository at
    a real AuditRepository bound to that conn's `audit_logs` MockCollection, so
    the image router's _repo() + _write_audit() both resolve without live Mongo.
    Returns (conn, audit_repo) for assertions."""
    from api import dependencies as deps
    from database.repositories.audit_repository import AuditRepository

    conn = _FakeConn()
    audit_repo = AuditRepository(conn.db["audit_logs"])

    monkeypatch.setattr(deps, "get_db", lambda: conn)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: audit_repo)
    return conn, audit_repo


def test_live_full_lifecycle_flow_and_audit_on_approve(client, auth_headers, patched_db):
    """End-to-end over the HTTP API: create -> assign -> start -> attach edited ->
    approve, asserting each state, an illegal transition 409, and that APPROVE
    wrote an audit_logs row."""
    conn, audit_repo = patched_db
    base = "/api/v1/online-store/images"

    # Create / queue.
    r = client.post(base, headers=auth_headers,
                    json={"product_id": "P1", "url": "http://x/raw.jpg"})
    assert r.status_code == 201, r.text
    img = r.json()["image"]
    iid = img["image_id"]
    assert img["status"] == "QUEUED" and img["kind"] == "RAW"

    # It shows up in the QUEUED queue filter.
    r = client.get(base, headers=auth_headers, params={"status": "QUEUED"})
    assert r.status_code == 200
    assert any(i["image_id"] == iid for i in r.json()["images"])

    # Illegal jump QUEUED -> APPROVED is a 409 (Fail Loudly).
    r = client.post(f"{base}/{iid}/status", headers=auth_headers,
                    json={"status": "APPROVED"})
    assert r.status_code == 409, r.text

    # Assign to a designer (status stays QUEUED).
    r = client.post(f"{base}/{iid}/assign", headers=auth_headers,
                    json={"assigned_to": "designer-1"})
    assert r.status_code == 200
    assert r.json()["image"]["assigned_to"] == "designer-1"

    # Start work: QUEUED -> IN_PROGRESS.
    r = client.post(f"{base}/{iid}/status", headers=auth_headers,
                    json={"status": "IN_PROGRESS"})
    assert r.status_code == 200
    assert r.json()["image"]["status"] == "IN_PROGRESS"

    # Attach the edited asset -> REVIEW.
    r = client.post(f"{base}/{iid}/edited", headers=auth_headers,
                    json={"edited_url": "http://x/edited.jpg"})
    assert r.status_code == 200
    body = r.json()["image"]
    assert body["status"] == "REVIEW"
    assert body["edited_url"] == "http://x/edited.jpg"
    assert body["kind"] == "EDITED"

    # No audit row yet (only APPROVE writes one).
    assert audit_repo.find_many({"action": "PRODUCT_IMAGE_APPROVED"}) == []

    # Approve -> APPROVED + an audit_logs row.
    r = client.post(f"{base}/{iid}/status", headers=auth_headers,
                    json={"status": "APPROVED"})
    assert r.status_code == 200
    assert r.json()["image"]["status"] == "APPROVED"

    rows = audit_repo.find_many({"action": "PRODUCT_IMAGE_APPROVED"})
    assert len(rows) == 1, "APPROVE must write exactly one audit row"
    row = rows[0]
    assert row["entity_type"] == "product_image"
    assert row["entity_id"] == iid
    assert row["details"]["product_id"] == "P1"


def test_live_edited_from_queued_is_409(client, auth_headers, patched_db):
    """Attaching an edited asset to a still-QUEUED image is a 409 (can't submit
    for review work that wasn't started)."""
    base = "/api/v1/online-store/images"
    r = client.post(base, headers=auth_headers,
                    json={"product_id": "P2", "url": "http://x/raw.jpg"})
    iid = r.json()["image"]["image_id"]
    r = client.post(f"{base}/{iid}/edited", headers=auth_headers,
                    json={"edited_url": "http://x/edited.jpg"})
    assert r.status_code == 409, r.text


def test_live_unknown_status_is_400(client, auth_headers, patched_db):
    """An unknown target status is a 400 (validation), distinct from the 409 for a
    known-but-illegal transition."""
    base = "/api/v1/online-store/images"
    r = client.post(base, headers=auth_headers,
                    json={"product_id": "P3", "url": "http://x/raw.jpg"})
    iid = r.json()["image"]["image_id"]
    r = client.post(f"{base}/{iid}/status", headers=auth_headers,
                    json={"status": "BOGUS"})
    assert r.status_code == 400, r.text


def test_live_create_requires_product_id_and_url(client, auth_headers, patched_db):
    """Pydantic-required product_id + url are enforced (422 when absent)."""
    base = "/api/v1/online-store/images"
    assert client.post(base, headers=auth_headers,
                       json={"url": "http://x/y.jpg"}).status_code == 422
    assert client.post(base, headers=auth_headers,
                       json={"product_id": "P1"}).status_code == 422


def test_live_get_and_delete_unknown_is_404(client, auth_headers, patched_db):
    base = "/api/v1/online-store/images"
    assert client.get(f"{base}/no-such", headers=auth_headers).status_code == 404
    assert client.delete(f"{base}/no-such", headers=auth_headers).status_code == 404
