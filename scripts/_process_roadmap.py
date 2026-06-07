"""Parse the enterprise-roadmap workflow output JSON and split into organized draft files.
Local diagnostic (underscore-prefixed). ASCII-only prints to avoid cp1252 crashes."""
import json, os, re, sys

OUT = r"C:\Users\avina\AppData\Local\Temp\claude\c--Users-avina-IMS-2-0-CLAUDE-COWORK-ims-2-0-railway-1\95d9a305-34dc-4c08-ad9f-83d01f8fa3a7\tasks\wy6uzuxpy.output"
ROOT = r"c:\Users\avina\IMS 2.0 CLAUDE COWORK\ims-2.0-railway-1"
BASE = os.path.join(ROOT, "docs", "roadmap", "_analysis")
FEAT = os.path.join(BASE, "features")
os.makedirs(FEAT, exist_ok=True)

with open(OUT, "r", encoding="utf-8") as f:
    raw = json.load(f)
# workflow envelope: {summary, agentCount, logs, result}
data = raw.get("result", raw) if isinstance(raw, dict) else raw
if isinstance(data, str):
    data = json.loads(data)

def w(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text if isinstance(text, str) else json.dumps(text, indent=2))

def slug(s):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:50]

def section_of(md, h):
    if not md:
        return ""
    i = md.find(h)
    if i < 0:
        return ""
    rest = md[i + len(h):]
    j = rest.find("\n## ")
    return (rest if j < 0 else rest[:j]).strip()

def meta_of(md):
    if not md:
        return ""
    for line in md.split("\n"):
        if line.strip().startswith("META:"):
            return line.strip()
    return ""

# ---- designs ----
designs = data.get("designs", [])
ok, failed = [], []
decisions_blob = ["# RAW per-feature owner decisions (from 52 design packets)\n"]
meta_table = ["# Feature META index\n", "| id | name | meta | quickwin | rec |", "|---|---|---|---|---|"]
for d in sorted(designs, key=lambda x: x.get("id", 0)):
    fid = d.get("id")
    name = d.get("name", "")
    md = d.get("md")
    if not md:
        failed.append(fid)
        continue
    ok.append(fid)
    w(os.path.join(FEAT, "F%02d-%s.md" % (fid, slug(name))), md)
    m = meta_of(md)
    qw = "yes" if "quickwin=yes" in m.replace(" ", "") else ("no" if "quickwin=no" in m.replace(" ", "") else "?")
    rec = section_of(md, "## Recommendation").replace("\n", " ")[:120]
    meta_table.append("| %s | %s | %s | %s | %s |" % (fid, name.replace("|", "/"), m.replace("META:", "").strip().replace("|", "/"), qw, rec.replace("|", "/")))
    od = section_of(md, "## Owner decisions")
    decisions_blob.append("\n## #%d %s\n%s\n" % (fid, name, od or "(none)"))

w(os.path.join(BASE, "_meta_index.md"), "\n".join(meta_table))
w(os.path.join(BASE, "_raw_owner_decisions.md"), "\n".join(decisions_blob))

# ---- recon ----
recon = data.get("recon", [])
rec_parts = ["# Subsystem recon (existing IMS 2.0 capabilities)\n"]
for r in recon:
    rec_parts.append("\n\n# === %s ===\n%s" % (r.get("label", r.get("key", "")), r.get("md") or "(failed)"))
w(os.path.join(BASE, "_appendix_recon.md"), "\n".join(rec_parts))

# ---- council ----
council = data.get("council", [])
c_parts = ["# Architect council (4 lenses)\n"]
for c in council:
    c_parts.append("\n\n# === Lens: %s ===\n%s" % (c.get("lens", ""), c.get("md") or "(failed)"))
w(os.path.join(BASE, "_appendix_council.md"), "\n".join(c_parts))

# ---- chair ----
chair = data.get("chair")
w(os.path.join(BASE, "00_CHAIR_ROADMAP.md"), chair or "(chair synthesis failed)")

# ---- summary ----
print("designs OK: %d  FAILED: %s" % (len(ok), failed if failed else "none"))
print("recon entries: %d (failed: %s)" % (len(recon), [r.get("key") for r in recon if not r.get("md")]))
print("council entries: %d (failed: %s)" % (len(council), [c.get("lens") for c in council if not c.get("md")]))
print("chair length chars: %d" % (len(chair) if chair else 0))
print("wrote to: %s" % BASE)
