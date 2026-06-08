"""
IMS 2.0 - E2 Policy Engine (settings matrix)
============================================
Resolves a named policy key to its most-specific value across the scope chain
  store override  >  entity override  >  global override  >  env  >  registry default
and writes scoped overrides safely (typed validation, per-value secret encryption,
hash-chained audit, explicit cache invalidation).

BINDING CORRECTIONS (CORRECTIONS.md P1 -- outrank the E2 packet):
  * Secret values are encrypted PER VALUE via settings._encrypt_value (NOT
    _encrypt_config, which only matches leaf names in _SENSITIVE_FIELDS and would
    leave dotted policy keys in PLAINTEXT).
  * Cache invalidation is an explicit cache.delete(exact_key) for the written scope
    (delete_pattern / invalidate_store are NO-OPS in the in-memory fallback). We cache
    the per-SCOPE document (not the resolved value). With Redis the delete clears the
    SHARED key -> every worker re-reads fresh. WITHOUT Redis the delete clears only the
    writing worker's in-process store, so sibling workers may serve the prior value
    until the (short) TTL expires -- eventually consistent, bounded by _TTL. A
    DB-unavailable read is NEVER cached (caching an empty {} would poison the scope,
    silently dropping all overrides for the TTL).
  * pricing.category_caps.* overrides may only LOWER the pricing_caps code constant,
    never raise it (luxury brand caps are NOT E2 keys at all).
  * A store with a MISSING entity_id (dirty prod data / unassigned) resolves to
    global -- it NEVER raises.

Standalone Mongo: set_policy writes ONE policy_settings document + ONE audit row
(two sequential single-document writes; no cross-collection transaction needed).
No emoji (Windows cp1252).
"""
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from api.services.cache import cache
from api.services import policy_registry as reg

_UNSET = object()
# Short TTL on purpose: with Redis absent (the fail-soft per-worker in-memory path),
# cache.delete on a write only clears the writing worker, so a short TTL bounds the
# cross-worker staleness window. With Redis present the shared-key delete is already
# coherent and the TTL only caps a transient-miss reread.
_TTL = getattr(cache, "TTL_SHORT", 60)
_DOC_CACHE_PREFIX = "policy_doc:"        # per-scope override doc
_SE_CACHE_PREFIX = "policy_se:"          # store_id -> entity_id memo


# ---------------------------------------------------------------------------
# DB + crypto helpers (fail-soft)
# ---------------------------------------------------------------------------


def _coll(name: str = "policy_settings"):
    try:
        from database.connection import get_db

        db = get_db()
        if db is None or not getattr(db, "is_connected", True):
            return None
        return db.get_collection(name)
    except Exception:  # noqa: BLE001
        return None


def _enc():
    """Lazy import of the per-value crypto primitives (avoids a router<->service
    import cycle). Returns (encrypt, decrypt) or identity functions if unavailable."""
    try:
        from api.routers.settings import _encrypt_value, _decrypt_value

        return _encrypt_value, _decrypt_value
    except Exception:  # noqa: BLE001
        return (lambda v: v), (lambda v: v)


# ---------------------------------------------------------------------------
# Scope addressing
# ---------------------------------------------------------------------------


def _scope_addr(scope: Optional[dict]) -> str:
    """Most-specific scope address for a scope dict. {} / None -> global."""
    scope = scope or {}
    sid = scope.get("store_id")
    eid = scope.get("entity_id")
    if sid:
        return f"store:{sid}"
    if eid:
        return f"entity:{eid}"
    return "global"


def _resolve_entity_id(store_id: str) -> Optional[str]:
    """store_id -> entity_id (memoized). A missing/None entity_id (unassigned or
    dirty data) returns None so resolution falls through to GLOBAL -- NEVER raises."""
    ck = f"{_SE_CACHE_PREFIX}{store_id}"
    cached = cache.get(ck)
    if cached is not None:
        return cached or None  # "" sentinel -> None
    eid = None
    try:
        coll = _coll("stores")
        if coll is not None:
            doc = coll.find_one({"store_id": store_id}) or {}
            eid = doc.get("entity_id")
    except Exception:  # noqa: BLE001
        eid = None
    cache.set(ck, eid or "", ttl=_TTL)
    return eid or None


