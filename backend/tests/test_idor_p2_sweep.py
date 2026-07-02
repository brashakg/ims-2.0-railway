"""IDOR P2 sweep -- regression for the already-merged object-level store guards
(PR #627). Five routers gained a store-membership check on their object
mutations so a store-A actor can no longer drive a store-B document:

  workshop.py      -- _assert_job_store_access (can_access_store_scoped -> 404
                      existence-hide) on the 12 job mutations.
  hr.py            -- validate_store_access(record.store_id) on payroll-approve,
                      leave approve/reject, attendance update / check-out.
  expenses.py      -- validate_store_access(advance.store_id) on advance
                      approve / disburse / settle.
  inventory.py     -- can_access_store_scoped(count.store_id) on stock-count
                      items / complete / GET / reconcile.
  vendor_returns.py-- validate_store_access(return.store_id) on PATCH
                      /{id}/status.

For ONE representative endpoint in each router this asserts:
  (a) a store-A actor against a store-B doc is rejected (403, or 404 for the
      workshop existence-hide) AND the underlying repo mutation is NOT called;
  (b) a same-store actor still succeeds and reaches the handler's write;
  (c) ADMIN / SUPERADMIN bypass (any store).

The route coroutines are driven DIRECTLY (same pattern as
test_idor_transfers.py) with EVERY repo / db / get_db accessor monkeypatched
and every doc the handler reads seeded in-memory (incl. each doc's store_id).
Assertions are on the repo-call spy and the response object / HTTPException --
never a whole-JSON substring scan. TEST-ONLY: no source is modified; the
guards already live on main and are correct.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import expenses as expenses_mod  # noqa: E402
from api.routers import hr as hr_mod  # noqa: E402
from api.routers import inventory as inventory_mod  # noqa: E402
from api.routers import vendor_returns as vendor_returns_mod  # noqa: E402
from api.routers import workshop as workshop_mod  # noqa: E402

OWN_STORE = "BV-PUN-01"
OTHER_STORE = "BV-BOK-01"


def _user(role, stores):
    """A store-scoped (or admin) caller token in the shape get_current_user
    yields. active_store_id drives single-store reach; store_ids is the union."""
    return {
        "user_id": f"u-{role.lower()}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": list(stores),
        "active_store_id": stores[0] if stores else None,
    }


# A store manager whose ONLY store is OWN_STORE -- foreign to OTHER_STORE.
MGR_OWN = _user("STORE_MANAGER", [OWN_STORE])
# A store manager local to OTHER_STORE (the doc's store) -- same-store happy path.
MGR_OTHER = _user("STORE_MANAGER", [OTHER_STORE])
# HQ admins -- cross-store by design.
ADMIN_HQ = _user("ADMIN", [])
SUPERADMIN_HQ = _user("SUPERADMIN", [])


def _run(coro):
    return asyncio.run(coro)


def _expect_status(coro, code):
    """Assert the coroutine raises HTTPException with `code`; return the exc."""
    with pytest.raises(HTTPException) as exc:
        _run(coro)
    assert exc.value.status_code == code, (
        f"expected {code}, got {exc.value.status_code}: {exc.value.detail}"
    )
    return exc.value


# ===========================================================================
# 1. WORKSHOP -- _assert_job_store_access existence-hides (404) a foreign job
# ===========================================================================


class _WorkshopRepo:
    """Seeded single-job repo recording every mutation (assign / status / update)."""

    def __init__(self, job):
        self.job = job
        self.assigns = []
        self.status_updates = []
        self.updates = []

    def find_by_id(self, jid):
        return dict(self.job) if self.job.get("job_id") == jid else None

    def assign_technician(self, jid, tech_id):
        if self.job.get("job_id") != jid:
            return False
        self.assigns.append((jid, tech_id))
        self.job["assigned_to"] = tech_id
        return True

    def update_status(self, jid, status, user_id=None, notes=None):
        if self.job.get("job_id") != jid:
            return False
        self.status_updates.append(status)
        self.job["status"] = status
        return True

    def update(self, jid, data):
        if self.job.get("job_id") != jid:
            return False
        self.updates.append(data)
        self.job.update(data)
        return True


def _seed_job(**over):
    base = {
        "job_id": "WS-1",
        "job_number": "WS-260601-AAAA01",
        "store_id": OTHER_STORE,
        "status": "PENDING",
        "customer_id": "CUST-1",
        "fitting_details": {"confirmed_by_sales": True},
    }
    base.update(over)
    return base


class TestWorkshopJobStoreGuard:
    """workshop.py: a store-scoped caller may not touch another store's job.
    The guard 404s (existence-hide) before any repo write."""

    def _install(self, monkeypatch, job):
        repo = _WorkshopRepo(job)
        monkeypatch.setattr(workshop_mod, "get_workshop_repository", lambda: repo)
        # /assign validates the technician via a lazily-imported user repo;
        # None disables that branch so the test isolates the store guard.
        monkeypatch.setattr(
            "api.dependencies.get_user_repository", lambda: None, raising=False
        )
        return repo

    def test_assign_cross_store_404_no_mutation(self, monkeypatch):
        repo = self._install(monkeypatch, _seed_job())
        err = _expect_status(
            workshop_mod.assign_job(
                "WS-1", technician_id="tech-9", current_user=MGR_OWN
            ),
            404,
        )
        assert err.detail == "Workshop job not found"
        assert repo.assigns == []  # mutation NOT called
        assert repo.job.get("assigned_to") is None

    def test_start_cross_store_404_no_mutation(self, monkeypatch):
        repo = self._install(monkeypatch, _seed_job())
        _expect_status(
            workshop_mod.start_job("WS-1", current_user=MGR_OWN), 404
        )
        assert repo.status_updates == []
        assert repo.job["status"] == "PENDING"

    def test_assign_same_store_succeeds(self, monkeypatch):
        repo = self._install(monkeypatch, _seed_job())
        res = _run(
            workshop_mod.assign_job(
                "WS-1", technician_id="tech-9", current_user=MGR_OTHER
            )
        )
        assert res["technician_id"] == "tech-9"
        assert res["message"] == "Job assigned"
        assert repo.assigns == [("WS-1", "tech-9")]

    def test_start_same_store_succeeds(self, monkeypatch):
        repo = self._install(monkeypatch, _seed_job())
        res = _run(workshop_mod.start_job("WS-1", current_user=MGR_OTHER))
        assert res["status"] == "IN_PROGRESS"
        assert repo.status_updates == ["IN_PROGRESS"]

    @pytest.mark.parametrize("actor", [ADMIN_HQ, SUPERADMIN_HQ])
    def test_admin_cross_store_bypass(self, monkeypatch, actor):
        repo = self._install(monkeypatch, _seed_job())
        res = _run(
            workshop_mod.assign_job(
                "WS-1", technician_id="tech-9", current_user=actor
            )
        )
        assert res["message"] == "Job assigned"
        assert repo.assigns == [("WS-1", "tech-9")]

    def test_unattributed_job_hidden_from_store_staff(self, monkeypatch):
        """A legacy job with NO store_id is admin-only (can_access_store_scoped
        treats a missing store as out-of-scope for store-level roles)."""
        repo = self._install(monkeypatch, _seed_job(store_id=None))
        _expect_status(
            workshop_mod.assign_job(
                "WS-1", technician_id="tech-9", current_user=MGR_OTHER
            ),
            404,
        )
        assert repo.assigns == []
        # ...but an admin may still drive it.
        res = _run(
            workshop_mod.assign_job(
                "WS-1", technician_id="tech-9", current_user=ADMIN_HQ
            )
        )
        assert res["message"] == "Job assigned"


# ===========================================================================
# 2. HR -- validate_store_access on leave approve/reject (403 cross-store)
# ===========================================================================


class _LeaveRepo:
    """Seeded single leave-request repo recording every update."""

    def __init__(self, leave):
        self.leave = leave
        self.updates = []

    def find_by_id(self, lid):
        return dict(self.leave) if self.leave.get("leave_id") == lid else None

    def update(self, lid, data):
        if self.leave.get("leave_id") != lid:
            return False
        self.updates.append(data)
        self.leave.update(data)
        return True


def _seed_leave(**over):
    base = {
        "leave_id": "LV-1",
        "store_id": OTHER_STORE,
        # The applicant is some OTHER employee, so the manager isn't approving
        # their own leave (the separation-of-duties gate is a different check).
        "employee_id": "emp-applicant",
        "status": "PENDING",
        "leave_type": "CASUAL",
    }
    base.update(over)
    return base


class TestHrLeaveStoreGuard:
    """hr.py: leave approve/reject 403 a foreign-store manager before the
    APPROVED/REJECTED write."""

    def _install(self, monkeypatch, leave):
        repo = _LeaveRepo(leave)
        monkeypatch.setattr(hr_mod, "get_leave_repository", lambda: repo)
        return repo

    def test_approve_cross_store_403_no_update(self, monkeypatch):
        repo = self._install(monkeypatch, _seed_leave())
        err = _expect_status(
            hr_mod.approve_leave("LV-1", current_user=MGR_OWN), 403
        )
        assert "store" in err.detail.lower()
        assert repo.updates == []
        assert repo.leave["status"] == "PENDING"

    def test_reject_cross_store_403_no_update(self, monkeypatch):
        repo = self._install(monkeypatch, _seed_leave())
        _expect_status(
            hr_mod.reject_leave(
                "LV-1", reason="conflict", current_user=MGR_OWN
            ),
            403,
        )
        assert repo.updates == []
        assert repo.leave["status"] == "PENDING"

    def test_approve_same_store_succeeds(self, monkeypatch):
        repo = self._install(monkeypatch, _seed_leave())
        res = _run(hr_mod.approve_leave("LV-1", current_user=MGR_OTHER))
        assert res["leave_id"] == "LV-1"
        assert repo.leave["status"] == "APPROVED"
        assert repo.updates and repo.updates[0]["approved_by"] == MGR_OTHER["user_id"]

    def test_reject_same_store_succeeds(self, monkeypatch):
        repo = self._install(monkeypatch, _seed_leave())
        res = _run(
            hr_mod.reject_leave(
                "LV-1", reason="staffing", current_user=MGR_OTHER
            )
        )
        assert res["leave_id"] == "LV-1"
        assert repo.leave["status"] == "REJECTED"
        assert repo.leave["rejection_reason"] == "staffing"

    @pytest.mark.parametrize("actor", [ADMIN_HQ, SUPERADMIN_HQ])
    def test_admin_cross_store_bypass(self, monkeypatch, actor):
        repo = self._install(monkeypatch, _seed_leave())
        res = _run(hr_mod.approve_leave("LV-1", current_user=actor))
        assert res["leave_id"] == "LV-1"
        assert repo.leave["status"] == "APPROVED"


# ===========================================================================
# 3. EXPENSES -- validate_store_access on advance approve (403 cross-store)
# ===========================================================================


class _AdvanceRepo:
    """Seeded single cash-advance repo recording every update."""

    def __init__(self, advance):
        self.advance = advance
        self.updates = []

    def find_by_id(self, aid):
        return dict(self.advance) if self.advance.get("advance_id") == aid else None

    def update(self, aid, data):
        if self.advance.get("advance_id") != aid:
            return False
        self.updates.append(data)
        self.advance.update(data)
        return True


def _seed_advance(**over):
    base = {
        "advance_id": "ADV-1",
        "store_id": OTHER_STORE,
        # Approver != requester so the separation-of-duties gate is not what trips.
        "employee_id": "emp-requester",
        "status": "PENDING",
        "amount": 5000.0,
    }
    base.update(over)
    return base


class TestExpensesAdvanceStoreGuard:
    """expenses.py: advance approve/disburse/settle 403 a foreign-store
    approver before the status write."""

    def _install(self, monkeypatch, advance):
        repo = _AdvanceRepo(advance)
        monkeypatch.setattr(expenses_mod, "get_advance_repository", lambda: repo)
        return repo

    def test_approve_cross_store_403_no_update(self, monkeypatch):
        repo = self._install(monkeypatch, _seed_advance())
        err = _expect_status(
            expenses_mod.approve_advance("ADV-1", current_user=MGR_OWN), 403
        )
        assert "store" in err.detail.lower()
        assert repo.updates == []
        assert repo.advance["status"] == "PENDING"

    def test_disburse_cross_store_403_no_update(self, monkeypatch):
        repo = self._install(monkeypatch, _seed_advance(status="APPROVED"))
        _expect_status(
            expenses_mod.disburse_advance(
                "ADV-1", reference="NEFT-9", current_user=MGR_OWN
            ),
            403,
        )
        assert repo.updates == []
        assert repo.advance["status"] == "APPROVED"

    def test_approve_same_store_succeeds(self, monkeypatch):
        repo = self._install(monkeypatch, _seed_advance())
        res = _run(expenses_mod.approve_advance("ADV-1", current_user=MGR_OTHER))
        assert res["advance_id"] == "ADV-1"
        assert repo.advance["status"] == "APPROVED"
        assert repo.updates and repo.updates[0]["approved_by"] == MGR_OTHER["user_id"]

    def test_disburse_same_store_succeeds(self, monkeypatch):
        repo = self._install(monkeypatch, _seed_advance(status="APPROVED"))
        res = _run(
            expenses_mod.disburse_advance(
                "ADV-1", reference="NEFT-9", current_user=MGR_OTHER
            )
        )
        assert res["advance_id"] == "ADV-1"
        assert res["reference"] == "NEFT-9"
        assert repo.advance["status"] == "DISBURSED"

    @pytest.mark.parametrize("actor", [ADMIN_HQ, SUPERADMIN_HQ])
    def test_admin_cross_store_bypass(self, monkeypatch, actor):
        repo = self._install(monkeypatch, _seed_advance())
        res = _run(expenses_mod.approve_advance("ADV-1", current_user=actor))
        assert res["advance_id"] == "ADV-1"
        assert repo.advance["status"] == "APPROVED"


# ===========================================================================
# 4. INVENTORY -- can_access_store_scoped on stock-count (404 cross-store)
# ===========================================================================


class _CountColl:
    """In-test stand-in for the `stock_counts` collection, recording writes."""

    def __init__(self, doc):
        self.doc = doc
        self.updates = []

    def find_one(self, flt, projection=None):
        if self.doc and self.doc.get("count_id") == flt.get("count_id"):
            return dict(self.doc)
        return None

    def update_one(self, flt, update, upsert=False):
        self.updates.append({"filter": flt, "update": update})
        if self.doc and self.doc.get("count_id") == flt.get("count_id"):
            self.doc.update(update.get("$set", {}))
        return None


class _CountDB:
    def __init__(self, coll):
        self._coll = coll

    def get_collection(self, name):
        return self._coll


def _seed_count(**over):
    base = {
        "count_id": "SC-1",
        "store_id": OTHER_STORE,
        "status": "in_progress",
        "items": [],
        "items_counted": 0,
    }
    base.update(over)
    return base


class TestInventoryStockCountStoreGuard:
    """inventory.py: stock-count item/get/complete/reconcile 404 a foreign
    caller (existence-hide via can_access_store_scoped) before any write."""

    def _install(self, monkeypatch, doc):
        coll = _CountColl(doc)
        monkeypatch.setattr(inventory_mod, "_get_db", lambda: _CountDB(coll))
        return coll

    def _item(self):
        return inventory_mod.StockCountItem(
            product_id="PRD-1", counted_quantity=3
        )

    def test_record_item_cross_store_404_no_write(self, monkeypatch):
        coll = self._install(monkeypatch, _seed_count())
        err = _expect_status(
            inventory_mod.record_count_item(
                "SC-1", item=self._item(), current_user=MGR_OWN
            ),
            404,
        )
        assert "not found" in err.detail.lower()
        assert coll.updates == []  # no item written
        assert coll.doc["items_counted"] == 0

    def test_get_count_cross_store_404(self, monkeypatch):
        self._install(monkeypatch, _seed_count())
        _expect_status(
            inventory_mod.get_stock_count("SC-1", current_user=MGR_OWN), 404
        )

    def test_record_item_same_store_succeeds(self, monkeypatch):
        coll = self._install(monkeypatch, _seed_count())
        res = _run(
            inventory_mod.record_count_item(
                "SC-1", item=self._item(), current_user=MGR_OTHER
            )
        )
        assert res["message"] == "Item recorded"
        assert res["items_counted"] == 1
        assert len(coll.updates) == 1

    def test_get_count_same_store_succeeds(self, monkeypatch):
        self._install(monkeypatch, _seed_count())
        res = _run(inventory_mod.get_stock_count("SC-1", current_user=MGR_OTHER))
        assert res["count_id"] == "SC-1"
        assert res["store_id"] == OTHER_STORE

    @pytest.mark.parametrize("actor", [ADMIN_HQ, SUPERADMIN_HQ])
    def test_admin_cross_store_bypass(self, monkeypatch, actor):
        coll = self._install(monkeypatch, _seed_count())
        res = _run(
            inventory_mod.record_count_item(
                "SC-1", item=self._item(), current_user=actor
            )
        )
        assert res["items_counted"] == 1
        assert len(coll.updates) == 1


# ===========================================================================
# 5. VENDOR RETURNS -- validate_store_access on PATCH /{id}/status (403)
# ===========================================================================


class _VendorReturnColl:
    """In-test stand-in for the `vendor_returns` collection, recording writes."""

    def __init__(self, doc):
        self.doc = doc
        self.updates = []

    def find_one(self, flt, projection=None):
        if self.doc and self.doc.get("return_id") == flt.get("return_id"):
            return dict(self.doc)
        return None

    def update_one(self, flt, update, upsert=False):
        self.updates.append({"filter": flt, "update": update})
        matched = bool(self.doc) and all(self.doc.get(k) == v for k, v in flt.items())
        if matched:
            self.doc.update(update.get("$set", {}))
        return type("R", (), {"matched_count": 1 if matched else 0})()


class _VendorReturnDB:
    def __init__(self, coll):
        self._coll = coll

    def get_collection(self, name):
        return self._coll


def _seed_vendor_return(**over):
    base = {
        "return_id": "VR-1",
        "store_id": OTHER_STORE,
        "vendor_id": "VEND-1",
        "status": "created",
        "total_value": 2000.0,
        "status_history": [],
    }
    base.update(over)
    return base


class TestVendorReturnStatusStoreGuard:
    """vendor_returns.py: PATCH /{id}/status 403s a foreign-store actor before
    the status (and any credit-note) write."""

    def _install(self, monkeypatch, doc):
        coll = _VendorReturnColl(doc)
        monkeypatch.setattr(
            vendor_returns_mod, "_get_db", lambda: _VendorReturnDB(coll)
        )
        return coll

    def _update(self, status="approved"):
        return vendor_returns_mod.VendorReturnStatusUpdate(status=status)

    def test_status_cross_store_403_no_write(self, monkeypatch):
        coll = self._install(monkeypatch, _seed_vendor_return())
        err = _expect_status(
            vendor_returns_mod.update_return_status(
                "VR-1", status_update=self._update(), current_user=MGR_OWN
            ),
            403,
        )
        assert "store" in err.detail.lower()
        assert coll.updates == []  # no status transition persisted
        assert coll.doc["status"] == "created"

    def test_status_same_store_succeeds(self, monkeypatch):
        coll = self._install(monkeypatch, _seed_vendor_return())
        res = _run(
            vendor_returns_mod.update_return_status(
                "VR-1", status_update=self._update("approved"), current_user=MGR_OTHER
            )
        )
        assert res["return"]["status"] == "approved"
        assert len(coll.updates) == 1
        assert coll.doc["status"] == "approved"

    @pytest.mark.parametrize("actor", [ADMIN_HQ, SUPERADMIN_HQ])
    def test_admin_cross_store_bypass(self, monkeypatch, actor):
        coll = self._install(monkeypatch, _seed_vendor_return())
        res = _run(
            vendor_returns_mod.update_return_status(
                "VR-1", status_update=self._update("approved"), current_user=actor
            )
        )
        assert res["return"]["status"] == "approved"
        assert coll.doc["status"] == "approved"
