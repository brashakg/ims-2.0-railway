"""
Codex whole-app audit, batch b27 (payroll engine + gender): L3-16, L3-17
========================================================================
Owner rulings 2026-11-11.

L3-16  Two salary calculators gave two net pays for one salary setup (the
       audit: Basic 12,000 + HRA 3,000 -> payroll engine 13,247, old writer
       13,360), and the Salary Sheet showed an old-writer row as zero
       Basic/HRA/Gross/deductions beside its net pay. RULING: the payroll
       engine is the official calculation. The old writer refuses and points
       to Payroll Run; the Salary Sheet reads the engine or shows nothing.
       Stored ``salary_records`` rows are not touched.
L3-17  The engine could tax by a gender-aware PT slab but no screen or API
       could give it the gender: PUT gender=FEMALE answered 200 and threw it
       away. RULING: a gender field on the salary setup, filled by an admin,
       fed to the engine; not set = today's behaviour (the general slab).

Every test drives the REAL payroll router over a strict in-memory Mongo double
and asserts on what is STORED, not only on the reply.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_audit_b27_payroll_engine_gender.py -q
"""

from __future__ import annotations

import copy
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from strict_fakes import StrictDB  # noqa: E402

from api.routers import payroll as payroll_router  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

EMP = "EMP-B27"
ENTITY = "ENT-B27"
STORE_MH = "ST-B27-PUNE"  # Maharashtra: the PT slab differs by gender
STORE_JH = "ST-B27-BOKARO"  # Jharkhand: one slab for everyone
MONTH, YEAR = 12, 2026

ADMIN = {
    "user_id": "admin-b27",
    "username": "admin",
    "roles": ["ADMIN"],
    "store_ids": [STORE_MH, STORE_JH],
    "active_store_id": STORE_MH,
}
MANAGER = {
    "user_id": "mgr-b27",
    "username": "manager",
    "roles": ["STORE_MANAGER"],
    "store_ids": [STORE_MH],
    "active_store_id": STORE_MH,
}

# The old writer's stored row for the audit's employee, exactly as it wrote it:
# a FLAT breakdown (no earnings/deductions keys) with net 13,360.
OLD_ROW = {
    "salary_record_id": "SR-OLD-B27",
    "employee_id": EMP,
    "employee_name": "ZZ Audit Manager",
    "store_id": STORE_MH,
    "month": MONTH,
    "year": YEAR,
    "breakdown": {
        "basic": 12000.0,
        "hra": 3000.0,
        "conveyance": 0.0,
        "medical": 0.0,
        "special_allowance": 0.0,
        "gross_salary": 15000.0,
        "pf_employee": 1440.0,
        "pf_employer": 1440.0,
        "professional_tax": 200.0,
        "esi": 0.0,
        "tds": 0.0,
        "lwp_deduction": 0.0,
        "advance_deduction": 0.0,
        "net_pay": 13360.0,
    },
    "status": "calculated",
}


def _db(store=STORE_MH, **config) -> StrictDB:
    """The audit's salary setup: Basic 12,000 + HRA 3,000, ESI on auto."""
    db = StrictDB()
    db.seed("users", [{"user_id": EMP, "full_name": "ZZ Audit Manager"}])
    db.seed(
        "stores",
        [
            {"store_id": STORE_MH, "state": "Maharashtra"},
            {"store_id": STORE_JH, "state": "Jharkhand"},
        ],
    )
    db.seed(
        "salary_config",
        [
            {
                "config_id": "CFG-B27",
                "employee_id": EMP,
                "entity_id": ENTITY,
                "store_id": store,
                "basic": 12000.0,
                "hra": 3000.0,
                "esi_applicable": None,
                "pt_applicable": True,
                "is_active": True,
                **config,
            }
        ],
    )
    return db


def _client(db, monkeypatch, user=None) -> TestClient:
    monkeypatch.setattr(payroll_router, "_get_db", lambda: db)
    app = FastAPI()
    app.include_router(payroll_router.router, prefix="/api/v1/payroll")
    app.dependency_overrides[get_current_user] = lambda: (user or ADMIN)
    return TestClient(app)