def _chain(scope: Optional[dict]) -> List[str]:
    """Scope addresses to try, most-specific first."""
    scope = scope or {}
    sid = scope.get("store_id")
    eid = scope.get("entity_id")
    chain: List[str] = []
    if sid:
        chain.append(f"store:{sid}")
        ent = _resolve_entity_id(sid)
        if ent:
            chain.append(f"entity:{ent}")
        chain.append("global")
    elif eid:
        chain.append(f"entity:{eid}")
        chain.append("global")
    else:
        chain.append("global")
    return chain


def _scope_doc_values(addr: str) -> Dict[str, Any]:
    """The `values` map for one scope address (cached per scope so in-memory
    cache.delete fully invalidates it). Missing doc / DB down -> {}."""
    ck = f"{_DOC_CACHE_PREFIX}{addr}"
    cached = cache.get(ck)
    if cached is not None:
        return cached
    coll = _coll()
    if coll is None:
        return {}  # DB unavailable -- do NOT cache (an empty {} would poison the scope for _TTL)
    try:
        doc = coll.find_one({"_id": addr}) or {}
    except Exception:  # noqa: BLE001
        return {}  # transient read failure -- do NOT cache the empty result; retry next request
    values = doc.get("values") or {}
    cache.set(ck, values, ttl=_TTL)
    return values


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def _coerce(spec: reg.PolicySpec, raw: Any) -> Any:
    """Coerce an env-string / stored value to the spec type. Tolerant: returns the
    raw value if coercion fails (the validator on WRITE is the strict gate)."""
    t = spec.type
    try:
        if t in ("money_paisa", "int", "days"):
            return int(raw)
        if t in ("float", "percent"):
            return float(raw)
        if t == "bool":
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("1", "true", "yes", "on")
        if t == "json":
            if isinstance(raw, (list, dict)):
                return raw
            return json.loads(raw)
    except Exception:  # noqa: BLE001
        return raw
    return raw


_MISSING = object()


def _nested_get(values: Dict[str, Any], key: str) -> Any:
    """Read a dotted policy key from the `values` subdoc. A `$set` of
    `values.<dotted.key>` makes Mongo NEST (values -> dotted -> key), so the key is
    NOT a flat field -- we must walk the path. Returns _MISSING when absent."""
    cur: Any = values
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def get_policy(key: str, scope: Optional[dict] = None, *, default: Any = _UNSET) -> Any:
    """Resolve `key` to its effective value: store > entity > global > env > registry
    default > `default` arg. Raises KeyError for an unknown key only when no default
    arg is supplied. Decrypts secret values."""
    spec = reg.REGISTRY.get(key)
    if spec is None:
        if default is not _UNSET:
            return default
        raise KeyError(f"unknown policy key: {key}")

    _, decrypt = _enc()
    for addr in _chain(scope):
        values = _scope_doc_values(addr)
        v = _nested_get(values, key)
        if v is not _MISSING:
            if spec.secret and isinstance(v, str):
                v = decrypt(v)
                if isinstance(v, str) and v.startswith(("fernet:", "enc:")):
                    return spec.default  # decrypt failed (e.g. key rotation) -> safe code default
            return _coerce(spec, v)

    # env fallback beats the registry code default (ops override); DB beats env.
    if spec.env:
        env_v = os.getenv(spec.env)
        if env_v is not None:
            return _coerce(spec, env_v)

    # Every registry key carries a code default (survives a fresh DB).
    return spec.default


def get_effective(key: str, scope: Optional[dict] = None) -> dict:
    """Resolve + report the SOURCE level (store/entity/global/env/default). Secret
    values are masked in the returned `value`."""
    spec = reg.REGISTRY.get(key)
    if spec is None:
        raise KeyError(f"unknown policy key: {key}")
    _, decrypt = _enc()
    source = "default"
    value: Any = None
    found = False
    for addr in _chain(scope):
        values = _scope_doc_values(addr)
        v = _nested_get(values, key)
        if v is not _MISSING:
            if spec.secret and isinstance(v, str):
                v = decrypt(v)
            value = _coerce(spec, v)
            source = addr.split(":", 1)[0]  # store|entity|global
            found = True
            break
    if not found:
        if spec.env and os.getenv(spec.env) is not None:
            value, source = _coerce(spec, os.getenv(spec.env)), "env"
        else:
            value, source = spec.default, "default"
    out_value = "****" if (spec.secret and value not in (None, "")) else value
    return {"key": key, "value": out_value, "source": source, "scope": _scope_addr(scope),
            "type": spec.type, "secret": spec.secret}


