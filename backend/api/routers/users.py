"""
IMS 2.0 - Users Router
=======================
User management endpoints
"""

import re

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field, EmailStr, field_validator
from typing import List, Optional, Dict
from datetime import datetime
import uuid

from .auth import get_current_user
from ..dependencies import get_user_repository
from ..services.role_caps import role_baseline_cap
from ..services.user_roles import (
    BCRYPT_MAX_BYTES,
    can_assign_roles,
    highest_level,
    password_within_bcrypt_limit,
    sanitize_module_access,
    validate_roles,
)

router = APIRouter()

# Canonical stored form: a bare 10-digit Indian mobile starting 6-9 (matches
# customers.py and the frontend validatePhone helper).
_PHONE_RE = re.compile(r"^[6-9]\d{9}$")


def _normalize_phone(phone: Optional[str]) -> Optional[str]:
    """Validate + normalize a phone number to the canonical bare 10-digit form.
    None/empty -> None (phone is optional). Delegates to the shared
    services.phone util so every router normalizes identically (single source of
    truth). Raises ValueError on a malformed value so Pydantic surfaces a 422."""
    from ..services.phone import normalize_indian_mobile

    return normalize_indian_mobile(phone)


# ============================================================================
# SCHEMAS
# ============================================================================


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    # max_length is the bcrypt input window: bcrypt only consumes the first 72
    # bytes, so a longer password would be silently truncated (two distinct
    # long secrets sharing a 72-byte prefix would both authenticate). Reject up
    # front. Note: this is a char count, byte-length is re-checked in
    # hash_password (a multibyte char can exceed 72 bytes within 72 chars).
    password: str = Field(..., min_length=8, max_length=BCRYPT_MAX_BYTES)
    full_name: str = Field(..., min_length=2)
    phone: Optional[str] = None
    roles: List[str] = Field(default=["SALES_STAFF"])
    store_ids: List[str] = Field(default=[])
    primary_store_id: Optional[str] = None
    # Per-user discount OVERRIDE. None = use the role baseline (services/
    # role_caps.py). A hardcoded default of 10 used to ELEVATE zero-privilege
    # roles (an Accountant -> effective 10% via max(baseline, override)); leaving
    # this None keeps every new user at their role-correct cap unless an admin
    # deliberately grants more.
    discount_cap: Optional[float] = Field(default=None, ge=0, le=100)
    # When True the user must change their password on first login. Defaults to
    # True because an admin always creates the account with a TEMPORARY password
    # the staff member should replace. An admin can pass False to provision a
    # permanent password. (Previously a duplicate dict key hard-coded this to
    # True and silently ignored whatever was sent -- see create_user.)
    must_change_password: bool = Field(default=True)
    # Per-user module access -- a DENY-ONLY override LAYERED ON TOP of the role.
    # Shape: {moduleKey: bool}. A key set to False HIDES + route-blocks that
    # module for this user even when their role would allow it. The role is the
    # ceiling: this can only further RESTRICT, never grant a module the role
    # forbids (enforced client-side by AND-ing with the role filter; there is no
    # server path that reads this to GRANT). None/absent -> role defaults apply.
    module_access: Optional[Dict[str, bool]] = None

    @field_validator("phone")
    @classmethod
    def _v_phone(cls, v):
        return _normalize_phone(v)

    @field_validator("roles")
    @classmethod
    def _v_roles(cls, v):
        # At least one role, and every role must be in the canonical set. The
        # PRIVILEGE-ESCALATION ceiling (can't assign above the caller) is
        # enforced in the endpoint where the actor's roles are known.
        if not v:
            raise ValueError("At least one role is required")
        ok, invalid = validate_roles(v)
        if not ok:
            raise ValueError(f"Unknown role(s): {', '.join(invalid)}")
        return v


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    roles: Optional[List[str]] = None
    store_ids: Optional[List[str]] = None
    primary_store_id: Optional[str] = None
    discount_cap: Optional[float] = Field(default=None, ge=0, le=100)
    is_active: Optional[bool] = None
    # Deny-only per-user module override (see UserCreate.module_access). Only
    # persisted when explicitly provided on update so an unrelated edit (e.g. a
    # phone change) never wipes an existing grant -- handled via exclude_unset.
    module_access: Optional[Dict[str, bool]] = None

    @field_validator("phone")
    @classmethod
    def _v_phone(cls, v):
        return _normalize_phone(v)

    @field_validator("roles")
    @classmethod
    def _v_roles(cls, v):
        # roles is optional on update; only validate when present. Empty list is
        # rejected -- stripping every role would lock the user out.
        if v is None:
            return v
        if not v:
            raise ValueError("At least one role is required")
        ok, invalid = validate_roles(v)
        if not ok:
            raise ValueError(f"Unknown role(s): {', '.join(invalid)}")
        return v


