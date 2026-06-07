"""Apply the WhatsApp-blocked comms directive to STATUS.md + DECISIONS.md. ASCII-only."""
import os
ROOT = r"c:\Users\avina\IMS 2.0 CLAUDE COWORK\ims-2.0-railway-1\docs\roadmap"

DIRECTIVE = """## [!] COMMS CHANNEL DIRECTIVE (2026-06-07) -- WhatsApp BLOCKED

Meta disabled the WhatsApp Business account (healthcare/commerce policy). Owner is appealing
to recover it; do NOT block the program on it. SMS (DLT) still works as a fallback but is on
hold pending the appeal outcome.

**Rule for build + test sessions:** any feature whose value is *sending an outbound customer
message* is **DEFERRED** until a live channel returns. Build everything else. If a deferred
feature is otherwise ready, you MAY build it DARK (channel code behind `DISPATCH_MODE=off`,
no live send, send-path covered by tests) but do NOT prioritize it over non-messaging work.

- **DEFERRED (message-send dependent):** #46 reminders(send), #41 reactivation(send),
  #47 CL-reorder(send), #51 use-it-or-lose-it(send), #52 WhatsApp-invoice, #42 lookbook(send),
  #43 VIP-trigger customer-message, #45 walkout *follow-up message* (walkout LOGGING is NOT
  deferred), E6 *live send* (the rail/config/cap may still be built dark).
- **NOT affected (build normally):** all engines (E2/E3/E4/PM/SC), #35, #40, #34, #24, #50
  (in-app bell), #39 (in-app call list), #8, #2, #9, #17, #25, #26, #15, #1, #20, #14, #13,
  #18, #19, #44, #33, #16, #23, #27, #6, Base-Bank, etc. In-app / push / on-screen features
  are fully unaffected.

Family-wallet (#49) OTP: when it lands, route OTP via SMS (works today), not WhatsApp.

"""

# STATUS.md -- insert directive before "### Build session"
sp = os.path.join(ROOT, "STATUS.md")
with open(sp, "r", encoding="utf-8") as f:
    s = f.read()
if "COMMS CHANNEL DIRECTIVE" not in s:
    if "### Build session" in s:
        s = s.replace("### Build session", DIRECTIVE + "### Build session", 1)
    else:
        s = s + "\n\n" + DIRECTIVE
    with open(sp, "w", encoding="utf-8") as f:
        f.write(s)
    print("STATUS.md: directive inserted")
else:
    print("STATUS.md: already has directive")

# DECISIONS.md -- annotate the MSG91/comms row
dp = os.path.join(ROOT, "DECISIONS.md")
with open(dp, "r", encoding="utf-8") as f:
    d = f.read()
OLD = "| 1 | MSG91 DLT / comms go-live | **Start now, utility templates first** (ORDER_READY, RX_EXPIRY); marketing later. Build comms behind flag; `DISPATCH_MODE=live` flips on after DLT approval. |"
NEW = "| 1 | MSG91 / comms go-live | **UPDATE 2026-06-07: WhatsApp Business DISABLED by Meta** (healthcare/commerce policy) -- owner appealing. **SMS (DLT) is the fallback channel and works.** Build comms behind flag; message-SEND features are DEFERRED (see STATUS.md COMMS CHANNEL DIRECTIVE); `DISPATCH_MODE=live` only after WhatsApp restored OR SMS templates approved. |"
if OLD in d:
    d = d.replace(OLD, NEW, 1)
    with open(dp, "w", encoding="utf-8") as f:
        f.write(d)
    print("DECISIONS.md: comms row updated")
elif "WhatsApp Business DISABLED" in d:
    print("DECISIONS.md: already updated")
else:
    print("DECISIONS.md: WARN anchor not found (manual edit needed)")
