"""Extract the engine-architecture workflow output into ENGINES.md + per-engine files. ASCII-only prints."""
import json, os

OUT = r"C:\Users\avina\AppData\Local\Temp\claude\c--Users-avina-IMS-2-0-CLAUDE-COWORK-ims-2-0-railway-1\95d9a305-34dc-4c08-ad9f-83d01f8fa3a7\tasks\wqc5wv9e6.output"
BASE = r"c:\Users\avina\IMS 2.0 CLAUDE COWORK\ims-2.0-railway-1\docs\roadmap"
ENG = os.path.join(BASE, "engines")
os.makedirs(ENG, exist_ok=True)

with open(OUT, "r", encoding="utf-8") as f:
    raw = json.load(f)
data = raw.get("result", raw)
if isinstance(data, str):
    data = json.loads(data)

specs = data.get("specs", [])
chair = data.get("chair")

ok = []
for s in specs:
    k = s.get("key"); md = s.get("md")
    if not md:
        continue
    ok.append(k)
    with open(os.path.join(ENG, "%s.md" % k), "w", encoding="utf-8") as fh:
        fh.write(md)

# combined ENGINES.md = chair build plan + all specs
parts = ["# IMS 2.0 Shared-Engine Architecture (ENGINES.md)\n",
         "> Authoritative engine contracts. Features MUST call these, never reimplement (PROTOCOL.md s6).\n",
         "> Per-engine detail also under `engines/`.\n\n---\n",
         "# Build Plan (chair synthesis)\n",
         chair or "(chair failed)",
         "\n\n---\n\n# Engine contracts\n"]
for s in specs:
    if s.get("md"):
        parts.append("\n\n## ===== %s: %s =====\n%s" % (s.get("key"), s.get("title"), s.get("md")))

with open(os.path.join(BASE, "ENGINES.md"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(parts))

print("engine specs OK:", ok)
print("chair length:", len(chair) if chair else 0)
print("wrote ENGINES.md +", len(ok), "per-engine files")