class UserResponse(BaseModel):
    user_id: str
    username: str
    email: str
    full_name: str
    phone: Optional[str]
    roles: List[str]
    store_ids: List[str]
    primary_store_id: Optional[str]
    discount_cap: float
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime]


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def hash_password(password: str) -> str:
    """Hash password using bcrypt directly.

    bcrypt only consumes the first 72 BYTES of the input and silently ignores
    the rest. We reject anything longer (rather than truncate) so the stored
    hash unambiguously covers the whole secret -- a >72-byte password is a 400,
    not a silently-weakened credential. A multibyte UTF-8 char can push the
    byte count over 72 even within the schema's 72-CHAR cap, so re-check here.
    """
    if not password_within_bcrypt_limit(password):
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at most {BCRYPT_MAX_BYTES} bytes",
        )
    import bcrypt as _bc

    return _bc.hashpw(password.encode(), _bc.gensalt(rounds=12)).decode()


def sanitize_user(user: dict) -> dict:
    """Remove sensitive fields from user response"""
    if user is not None:
        user.pop("password_hash", None)
        user.pop("password", None)
    return user


# Roles that constitute org-wide administrative control. We refuse to let the
# LAST active holder of admin power be removed/deactivated/demoted, which would
# lock everyone out of user management.
_ADMIN_ROLES = ("ADMIN", "SUPERADMIN")


def _count_other_active_admins(repo, exclude_user_id: str) -> int:
    """Number of OTHER active users still holding an admin role. Used to block
    removing the last admin. Returns a large sentinel if the repo can't be
    queried so we fail OPEN on counting (never wrongly block) -- the guard is a
    safety net, not the primary access control.
    """
    try:
        admins = []
        for role in _ADMIN_ROLES:
            # find_by_role already filters is_active=True.
            admins.extend(repo.find_by_role(role) or [])
        seen = set()
        count = 0
        for u in admins:
            uid = u.get("user_id")
            if uid in seen or uid == exclude_user_id:
                continue
            seen.add(uid)
            count += 1
        return count
    except Exception:
        return 10**6


def _is_admin_user(user: dict) -> bool:
    return any(r in _ADMIN_ROLES for r in (user.get("roles") or []))


def _find_by_email_ci(repo, email: str):
    """Case-insensitive email-existence lookup. Tries a regex match against the
    Mongo collection (anchored, escaped); falls back to the repo's exact
    find_by_email when the collection isn't available (e.g. a fake test repo).
    """
    try:
        coll = getattr(repo, "collection", None)
        if coll is not None:
            return coll.find_one(
                {"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}}
            )
    except Exception:
        pass
    return repo.find_by_email(email)


# ============================================================================
# ROLE CHECKS
# ============================================================================


def require_admin(current_user: dict = Depends(get_current_user)):
    """Require ADMIN or SUPERADMIN role"""
    if not any(r in ["ADMIN", "SUPERADMIN"] for r in current_user.get("roles", [])):
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def require_manager(current_user: dict = Depends(get_current_user)):
    """Require STORE_MANAGER or higher"""
    allowed = ["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"]
    if not any(r in allowed for r in current_user.get("roles", [])):
        raise HTTPException(status_code=403, detail="Manager access required")
    return current_user


# ============================================================================
# ENDPOINTS
# ============================================================================


# NOTE: Specific routes MUST come before /{user_id} to avoid being matched as user_id
@router.get("/store/{store_id}", response_model=List[dict])
async def get_store_users(
    store_id: str,
    role: Optional[str] = Query(None),
    current_user: dict = Depends(require_manager),
):
    """Get users for a specific store"""
    repo = get_user_repository()

    if repo is not None:
        if role:
            users = repo.find_by_role(role, store_id)
        else:
            users = repo.find_by_store(store_id)
        return [sanitize_user(u) for u in users]

    return []


@router.get("/role/{role}", response_model=List[dict])
async def get_users_by_role(
    role: str,
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_manager),
):
    """Get users by role"""
    repo = get_user_repository()

    if repo is not None:
        users = repo.find_by_role(role, store_id)
        return [sanitize_user(u) for u in users]

    return []


@router.get("/search")
async def search_users(
    q: str = Query(..., min_length=2),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_manager),
):
    """Search users by name, username, or email"""
    repo = get_user_repository()

    if repo is not None:
        users = repo.search_users(q, store_id)
        return {"users": [sanitize_user(u) for u in users]}

    return {"users": []}


