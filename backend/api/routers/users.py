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
from ..dependencies import get_user_repository, resolve_store_scope, get_audit_repository
from ..services.role_caps import role_baseline_cap, effective_discount_cap
from ..services.user_roles import (
    BCRYPT_MAX_BYTES,
    can_assign_roles,
    generate_temp_password,
    grantable_capabilities_for,
    highest_level,
    normalize_role,
    normalize_roles,
    password_within_bcrypt_limit,
    sanitize_module_access,
    sanitize_permissions,
    validate_roles,
)
from ..services import permission_audit as _perm_audit

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


# Govt-ID number normalizers. These are LIGHT, fail-SOFT validators: onboarding
# must not be hard-blocked by an ID-format quirk (the owner directive). An empty
# value clears the field; a present value is stripped/upper-cased and only
# format-checked for PAN (a fixed 10-char pattern) and Aadhaar (12 digits). We
# never log the raw value anywhere.
_PAN_NUM_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_AADHAAR_RE = re.compile(r"^\d{12}$")


def _norm_id_optional(v: Optional[str]) -> Optional[str]:
    """Strip + upper-case an optional ID field; '' -> None."""
    if v is None:
        return None
    s = str(v).strip().upper()
    return s or None


def _norm_pan(v: Optional[str]) -> Optional[str]:
    """PAN: AAAAA9999A. Fail-soft -- reject only an obviously malformed value so
    we don't store junk, but the field stays optional (None when blank)."""
    s = _norm_id_optional(v)
    if s is None:
        return None
    if not _PAN_NUM_RE.match(s):
        raise ValueError("PAN must be 10 characters in the form AAAAA9999A")
    return s


def _norm_aadhaar(v: Optional[str]) -> Optional[str]:
    """Aadhaar: 12 digits. Strips spaces/hyphens commonly typed in groups of 4."""
    if v is None:
        return None
    s = re.sub(r"[\s-]", "", str(v).strip())
    if not s:
        return None
    if not _AADHAAR_RE.match(s):
        raise ValueError("Aadhaar number must be 12 digits")
    return s


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
    store_ids: List[str] = Field(default_factory=list)
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
    # Per-user CAPABILITY override (council ruling sec.2). TWO-SIDED:
    # {"grant": {cap: true}, "deny": {cap: true}}. A grant adds a role-denied
    # capability (capped at the actor's level + the inviolable business floors);
    # a deny removes a role-granted one. Sanitized + escalation-guarded in the
    # endpoint where the actor's roles are known. None/absent -> DARK (today's
    # behaviour exactly).
    permissions: Optional[Dict[str, Dict[str, bool]]] = None
    # Govt-ID + statutory numbers captured at onboarding. All OPTIONAL and
    # fail-soft validated (PAN format / Aadhaar 12-digit) so an ID quirk never
    # blocks creating the account. NEVER logged in raw form.
    aadhaar_no: Optional[str] = None
    pan_no: Optional[str] = None
    uan_no: Optional[str] = None  # PF/UAN
    pf_no: Optional[str] = None
    esic_no: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def _v_phone(cls, v):
        return _normalize_phone(v)

    @field_validator("aadhaar_no")
    @classmethod
    def _v_aadhaar(cls, v):
        return _norm_aadhaar(v)

    @field_validator("pan_no")
    @classmethod
    def _v_pan(cls, v):
        return _norm_pan(v)

    @field_validator("uan_no", "pf_no", "esic_no")
    @classmethod
    def _v_id_optional(cls, v):
        return _norm_id_optional(v)

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
        # Merge deprecated role aliases into their survivor (SALES_CASHIER ->
        # SALES_STAFF, backlog #12) so no NEW user is ever persisted with the
        # retired role. Recognized-but-deprecated input is accepted, not rejected.
        return normalize_roles(v)


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
    # Two-sided capability override (see UserCreate.permissions). Only persisted
    # when explicitly sent (exclude_unset) so an unrelated edit never wipes it.
    permissions: Optional[Dict[str, Dict[str, bool]]] = None
    # Govt-ID + statutory numbers (see UserCreate). Only persisted when sent
    # (exclude_unset) so an unrelated edit never wipes them.
    aadhaar_no: Optional[str] = None
    pan_no: Optional[str] = None
    uan_no: Optional[str] = None
    pf_no: Optional[str] = None
    esic_no: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def _v_phone(cls, v):
        return _normalize_phone(v)

    @field_validator("aadhaar_no")
    @classmethod
    def _v_aadhaar(cls, v):
        return _norm_aadhaar(v)

    @field_validator("pan_no")
    @classmethod
    def _v_pan(cls, v):
        return _norm_pan(v)

    @field_validator("uan_no", "pf_no", "esic_no")
    @classmethod
    def _v_id_optional(cls, v):
        return _norm_id_optional(v)

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
        # Merge deprecated role aliases into their survivor (SALES_CASHIER ->
        # SALES_STAFF, backlog #12) on update too.
        return normalize_roles(v)


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
# PERMISSION ESCALATION GUARD + DISCOUNT-CAP CLAMP (council ruling sec.2)
# ============================================================================


