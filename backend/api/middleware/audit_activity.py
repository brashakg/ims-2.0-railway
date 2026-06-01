"""
IMS 2.0 - Request-time activity-audit middleware ("Audit Everything")
=====================================================================

SYSTEM_INTENT core philosophy: "Audit Everything." The owner reported that the
SUPERADMIN Activity Log was missing whole classes of action -- clinic / Rx
saves, customer creation, mobile-number edits -- because individual routers were
never wired to ``get_audit_repository().create(...)``.

This middleware closes that gap STRUCTURALLY: after EVERY successful,
authenticated, MUTATING request under ``/api/v1/*`` it writes ONE row into the
append-only, hash-chained ``audit_logs`` collection (the SAME trail the Activity
Log screen + JARVIS read via ``GET /api/v1/settings/audit-logs``). So every
mutation -- present and future, audited at the handler or not -- reaches the
activity log with no per-route work.

It sits alongside the RICH, domain-level audit the key routers also emit
(customers / prescriptions / clinical now record ``source="domain"`` rows with
before/after state). The two are complementary, not redundant: the domain rows
carry the field-level diff a reviewer wants; this baseline row guarantees that
*something* is always recorded for a write even when no handler bothered.

DESIGN CONTRACT
---------------
  * Only ``POST / PUT / PATCH / DELETE`` under ``/api/v1/*`` are considered;
    everything else (GET/HEAD/OPTIONS, non-/api/v1) flows through untouched.
  * Only a SUCCESSFUL response is logged (2xx/3xx). A 4xx/5xx write didn't
    change state, so it is not recorded as an action (it would be pure noise +
    could imply a mutation that never happened).
  * The caller is identified by REUSING ``auth.decode_token`` -- the exact same
    secret / algorithm / claim shape ``get_current_user`` reads, never a
    divergent copy. No usable token => the request was anonymous (or its own
    auth dependency already 401'd it) => nothing is logged.
  * SKIP list: auth login/refresh/logout/change-password (already audited by the
    auth router), the audit router itself, the seed endpoint, health, and the
    public webhooks surface (no IMS user) -- all pure noise here.
  * FAIL-SOFT, ALWAYS: the entire logging path is wrapped so a logging failure
    (DB down, chain head unreachable, anything) can NEVER block, delay-fail, or
    change the status of the real request. The business action wins; a missing
    audit row shows up honestly as a gap, exactly like the chain's own
    fail-soft insert.

This mirrors the fail-soft, decode-just-enough posture of
``rbac_enforcement_middleware`` and ``block_investor_writes`` in
``api/main.py``.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import Request

logger = logging.getLogger(__name__)

# Mutating HTTP methods we record. A read never produces an activity row.
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Method -> coarse action verb stamped on the baseline row. The domain rows use
# richer, specific actions (CUSTOMER_CREATED, MOBILE_NUMBER_CHANGED, ...); this
# is the catch-all so the Activity Log's CREATE/UPDATE/DELETE tone-mapping still
# colour-codes the row.
_METHOD_VERB = {
    "POST": "CREATE",
    "PUT": "UPDATE",
    "PATCH": "UPDATE",
    "DELETE": "DELETE",
}

# Exact ``/api/v1/...`` paths that must never be logged here. These are either
# already audited elsewhere (auth) or are pure infrastructure/noise.
_SKIP_EXACT = frozenset(
    {
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/refresh",
        "/api/v1/auth/change-password",
        "/api/v1/admin/seed-database",
        "/api/v1/health",
    }
)

# Path PREFIXES skipped wholesale. The webhooks family is hit by external
# systems (Shopify/Razorpay/...) with no IMS user; the audit router is
# read-only/integrity (its own verify is a GET anyway). Both would be noise.
_SKIP_PREFIXES = (
    "/api/v1/webhooks",
    "/api/v1/audit",
)


def _roles_and_identity(request: Request):
    """Return (user_id, user_name, store_id, roles) from a valid Bearer token,
    or ``None`` when there is no usable token.

    Reuses ``auth.decode_token`` so the secret + algorithm + claim names can
    never diverge from ``get_current_user``. ``None`` means "anonymous / bad
    token" -> we record nothing (the route's own auth dependency handles the
    401). The JWT carries ``user_id`` / ``username`` / ``roles`` /
    ``active_store_id`` (see auth.create_access_token call sites).
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        # Local import keeps the middleware import-order-independent and mirrors
        # the lazy import used by block_investor_writes / rbac_enforcement.
        from ..routers.auth import decode_token

        payload = decode_token(token)
    except Exception:  # noqa: BLE001 - any decode failure => treat as anonymous
        return None
    user_id = payload.get("user_id") or payload.get("username")
    if not user_id:
        return None
    user_name = payload.get("username") or user_id
    store_id = payload.get("active_store_id")
    roles = payload.get("roles", []) or []
    return user_id, user_name, store_id, roles


