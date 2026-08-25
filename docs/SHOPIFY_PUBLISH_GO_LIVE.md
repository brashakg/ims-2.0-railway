# Before the first press: switching the website ON

**Who does this:** Avinash (owner), 10 minutes, in two browser tabs — Shopify admin and Railway.
**Why:** pressing "Send to website" now PUBLISHES. For a product to actually appear on
bettervision.in, four things must be true. Three of them are already shown on the IMS screen.
The fourth — the **Online Store sales channel** — is the one nobody has ever set, and until it is
set every press will honestly say "NOT made visible" and nothing will go live.

Nothing here is dangerous. Nothing here publishes anything. It only makes publishing possible.

---

## What the press needs (the four doors)

| Door | Where it lives | How you check it |
|---|---|---|
| 1. Shopify writes ON | Railway variable `IMS_SHOPIFY_WRITES=1` | tile on the sync screen |
| 2. Dispatch mode live | Railway variable `SHOPIFY_DISPATCH_MODE=live` | tile on the sync screen |
| 3. Shopify credentials | IMS Settings → Integrations | tile on the sync screen |
| 4. **Online Store channel** | Shopify app scope + (optionally) one Railway variable | **new tile** on the sync screen |

The screen is **IMS → Online Store → Shopify sync**. All four tiles must be green before a press
can put anything in front of a customer.

---

## Step 1 — give the app permission to publish (Shopify)

Publishing to a sales channel is a separate permission from editing products. The app almost
certainly does not have it yet.

1. Open **admin.shopify.com** and pick the **Better Vision** store.
2. Bottom-left: **Settings**.
3. Left menu: **Apps and sales channels**.
4. Top right: **Develop apps**.
5. Click the app IMS uses (the custom app whose Admin API token is pasted in IMS Settings →
   Integrations — if there is only one, it is that one).
6. Tab: **Configuration** → in the "Admin API integration" box, click **Edit**.
7. In the scopes search box type `publication`, then tick **both**:
   - `read_publications`
   - `write_publications`
8. Click **Save** (top right).
9. Go to the **API credentials** tab and click **Install app** / **Update app** if a button
   appears there — a scope change is not live until the app is re-installed.
10. **If, and only if, that step shows you a NEW Admin API access token** (a `shpat_…` value):
    copy it, open IMS → **Settings → Integrations → Shopify → Configure**, paste it, save.
    A scope change can invalidate the old token; if you skip this, IMS will report
    "credentials not configured" and nothing will push at all.

While you are here, confirm the store actually HAS the channel: **Settings → Apps and sales
channels → Sales channels** should list **Online Store**. (It does — bettervision.in is served by
it — but if it is ever removed, publishing has nowhere to go.)

## Step 2 — look at the IMS screen

1. Open IMS → **Online Store → Shopify sync**.
2. Look at the fourth tile, **"Online Store channel"**.
   - **Green, "publication looked_up"** (or "publication pinned") → done. Skip step 3 entirely.
   - **Red, "NOT resolved — presses will publish nothing"** → do step 3.

The screen asks Shopify for the channel every time it loads on a server that has not looked it up
yet, so the tile is answering about your shop right now — a red tile is a real problem, not a
leftover from a restart. If it is red, step 3 fixes it for good.

## Step 3 — only if the tile is still red: pin the channel by hand

You need one identifier out of Shopify. It looks like `gid://shopify/Publication/12345678`.

1. In the Shopify admin, install the free **Shopify GraphiQL App** if it is not already there
   (Settings → Apps and sales channels → Shopify App Store → search "GraphiQL").
2. Open it. You get a two-pane screen; the left pane is where you type.
3. Delete whatever is in the left pane and paste exactly this:

   ```graphql
   { publications(first: 25) { nodes { id name } } }
   ```

4. Press the ▶ (play) button.
5. On the right you get a list. Find the entry whose `"name"` is **"Online Store"** and copy its
   `"id"` — the whole `gid://shopify/Publication/…` string, without the quotes.
6. Open **railway.app** → project **IMS 2.0** → the **backend** service (the one that serves
   `ims-20-railway-production.up.railway.app`).
7. Tab: **Variables** → **New Variable**.
   - Name: `SHOPIFY_ONLINE_STORE_PUBLICATION_ID`
   - Value: the `gid://shopify/Publication/…` you copied
8. Click **Add**, then **Deploy** when Railway offers it, and wait for the deploy to go green
   (about a minute).
9. Reload the IMS sync screen. The fourth tile must now read **"publication pinned"**.

## Step 4 — prove it on ONE product, not on the catalogue

1. IMS → **Online Store → Products**.
2. Pick one product that has a photograph and a price you are happy for customers to see.
3. Press **Send to website** and confirm.
4. Expect a green toast that says **LIVE**. Then open **bettervision.in**, search the product name,
   and see it.
5. If you do not like what you see: the same row has **Take off website**. It hides the product
   again immediately and nothing is deleted. A bulk press will not put it back — only pressing
   that one product again will.

Once one product is proven, the bulk **Push** on the sync screen sends up to **25 products per
press**. Press it again for the next 25.

---

## What you will see when something is wrong (and why that is good)

The whole point of this change is that the button can no longer claim success for work it did not
do. After a bulk press the panel and the toast tell you exactly what happened:

- **"N processed"** — these are live on bettervision.in now.
- **"N refused (no photograph)"** — nothing was sent. A listing with an empty grey box is worse
  than no listing. Add a photo to the product in IMS and press again.
- **"N NOT made visible"** — these reached Shopify but no customer can see them: usually the
  Online Store channel (this document), sometimes a photograph Shopify could not download, or a
  price IMS could not prove. They stay in the queue; fix the cause and press again.
- **"N skipped (taken down)"** — you pulled these off the website by hand. A bulk press never
  puts one back.
- **"N archived (not listed)"** — retired products. The update reached Shopify, but an archived
  product is not on the storefront, so it is never counted as live.
- **"Stopped at the safety cap of 25 products — run again to continue"** — press again for the
  next 25. If instead it says **"NOTHING was published this press"**, pressing again will not
  help: every product it tried was refused or held back, and the lines above say why.

If step 1 or step 3 is wrong on the day, you will see **"NOT made visible"** on everything and a
red tile on the sync screen. That is the system telling you the truth: nothing went live. It is
not a failure of the products, and pressing harder will not help — fix the channel, then press.

---

## The one-time backfill (run once, by Claude, before the first bulk press)

The existing catalogue was never queued, and the products IMS pushed to Shopify in the past went
up as invisible drafts. A script queues both groups so the press has something to send:

```
railway run --service MongoDB -- .venv\Scripts\python.exe backend\scripts\backfill_online_store_queue.py
```

It prints a plan and writes nothing. Adding `--apply` writes the queue flags. It never talks to
Shopify and cannot publish anything; it only decides what the next press is allowed to look at.
Products with no photograph are listed by SKU and deliberately left out.
