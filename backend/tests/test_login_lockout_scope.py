# ============================================================================
# One colleague's typos must not lock the whole shop out of the till
# ============================================================================
# Every staff member in a shop shares ONE public internet address. The login
# limiter counted failures per ADDRESS -- five in fifteen minutes -- with no
# regard for who made them. So three people fumbling their passwords at 10am
# locked the ENTIRE shop out for fifteen minutes, including the people who had
# not typed anything yet. On a screen that bills customers, that is a
# self-inflicted outage, and it was easier to trigger by accident than on
# purpose.
#
# The fix keeps every real signal and removes only the collision:
#   * per (address, person)   5 / 15 min  -- "this person keeps failing here"
#   * per username            10 / 30 min -- one account attacked from anywhere
#   * per address             locked only after 8 DISTINCT usernames fail --
#                             credential stuffing, which a six-person shop
#                             cannot reach and a sprayer reaches immediately.

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.routers.auth import LoginRateLimiter  # noqa: E402

SHOP_IP = "203.0.113.40"


@pytest.fixture()
def limiter():
    return LoginRateLimiter()


def _fail(lim, username, ip=SHOP_IP, times=1):
    """Record `times` failed attempts, checking first as the route does."""
    for _ in range(times):
        lim.check(ip, username)
        lim.record(ip, username, success=False)


def test_a_colleagues_typos_do_not_lock_out_the_rest_of_the_shop(limiter):
    """THE BUG. Three people, five failures between them, one shop address."""
    _fail(limiter, "priya", times=2)
    _fail(limiter, "rahul", times=2)
    _fail(limiter, "imran", times=1)

    assert limiter.check(SHOP_IP, "meera") is None, (
        "a fourth member of staff was locked out of the till because three "
        "colleagues mistyped their passwords"
    )
    # ...and the people who fumbled can still get in on their next try.
    assert limiter.check(SHOP_IP, "priya") is None
    assert limiter.check(SHOP_IP, "rahul") is None


def test_one_person_failing_repeatedly_is_still_locked(limiter):
    """The control that must survive: five failures by ONE person."""
    _fail(limiter, "priya", times=5)
    msg = limiter.check(SHOP_IP, "priya")
    assert msg and "15 minutes" in msg, msg
    # Their colleague at the same counter is unaffected.
    assert limiter.check(SHOP_IP, "rahul") is None


def test_the_same_person_from_a_different_address_gets_their_own_budget(limiter):
    """Per (address, person): being locked at the shop must not follow the
    person to a different network, because the pair is the unit."""
    _fail(limiter, "priya", times=5)
    assert limiter.check(SHOP_IP, "priya") is not None
    assert limiter.check("198.51.100.7", "priya") is None


def test_one_account_attacked_from_many_addresses_is_still_locked(limiter):
    """The per-username control, unchanged: 10 failures in 30 minutes.

    Without this, distributing an attack across addresses would defeat the
    per-pair counter entirely.
    """
    for i in range(10):
        _fail(limiter, "priya", ip=f"198.51.100.{i}")
    msg = limiter.check("198.51.100.99", "priya")
    assert msg and "locked" in msg.lower(), msg


def test_credential_stuffing_from_one_address_is_locked(limiter):
    """The address backstop still bites - on DISTINCT usernames, which is what
    actually distinguishes an attacker from a busy shop."""
    for i in range(8):
        _fail(limiter, f"victim{i}", times=1)
    msg = limiter.check(SHOP_IP, "victim9")
    assert msg and "this location" in msg, msg


def test_a_busy_shop_never_reaches_the_stuffing_threshold(limiter):
    """The negative control that gives the test above its meaning.

    Six real people each failing twice is twelve failures from one address -
    far past the old limit of five - and must still not lock anyone out.
    """
    for name in ("priya", "rahul", "imran", "meera", "arjun", "sana"):
        _fail(limiter, name, times=2)
    assert limiter.check(SHOP_IP, "priya") is None
    assert limiter.check(SHOP_IP, "newjoiner") is None


def test_a_successful_login_reopens_the_address(limiter):
    """A genuine sign-in clears the locks, so a shop recovers by itself."""
    for i in range(8):
        _fail(limiter, f"victim{i}", times=1)
    assert limiter.check(SHOP_IP, "victim9") is not None

    limiter.record(SHOP_IP, "priya", success=True)
    assert limiter.check(SHOP_IP, "victim9") is None


def test_rows_written_before_the_change_do_not_crash_the_counter(limiter):
    """The address key gained a third element (the username). A live process
    upgraded mid-window holds two-element rows written by the old code; reading
    them must degrade, not raise."""
    limiter._attempts[f"ip:{SHOP_IP}"].append((9e9, False))  # legacy shape
    assert limiter.check(SHOP_IP, "priya") is None