def _guard_permission_grants(actor: dict, permissions: Optional[dict]) -> None:
    """Reject any GRANT the actor is not themselves allowed to give.

    An actor may only grant capabilities they themselves HOLD and that are not
    ungrantable (jarvis:* / SUPERADMIN-only) -- the per-user analogue of the
    role-assignment ceiling (reuses the same level model via
    grantable_capabilities_for). DENIES are always allowed (removing access is
    never an escalation). Raises HTTPException(403) naming the first offending
    capability. ``permissions`` is the ALREADY-SANITIZED map; None/empty -> no-op.
    """
    if not permissions:
        return
    grant = permissions.get("grant") or {}
    granted = {k for k, v in grant.items() if v is True}
    if not granted:
        return
    allowed = grantable_capabilities_for(actor.get("roles", []))
    for cap in sorted(granted):
        if cap not in allowed:
            raise HTTPException(
                status_code=403,
                detail=(
                    "You cannot grant a permission you do not hold or that is "
                    f"above your level: {cap}"
                ),
            )


def _clamp_discount_cap(
    actor: dict, requested_cap: Optional[float], target_roles: list
) -> Optional[float]:
    """Clamp a requested per-user discount_cap to what the ACTOR may grant.

    LIVE BUG (ruling sec.2): role_caps/create_user stored ``discount_cap`` with
    NO escalation guard, so any user-editing admin could set a 100% cap on a
    junior. We clamp the requested cap to the actor's OWN effective cap: an
    admin can never grant a higher discount authority than they themselves wield.
    SUPERADMIN/ADMIN have an effective 100 cap, so they are unconstrained (as
    intended -- they are the unlimited tier). Returns the clamped value (or None
    when none was requested, leaving the role baseline to apply downstream).
    """
    if requested_cap is None:
        return None
    actor_cap = effective_discount_cap(actor.get("roles", []), None)
    # effective_discount_cap floors at the actor's role baseline; for the clamp
    # we want the actor's MAX authority, which is exactly that value (100 for
    # SUPERADMIN/ADMIN). Reject above it rather than silently clamping so the
    # admin learns their action was rejected.
    req = max(0.0, min(100.0, float(requested_cap)))
    if req > actor_cap + 1e-9:
        raise HTTPException(
            status_code=403,
            detail=(
                f"You cannot grant a discount cap above your own ({actor_cap:.0f}%)."
            ),
        )
    return req


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
        store_id = resolve_store_scope(store_id, current_user)
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
        store_id = resolve_store_scope(store_id, current_user)
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
        store_id = resolve_store_scope(store_id, current_user)
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
        store_id = resolve_store_scope(store_id, current_user)
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

    # PER-USER CAPABILITY OVERRIDE (council ruling sec.2): sanitize to the
    # well-formed grant/deny shape (junk + ungrantable-grant dropped), then run
    # the escalation guard (an actor may only grant what they themselves hold).
    _actor_is_super = "SUPERADMIN" in set(current_user.get("roles", []))
    _clean_perms = sanitize_permissions(user.permissions, _actor_is_super)
    _guard_permission_grants(current_user, _clean_perms)
    # LIVE BUG retrofit: clamp the requested discount_cap to the actor's own cap
    # (was stored unguarded -- a junior could be given 100%).
    _clamped_cap = _clamp_discount_cap(current_user, user.discount_cap, user.roles)

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
            # wins (now CLAMPED to the actor's own cap -- see _clamp_discount_cap);
            # otherwise fall back to the role baseline (0 for non-discount roles).
            "discount_cap": (
                _clamped_cap
                if _clamped_cap is not None
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
            # Two-sided capability override (sanitized + guarded above). {} when
            # none -> DARK (the resolver returns the role decision unchanged).
            "permissions": _clean_perms or {},
        }

        # Govt-ID + statutory numbers (all optional, already format-normalized by
        # the schema validators). Only persist a field when a value was supplied
        # so the stored doc isn't littered with nulls. NEVER logged in raw form.
        for _id_field in ("aadhaar_no", "pan_no", "uan_no", "pf_no", "esic_no"):
            _val = getattr(user, _id_field, None)
            if _val:
                user_data[_id_field] = _val

        created = repo.create(user_data)
        if created:
            # AUDIT (no-log-no-commit): only when an actual override was set --
            # a plain account create with no permission deltas needs no
            # permission-audit row (the existing create flow is unchanged/DARK).
            if _clean_perms or _clamped_cap is not None:
                audit = _perm_audit.write_permission_audit(
                    get_audit_repository(),
                    action="PERMISSIONS_CREATE",
                    actor=current_user,
                    target_user_id=created["user_id"],
                    prior=_perm_audit.snapshot_of(None),
                    after=_perm_audit.snapshot_of(user_data),
                    store_id=user_data.get("primary_store_id"),
                )
                if audit is None:
                    # The control-surface audit could not be written. Roll the
                    # account back so we never leave an un-audited permission
                    # grant standing (no-log-no-commit).
                    try:
                        repo.update(
                            created["user_id"],
                            {"is_active": False, "permissions": {}},
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    raise HTTPException(
                        status_code=500,
                        detail="Could not record the permission change; user not created.",
                    )
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

        # PER-USER CAPABILITY OVERRIDE (council ruling sec.2): sanitize + run the
        # escalation guard on the edit path exactly as create. Only when the
        # client explicitly sent ``permissions`` (exclude_unset) so an unrelated
        # edit never wipes an existing override.
        _actor_is_super = "SUPERADMIN" in set(actor_roles)
        _perm_touched = "permissions" in update_data
        if _perm_touched:
            _clean = sanitize_permissions(update_data["permissions"], _actor_is_super)
            _guard_permission_grants(current_user, _clean)
            update_data["permissions"] = _clean or {}

        # LIVE BUG retrofit: clamp any requested discount_cap to the actor's own
        # cap (was stored unguarded on update too).
        _cap_touched = "discount_cap" in update_data
        if _cap_touched:
            update_data["discount_cap"] = _clamp_discount_cap(
                current_user,
                update_data.get("discount_cap"),
                update_data.get("roles", existing.get("roles", [])),
            )

        update_data["updated_by"] = current_user.get("user_id")

        # AUDIT (no-log-no-commit): a change touching ANY override-bearing field
        # writes the full prior+after snapshot BEFORE we commit. A plain edit
        # (phone/name only) skips this -- the existing flow stays DARK.
        _audit_needed = _perm_touched or _cap_touched or "module_access" in update_data
        if _audit_needed:
            _prior = _perm_audit.snapshot_of(existing)
            _after_doc = dict(existing)
            _after_doc.update(update_data)
            audit = _perm_audit.write_permission_audit(
                get_audit_repository(),
                action="PERMISSIONS_UPDATE",
                actor=current_user,
                target_user_id=user_id,
                prior=_prior,
                after=_perm_audit.snapshot_of(_after_doc),
                store_id=existing.get("primary_store_id"),
            )
            if audit is None:
                raise HTTPException(
                    status_code=500,
                    detail="Could not record the permission change; update aborted.",
                )

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
    # Merge deprecated role aliases into their survivor (SALES_CASHIER ->
    # SALES_STAFF, backlog #12) so adding the retired role grants the survivor.
    role = normalize_role(role)
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
    # The SECURE default flow supplies NO password: the server GENERATES a strong
    # temporary one (see generate_temp_password) and returns it ONCE. An admin
    # never needs to invent (or transmit) a password. The optional ``new_password``
    # is kept ONLY for backward compatibility with any caller that still posts an
    # explicit value; when present it must clear the create-user floor (8) and the
    # bcrypt byte ceiling (72). NOTE: even the supplied-password path never echoes
    # the plaintext back -- only a server-generated temp is returned (the admin
    # already knows a value they chose), so this can't become a password-disclosure
    # oracle. Byte-length re-checked in hash_password.
    new_password: Optional[str] = Field(
        default=None, min_length=8, max_length=BCRYPT_MAX_BYTES
    )


def _write_password_reset_audit(actor: dict, target: dict) -> None:
    """Append ONE fail-soft ``PASSWORD_RESET`` audit row.

    Records WHO reset WHOSE password and when -- but NEVER the temporary password
    value (a plaintext credential must never reach the immutable audit trail).
    Fail-SOFT (unlike the permission-audit no-log-no-commit path): a password
    reset is an operational recovery action, not a permission grant, so an audit
    backend hiccup must not block an admin from restoring a locked-out user's
    access. Any error is swallowed.
    """
    try:
        audit_repo = get_audit_repository()
        if audit_repo is None:
            return
        audit_repo.create(
            {
                "action": "PASSWORD_RESET",
                "entity_type": "user",
                "entity_id": target.get("user_id"),
                "user_id": actor.get("user_id"),
                "actor_username": actor.get("username"),
                "actor_roles": actor.get("roles", []),
                "target_user_id": target.get("user_id"),
                "target_username": target.get("username"),
                "store_id": target.get("primary_store_id"),
                "severity": "INFO",
                "timestamp": datetime.now(),
                # Deliberately NO password field of any kind.
            }
        )
    except Exception:  # noqa: BLE001 - audit must never block the reset
        pass


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: str,
    # Optional body: the secure default flow needs NO input at all (the server
    # generates the temp), so a bare POST with no JSON body is valid. A default of
    # None makes FastAPI treat the body as optional rather than 422'ing an empty
    # request. When a body IS sent, its fields are still validated by the model.
    body: Optional[ResetPasswordBody] = None,
    current_user: dict = Depends(require_admin),
):
    """Reset a user to a TEMPORARY password (SUPERADMIN / ADMIN only).

    Default (secure) flow: the server GENERATES a strong temporary password,
    bcrypt-hashes it, force-flags ``must_change_password`` so the user must
    replace it at next login, and returns the plaintext temp EXACTLY ONCE so the
    admin can hand it over. The plaintext is NEVER stored (only the bcrypt hash
    is persisted) and NEVER logged (the audit row records the actor/target only,
    not the value). This REPLACES the unsafe 'view current password' notion --
    real passwords are one-way bcrypt hashes and cannot be revealed.

    ROLE-ESCALATION GUARD: the caller may only reset a user whose highest role
    level is AT OR BELOW the caller's own. So an ADMIN may NOT reset a SUPERADMIN
    (403); a SUPERADMIN may reset anyone. Resetting a disabled user, or your own
    account, is allowed.
    """
    repo = get_user_repository()
    if repo is None:
        # No DB (e.g. a degraded/local stub): still generate + return a temp so
        # the contract holds, but nothing is persisted.
        temp = generate_temp_password()
        return {
            "user_id": user_id,
            "temporary_password": temp,
            "must_change_password": True,
            "message": "Password reset",
        }

    existing = repo.find_by_id(user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")

    # ROLE-ESCALATION GUARD: a non-SUPERADMIN actor may only reset a user at or
    # below their own level. This mirrors update_user / delete_user so an ADMIN
    # can't reset (and thus hijack) a SUPERADMIN's account. SUPERADMIN bypasses.
    if "SUPERADMIN" not in set(current_user.get("roles", [])):
        if highest_level(existing.get("roles", [])) > highest_level(
            current_user.get("roles", [])
        ):
            raise HTTPException(
                status_code=403,
                detail="You cannot reset the password of a user with a higher role than yours",
            )

    # Secure default: SERVER generates the temp. A supplied new_password (legacy)
    # is honoured but its plaintext is NEVER returned (the admin chose it). A None
    # body (bare POST) is the secure default path.
    supplied = body.new_password if body is not None else None
    server_generated = supplied is None
    temp = generate_temp_password() if server_generated else supplied

    repo.update(
        user_id,
        {
            "password_hash": hash_password(temp),
            "password_reset_at": datetime.now().isoformat(),
            "password_reset_by": current_user.get("user_id"),
            "must_change_password": True,
        },
    )

    # Fail-soft audit -- records the actor + target, NEVER the temp value.
    _write_password_reset_audit(current_user, existing)

    response = {
        "user_id": user_id,
        "username": existing.get("username"),
        "must_change_password": True,
        "message": "Password reset successfully",
    }
    # Return the plaintext ONCE only when WE generated it. A supplied password is
    # already known to the caller, so we never echo it back (no disclosure path).
    if server_generated:
        response["temporary_password"] = temp
    return response


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
        # Merge deprecated role aliases into their survivor (backlog #12).
        body.role = normalize_role(body.role)
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


# ============================================================================
# E4 - Approval PIN management (set / clear / status)
# ============================================================================
# A per-approver PIN authorizes maker-checker approvals (E4). A PIN is a short
# password: it is hashed/verified with bcrypt and is NEVER returned by any
# endpoint. Self-service rotation requires the current PIN; an ADMIN can force-
# set/clear without it. The engine (api/services/approvals.py) owns hashing, the
# brute-force throttle, and the audit row.


class ApprovalPinSet(BaseModel):
    pin: str = Field(..., min_length=4, max_length=6)
    # Required for SELF-rotation when a PIN already exists; ignored when an ADMIN
    # force-sets another user's PIN.
    current_pin: Optional[str] = None


def _is_self_or_admin(user_id: str, current_user: dict) -> bool:
    if current_user.get("user_id") == user_id:
        return True
    return any(r in _ADMIN_ROLES for r in (current_user.get("roles") or []))


@router.put("/{user_id}/approval-pin")
async def set_approval_pin(
    user_id: str,
    body: ApprovalPinSet,
    current_user: dict = Depends(get_current_user),
):
    """Set / rotate an approval PIN. Self OR ADMIN/SUPERADMIN. A non-admin self-
    rotation must supply the current PIN when one is already set."""
    if not _is_self_or_admin(user_id, current_user):
        raise HTTPException(status_code=403, detail="Cannot set another user's PIN")

    from ..services import approvals as _appr
    from database.connection import get_db

    db = get_db().db
    if db is None:
        raise HTTPException(status_code=503, detail="User store unavailable")

    is_admin = any(r in _ADMIN_ROLES for r in (current_user.get("roles") or []))
    is_self = current_user.get("user_id") == user_id

    # Self-rotation must verify the current PIN if one exists (admins bypass).
    if is_self and not is_admin:
        status = _appr.has_approver_pin(db, user_id)
        if status.get("has_pin"):
            if not body.current_pin:
                raise HTTPException(status_code=400, detail="current_pin required to rotate")
            check = _appr.verify_approver_pin(db, user_id, body.current_pin)
            if not check:
                raise HTTPException(status_code=403, detail="Current PIN is incorrect")

    res = _appr.set_approver_pin(db, user_id, body.pin, set_by=current_user.get("user_id"))
    if not res.get("ok"):
        err = res.get("error")
        if err == "invalid_pin_format":
            raise HTTPException(status_code=400, detail="PIN must be 4-6 digits")
        if err == "user_not_found":
            raise HTTPException(status_code=404, detail="User not found")
        if err == "no_db":
            raise HTTPException(status_code=503, detail="User store unavailable")
        raise HTTPException(status_code=500, detail="Failed to set PIN")
    return res


@router.delete("/{user_id}/approval-pin")
async def delete_approval_pin(
    user_id: str, current_user: dict = Depends(require_admin)
):
    """Clear a user's approval PIN (ADMIN/SUPERADMIN only)."""
    from ..services import approvals as _appr
    from database.connection import get_db

    db = get_db().db
    if db is None:
        raise HTTPException(status_code=503, detail="User store unavailable")
    res = _appr.clear_approver_pin(db, user_id, cleared_by=current_user.get("user_id"))
    if not res.get("ok") and res.get("error") == "user_not_found":
        raise HTTPException(status_code=404, detail="User not found")
    return {"ok": True}


@router.get("/{user_id}/approval-pin/status")
async def get_approval_pin_status(
    user_id: str, current_user: dict = Depends(get_current_user)
):
    """Whether the user has an approval PIN set (never the hash). Self OR ADMIN."""
    if not _is_self_or_admin(user_id, current_user):
        raise HTTPException(status_code=403, detail="Cannot view another user's PIN status")
    from ..services import approvals as _appr
    from database.connection import get_db

    db = get_db().db
    return _appr.has_approver_pin(db, user_id)


# ============================================================================
# PER-USER CAPABILITY PERMISSIONS (council ruling sec.2) - editor + audit/revert
# ============================================================================
# NOTE: /permissions/options is a LITERAL path; it is declared BEFORE the
# templated /{user_id}/permissions so the router never tries to match "options"
# as a user_id.


@router.get("/permissions/options")
async def get_permission_options(current_user: dict = Depends(require_admin)):
    """Delta-toggle metadata for the per-user override editor (plain-English
    sentences, never raw capability keys for the owner). The FE asks once and
    renders the preset toggles per the target user's role.

    Returns: the curated commonOverrides per role + the capabilities the ACTOR
    may grant (so the FE can gray out anything above the actor's level) + the
    schema version (recorded against each override for audit vintage)."""
    from ..services import capability_deltas as _cd
    from ..services.user_roles import grantable_capabilities_for

    return {
        "schema_version": _cd.DELTA_SCHEMA_VERSION,
        "discount_cap_field": _cd.DISCOUNT_CAP_FIELD,
        "role_deltas": _cd.ROLE_DELTAS,
        # The capabilities THIS actor may grant (cap their own level). The FE
        # uses this to show ungrantable toggles grayed-with-reason.
        "grantable": sorted(grantable_capabilities_for(current_user.get("roles", []))),
    }


@router.get("/{user_id}/permissions")
async def get_user_permissions(
    user_id: str, current_user: dict = Depends(require_admin)
):
    """The user's current capability override + the change history timeline
    (reuses the immutable audit_logs rows for the visible diff timeline)."""
    repo = get_user_repository()
    if repo is None:
        return {"permissions": {}, "module_access": {}, "discount_cap": None, "history": []}
    existing = repo.find_by_id(user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")
    history = _perm_audit.find_permission_history(get_audit_repository(), user_id)
    # Strip Mongo _id from history rows for a clean JSON response.
    clean_hist = []
    for h in history:
        h.pop("_id", None)
        clean_hist.append(h)
    return {
        "permissions": existing.get("permissions") or {},
        "module_access": existing.get("module_access") or {},
        "discount_cap": existing.get("discount_cap"),
        "history": clean_hist,
    }


class PermissionRevertBody(BaseModel):
    # The audit log_id of the prior snapshot to re-apply. The revert re-runs the
    # SAME escalation guard + sanitize so it can never launder an escalation.
    audit_log_id: str = Field(..., min_length=1)


@router.post("/{user_id}/permissions/revert")
async def revert_user_permissions(
    user_id: str,
    body: PermissionRevertBody,
    current_user: dict = Depends(require_admin),
):
    """Re-apply a PRIOR permission snapshot THROUGH the same escalation guard +
    sanitize + audit (council ruling sec.2). A lower-level admin can only revert
    to a state THEY are allowed to set -- the revert cannot launder an
    escalation a higher admin once made. Writes its own PERMISSIONS_REVERT row."""
    repo = get_user_repository()
    audit_repo = get_audit_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="User store unavailable")

    existing = repo.find_by_id(user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")

    actor_roles = current_user.get("roles", [])
    # Same higher-account guard as update_user: a non-SUPERADMIN cannot touch a
    # higher-ranked user.
    if "SUPERADMIN" not in set(actor_roles):
        if highest_level(existing.get("roles", [])) > highest_level(actor_roles):
            raise HTTPException(
                status_code=403,
                detail="You cannot modify a user with a higher role than yours",
            )

    # Locate the prior snapshot in the immutable audit trail.
    history = _perm_audit.find_permission_history(audit_repo, user_id, limit=200)
    target = next((h for h in history if h.get("log_id") == body.audit_log_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Audit snapshot not found")
    # Re-apply the AFTER state of the chosen historical row (that is the state
    # the user wants to return TO). Re-run it through the guard + sanitize so a
    # state set by a higher admin can't be re-applied by a lower one.
    restore = target.get("permissions_after") or {}
    _actor_is_super = "SUPERADMIN" in set(actor_roles)
    _clean = sanitize_permissions(restore.get("permissions"), _actor_is_super)
    _guard_permission_grants(current_user, _clean)
    _cap = _clamp_discount_cap(
        current_user, restore.get("discount_cap"), existing.get("roles", [])
    )

    prior = _perm_audit.snapshot_of(existing)
    update_data = {
        "permissions": _clean or {},
        "module_access": sanitize_module_access(restore.get("module_access")) or {},
        "discount_cap": _cap
        if _cap is not None
        else role_baseline_cap(existing.get("roles", [])),
        "updated_by": current_user.get("user_id"),
    }
    after_doc = dict(existing)
    after_doc.update(update_data)

    # No-log-no-commit: the REVERT writes its own audit row before committing.
    audit = _perm_audit.write_permission_audit(
        audit_repo,
        action="PERMISSIONS_REVERT",
        actor=current_user,
        target_user_id=user_id,
        prior=prior,
        after=_perm_audit.snapshot_of(after_doc),
        store_id=existing.get("primary_store_id"),
        note=f"reverted to snapshot {body.audit_log_id}",
    )
    if audit is None:
        raise HTTPException(
            status_code=500,
            detail="Could not record the revert; nothing changed.",
        )
    if repo.update(user_id, update_data):
        return {"user_id": user_id, "message": "Permissions reverted"}
    raise HTTPException(status_code=500, detail="Failed to revert permissions")
