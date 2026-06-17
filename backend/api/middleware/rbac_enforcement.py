"""
IMS 2.0 - Request-time RBAC enforcement middleware (defense-in-depth)
=====================================================================

A Starlette/FastAPI HTTP middleware that turns the declarative policy registry
in ``api.services.rbac_policy`` into a SECOND, request-time enforcement layer
sitting on top of the existing per-route gates (``Depends(require_roles(...))``,
router-level dependencies, and inline handler checks).

DESIGN CONTRACT - perfectly behavior-preserving
------------------------------------------------
The policy table MIRRORS the current route gates exactly (it was derived from
them and is coverage-locked by ``tests/test_rbac_policy.py``). Adding this
middleware therefore must not change any endpoint's effective access. The
guarantees that keep it behavior-preserving:

  * Only ``/api/v1/*`` request paths are considered. ``openapi.json`` / docs and
    every non-/api/v1 utility route are skipped untouched.
  * UN-CATALOGUED route (policy_for -> None)  => ALLOW (FAIL-OPEN) + warn-log.
    The coverage-lock test guarantees completeness at build time, so a miss here
    means a brand-new/dynamic route the route's OWN gate still protects; never
    break it from the middleware.
  * PUBLIC  => ALLOW. (portal/OTP, webhooks/HMAC, auth login/refresh, ...)
  * AUTHENTICATED or a role list => decode the Bearer token REUSING auth.py's
    ``decode_token`` (same SECRET_KEY / ALGORITHM - never a divergent copy):
      - no token / invalid / expired => PASS THROUGH. We do NOT 401 here; the
        route's own ``Depends(get_current_user)`` returns the canonical 401 so
        the error shape (body + WWW-Authenticate header) is unchanged.
      - valid token => extract roles (same ``roles`` claim get_current_user
        reads) and call ``check_access``. Only a definite False (the caller is
        authenticated but lacks the role) yields a 403 here - which is exactly
        what the route's gate would also return, just one layer earlier.

  SUPERADMIN is allowed through by ``check_access`` itself (mirrors
  ``require_roles``). Store-scope / ownership / discount-cap conditions are NOT
  evaluated here - they are data-level checks the handler still performs.

  SELF-ENFORCED rows (``policy["self_enforced"]``): a few routes reject a wrong
  role with a non-generic response that is intentional and relied upon - 404
  existence-hiding (``/jarvis/**``, ``/admin/techcherry/**``) or a body-specific
  clinical 403 (prescription create). For these, on a role denial the middleware
  DEFERS to the route (lets it through) so the route's own gate delivers the
  canonical rejection; the middleware never substitutes its generic 403. This
  keeps the enforcer perfectly behavior-preserving for those routes too.

This is intentionally the same fail-soft posture as ``block_investor_writes`` in
``api/main.py``: decode just enough to inspect roles, and on any token problem
defer to the downstream auth dependency rather than inventing an error.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Paths under /api/v1 that are not real routes and must never be gated.
_SKIP_PATHS = frozenset(
    {
        "/api/v1/openapi.json",
        "/api/v1/docs",
        "/api/v1/redoc",
        "/api/v1/docs/oauth2-redirect",
    }
)


def _payload_from_bearer(request: Request):
    """Return the decoded JWT payload from a valid Bearer token, or ``None`` if
    there is no usable token (missing / malformed / invalid / expired).

    Reuses auth.py's ``decode_token`` so the secret + algorithm + expiry handling
    can never diverge from ``get_current_user``. ``None`` deliberately means
    "let the route's own auth dependency decide" (it will 401) - we do not raise.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        # Local import keeps middleware import-order-independent and mirrors the
        # lazy import used by block_investor_writes.
        from ..routers.auth import decode_token

        return decode_token(token)
    except Exception:  # noqa: BLE001 - any decode failure => defer to route 401
        return None


def _roles_from_payload(payload):
    """The ``roles`` claim from a decoded payload (same claim get_current_user
    reads). ``None`` payload -> None; a payload with no/empty roles -> []."""
    if payload is None:
        return None
    return payload.get("roles", []) or []


# Sentinel for "we looked up the user's overrides this request and there were
# none" so a per-request lookup is done at most once even when both the deny and
# the grant insertion points run (they can't both run -- deny on allow-branch,
# grant on deny-branch -- but the cache also covers the SUPERADMIN short-circuit).
_NO_OVERRIDES = ({}, {})


def _user_overrides(request: Request, payload):
    """Live per-request lookup of the user's stored ``permissions`` +
    ``module_access`` overrides, CACHED on ``request.state`` so a revoke takes
    effect on the NEXT request (overrides stay OUT of the JWT, ruling sec.2).

    Returns ``(permissions, module_access)`` -- both possibly empty/None. The
    lookup is by ``user_id`` (then ``username``) via the user repository,
    mirroring auth.py's profile path. Fail-soft: any lookup problem returns
    ``_NO_OVERRIDES`` so the per-user layer is simply inert (the role decision
    stands) -- it can NEVER turn a lookup failure into a denial of a legit user.

    DARK: a user with no ``permissions`` field and no ``module_access`` returns
    (None/{}, None/{}), and the resolver then returns the role decision
    unchanged -- identical to today.
    """
    cached = getattr(request.state, "_rbac_user_overrides", None)
    if cached is not None:
        return cached
    result = _NO_OVERRIDES
    try:
        uid = payload.get("user_id") if payload else None
        uname = payload.get("username") if payload else None
        from ..dependencies import get_user_repository

        repo = get_user_repository()
        if repo is not None and (uid or uname):
            rec = None
            if uid:
                rec = repo.find_by_id(uid)
            if rec is None and uname:
                try:
                    rec = repo.collection.find_one({"username": uname})
                except Exception:  # noqa: BLE001
                    rec = None
            if rec:
                result = (rec.get("permissions"), rec.get("module_access"))
    except Exception:  # noqa: BLE001 - fail-soft: no overrides applied
        result = _NO_OVERRIDES
    try:
        request.state._rbac_user_overrides = result
    except Exception:  # noqa: BLE001 - request.state always assignable, be safe
        pass
    return result


