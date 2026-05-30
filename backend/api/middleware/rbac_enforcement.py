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


def _roles_from_bearer(request: Request):
    """Return the caller's roles from a valid Bearer token, or ``None`` if there
    is no usable token (missing / malformed / invalid / expired / revoked).

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

        payload = decode_token(token)
    except Exception:  # noqa: BLE001 - any decode failure => defer to route 401
        return None
    return payload.get("roles", []) or []


def _forbidden_response(request: Request, method: str, path: str, allowed) -> JSONResponse:
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

    # PUBLIC -> no auth at all.
    if allowed == rbac_policy.PUBLIC:
        return await call_next(request)

    # AUTHENTICATED or a role list: inspect the token's roles.
    roles = _roles_from_bearer(request)
    if roles is None:
        # No usable token: defer to the route's own get_current_user -> 401
        # (canonical shape). Never 403 here.
        return await call_next(request)

    if rbac_policy.check_access(method, path, roles):
        return await call_next(request)

    # Denied at the role-class level. For routes that DELIBERATELY reject with a
    # non-generic response (404 existence-hiding under /jarvis & /admin/techcherry,
    # or a body-specific clinical 403 on prescription create), DEFER to the route
    # so its canonical response - and any security intent like not leaking that
    # the path exists - is preserved exactly. The route's own gate (still in
    # place) returns the real rejection. ``allowed`` is unchanged; only the
    # delivery of the denial is left to the route.
    if policy.get("self_enforced"):
        return await call_next(request)

    return _forbidden_response(request, method, path, allowed)
