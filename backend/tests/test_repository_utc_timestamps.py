"""RUN-02: repository timestamps are UTC instants, whatever the host's timezone.

Stored ``created_at`` / ``updated_at`` / ``sold_at`` keep their existing BSON shape
(naive datetime in the UTC frame -- what Railway's UTC host always wrote and what
every reader in api/utils/ist.py assumes). Before the fix the repositories wrote
``datetime.now()``, i.e. the HOST'S local wall clock: on an IST machine a sale at
04:32 UTC was stored as 10:02 and displayed as 15:32 IST.

The clock below simulates an IST host deterministically on any CI machine: a bare
``now()`` returns the IST wall clock, ``now(tz)`` returns the true instant.
"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from api.utils import event_time as et
from database.connection import MockCollection
from database.repositories import base_repository as base_mod
from database.repositories import product_repository as product_mod
from database.repositories.base_repository import BaseRepository
from database.repositories.product_repository import StockRepository

INSTANT = datetime(2026, 11, 4, 4, 32, 58, tzinfo=timezone.utc)
UTC_NAIVE = INSTANT.replace(tzinfo=None)
IST = timezone(timedelta(hours=5, minutes=30))


class IstHostClock(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:  # the host's local wall clock (Asia/Kolkata)
            return INSTANT.astimezone(IST).replace(tzinfo=None)
        return INSTANT.astimezone(tz)


@pytest.fixture
def ist_host(monkeypatch):
    for mod in (et, base_mod, product_mod):
        monkeypatch.setattr(mod, "datetime", IstHostClock)


class _Repo(BaseRepository):
    entity_name = "thing"
    id_field = "thing_id"


def test_create_update_delete_stamp_the_utc_instant_on_an_ist_host(ist_host):
    repo = _Repo(MockCollection("things"))
    doc = repo.create({"name": "a"})
    assert doc["created_at"] == UTC_NAIVE
    assert doc["updated_at"] == UTC_NAIVE
    # Same stored shape as legacy rows (naive), so mixed old/new comparisons and
    # every naive-UTC reader keep working.
    assert doc["created_at"].tzinfo is None

    stored = repo.collection._data[doc["thing_id"]]
    assert stored["created_at"] == UTC_NAIVE

    assert repo.update(doc["thing_id"], {"name": "b"})
    assert repo.collection._data[doc["thing_id"]]["updated_at"] == UTC_NAIVE
    assert repo.soft_delete(doc["thing_id"])
    assert repo.collection._data[doc["thing_id"]]["deleted_at"] == UTC_NAIVE
    assert repo.update_if(doc["thing_id"], {}, {"name": "c"})
    assert repo.collection._data[doc["thing_id"]]["updated_at"] == UTC_NAIVE


class _CaptureCollection:
    def __init__(self):
        self.updates = []

    def find_one_and_update(self, flt, update, **kw):
        self.updates.append(update)
        return {"stock_id": "S1"}


def test_unit_sold_at_is_the_utc_instant_on_an_ist_host(ist_host):
    coll = _CaptureCollection()
    repo = StockRepository(coll)
    assert repo.mark_sold("S1", "ORD-1")
    assert coll.updates[-1]["$set"]["sold_at"] == UTC_NAIVE


def test_utc_now_naive_is_host_independent(ist_host):
    assert et.utc_now_naive() == UTC_NAIVE
    assert et.utc_now_naive().tzinfo is None


# Comparisons against user-entered calendar fields whose frame is not an event
# instant; left on the host clock deliberately (see PR notes).
_ALLOWED = {
    ("task_repository.py", '"due_at": {"$lt": datetime.now()}'),
    ("vendor_repository.py", '"expected_date": {"$lt": datetime.now()}'),
}


def test_no_repository_writes_the_host_local_clock():
    root = Path(base_mod.__file__).parent
    offenders = []
    for path in sorted(root.glob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            code = line.split("#", 1)[0]
            if re.search(r"datetime\.(now|utcnow)\(\)", code):
                if (path.name, code.strip()) not in _ALLOWED:
                    offenders.append(f"{path.name}: {line.strip()}")
    assert offenders == []
