"""Extract Phase-1 packets to features/. ASCII-only."""
import json, os
OUT = r"C:\Users\avina\AppData\Local\Temp\claude\c--Users-avina-IMS-2-0-CLAUDE-COWORK-ims-2-0-railway-1\95d9a305-34dc-4c08-ad9f-83d01f8fa3a7\tasks\wv7btazrl.output"
FEAT = r"c:\Users\avina\IMS 2.0 CLAUDE COWORK\ims-2.0-railway-1\docs\roadmap\features"
os.makedirs(FEAT, exist_ok=True)
with open(OUT, "r", encoding="utf-8") as f:
    raw = json.load(f)
data = raw.get("result", raw)
if isinstance(data, str):
    data = json.loads(data)
packets = data.get("packets", [])
ok, failed = [], []
for p in packets:
    pid = p.get("id"); md = p.get("md")
    if not md:
        failed.append(pid); continue
    ok.append(pid)
    with open(os.path.join(FEAT, "%s.md" % pid), "w", encoding="utf-8") as fh:
        fh.write(md)
print("packets OK:", ok)
print("FAILED:", failed if failed else "none")
