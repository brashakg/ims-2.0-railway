# ============================================================================
# An id is not authority: a customer belongs to a shop
# ============================================================================
# An audit of customers.py found THIRTEEN of nineteen per-customer doors with
# no ownership check at all. The caller passed a customer id, the handler
# fetched it, and nothing asked whether that customer was theirs. Four of the
# thirteen moved money.
#
# The asymmetry that gave it away, and the reason this cannot be dismissed as
# an unwritten convention: GET /{id}/store-credit/ledger DID scope, by calling
# _scoped_customer_or_404. Reading the ledger was protected while
# adding, issuing and redeeming credit against it were not. Somebody knew the
# rule and wrote it in one place out of five.
#
# THE RULE: _scoped_customer_or_404 / customer_in_scope is the ONE predicate
# that answers "may this user touch THIS customer". ADMIN and SUPERADMIN are
# unrestricted; a store-level role may only touch customers homed on a store
# they hold.
#
# These tests are structural on purpose. Standing up the full app with a
# cross-store token for every one of nineteen doors is a much heavier test than
# the one-line contract it would be checking, and the contract that actually
# decides the outcome is "does this handler call the predicate at all".

from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.routers.customers as customers  # noqa: E402

SRC = open(customers.__file__, encoding="utf-8").read()
LINES = SRC.split("\n")

# Doors that legitimately need no check, each with the reason. Anything NOT
# here must call the predicate.
EXEMPT = {
    # A stub: never touches the database, always returns []. A check here would
    # be dead code that reads like protection.
    ("GET", "/{customer_id}/prescriptions"),
    # Delegate to _post_credit_entry, which does the check once for both.
    ("POST", "/{customer_id}/store-credit/issue"),
    ("POST", "/{customer_id}/store-credit/redeem"),
}


def _per_customer_doors():
    """(method, path, handler_body) for every route carrying a customer id."""
    out = []
    for i, line in enumerate(LINES):
        m = re.match(
            r'@router\.(get|post|put|patch|delete)\("([^"]*\{customer_id\}[^"]*)"',
            line.strip(),
        )
        if not m:
            continue
        j, body = i + 1, []
        while j < len(LINES) and not LINES[j].startswith("@router."):
            body.append(LINES[j])
            j += 1
        out.append((m.group(1).upper(), m.group(2), "\n".join(body)))
    return out


def test_every_per_customer_door_checks_ownership():
    """The sweep. A new door that forgets the check fails here BY NAME."""
    doors = _per_customer_doors()
    assert len(doors) >= 15, f"only found {len(doors)} doors - did the file move?"
    missing = [
        f"{meth} {path}"
        for meth, path, body in doors
        if (meth, path) not in EXEMPT
        and "_scoped_customer_or_404" not in body
        and "customer_in_scope" not in body
    ]
    assert not missing, (
        "these per-customer doors do not check store ownership, so any signed-in "
        "user can reach that customer by guessing an id:\n  " + "\n  ".join(missing)
    )


def test_the_money_doors_specifically():
    """Called out separately because these move rupees and loyalty balances,
    and because three of them were the ones actually found unprotected."""
    bodies = {(m, p): b for m, p, b in _per_customer_doors()}
    for path in ("/{customer_id}/loyalty/add", "/{customer_id}/store-credit/add"):
        body = bodies[("POST", path)]
        assert "_scoped_customer_or_404" in body, f"{path} moves money unscoped"
        assert "write=True" in body, f"{path} must take the WRITE scope, not the read one"

    # issue + redeem delegate here; one check covers both.
    src = SRC[SRC.index("def _post_credit_entry"):]
    src = src[: src.index("\n@router.")]
    assert "_scoped_customer_or_404" in src
    assert "find_by_id(customer_id)" not in src, (
        "_post_credit_entry fetches the customer without the scope check again"
    )


def test_reading_the_ledger_and_writing_to_it_are_equally_scoped():
    """The exact asymmetry that revealed the bug. If these ever diverge again,
    this is the test that says so."""
    bodies = {(m, p): b for m, p, b in _per_customer_doors()}
    read = bodies[("GET", "/{customer_id}/store-credit/ledger")]
    write = bodies[("POST", "/{customer_id}/store-credit/add")]
    assert "_scoped_customer_or_404" in read
    assert "_scoped_customer_or_404" in write


# --- the predicate itself -------------------------------------------------

class _User(dict):
    pass


def _user(roles, stores):
    return {"user_id": "u", "roles": roles, "store_ids": stores,
            "active_store_id": stores[0] if stores else None}


@pytest.mark.parametrize("key", [
    "home_store_id", "preferred_store_id", "store_id", "primary_store_id",
])
def test_a_customer_is_resolvable_on_every_key_a_write_door_uses(key):
    """customer_service._build_skeleton stamps home_store_id, preferred_store_id,
    primary_store_id AND store_ids together - its docstring records that a
    skeleton homed only on primary_store_id was invisible to the native lists.
    can_access_store_scoped treats an UNRESOLVED store as out of scope, so a key
    missing from the resolver is not a loose check: it is a customer their own
    shop can no longer open."""
    assert customers._customer_store_id({key: "S1"}) == "S1"


def test_store_ids_list_resolves_too():
    assert customers._customer_store_id({"store_ids": ["S1", "S2"]}) == "S1"


def test_an_unhomed_customer_resolves_to_nothing():
    """The negative control. If this returned a truthy value, every test above
    would pass while the predicate waved everyone through."""
    assert customers._customer_store_id({}) is None
    assert customers._customer_store_id({"store_ids": []}) is None
    assert customers._customer_store_id(None) is None
    assert customers._customer_store_id("not-a-doc") is None


def test_the_owning_store_passes_and_a_stranger_does_not():
    doc = {"customer_id": "C1", "store_id": "S1"}
    assert customers.customer_in_scope(doc, _user(["STORE_MANAGER"], ["S1"])) is True
    assert customers.customer_in_scope(doc, _user(["STORE_MANAGER"], ["S2"])) is False


def test_admin_and_superadmin_are_unrestricted():
    """A fix that refused everyone would satisfy every negative test above."""
    doc = {"customer_id": "C1", "store_id": "S1"}
    for role in ("ADMIN", "SUPERADMIN"):
        assert customers.customer_in_scope(doc, _user([role], [])) is True