def _run(c, dry_run=False):
    r = c.post(
        "/api/v1/payroll/run",
        json={"month": MONTH, "year": YEAR, "entity_id": ENTITY, "dry_run": dry_run},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _preview_pt(c) -> float:
    (row,) = _run(c, dry_run=True)["rows"]
    return row["breakdown"]["deductions"]["professional_tax"]


def _stored_config(db) -> dict:
    return db.get_collection("salary_config").find_one({"employee_id": EMP})


def _sheet(c) -> dict:
    r = c.get(
        "/api/v1/payroll/salary-sheet",
        params={"month": MONTH, "year": YEAR, "store_id": STORE_MH},
    )
    assert r.status_code == 200, r.text
    return r.json()


# ===========================================================================
# L3-16 -- one official salary calculation
# ===========================================================================

OLD_WRITER_BODY = {
    "employee_id": EMP,
    "month": MONTH,
    "year": YEAR,
    "working_days": 26,
    "leave_without_pay_days": 0,
}


def test_l3_16_old_salary_writer_refuses_and_saves_nothing(monkeypatch):
    """The audit's move: an admin posts the old writer for the same employee
    and month. It answered 201 and saved net 13,360 next to the engine's
    13,247."""
    db = _db()
    c = _client(db, monkeypatch)
    r = c.post("/api/v1/payroll/salary/calculate", json=OLD_WRITER_BODY)
    assert r.status_code == 410, r.text
    assert "Payroll Run" in r.json()["detail"]
    assert "switched off" in r.json()["detail"]
    assert db.get_collection("salary_records").count_documents({}) == 0
    assert db.get_collection("payroll").count_documents({}) == 0


def test_l3_16_old_salary_writer_refuses_whatever_is_posted(monkeypatch):
    """No payload gets past it: not an empty body, not an unknown employee."""
    db = _db()
    c = _client(db, monkeypatch)
    for body in (None, {}, {**OLD_WRITER_BODY, "employee_id": "NOBODY"}):
        r = c.post("/api/v1/payroll/salary/calculate", json=body)
        assert r.status_code == 410, (body, r.text)
    assert db.get_collection("salary_records").count_documents({}) == 0


def test_l3_16_old_salary_writer_stays_admin_only(monkeypatch):
    """Earlier rule kept: a non-admin is refused 403 before anything else."""
    db = _db()
    c = _client(db, monkeypatch, user=MANAGER)
    r = c.post("/api/v1/payroll/salary/calculate", json=OLD_WRITER_BODY)
    assert r.status_code == 403, r.text


def test_l3_16_stored_old_rows_are_left_exactly_as_they_are(monkeypatch):
    """Stored salary_records are neither rewritten nor deleted."""
    db = _db()
    db.seed("salary_records", [copy.deepcopy(OLD_ROW)])
    c = _client(db, monkeypatch)
    assert c.post("/api/v1/payroll/salary/calculate", json=OLD_WRITER_BODY).status_code == 410
    _sheet(c)
    _run(c)
    assert list(db.get_collection("salary_records").find({})) == [OLD_ROW]


def test_l3_16_the_old_calculator_is_gone():
    """One calculator, not two kept in step: the old one is deleted."""
    assert not hasattr(payroll_router, "_calculate_salary")
    assert not hasattr(payroll_router, "_calculate_tds")


def test_l3_16_salary_sheet_does_not_show_an_old_row_as_zeros(monkeypatch):
    """The audit's screen: with only the old writer's row for the month, the
    Salary Sheet listed the employee with Basic 0, HRA 0, Gross 0, every
    deduction 0 -- and Net Pay 13,360. Now the month is plainly empty."""
    db = _db()
    db.seed("salary_records", [copy.deepcopy(OLD_ROW)])
    sheet = _sheet(_client(db, monkeypatch))
    assert sheet["salaries"] == []
    assert sheet["total"] == 0


def test_l3_16_every_reader_shows_the_engine_result(monkeypatch):
    """Same setup, same month: the engine's 13,247 on the run, the run rows,
    the Salary Sheet and the payslip -- with its full breakdown -- even while
    an old 13,360 row for that month is still stored."""
    db = _db()
    db.seed("salary_records", [copy.deepcopy(OLD_ROW)])
    c = _client(db, monkeypatch)

    (run_row,) = _run(c)["rows"]
    # 15,000 - PF 1,440 - ESI 113 (auto, gross <= 21,000) - PT 200
    assert run_row["net_salary"] == 13247.0

    stored = db.get_collection("payroll").find_one({"employee_id": EMP})
    assert stored["net_salary"] == 13247.0
    assert stored["breakdown"]["deductions"]["esi_employee"] == 113.0

    (sheet_row,) = _sheet(c)["salaries"]
    assert sheet_row["payroll_id"] == stored["payroll_id"]
    assert (sheet_row["basic"], sheet_row["hra"]) == (12000.0, 3000.0)
    assert sheet_row["gross_salary"] == 15000.0
    assert (sheet_row["pf_employee"], sheet_row["esi"]) == (1440.0, 113.0)
    assert sheet_row["professional_tax"] == 200.0
    assert sheet_row["net_pay"] == 13247.0

    slip = c.get(f"/api/v1/payroll/payslip/{EMP}/{MONTH}/{YEAR}").json()["payslip"]
    assert slip["breakdown"]["net_pay"] == 13247.0
    assert slip["breakdown"]["esi"] == 113.0

    rows = c.get(
        "/api/v1/payroll/run/rows",
        params={"month": MONTH, "year": YEAR, "entity_id": ENTITY},
    ).json()["rows"]
    assert [r["net_salary"] for r in rows] == [13247.0]


class _RawMongoLikeDB(StrictDB):
    """pymongo's Database refuses a truth test, exactly like this."""

    def __bool__(self):
        raise NotImplementedError(
            "Database objects do not implement truth value testing or bool(). "
            "Please compare with None instead: database is not None"
        )


def test_l3_16_staff_own_payslip_shows_the_engine_result_on_a_real_database(monkeypatch):
    """Found by this batch's real-Mongo probe: GET /hr/me/payslip hands the
    payslip builder the RAW Mongo database; the builder tested it with
    ``not db``, which raises on pymongo, and the staff member was told "no
    payslip" for a saved payroll run. Every in-memory double allowed the truth
    test, so no earlier test could see it."""
    from api.routers import hr_self_service

    db = _RawMongoLikeDB()
    for name in ("users", "stores", "salary_config"):
        db.seed(name, list(_db().get_collection(name).find({})))
    monkeypatch.setattr(hr_self_service, "_get_db", lambda: db)
    app = FastAPI()
    app.include_router(payroll_router.router, prefix="/api/v1/payroll")
    app.include_router(hr_self_service.router, prefix="/api/v1/hr")
    staff = {"user_id": EMP, "username": "staff", "roles": ["STORE_MANAGER"],
             "store_ids": [STORE_MH], "active_store_id": STORE_MH}
    who = {"user": ADMIN}
    app.dependency_overrides[get_current_user] = lambda: who["user"]
    c = TestClient(app)

    monkeypatch.setattr(payroll_router, "_get_db", lambda: _Truthy(db))
    _run(c)
    # Staff see a payslip once the run is approved (batch b09's rule).
    db.get_collection("payroll").update_one(
        {"employee_id": EMP}, {"$set": {"status": "APPROVED"}}
    )
    who["user"] = staff
    mine = c.get("/api/v1/hr/me/payslip").json()["payslip"]
    assert mine is not None
    assert mine["breakdown"]["net_pay"] == 13247.0
    assert (mine["month"], mine["year"]) == (MONTH, YEAR)


class _Truthy:
    """The payroll router's own handle is a wrapper that allows a truth test."""

    def __init__(self, db):
        self._db = db

    def get_collection(self, name):
        return self._db.get_collection(name)


# ===========================================================================
# L3-17 -- gender on the salary setup feeds the PT slab
# ===========================================================================


def test_l3_17_put_gender_is_saved_not_thrown_away(monkeypatch):
    """The audit's exact call: PUT {"gender": "FEMALE"} answered 200
    "no_changes" and the next read had no gender."""
    db = _db()
    c = _client(db, monkeypatch)
    r = c.put(f"/api/v1/payroll/config/{EMP}", json={"gender": "FEMALE"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"
    assert _stored_config(db)["gender"] == "FEMALE"
    assert c.get(f"/api/v1/payroll/config/{EMP}").json()["config"]["gender"] == "FEMALE"
    # Nothing else on the setup moved.
    assert _stored_config(db)["basic"] == 12000.0
    assert _stored_config(db)["store_id"] == STORE_MH


def test_l3_17_gender_picks_the_pt_slab_in_the_payroll_run(monkeypatch):
    """The audit's numbers: 15,000 gross in Maharashtra. Not set -> 200 (the
    general slab, as before). Female -> 0 (women are nil up to 25,000 on the
    configured slab). Male -> 200. Set back to "not set" -> 200 again."""
    db = _db()
    c = _client(db, monkeypatch)
    assert _preview_pt(c) == 200.0  # today's behaviour, gender never set

    for gender, expected in (("FEMALE", 0.0), ("MALE", 200.0), ("", 200.0)):
        r = c.put(f"/api/v1/payroll/config/{EMP}", json={"gender": gender})
        assert r.status_code == 200, r.text
        assert _stored_config(db)["gender"] == gender
        assert _preview_pt(c) == expected, gender


def test_l3_17_saved_run_stores_the_female_slab_and_net(monkeypatch):
    db = _db(gender="FEMALE")
    c = _client(db, monkeypatch)
    _run(c)
    stored = db.get_collection("payroll").find_one({"employee_id": EMP})
    assert stored["breakdown"]["deductions"]["professional_tax"] == 0.0
    assert stored["net_salary"] == 13447.0  # 13,247 + the 200 PT not charged
    (sheet_row,) = _sheet(c)["salaries"]
    assert sheet_row["professional_tax"] == 0.0
    assert sheet_row["net_pay"] == 13447.0


def test_l3_17_gender_can_be_given_when_the_setup_is_created(monkeypatch):
    db = _db()
    c = _client(db, monkeypatch)
    r = c.post(
        "/api/v1/payroll/config",
        json={"employee_id": "EMP-B27-NEW", "store_id": STORE_MH, "basic": 12000, "gender": "FEMALE"},
    )
    assert r.status_code == 201, r.text
    new = db.get_collection("salary_config").find_one({"employee_id": "EMP-B27-NEW"})
    assert new["gender"] == "FEMALE"


@pytest.mark.parametrize("bad", ["female", "F", "OTHER", "ANY", 1])
def test_l3_17_an_unknown_gender_is_refused_not_guessed(monkeypatch, bad):
    """Only the two values the configured slabs know, or empty for not set."""
    db = _db(gender="FEMALE")
    c = _client(db, monkeypatch)
    r = c.put(f"/api/v1/payroll/config/{EMP}", json={"gender": bad})
    assert r.status_code == 422, r.text
    assert _stored_config(db)["gender"] == "FEMALE"


def test_l3_17_only_an_admin_can_set_gender(monkeypatch):
    """Salary setup stays ADMIN / SUPERADMIN only."""
    db = _db()
    c = _client(db, monkeypatch, user=MANAGER)
    r = c.put(f"/api/v1/payroll/config/{EMP}", json={"gender": "FEMALE"})
    assert r.status_code == 403, r.text
    assert "gender" not in _stored_config(db)
    assert c.get("/api/v1/payroll/config").status_code == 403


def test_l3_17_an_update_without_gender_leaves_it_alone(monkeypatch):
    db = _db(gender="FEMALE")
    c = _client(db, monkeypatch)
    assert c.put(f"/api/v1/payroll/config/{EMP}", json={"hra": 3000}).status_code == 200
    assert _stored_config(db)["gender"] == "FEMALE"


def test_l3_17_csv_import_without_a_gender_column_keeps_the_gender(monkeypatch):
    """The bulk import replaces the setup's fields. A file that has no gender
    column must not move the employee back to the general slab."""
    db = _db(gender="FEMALE")
    c = _client(db, monkeypatch)
    row = {"employee_id": EMP, "entity_id": ENTITY, "store_id": STORE_MH, "basic": 12000, "hra": 3000}
    r = c.post("/api/v1/payroll/config/bulk", json={"configs": [row]})
    assert r.status_code == 201, r.text
    assert r.json()["updated"] == 1
    assert _stored_config(db)["gender"] == "FEMALE"
    assert _preview_pt(c) == 0.0
    # A file that DOES carry the column is obeyed.
    r = c.post("/api/v1/payroll/config/bulk", json={"configs": [{**row, "gender": "MALE"}]})
    assert r.status_code == 201, r.text
    assert _stored_config(db)["gender"] == "MALE"


def test_l3_17_gender_changes_nothing_where_the_slab_has_one_rate(monkeypatch):
    """Jharkhand's slab is not split by gender: same PT either way."""
    db = _db(store=STORE_JH, basic=40000.0, hra=10000.0)  # 6,00,000 a year -> 150
    c = _client(db, monkeypatch)
    assert _preview_pt(c) == 150.0
    assert c.put(f"/api/v1/payroll/config/{EMP}", json={"gender": "FEMALE"}).status_code == 200
    assert _preview_pt(c) == 150.0


def test_l3_17_setting_gender_does_not_recalculate_a_saved_month(monkeypatch):
    """No silent recalculation: saving the gender touches no payroll row, and
    an APPROVED month is not rewritten by a later run either."""
    db = _db()
    c = _client(db, monkeypatch)
    _run(c)
    payroll = db.get_collection("payroll")
    before = copy.deepcopy(payroll.find_one({"employee_id": EMP}))
    assert before["breakdown"]["deductions"]["professional_tax"] == 200.0

    assert c.put(f"/api/v1/payroll/config/{EMP}", json={"gender": "FEMALE"}).status_code == 200
    assert payroll.find_one({"employee_id": EMP}) == before

    payroll.update_one({"employee_id": EMP}, {"$set": {"status": "APPROVED"}})
    approved = copy.deepcopy(payroll.find_one({"employee_id": EMP}))
    (row,) = _run(c)["rows"]
    assert row["skipped"] is True
    assert payroll.find_one({"employee_id": EMP}) == approved
