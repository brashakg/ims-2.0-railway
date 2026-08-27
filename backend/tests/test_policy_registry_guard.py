"""
Repo-wide guard: every policy key the backend reads through
``api.services.policy_engine.get_policy`` MUST be declared in
``api.services.policy_registry.REGISTRY``.

Why this matters: get_policy returns the caller's ``default`` argument
IMMEDIATELY when the key is missing from the registry -- BEFORE consulting the
store/entity/global DB scopes, the env fallback, or the registry default. So a
``get_policy("some.key", default=X)`` call whose key is unregistered is not
"off by default": it is UNREACHABLE. No Settings screen, no Mongo override and
no env var can ever change it. PR #1011 found and deleted one of these
(``pm.purchase_recipient_follows_store``); a 2026-08-27 sweep found seventeen
more and registered them. This test stops the next one from landing.

Coverage (AST-based, one level of indirection deep):
  1. ``get_policy("literal", ...)`` -- the direct-literal case.
  2. ``get_policy(MODULE_CONSTANT, ...)`` / ``get_policy(svc.CONSTANT, ...)``
     -- an UPPER_CASE name is resolved against module-level string constants
     (same module first, then repo-wide by terminal name).
  3. ``wrapper("literal", ...)`` -- a module-local function that forwards one
     of its own parameters as get_policy's key (e.g. petty_cash_service's
     ``_policy(key, ...)``); literal keys at that argument position are
     checked at every same-module call site.

Documented, accepted blind spots: a key built dynamically (f-string / concat),
a key passed through TWO wrapper hops, or a wrapper called from a DIFFERENT
module than where it is defined. If you add one of those, add its key to the
registry yourself -- and preferably restructure so this guard can see it.

Do NOT "fix" a failure here by monkeypatching get_policy in a test -- that is
exactly the hollow pattern PR #1011 removed. Either register the key (with
type, default matching the code fallback, scopes and write_roles) or delete
the dead read.

No emoji (Windows cp1252).
"""
import ast
import re
from pathlib import Path

from api.services.policy_registry import REGISTRY

BACKEND_ROOT = Path(__file__).resolve().parents[1]
# Production code only. Tests intentionally exercise unknown-key behavior.
SCAN_ROOTS = (BACKEND_ROOT / "api", BACKEND_ROOT / "agents")

# Callees treated as direct policy reads. get_policy_fn is the conventional
# name for an injected get_policy callable (see services/serial_tracking.py).
_DIRECT_CALLEES = {"get_policy", "get_policy_fn"}

_CONST_NAME = re.compile(r"^_?[A-Z][A-Z0-9_]*$")


def _terminal(node):
    """The rightmost identifier of a call target: get_policy / pe.get_policy."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _module_consts(tree):
    """Module-level NAME = "string" assignments (plain and annotated)."""
    consts = {}
    for stmt in tree.body:
        targets = []
        value = None
        if isinstance(stmt, ast.Assign):
            targets, value = stmt.targets, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets, value = [stmt.target], stmt.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                consts[t.id] = value.value
    return consts


def _wrapper_params(tree):
    """{func_name: key_param_index} for functions that forward one of their own
    parameters as the first argument of a get_policy call."""
    wrappers = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = [a.arg for a in (fn.args.posonlyargs + fn.args.args)]
        for call in ast.walk(fn):
            if not isinstance(call, ast.Call) or _terminal(call.func) not in _DIRECT_CALLEES:
                continue
            if call.args and isinstance(call.args[0], ast.Name) and call.args[0].id in params:
                wrappers[fn.name] = params.index(call.args[0].id)
    return wrappers


def _iter_backend_files():
    for root in SCAN_ROOTS:
        yield from sorted(root.rglob("*.py"))


def _scan():
    """-> list of (relpath, lineno, key) for every statically resolvable
    policy-key read under the scan roots."""
    parsed = []  # (relpath, tree, consts, wrappers)
    global_consts = {}  # NAME -> set of string values across all modules
    for path in _iter_backend_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # a broken file is its own CI failure
            raise AssertionError(f"could not parse {path}: {exc}")
        consts = _module_consts(tree)
        for name, value in consts.items():
            global_consts.setdefault(name, set()).add(value)
        parsed.append((str(path.relative_to(BACKEND_ROOT)), tree, consts, _wrapper_params(tree)))

    found = []

    def _resolve(node, consts):
        """A key argument node -> list of candidate key strings (may be [])."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        name = _terminal(node)
        if name and _CONST_NAME.match(name):
            if name in consts:  # same-module definition wins
                return [consts[name]]
            return sorted(global_consts.get(name, ()))
        return []  # dynamic / function param -- out of static reach

    for relpath, tree, consts, wrappers in parsed:
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            callee = _terminal(call.func)
            key_node = None
            if callee in _DIRECT_CALLEES:
                if call.args:
                    key_node = call.args[0]
            elif callee in wrappers:  # same-module wrapper call
                idx = wrappers[callee]
                if len(call.args) > idx:
                    key_node = call.args[idx]
            if key_node is None:
                continue
            for key in _resolve(key_node, consts):
                found.append((relpath, call.lineno, key))
    return found


def test_every_statically_visible_policy_key_is_registered():
    found = _scan()
    unknown = sorted(
        {(relpath, lineno, key) for relpath, lineno, key in found if key not in REGISTRY}
    )
    assert not unknown, (
        "get_policy is being called with key(s) missing from policy_registry.REGISTRY. "
        "For an unregistered key, get_policy returns the caller default BEFORE the DB "
        "scopes / env / Settings are consulted -- the setting is UNREACHABLE, not "
        "'off by default'. Register each key (PolicySpec with a default matching the "
        "code fallback) or delete the dead read. Never monkeypatch around this test.\n"
        + "\n".join(f"  backend/{p}:{ln} -> {k!r}" for p, ln, k in unknown)
    )


def test_the_scanner_actually_sees_the_repo():
    """Anti-vacuity: if a refactor blinds the scanner (nothing found), the guard
    above would pass while protecting nothing. The backend reads well over 30
    distinct registered keys today; a collapse below 25 means the scanner broke,
    not that the code stopped using policies."""
    distinct_keys = {key for _, _, key in _scan()}
    assert len(distinct_keys) >= 25, (
        f"policy-key scanner only found {len(distinct_keys)} distinct keys -- "
        "it has likely gone blind (check SCAN_ROOTS / resolution logic)"
    )
    # And the exact seams this guard was built from must remain visible to it:
    for sentinel in (
        "reconciliation.mdr_bps",            # direct literal (router)
        "hr.roster_required_optometrists",   # cross-module constant (svc.CONST)
        "petty_cash.float_limit",            # literal through a module-local wrapper
    ):
        assert sentinel in distinct_keys, (
            f"scanner no longer sees {sentinel!r}; its resolution path regressed"
        )
