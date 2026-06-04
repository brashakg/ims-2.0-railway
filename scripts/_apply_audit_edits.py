"""Local-only: apply the audit-fix workflow's structured edits for the
hr / finance-display / orders-inventory-tasks lanes (the ones that returned
edits but were never written to disk). Exact find/replace -- reports MISS/AMBIG
so nothing is applied fuzzily.
"""
import json
import os

OUT = (
    r"C:\Users\avina\AppData\Local\Temp\claude"
    r"\c--Users-avina-IMS-2-0-CLAUDE-COWORK-ims-2-0-railway-1"
    r"\bcb2eb4c-bd3d-4a12-a90d-7ce559b8125c\tasks\wfatw1qw5.output"
)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANES = {"hr", "finance-display", "orders-inventory-tasks"}

data = json.load(open(OUT, encoding="utf-8"))
results = (data.get("result") or data).get("results") or []

applied, missed, ambig = 0, [], []
for r in results:
    if r.get("lane") not in LANES:
        continue
    print("=== LANE", r.get("lane"), "===")
    for e in r.get("edits", []):
        path = e["file"].replace("/", os.sep)
        find, repl = e["find"], e["replace"]
        full = os.path.join(REPO, path)
        tag = (e.get("finding") or "")[:62]
        try:
            src = open(full, encoding="utf-8").read()
        except Exception as ex:  # noqa: BLE001
            print("  NOFILE", path, ex)
            missed.append((path, tag))
            continue
        c = src.count(find)
        if c == 0:
            print("  MISS  ", path, "::", tag)
            missed.append((path, tag))
            continue
        if c > 1:
            print("  AMBIG ", path, "x%d" % c, "::", tag)
            ambig.append((path, tag))
            continue
        open(full, "w", encoding="utf-8").write(src.replace(find, repl))
        print("  OK    ", path, "::", tag)
        applied += 1

print("\nAPPLIED %d  MISSED %d  AMBIG %d" % (applied, len(missed), len(ambig)))
for p, t in missed + ambig:
    print("   needs-manual:", p, "::", t)