@router.get("/summary")
async def get_user_summary(
    store_id: Optional[str] = Query(None), current_user: dict = Depends(require_manager)
):
    """Get user count summary by role"""
    repo = get_user_repository()

    if repo is not None:
        summary = repo.get_user_summary(store_id)
        return {"summary": summary}

    return {"summary": {}}


@router.get("", response_model=List[dict])
@router.get("/", response_model=List[dict])
async def list_users(
    store_id: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    active_only: bool = Query(True),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(require_manager),
):
    """List users with filters"""
    repo = get_user_repository()

    if repo is not None:
        filter_dict = {}
        if store_id:
            filter_dict["store_ids"] = store_id
        if role:
            filter_dict["roles"] = role
        if active_only:
            filter_dict["is_active"] = True

        users = repo.find_many(filter_dict, skip=skip, limit=limit)
        return [sanitize_user(u) for u in users]

    return []


@router.post("", response_model=dict, status_code=201)
@router.post("/", response_model=dict, status_code=201)
async def create_user(user: UserCreate, current_user: dict = Depends(require_admin)):
    """Create new user (Admin only)"""
    repo = get_user_repository()

    # PRIVILEGE-ESCALATION GUARD: an actor may only create a user with roles at
    # or below their own highest level. This blocks an ADMIN minting a
    # SUPERADMIN (or self-escalation by creating a higher-privileged sibling).
    # Enforced before any DB work so it holds even in the repo-is-None path.
    ok, bad_role = can_assign_roles(current_user.get("roles", []), user.roles)
    if not ok:
        raise HTTPException(
            status_code=403,
            detail=f"You cannot assign a role above your own level: {bad_role}",
        )

    if repo is not None:
        # Check if username exists
        if repo.find_by_username(user.username):
            raise HTTPException(status_code=400, detail="Username already exists")

        # Check if email exists (case-insensitively -- emails are
        # case-insensitive in practice; otherwise Admin@x and admin@x are two
        # accounts). Falls back to the exact lookup when the repo can't match
        # case-insensitively.
        if _find_by_email_ci(repo, user.email):
            raise HTTPException(status_code=400, detail="Email already exists")

        user_data = {
            "username": user.username,
            "email": user.email,
            "password_hash": hash_password(user.password),
            "full_name": user.full_name,
            "phone": user.phone,
            "roles": user.roles,
            "store_ids": user.store_ids,
            "primary_store_id": user.primary_store_id
            or (user.store_ids[0] if user.store_ids else None),
            # Store a concrete, role-appropriate cap: an admin-supplied override
            # wins; otherwise fall back to the role baseline (0 for non-discount
            # roles) instead of a blanket 10.
            "discount_cap": (
                user.discount_cap
                if user.discount_cap is not None
                else role_baseline_cap(user.roles)
            ),
            "is_active": True,
            # Honour the (default-True) flag instead of the old duplicate-key bug
            # that hard-coded True and ignored the request. Cleared on first
            # /auth/change-password.
            "must_change_password": user.must_change_password,
            "created_by": current_user.get("user_id"),
            # DENY-ONLY module override, sanitized so it can never ESCALATE
            # above the role ceiling: True/grant entries and unknown keys are
            # dropped, only explicit deny (False) survives. Default {} so the
            # stored shape is always a dict, not null.
            "module_access": sanitize_module_access(user.module_access) or {},
        }

        created = repo.create(user_data)
        if created:
            return {
                "user_id": created["user_id"],
                "username": created["username"],
                "message": "User created successfully",
            }

        raise HTTPException(status_code=500, detail="Failed to create user")

    return {
        "user_id": str(uuid.uuid4()),
        "username": user.username,
        "message": "User created successfully",
    }


@router.get("/{user_id}", response_model=dict)
async def get_user(user_id: str, current_user: dict = Depends(require_manager)):
    """Get user by ID"""
    repo = get_user_repository()

    if repo is not None:
        user = repo.find_by_id(user_id)
        if user is not None:
            return sanitize_user(user)
        raise HTTPException(status_code=404, detail="User not found")

    return {"user_id": user_id}