def _entity_and_id(path: str):
    """Derive (entity_type, entity_id) from a ``/api/v1/<segment>/...`` path.

    entity_type = UPPERCASE of the first path segment after ``/api/v1/`` with a
    trailing 's' stripped and dashes -> underscores (customers -> CUSTOMER,
    follow-ups -> FOLLOW_UP, prescriptions -> PRESCRIPTION). entity_id = the
    last path segment when it looks like an identifier rather than a sub-action
    verb (so ``/customers/{id}`` yields the id, but ``/customers/{id}/patients``
    yields ``patients`` -> treated as a sub-resource, not an id, and dropped).
    Best-effort + defensive: any oddity yields ("UNKNOWN", None) rather than
    raising.
    """
    try:
        rest = path[len("/api/v1/"):]
        segments = [s for s in rest.split("/") if s]
        if not segments:
            return "UNKNOWN", None
        head = segments[0].rstrip("/")
        entity = head.replace("-", "_").upper()
        # Singularise the common trailing 's' (CUSTOMERS -> CUSTOMER). Keep
        # words that legitimately end in 's' but aren't plurals rare enough to
        # not matter for a coarse label.
        if entity.endswith("S") and len(entity) > 1:
            entity = entity[:-1]

        entity_id = None
        if len(segments) >= 2:
            last = segments[-1]
            # Heuristic: an id segment contains a digit or a hyphen (uuid /
            # ORD-... / RX-...), or is long-ish. A short all-alpha tail like
            # "patients" / "redo" / "complete" is a sub-action, not an id.
            looks_like_id = (
                any(ch.isdigit() for ch in last)
                or "-" in last
                or len(last) >= 16
            )
            if looks_like_id:
                entity_id = last
        return entity or "UNKNOWN", entity_id
    except Exception:  # noqa: BLE001 - never let path parsing break a request
        return "UNKNOWN", None


def _client_ip(request: Request) -> str:
    """First hop of X-Forwarded-For, else the direct client host."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _should_consider(method: str, path: str) -> bool:
    """True iff this request is a candidate for a baseline activity row."""
    if method not in _MUTATING_METHODS:
        return False
    if not path.startswith("/api/v1/"):
        return False
    if path in _SKIP_EXACT:
        return False
    for pref in _SKIP_PREFIXES:
        if path == pref or path.startswith(pref + "/"):
            return False
    return True


async def audit_activity_middleware(request: Request, call_next):
    """Record ONE baseline audit row per successful authenticated mutation.

    See the module docstring for the full contract. The downstream handler runs
    first; only afterwards -- and only on success -- do we best-effort log.
    """
    response = await call_next(request)

    # Everything below is best-effort. A failure here must NEVER affect the
    # response that already succeeded.
    try:
        method = request.method
        path = request.url.path
        if not _should_consider(method, path):
            return response

        status_code = getattr(response, "status_code", 0) or 0
        # Only record state-changing successes. 4xx/5xx didn't mutate anything.
        if status_code < 200 or status_code >= 400:
            return response

        identity = _roles_and_identity(request)
        if identity is None:
            # Anonymous / bad token -> nothing to attribute. (Public routes like
            # the customer portal land here and are correctly skipped.)
            return response
        user_id, user_name, store_id, _roles = identity

        from ..dependencies import get_audit_repository

        audit_repo = get_audit_repository()
        if audit_repo is None:
            return response

        entity_type, entity_id = _entity_and_id(path)
        action = _METHOD_VERB.get(method, method)

        audit_repo.create(
            {
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "store_id": store_id,
                "user_id": user_id,
                "user_name": user_name,
                # timestamp is what the Activity Log sorts + range-filters on;
                # BaseRepository only stamps created_at/updated_at, so set it
                # explicitly or the row would sort to the epoch / show no time.
                "timestamp": datetime.utcnow(),
                "severity": "INFO",
                "source": "middleware",
                "method": method,
                "path": path,
                "status_code": status_code,
                "ip": _client_ip(request),
            }
        )
    except Exception as exc:  # noqa: BLE001 - audit must never break the request
        logger.warning("[AUDIT_ACTIVITY] baseline audit log failed (ignored): %s", exc)

    return response
