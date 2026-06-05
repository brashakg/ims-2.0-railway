# IMS 2.0 — Owner Action Guide (plain-English, step-by-step)

For Avinash. No coding needed. Each item says **why** it matters, **what you do**, and **what to give Claude** (so the built-but-dormant code switches on). Do them top-to-bottom; the early ones unblock the most.

> **The one skill you'll reuse: adding a "Variable" on Railway.** Most "turn it on" steps are just pasting a value into Railway. Here's how, once:
> 1. Go to **railway.app** → sign in → open the **"IMS 2.0"** project.
> 2. Click the **backend** service tile (the box that runs the API).
> 3. Click the **"Variables"** tab at the top.
> 4. Click **"+ New Variable"**. Type the NAME (left box) and the VALUE (right box). Click **Add**.
> 5. Railway auto-redeploys in ~1–2 min. Done.
> Whenever a step below says *"set `X` on Railway"*, that's exactly this.

---

## ① FIRST: Clean up the product/customer data (biggest unblock)

**Why:** the database has ~10,800 products with a missing internal id, 5 duplicate item codes (SKUs), 612 blank barcodes, and some duplicate customer ids. These block a few safety features (unique-barcode scanning, clean reporting). A script (already written + safe) fixes them.

**It's safe:** it runs in two modes — a **dry-run** that only *shows* what it would change (changes nothing), then a **commit** that actually fixes it. It never deletes anything.

**Easiest path — let Claude do it:** just reply **"run the data-cleanup dry-runs"** and Claude will run the read-only previews and show you the exact numbers (e.g. "would backfill 10,805 product ids"). You eyeball them, then say **"commit them"** and Claude applies the fixes. *This is the recommended path since it needs a terminal.*

**If you'd rather do it yourself** (needs the Railway CLI on your PC):
1. Install the Railway CLI once: open **Command Prompt** (Windows search → "cmd") and run `npm i -g @railway/cli`, then `railway login` (opens your browser to sign in).
2. In Command Prompt, go to the project folder: `cd "C:\Users\avina\IMS 2.0 CLAUDE COWORK\ims-2.0-railway-1"`.
3. **Preview (safe, changes nothing):** run these four, read the counts:
   ```
   railway run --service backend python scripts/prod_data_cleanup.py --step inv2
   railway run --service backend python scripts/prod_data_cleanup.py --step inv3
   railway run --service backend python scripts/prod_data_cleanup.py --step inv4
   railway run --service backend python scripts/prod_data_cleanup.py --step ops3
   ```
4. **Apply (only after the previews look right):** add `--commit` to each:
   ```
   railway run --service backend python scripts/prod_data_cleanup.py --commit --step inv2
   ```
   …and the same for `inv3`, `inv4`, `ops3`.

---

## ② SECOND: Set two security keys (5 minutes)

**Why:** these protect logins and the one-time setup. Without strong values the app warns it's insecure.

**What you do** (using "the one skill" above):
1. Generate two long random strings. Easiest: go to **1password.com/password-generator** (or Bitwarden's generator) → set length 40+ → **Copy**. Do it twice (two different values).
2. On Railway → backend → Variables, add:
   - Name `JWT_SECRET_KEY`, value = the first random string.
   - Name `SEED_SECRET`, value = the second random string.
3. That's it. (Keep both somewhere private, like a note in your password manager.)

---

## ③ THIRD: Switch on features (each is built — it just needs its account/key)

Do these in any order, whenever you're ready for that feature. For each: make the account → copy the value → paste it on Railway (or in the app's **Settings → Integrations**).