@router.put("/{user_id}", response_model=dict)
async def update_user(
    user_id: str, user: UserUpdate, current_user: dict = Depends(require_admin)
):
    """Update user (Admin only)"""
    repo = get_user_repository()

    if repo is not None:
        existing = repo.find_by_id(user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="User not found")

        actor_roles = current_user.get("roles", [])

        # PRIVILEGE GUARD 1: a non-SUPERADMIN actor may not modify a user who
        # already outranks them (e.g. an ADMIN editing a SUPERADMIN). Otherwise
        # an admin could strip/alter a higher account. SUPERADMIN bypasses.
        if "SUPERADMIN" not in set(actor_roles):
            if highest_level(existing.get("roles", [])) > highest_level(actor_roles):
                raise HTTPException(
                    status_code=403,
                    detail="You cannot modify a user with a higher role than yours",
                )

        # exclude_unset => fields the client didn't send are NOT in the dict, so
        # an unrelated edit can't wipe module_access (or any other field). When
        # module_access IS sent it round-trips through (sanitized) and overwrites.
        update_data = user.model_dump(exclude_unset=True)

        # PRIVILEGE GUARD 2: if roles are being changed, the actor must be
        # allowed to assign the NEW set (can't escalate a user -- or themselves
        # -- above their own level).
        if "roles" in update_data:
            ok, bad_role = can_assign_roles(actor_roles, update_data["roles"])
            if not ok:
                raise HTTPException(
                    status_code=403,
                    detail=f"You cannot assign a role above your own level: {bad_role}",
                )
            # LAST-ADMIN GUARD: don't let a role change strip the final admin.
            if _is_admin_user(existing) and not any(
                r in _ADMIN_ROLES for r in update_data["roles"]
            ):
                if _count_other_active_admins(repo, user_id) == 0:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot remove admin role from the last active admin",
                    )

        # Sanitize the deny-only module override on the write path too -- the
        # schema accepts Dict[str, bool], so a grant/unknown key would otherwise
        # slip through here even though create_user already guards it.
        if "module_access" in update_data:
            update_data["module_access"] = (
                sanitize_module_access(update_data["module_access"]) or {}
            )

        update_data["updated_by"] = current_user.get("user_id")

        if repo.update(user_id, update_data):
            return {"user_id": user_id, "message": "User updated successfully"}

        raise HTTPException(status_code=500, detail="Failed to update user")

    return {"user_id": user_id, "message": "User updated successfully"}


@router.delete("/{user_id}")
async def delete_user(user_id: str, current_user: dict = Depends(require_admin)):
    """Deactivate user (soft delete)"""
    repo = get_user_repository()

    if repo is not None:
        existing = repo.find_by_id(user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="User not found")

        # Prevent deactivating yourself
        if user_id == current_user.get("user_id"):
            raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

        # Don't let a non-SUPERADMIN deactivate a higher-ranked account.
        if "SUPERADMIN" not in set(current_user.get("roles", [])):
            if highest_level(existing.get("roles", [])) > highest_level(
                current_user.get("roles", [])
            ):
                raise HTTPException(
                    status_code=403,
                    detail="You cannot deactivate a user with a higher role than yours",
                )

        # LAST-ADMIN GUARD: refuse to deactivate the final active admin, which
        # would lock the whole org out of user management.
        if _is_admin_user(existing) and _count_other_active_admins(repo, user_id) == 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot deactivate the last active admin",
            )

        if repo.update(
            user_id, {"is_active": False, "deactivated_by": current_user.get("user_id")}
        ):
            return {"message": "User deactivated"}

        raise HTTPException(status_code=500, detail="Failed to deactivate user")

    return {"message": "User deactivated"}


@router.post("/{user_id}/roles/{role}")
async def add_role(
    user_id: str, role: str, current_user: dict = Depends(require_admin)
):
    """Add role to user"""
    # Validate the role is real and that the actor is allowed to grant it.
    # Without this an ADMIN could POST .../roles/SUPERADMIN to self-escalate or
    # escalate anyone -- the original handler did NO check at all.
    ok, invalid = validate_roles([role])
    if not ok:
        raise HTTPException(status_code=400, detail=f"Unknown role: {role}")
    can, bad = can_assign_roles(current_user.get("roles", []), [role])
    if not can:
        raise HTTPException(
            status_code=403,
            detail=f"You cannot assign a role above your own level: {bad}",
        )

    repo = get_user_repository()

    if repo is not None:
        existing = repo.find_by_id(user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="User not found")

        if repo.add_role(user_id, role):
            return {"message": f"Role {role} added to user"}

        raise HTTPException(status_code=500, detail="Failed to add role")

    return {"message": f"Role {role} added to user"}