def get_policies(keys: Optional[List[str]], scope: Optional[dict] = None) -> Dict[str, dict]:
    """Batch get_effective for `keys` (or the whole registry when None)."""
    target = keys if keys else list(reg.REGISTRY.keys())
    out: Dict[str, dict] = {}
    for k in target:
        if k in reg.REGISTRY:
            out[k] = get_effective(k, scope)
    return out


def registry() -> List[dict]:
    return reg.registry_public()


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


class PolicyError(ValueError):
    """Raised for a rejected set_policy (caller maps to HTTP 400/403)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _validate_value(spec: reg.PolicySpec, value: Any) -> Any:
    t = spec.type
    if t == "bool":
        if not isinstance(value, bool):
            raise PolicyError(f"{spec.key} must be a boolean")
        return value
    if t == "enum":
        if value not in (spec.enum or ()):
            raise PolicyError(f"{spec.key} must be one of {list(spec.enum or ())}")
        return value
    if t == "json":
        if not isinstance(value, (list, dict)):
            raise PolicyError(f"{spec.key} must be a JSON array or object")
        return value
    if t == "text":
        return str(value)
    # numeric types
    try:
        num = int(value) if t in ("money_paisa", "int", "days") else float(value)
    except (TypeError, ValueError):
        raise PolicyError(f"{spec.key} must be a number")
    if isinstance(value, bool):  # bool is an int subclass -- reject for numeric keys
        raise PolicyError(f"{spec.key} must be a number, not a boolean")
    if spec.minimum is not None and num < spec.minimum:
        raise PolicyError(f"{spec.key} must be >= {spec.minimum}")
    if spec.maximum is not None and num > spec.maximum:
        raise PolicyError(f"{spec.key} must be <= {spec.maximum}")
    return num


def _luxury_guard(spec: reg.PolicySpec, value: Any) -> None:
    """pricing.category_caps.* may only LOWER the pricing_caps code constant."""
    if not spec.lower_only_vs_category:
        return
    try:
        from api.services.pricing_caps import CATEGORY_DISCOUNT_CAPS

        floor = CATEGORY_DISCOUNT_CAPS.get(spec.lower_only_vs_category)
    except Exception:  # noqa: BLE001
        floor = None
    if floor is not None and float(value) > float(floor):
        raise PolicyError(
            f"{spec.key} may only be LOWERED: {value} exceeds the code cap {floor} "
            f"for {spec.lower_only_vs_category} (luxury/category caps cannot be raised via settings)"
        )


def _roles_of(actor: Optional[dict]) -> set:
    actor = actor or {}
    roles = actor.get("roles") or ([actor.get("activeRole")] if actor.get("activeRole") else [])
    return {r for r in roles if r}


def set_policy(key: str, value: Any, scope: dict, *, actor: Optional[dict] = None) -> dict:
    spec = reg.REGISTRY.get(key)
    if spec is None:
        raise PolicyError(f"unknown policy key: {key}")
    addr = _scope_addr(scope)
    level = addr.split(":", 1)[0]

    # 1. scope level allowed for this key
    if level not in spec.scopes:
        raise PolicyError(f"{key} cannot be set at {level} scope (allowed: {list(spec.scopes)})")

    # 2. role gate (fine-grained per-key)
    roles = _roles_of(actor)
    is_super = "SUPERADMIN" in roles
    if not is_super and not (roles & set(spec.write_roles)):
        raise PolicyError(f"role not permitted to write {key}", status=403)

    # 3. scope-role: a store-only role (STORE_MANAGER) may write ONLY at store scope
    # for its own store. Global/entity overrides require a higher write role.
    if level in ("global", "entity"):
        if not is_super and not (roles & (set(spec.write_roles) - {"STORE_MANAGER"})):
            raise PolicyError(f"a store-scoped role cannot write {key} at {level} scope", status=403)

    # 4. store-scope ownership (STORE_MANAGER may only write their own store)
    if level == "store":
        sid = scope.get("store_id")
        hq = roles & {"SUPERADMIN", "ADMIN", "AREA_MANAGER"}
        if not hq and sid not in (actor or {}).get("store_ids", []):
            raise PolicyError("not permitted to write policy for this store", status=403)

    # 5. type + bounds + 6. luxury LOWER-only guard
    clean = _validate_value(spec, value)
    _luxury_guard(spec, clean)

    # 6. encrypt secret values PER VALUE (never _encrypt_config). Non-string secrets
    #    (e.g. a json map) are JSON-serialized before encryption; get_policy decrypts
    #    then _coerce re-parses. So a `secret` value is NEVER stored in plaintext.
    stored = clean
    if spec.secret:
        enc, _ = _enc()
        stored = enc(clean if isinstance(clean, str) else json.dumps(clean))

    coll = _coll()
    if coll is None:
        raise PolicyError("settings store unavailable", status=503)

    # Atomic write + pre-image fetch in ONE op (mirrors vouchers.redeem_voucher_atomic):
    # avoids a read-then-write race that would record a misleading audit before-state.
    now = datetime.utcnow()
    try:
        from pymongo import ReturnDocument

        pre = coll.find_one_and_update(
            {"_id": addr},
            {"$set": {f"values.{key}": stored, "level": level,
                      "scope_id": scope.get("store_id") or scope.get("entity_id"),
                      "updated_at": now, "updated_by": (actor or {}).get("user_id")},
             "$setOnInsert": {"created_at": now}},
            upsert=True,
            return_document=ReturnDocument.BEFORE,
        )
    except Exception as exc:  # noqa: BLE001
        raise PolicyError(f"failed to write policy: {exc}", status=500)
    _b = _nested_get((pre or {}).get("values") or {}, key)
    before = None if _b is _MISSING else _b

    _audit_policy(key, addr, before, (stored if spec.secret else clean), actor, spec.secret)
    cache.delete(f"{_DOC_CACHE_PREFIX}{addr}")
    return get_effective(key, scope)


def clear_override(key: str, scope: dict, *, actor: Optional[dict] = None) -> dict:
    spec = reg.REGISTRY.get(key)
    if spec is None:
        raise PolicyError(f"unknown policy key: {key}")
    addr = _scope_addr(scope)
    level = addr.split(":", 1)[0]
    if level == "global":
        raise PolicyError("cannot clear the global value; use PUT to change it")
    roles = _roles_of(actor)
    is_super = "SUPERADMIN" in roles
    if not is_super and not (roles & set(spec.write_roles)):
        raise PolicyError(f"role not permitted to write {key}", status=403)
    if level == "entity" and not is_super and not (roles & (set(spec.write_roles) - {"STORE_MANAGER"})):
        raise PolicyError(f"a store-scoped role cannot clear {key} at entity scope", status=403)
    if level == "store":
        sid = scope.get("store_id")
        hq = roles & {"SUPERADMIN", "ADMIN", "AREA_MANAGER"}
        if not hq and sid not in (actor or {}).get("store_ids", []):
            raise PolicyError("not permitted to write policy for this store", status=403)

    coll = _coll()
    if coll is None:
        raise PolicyError("settings store unavailable", status=503)
    before = None
    was_present = False
    try:
        existing = coll.find_one({"_id": addr}) or {}
        _b = _nested_get(existing.get("values") or {}, key)
        was_present = _b is not _MISSING
        before = _b if was_present else None
        if was_present:
            coll.update_one({"_id": addr}, {"$unset": {f"values.{key}": ""},
                                            "$set": {"updated_at": datetime.utcnow(),
                                                     "updated_by": (actor or {}).get("user_id")}})
    except Exception as exc:  # noqa: BLE001
        raise PolicyError(f"failed to clear policy: {exc}", status=500)
    # No phantom audit / cache churn when there was nothing to clear.
    if was_present:
        _audit_policy(key, addr, before, None, actor, spec.secret, action="policy_clear")
        cache.delete(f"{_DOC_CACHE_PREFIX}{addr}")
    return get_effective(key, scope)


def _audit_policy(key, addr, before, after, actor, secret, action="policy_update"):
    """One append-only audit row via AuditRepository.create (never append_audit_entry).
    Secret values are masked in the audit diff. Fail-soft."""
    try:
        from api.dependencies import get_audit_repository

        repo = get_audit_repository()
        if repo is None:
            return
        bv = "****" if (secret and before not in (None, "")) else before
        av = "****" if (secret and after not in (None, "")) else after
        repo.create({
            "action": action,
            "entity_type": "policy_setting",
            "entity_id": key,
            "store_id": (actor or {}).get("active_store_id"),
            "user_id": (actor or {}).get("user_id"),
            "user_name": (actor or {}).get("full_name") or (actor or {}).get("username"),
            "severity": "INFO",
            "source": "settings",
            "before_state": {key: bv},
            "after_state": {key: av},
            "detail": {"scope": addr},
        })
    except Exception:  # noqa: BLE001
        return