### A. Product-image auto-editing (clean backgrounds + shadows)
**Why:** auto-cleans your staff-shot product photos for the online store.
1. Go to **photoroom.com/api** → **Sign up** → **API** section → **copy your API Key**.
2. You also need a place to store the cleaned images: create a free **Cloudflare R2** bucket (cloudflare.com → R2 → Create bucket → name it e.g. `bv-product-images`) and copy its **Access Key, Secret, and Account/endpoint**. (If this part is fiddly, tell Claude "help me set up the image bucket" and it'll walk you through R2 click-by-click.)
3. On Railway → backend → Variables, set: `PHOTOROOM_API_KEY` (the Photoroom key), and the R2 values: `IMAGE_STORAGE_PROVIDER`=`s3`, `IMAGE_S3_BUCKET`, `IMAGE_S3_ACCESS_KEY`, `IMAGE_S3_SECRET_KEY`, `IMAGE_S3_ENDPOINT`, `IMAGE_S3_PUBLIC_BASE`.
4. Set `IMAGE_EDIT_PROVIDER`=`photoroom`.
5. Tell Claude the backdrop/look you want (e.g. "pure white background, soft shadow, square") and it locks that as the standard.

### B. WhatsApp / SMS messages (reminders, birthdays, follow-ups)
**Why:** the marketing agent (MEGAPHONE) sends these.
1. You likely already have **MSG91**. Log in at **msg91.com** → copy your **Auth Key**, your **WhatsApp integrated number**, and your **SMS template/sender** ids.
2. On Railway set: `MSG91_API_KEY`, `MSG91_WHATSAPP_INTEGRATED_NUMBER`, `MSG91_SMS_TEMPLATE_ID`, `MSG91_SENDER`.
3. **Safety:** keep `DISPATCH_MODE`=`off` until you're ready; set it to `test` (sends only to your `TEST_PHONE`) to try it, then `live` to send for real.

### C. The AI agents (ORACLE analysis, Jarvis chat — SUPERADMIN only)
**Why:** powers the owner-only AI analysis + chat.
1. Go to **console.anthropic.com** → **API Keys** → **Create Key** → copy it.
2. On Railway set `ANTHROPIC_API_KEY` = that key.

### D. GST e-invoicing (IRN + QR on tax invoices) — *legally required at B2B scale*
**Why:** big-value B2B invoices legally need a government IRN + signed QR.
1. Pick a **GSP/ASP provider** (e.g. ClearTax, Masters India, Cygnet) and sign up for **e-invoicing API access** for each of your GSTINs. They give you a **username, password, and API URL** per GSTIN.
2. In the app: **Settings → Integrations**, add an "e-invoice" entry per GSTIN with those values (or tell Claude the values and it'll guide where they go).
3. On Railway set `IMS_EINVOICE_ENABLED`=`1`.
> This one has a real cost + paperwork — do it when B2B volume justifies it. Tell Claude when you've picked a GSP and it'll finish the wiring.

### E. UPI QR on bills + auto payment matching
**Why:** show a scannable UPI QR on the bill and auto-tick the payment.
1. In the app **Settings → Stores**, set each store's **UPI VPA** (your `name@bank` UPI id). *(The QR works with just this.)*
2. For auto-matching, sign up at **razorpay.com** → copy **Key Id + Key Secret** → app **Settings → Integrations → Razorpay**.

### F. Online store go-live (Shopify) — the BVI cutover
**Why:** make IMS the single source of truth for your online catalog + stock.
> This is the biggest one and has a specific safe order. **Don't do it piecemeal — when you're ready, tell Claude "let's do the Shopify cutover"** and it will walk you through each step live (paste Shopify token + webhook secret → run the data migration → flip the `IMS_SHOPIFY_WRITES` switch while turning the old BVI app to read-only, all in one sitting). Doing it out of order risks double-writes.

### G. The three features building right now (turn on later)
- **Inbound WhatsApp** (customers chat to book/reorder): needs a **Meta WhatsApp Business API** account (developers.facebook.com → WhatsApp). Tell Claude when you have it.
- **Ad dashboard** (see Google/Meta ad spend in IMS): needs **Google Ads API + Meta Marketing API** access (usually via your agency). Tell Claude when you have the keys.
- **ONDC seller node** (be on Paytm/PhonePe shopping): needs an **SNP partner** (a company that connects you to ONDC). Tell Claude when you've chosen one.

---

### What Claude can do FOR you (just ask)
- Run the data-cleanup previews + (after your OK) the fixes.
- Walk you through any signup above screen-by-screen.
- Once you paste a value into Railway, verify the feature actually turned on.

**Nothing here is urgent except ① (data cleanup) and ② (secrets).** Everything else is "turn on when you want that feature."
