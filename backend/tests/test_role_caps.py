"""
IMS 2.0 — Role-based discount cap tests
=========================================
Locks the contract that SUPERADMIN/ADMIN are unlimited and that role
baselines + user overrides interact correctly.

Regression: a SUPERADMIN whose user.discount_cap field defaults to 10
should still see 100, not 10. That's the long-standing prod bug this
helper fixes.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRoleBaselineCap:
    def test_superadmin_is_unlimited(self):
        from api.services.role_caps import role_baseline_cap
        assert role_baseline_cap(["SUPERADMIN"]) == 100.0

    def test_admin_is_unlimited(self):
        from api.services.role_caps import role_baseline_cap
        assert role_baseline_cap(["ADMIN"]) == 100.0

    def test_area_manager_25(self):
        from api.services.role_caps import role_baseline_cap
        assert role_baseline_cap(["AREA_MANAGER"]) == 25.0

    def test_store_manager_20(self):
        from api.services.role_caps import role_baseline_cap
        assert role_baseline_cap(["STORE_MANAGER"]) == 20.0

    def test_sales_cashier_10(self):
        from api.services.role_caps import role_baseline_cap
        assert role_baseline_cap(["SALES_CASHIER"]) == 10.0

    def test_multiple_roles_take_highest(self):
        from api.services.role_caps import role_baseline_cap
        # Highest wins
        assert role_baseline_cap(["SALES_STAFF", "STORE_MANAGER"]) == 20.0
        assert role_baseline_cap(["SALES_STAFF", "SUPERADMIN"]) == 100.0

    def test_empty_roles_zero(self):
        from api.services.role_caps import role_baseline_cap
        assert role_baseline_cap([]) == 0.0
        assert role_baseline_cap(None) == 0.0  # type: ignore[arg-type]


class TestEffectiveDiscountCap:
    """REGRESSION: SUPERADMIN reported a 10% cap. Root cause was the
    `discount_cap` field on the user document defaulting to 10.0 for
    every user, and the enforcement code reading that field directly
    via `current_user.get('discount_cap', 10.0)`. The new helper
    overrides any user-doc value when the role is SUPERADMIN/ADMIN."""

    def test_superadmin_always_unlimited_regardless_of_override(self):
        from api.services.role_caps import effective_discount_cap
        # The exact bug: user_override=10 (default from old user docs)
        # SUPERADMIN must STILL get 100
        assert effective_discount_cap(["SUPERADMIN"], 10.0) == 100.0
        assert effective_discount_cap(["SUPERADMIN"], 0.0) == 100.0
        assert effective_discount_cap(["SUPERADMIN"], None) == 100.0
        assert effective_discount_cap(["ADMIN"], 10.0) == 100.0

    def test_user_override_can_exceed_role_baseline(self):
        """A manager can be GIVEN extra discount privilege via override."""
        from api.services.role_caps import effective_discount_cap
        # Store manager baseline 20; admin grants them 35 → effective 35
        assert effective_discount_cap(["STORE_MANAGER"], 35.0) == 35.0

    def test_user_override_cannot_go_below_role_baseline(self):
        """Setting a manager's override to 5 doesn't demote them below
        their role's 20% baseline — they're still a manager."""
        from api.services.role_caps import effective_discount_cap
        assert effective_discount_cap(["STORE_MANAGER"], 5.0) == 20.0

    def test_no_override_uses_role_baseline(self):
        from api.services.role_caps import effective_discount_cap
        assert effective_discount_cap(["SALES_STAFF"]) == 10.0
        assert effective_discount_cap(["STORE_MANAGER"], None) == 20.0

    def test_override_clamps_to_100(self):
        from api.services.role_caps import effective_discount_cap
        # 250 → 100
        assert effective_discount_cap(["STORE_MANAGER"], 250.0) == 100.0
        # -10 → role baseline (since negative is clamped to 0 → max(0, baseline))
        assert effective_discount_cap(["STORE_MANAGER"], -10.0) == 20.0

    def test_unknown_role_zero(self):
        from api.services.role_caps import effective_discount_cap
        assert effective_discount_cap(["UNKNOWN_ROLE"]) == 0.0
