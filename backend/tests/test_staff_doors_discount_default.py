"""L1-F03 (audit 2026-11-04): the two staff-creation doors gave different
default discount authority. Settings > Users sent a 10% override for every new
person (an Accountant became effectively 10%) and displayed `value || 10`;
Staff Onboarding sent no override (role standard).

Server half of the fix: the full-record user projection now carries the cap
the till actually enforces, computed by the ONE shared rule
(role_caps.effective_discount_cap), so the screen shows what is enforced
instead of guessing from the stored override.
"""

import pytest

from api.routers import users as users_router
from api.services.role_caps import effective_discount_cap


@pytest.mark.parametrize(
    "roles, stored, expected",
    [
        (["ACCOUNTANT"], 0.0, 0.0),  # role standard stored by the server
        (["ACCOUNTANT"], None, 0.0),  # no override at all
        (["ACCOUNTANT"], 10.0, 10.0),  # legacy 10 from the old Users door: still enforced, now visible
        (["SALES_STAFF"], 0.0, 10.0),  # an override below the standard does not reduce it
        (["SALES_STAFF"], 15.0, 15.0),
        (["CASHIER"], None, 0.0),
        (["DESIGN_MANAGER"], None, 0.0),
        (["ADMIN"], 5.0, 100.0),
    ],
)
def test_user_projection_states_the_enforced_cap(roles, stored, expected):
    doc = {"user_id": "u1", "username": "u1", "roles": roles}
    if stored is not None:
        doc["discount_cap"] = stored
    out = users_router.sanitize_user(dict(doc), {"roles": ["ADMIN"]})
    assert out["effective_discount_cap"] == expected
    # Same number the discount gate uses -- one rule, not a second copy.
    assert out["effective_discount_cap"] == effective_discount_cap(roles, stored)
    # The stored override itself is passed through unchanged (never rewritten).
    assert out.get("discount_cap") == stored
