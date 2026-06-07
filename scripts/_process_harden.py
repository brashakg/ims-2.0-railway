"""Extract hardening workflow output: findings -> HARDENING.md, corrections -> printed, packets -> features/. ASCII-only."""
import json, os

OUT = r"C:\Users\avina\AppData\Local\Temp\claude\c--Users-avina-IMS-2-0-CLAUDE-COWORK-ims-2-0-railway-1\95d9a305-34dc-4c08-ad9f-83d01f8fa3a7\tasks\wpxqrt707.output"
BASE = r"c:\Users\avina\IMS 2.0 CLAUDE COWORK\ims-2.0-railway-1\docs\roadmap"
FEAT = os.path.join(BASE, "features")
os.makedirs(FEAT, exist_ok=True)

with open(OUT, "r", encoding="utf-8") as f:
    raw = json.load(f)
data = raw.get("result", raw)
if isinstance(data, str):
    data = json.loads(data)

findings = data.get("findings", [])
corrections = data.get("corrections")
packets = data.get("packets", [])

# HARDENING.md = all findings + chair corrections
parts = ["# IMS 2.0 Roadmap — Hardening Report\n",
         "> Adversarial verification of all artifacts vs real code, + chair GO/NO-GO + corrections.\n\n",
         "# ===== CHAIR VERDICT & CORRECTIONS =====\n", corrections or "(chair failed)",
         "\n\n# ===== RAW HARDENING FINDINGS =====\n"]
for fnd in findings:
    parts.append("\n\n## Area: %s\n%s" % (fnd.get("key"), fnd.get("md") or "(failed)"))
with open(os.path.join(BASE, "HARDENING.md"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(parts))

# packets -> features/
pok = []
for p in packets:
    pid = p.get("id"); md = p.get("md")
    if not md:
        continue
    pok.append(pid)
    with open(os.path.join(FEAT, "%s.md" % pid), "w", encoding="utf-8") as fh:
        fh.write(md)

print("findings:", [f.get("key") for f in findings])
print("packets OK:", pok)
print("corrections length:", len(corrections) if corrections else 0)
print("wrote HARDENING.md +", len(pok), "packets to features/")