def _forbidden_response(
    request: Request, method: str, path: str, allowed
) -> JSONResponse:
    """Build the 403 JSONResponse, attaching CORS headers for an allowed origin
    so the browser surfaces the real status instead of masking it as a network
    error (same treatment as block_investor_writes / the HTTPException handler).
    """
    if isinstance(allowed, list):
        roles_desc = ", ".join(allowed)
    else:
        roles_desc = str(allowed)
    response = JSONResponse(
        status_code=403,
        content={"detail": f"Forbidden: {method} {path} requires one of {roles_desc}"},
    )
    origin = request.headers.get("origin")
    if origin:
        try:
            from ..main import _is_allowed_origin

            if _is_allowed_origin(origin):
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Credentials"] = "true"
        except Exception:  # noqa: BLE001 - CORS decoration is best-effort
            pass
    return response


async def rbac_enforcement_middleware(request: Request, call_next):
    """Request-time RBAC enforcer. See module docstring for the full contract."""
    path = request.url.path

    # Only act on the versioned API surface; everything else flows untouched.
    if not path.startswith("/api/v1/"):
        return await call_next(request)
    if path in _SKIP_PATHS:
        return await call_next(request)

    method = request.method
    if method in ("OPTIONS", "HEAD"):
        # Preflight / HEAD never carry a role decision; CORS + routing handle them.
        return await call_next(request)

    from ..services import rbac_policy

    policy = rbac_policy.policy_for(method, path)

    # Un-catalogued -> FAIL OPEN. The route's own gate still applies; the
    # coverage-lock test guarantees this is only ever a new/dynamic path.
    if policy is None:
        logger.warning("[RBAC] un-catalogued route, allowing: %s %s", method, path)
        return await call_next(request)

    allowed = policy["allowed"]

    # PUBLIC -> no auth at all. (No capability either -> per-user layer inert.)
    if allowed == rbac_policy.PUBLIC:
        return await call_next(request)

    # AUTHENTICATED or a role list: inspect the token's payload + roles.
    payload = _payload_from_bearer(request)
    roles = _roles_from_payload(payload)
    if not roles:
        # Either no usable token (missing / malformed / invalid / expired ->
        # None) OR a valid token whose ``roles`` claim is empty/absent (-> []).
        # In BOTH cases defer to the route: its ``get_current_user`` returns the
        # canonical 401 for a bad token, while a valid zero-role token still
        # reaches an AUTHENTICATED route (which would 200) and is 403'd by a
        # role-gated route's own ``require_roles``. We must never substitute a
        # hard 403 here for an empty role set -- that would be stricter than the
        # route on AUTHENTICATED endpoints, breaking the behavior-preserving
        # contract. The route decides.
        return await call_next(request)

    role_allowed = rbac_policy.check_access(method, path, roles)

    # ----- PER-USER CAPABILITY LAYER (council ruling sec.2) -----------------
    # Resolve the route's single capability + apply the frozen precedence chain.
    # SUPERADMIN is exempt from the per-user override layer entirely (they are
    # the actor who SETS overrides; an override must never lock the top admin
    # out of anything). DARK: with no stored overrides this is a pure no-op and
    # the decision equals ``role_allowed`` -- identical to today.
    from ..services.capabilities import capability_for
    from ..services.permission_resolver import apply_user_permissions

    capability = capability_for(method, path)
    if "SUPERADMIN" not in set(roles) and capability is not None:
        permissions, module_access = _user_overrides(request, payload)
        final_allowed = apply_user_permissions(
            role_allowed, capability, permissions, module_access
        )
    else:
        final_allowed = role_allowed

    # INSERTION POINT 1 (DENY subtract) -- runs on the role-ALLOWED branch: a
    # capability DENY (or a module-deny shim) turned a role-allowed route into a
    # denial. INSERTION POINT 2 (GRANT add) -- runs on the role-DENIED branch: a
    # capability GRANT turned a role-denied route into an allow. Both are folded
    # into ``apply_user_permissions``; the single ``final_allowed`` carries the
    # result of whichever fired.
    if final_allowed:
        return await call_next(request)

    # Denied (by role and not grant-rescued, OR by an explicit capability/module
    # deny that overrode a role allow). For routes that DELIBERATELY reject with
    # a non-generic response (404 existence-hiding under /jarvis &
    # /admin/techcherry, or a body-specific clinical 403 on prescription create),
    # DEFER to the route so its canonical response is preserved exactly.
    #
    # NOTE: deferral only matters when the ROLE denied (the route's own gate will
    # then produce the canonical rejection). When a per-user DENY overrode a
    # role-ALLOW, the route would otherwise 200, so the enforcer MUST deliver the
    # 403 itself -- deferring would silently honour the role and ignore the deny.
    if policy.get("self_enforced") and not role_allowed:
        return await call_next(request)

    return _forbidden_response(request, method, path, allowed)