@router.delete("/{user_id}/roles/{role}")
async def remove_role(
    user_id: str, role: str, current_user: dict = Depends(require_admin)
):
    """Remove role from user"""
    # A non-SUPERADMIN may not strip a role above their own level (removing
    # someone's SUPERADMIN is itself a privileged operation).
    can, bad = can_assign_roles(current_user.get("roles", []), [role])
    if not can:
        raise HTTPException(
            status_code=403,
            detail=f"You cannot modify a role above your own level: {bad}",
        )

    repo = get_user_repository()

    if repo is not None:
        existing = repo.find_by_id(user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="User not found")

        current_roles = existing.get("roles", []) or []
        # LAST-ROLE GUARD: don't strip a user's only remaining role -- a
        # role-less account can't do anything and is effectively orphaned.
        if role in current_roles and len(current_roles) <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot remove the user's only role",
            )
        # LAST-ADMIN GUARD: removing the admin role must leave at least one
        # other active admin standing.
        if (
            role in _ADMIN_ROLES
            and _is_admin_user(existing)
            and not any(r in _ADMIN_ROLES for r in current_roles if r != role)
            and _count_other_active_admins(repo, user_id) == 0
        ):
            raise HTTPException(
                status_code=400,
                detail="Cannot remove admin role from the last active admin",
            )

        if repo.remove_role(user_id, role):
            return {"message": f"Role {role} removed from user"}

        raise HTTPException(status_code=500, detail="Failed to remove role")

    return {"message": f"Role {role} removed from user"}


class ResetPasswordBody(BaseModel):
    # Match the create-user floor (8) and the bcrypt byte ceiling (72); the old
    # min_length=6 let an admin reset to a weaker password than the account
    # could be created with. Byte-length re-checked in hash_password.
    new_password: str = Field(..., min_length=8, max_length=BCRYPT_MAX_BYTES)


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: str, body: ResetPasswordBody, current_user: dict = Depends(require_admin)
):
    """Admin resets a user's password. Frontend adminUserApi.resetPassword
    was 404'ing (no such route)."""
    repo = get_user_repository()
    if repo is None:
        return {"user_id": user_id, "message": "Password reset"}
    existing = repo.find_by_id(user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")
    repo.update(
        user_id,
        {
            "password_hash": hash_password(body.new_password),
            "password_reset_at": datetime.now().isoformat(),
            "password_reset_by": current_user.get("user_id"),
            "must_change_password": True,
        },
    )
    return {"user_id": user_id, "message": "Password reset successfully"}


class AssignStoreBody(BaseModel):
    store_id: str = Field(..., min_length=1)
    role: Optional[str] = None


@router.post("/{user_id}/assign-store")
async def assign_store(
    user_id: str, body: AssignStoreBody, current_user: dict = Depends(require_admin)
):
    """Grant a user access to a store (optionally with a role). Frontend
    adminUserApi.assignStore was 404'ing - it posts a body rather than
    using the path-param /{user_id}/stores/{store_id} variant."""
    # The optional role is a privilege-escalation vector (it calls add_role),
    # so validate + guard it exactly like the dedicated add-role endpoint
    # BEFORE any DB write. Previously it was added with no check at all.
    if body.role:
        ok, _ = validate_roles([body.role])
        if not ok:
            raise HTTPException(status_code=400, detail=f"Unknown role: {body.role}")
        can, bad = can_assign_roles(current_user.get("roles", []), [body.role])
        if not can:
            raise HTTPException(
                status_code=403,
                detail=f"You cannot assign a role above your own level: {bad}",
            )

    repo = get_user_repository()
    if repo is None:
        return {
            "user_id": user_id,
            "store_id": body.store_id,
            "message": "Store assigned",
        }
    existing = repo.find_by_id(user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")
    repo.add_store(user_id, body.store_id)
    if body.role:
        # Add the role if the repo supports it; ignore failures so the
        # store assignment still succeeds.
        try:
            repo.add_role(user_id, body.role)
        except Exception:
            pass
    return {"user_id": user_id, "store_id": body.store_id, "message": "Store assigned"}


@router.post("/{user_id}/stores/{store_id}")
async def add_store_access(
    user_id: str, store_id: str, current_user: dict = Depends(require_admin)
):
    """Add store access to user"""
    repo = get_user_repository()

    if repo is not None:
        existing = repo.find_by_id(user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="User not found")

        if repo.add_store(user_id, store_id):
            return {"message": f"Store {store_id} access granted"}

        raise HTTPException(status_code=500, detail="Failed to add store access")

    return {"message": f"Store {store_id} access granted"}


@router.delete("/{user_id}/stores/{store_id}")
async def remove_store_access(
    user_id: str, store_id: str, current_user: dict = Depends(require_admin)
):
    """Remove store access from user"""
    repo = get_user_repository()

    if repo is not None:
        existing = repo.find_by_id(user_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="User not found")

        if repo.remove_store(user_id, store_id):
            return {"message": f"Store {store_id} access revoked"}

        raise HTTPException(status_code=500, detail="Failed to remove store access")

    return {"message": f"Store {store_id} access revoked"}
