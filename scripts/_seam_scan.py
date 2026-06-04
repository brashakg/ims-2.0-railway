"""
Half-built-seam scanner
=======================
Diffs every frontend API call (frontend/src/services/api/*.ts) against every
real backend route (api.main.app.routes). Reports FE calls whose (method, path)
has NO matching backend route -> a feature whose UI calls an endpoint that does
not exist (a half-built seam / 404 at runtime).

Heuristic, lead-generating: resolves simple per-file `const X = '/path'` bases,
collapses path params, strips query strings. Manually verify each hit.
Untracked diagnostic -- safe to delete.
"""
import os
import re
import glob

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
import sys
sys.path.insert(0, "backend")

from api.main import app  # noqa: E402

PARAM = re.compile(r"\{[^}]*\}")
TMPL = re.compile(r"\$\{[^}]*\}")


def norm(path: str) -> str:
    path = path.split("?")[0]
    path = TMPL.sub("{}", path)
    path = PARAM.sub("{}", path)
    if path.startswith("/api/v1"):
        path = path[len("/api/v1"):]
    path = path.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return path or "/"


# --- backend routes: set of (method, normalized_path) and a path-only set ---
be = set()
be_paths = {}
for r in app.routes:
    methods = getattr(r, "methods", None)
    path = getattr(r, "path", None)
    if not methods or not path:
        continue
    np = norm(path)
    for m in methods:
        if m in ("HEAD", "OPTIONS"):
            continue
        be.add((m, np))
        be_paths.setdefault(np, set()).add(m)

# --- frontend api calls ---
CALL = re.compile(r"api\.(get|post|put|patch|delete)\s*\(\s*([`'\"])(.*?)\2", re.S)
CONST = re.compile(r"const\s+(\w+)\s*=\s*['\"]([^'\"]+)['\"]")

misses = []
for fp in glob.glob("frontend/src/services/api/*.ts"):
    with open(fp, "r", encoding="utf-8") as fh:
        src = fh.read()
    consts = {m.group(1): m.group(2) for m in CONST.finditer(src)}
    for m in CALL.finditer(src):
        method = m.group(1).upper()
        raw = m.group(3)
        # resolve ${CONST} -> its value when the const is a known string base
        def _sub(mm):
            name = mm.group(0)[2:-1].strip()
            return consts.get(name, "{}")
        resolved = re.sub(r"\$\{[^}]*\}", _sub, raw)
        np = norm(resolved)
        if not np.startswith("/"):
            continue
        line = src[: m.start()].count("\n") + 1
        if (method, np) not in be:
            other = sorted(be_paths.get(np, set()))
            misses.append((fp, line, method, np, other))

print(f"Backend routes: {len(be)}  |  FE calls scanned across {len(glob.glob('frontend/src/services/api/*.ts'))} files")
print(f"FE calls with NO exact (method, path) backend match: {len(misses)}\n")
print("=" * 100)
for fp, line, method, np, other in sorted(misses, key=lambda x: (x[0], x[1])):
    short = fp.replace("frontend/src/services/api/", "")
    if other:
        print(f"[METHOD-MISMATCH] {short}:{line}  FE {method} {np}  -- backend has {other} on this path")
    else:
        print(f"[NO-ROUTE]        {short}:{line}  FE {method} {np}")
