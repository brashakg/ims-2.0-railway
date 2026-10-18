"""Real guarded writes prove lock compatibility; no live database or inventory."""
from datetime import datetime, timedelta, timezone

import mongomock
import pytest
from fastapi import HTTPException

from api.routers.vendors import grn_accept_lock as locks

NOW = datetime(2026, 4, 1, 0, tzinfo=timezone.utc)
OFFSETS = [None, timezone.utc, timezone(timedelta(hours=5, minutes=30)), timezone(timedelta(hours=-4))]


class Repo:
    def __init__(self, stamp):
        self.collection = mongomock.MongoClient().db.grns
        self.collection.insert_one({"grn_id": "G1", "status": "PENDING", "accept_lock_at": stamp, "accept_lock_token": "OLD"})

    def find_by_id(self, key):
        return self.collection.find_one({"grn_id": key})


def face(instant, tz):
    return (instant.astimezone(tz) if tz else instant.replace(tzinfo=None)).isoformat()


@pytest.mark.parametrize("tz", OFFSETS)
@pytest.mark.parametrize("age,claimable", [(299, False), (300, False), (301, True)])
def test_lock_expiry_boundary_and_old_new_offsets_keep_atomic_claim(monkeypatch, tz, age, claimable):
    monkeypatch.setattr(locks, "utc_now", lambda: NOW)
    stamp = face(NOW - timedelta(seconds=age), tz)
    repo = Repo(stamp)
    assert locks._accept_lock_live(stamp) is not claimable
    token = locks._claim_grn_for_accept(repo, "G1", "ACCOUNTS")
    assert bool(token) is claimable
    if claimable:
        assert repo.find_by_id("G1")["accept_lock_at"] == NOW.isoformat()
        assert locks._claim_grn_for_accept(repo, "G1", "OTHER") is None


def test_live_heartbeat_invalidates_stale_takeover_snapshot(monkeypatch):
    monkeypatch.setattr(locks, "utc_now", lambda: NOW)
    repo = Repo(face(NOW - timedelta(seconds=301), OFFSETS[2]))
    write = repo.collection.find_one_and_update
    def interleave(query, update):
        repo.collection.update_one({"grn_id": "G1"}, {"$set": {"accept_lock_at": NOW.isoformat()}})
        return write(query, update)
    monkeypatch.setattr(repo.collection, "find_one_and_update", interleave)
    assert locks._claim_grn_for_accept(repo, "G1", "LOSER") is None
    assert repo.find_by_id("G1")["accept_lock_token"] == "OLD"


@pytest.mark.parametrize("tz", OFFSETS)
def test_heartbeat_handles_offset_then_rejects_competing_token(monkeypatch, tz):
    monkeypatch.setattr(locks, "utc_now", lambda: NOW)
    repo = Repo(face(NOW - timedelta(seconds=15), tz))
    state = {"units": 25, "at": NOW.replace(tzinfo=None) - timedelta(seconds=15), "confirmed_at": NOW.replace(tzinfo=None), "errors": 0}
    locks._grn_accept_heartbeat_tick(repo, "G1", "OLD", state)
    assert repo.find_by_id("G1")["accept_lock_at"] == NOW.isoformat()
    repo.collection.update_one({"grn_id": "G1"}, {"$set": {"accept_lock_token": "OTHER"}})
    state["units"] = 25
    with pytest.raises(HTTPException) as caught:
        locks._grn_accept_heartbeat_tick(repo, "G1", "OLD", state)
    assert caught.value.status_code == 409
    assert repo.find_by_id("G1")["accept_lock_token"] == "OTHER"


def test_invalid_timestamp_cannot_be_claimed_as_stale(monkeypatch):
    monkeypatch.setattr(locks, "utc_now", lambda: NOW)
    repo = Repo("not-a-time")
    assert locks._claim_grn_for_accept(repo, "G1", "A") is None
    assert locks._grn_accept_lock_minutes_left("not-a-time") is None
