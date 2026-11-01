# IMS 2.0 — live plan status

Updated **2026-11-01** (late evening): **New owner asks, in build:** (1) on Settings > Users the **Superadmin can set any user's password** - it works straight away (no forced change) and signs that person's other logins out; ADMIN keeps today's temporary-password reset. (2) On the Catalog page **every option filter becomes tick boxes** (brand, category, colour, frame type/material, shape, gender, size, added by, edited by): several ticks in one filter = any of them, different filters = and; old links keep working. (3) **Add Product duplicate check** (owner: RB 4345 / 0RB4345 / RB-4345 / 4345 are the same model but IMS does not notice) - investigating the real catalogue first, then a design for the owner before building. **Also running:** POS fix 1 #1264 (round 14 done, money review 15 next), S4 #1303 (the owner's two cost rules), S3 transfers #1321, review of #1305 and #1319, and why a login survived a closed browser. **Found today:** local test runs that need a database hang on this PC (no local MongoDB) - a watchdog now stops them and builders are told to leave them to CI; the Mac mini fixes it for good. Earlier, 2026-11-01 (evening): **GST 17(5) #1302 merged** (a203f652; 10 review rounds, merged after round 10 per the owner): expense types blocked under section 17(5) default to no input credit, and input credit on written-off stock is reversed in GSTR-3B 4(B)(1), exact to the paisa, on the GSTIN that claimed it, in the open month, re-claimed if the piece is found. Still for the CA: can a negative 4(C) be filed (or does the excess go to 3.1(a))? Should a re-claim also be listed in 4(D)(1)? Owner: there is no write-off door yet for free samples. **Owner rulings:** Claude reviews and merges #1305 and #1319 (review running); no Vercel Pro; a Mac mini (32 GB) is coming to run the work ~18 h/day. **Being checked:** the owner stayed logged in after closing the browser for 20 minutes although auto-logout is 15 minutes - investigation running. Earlier, 2026-11-01 (15:50): **Owner asked why work is slow; three more rulings (section 5).** Diagnosis: 16 code changes merged today, but the three money/GST builds take 9-14 review rounds each because (1) every should-fix had to be fixed before merge - **the owner keeps that bar**; (2) each round added new machinery that the next round reviewed; (3) a round costs about 2 hours (build, CI, five reviewers, then re-checks one at a time); (4) 30 merges to main today made every open build rebase and re-run its checks. Changes: re-checks run three at a time; builds rebase only on a real conflict; no new machinery inside an open build (follow-up PRs instead); small nits go to a follow-up list. **Rulings:** exit rule stays zero-should-fix; GST 17(5) #1302 gets one more round (two held-reversal cases) then merges; **workshop staff never record a payment** (kept out of the payments door in #1264). **S4 #1303** is building the two average-cost rules now. **S3 transfers #1321** opened as a draft (fix round 1). Not started: S7 Stock today, S6 menu cleanup. Earlier, 2026-11-01 (15:05): **POS fix 1 #1264 - money review 14:** the first round where most reviewers approve (3 of 5: roles/screens, deploy steps, cross-cutting). Two still block on 19 confirmed problems, now being fixed:
- the recovery steps could release the same pieces twice when the background settler and the panel act at once;
- a cash part recorded as owed still lowered the day's expected cash;
- the till could reuse its saved 'same sale' key for a later identical sale.

Decisions: a database guard against duplicate sales goes in now (with a read-only duplicate check on prod first), and Accounts get a 'cash owed - now paid' button. Owner question noted: can workshop staff record a payment at handover? Kept as it is today until answered. Earlier, 2026-11-01 (14:15): **#1325 merged** (owner ruling): a contact-lens batch is **expired on its expiry date**. The inventory screen, the till (incl. scanning a box), the near-expiry filter, the expiring-stock report and the goods-receipt warning now share one rule. The server side is live; the one screen change waits for the Vercel redeploy. **Vercel:** the free plan's 100-deploys-a-day limit refused even a manual production redeploy, so the website/app stays on #1315 until it resets (~13:30 IST on 2 Nov); Claude redeploys then. #1280's Menus page and this screen change go live together. **Still in review/fix:** POS fix 1 #1264 (money review 14), GST 17(5) #1302 (round 8: a written-off piece whose transfer was received but not yet completed), S4 #1303 (then the owner's two cost rules), S3 transfers. Earlier, 2026-11-01 (13:25): **Self-maintaining website menu #1280 merged** (after 10 review rounds); its server side is live. Before the merge a read-only prod check found no blocked collection IMS cannot read. The menu template is now in IMS and nothing was pushed to the website. It holds New Arrivals (389) | Sunglasses (469: by gender, shape, price, top 10 brands) | Eyeglasses (1) | Shop by Brand (23, A-Z) | About Us (8 links); left out: 0. **Still to do:** (1) Vercel's free plan hit its daily deploy limit, so the website/app is still on #1315 and the new Menus page is not live yet. Claude redeploys as soon as Vercel allows (within 24 h); #1324 (merged) stops docs/backend-only changes using the allowance. (2) Once the page is live, the owner checks the Boost Mega Menu app's per-item settings, then presses 'Regenerate & push now'. That backs up the current menu first, and one script restores it if needed. From then on the menu updates itself after each sync. Note: the New Collection builder can no longer make 'any of' collections (one collection per brand or shape now). Earlier, 2026-11-01 (12:15): **The owner answered all six open questions** (section 5): no shipping refunds at the counter (built in #1264); average cost averages only the pieces still held, and walk-in buys are averaged in (both go into #1303 before it merges, then the one-time backfill); automatic reordering stays off; a contact-lens batch is expired on its expiry date (fix in build); the three POS screens' date gets fixed after #1264. Earlier, 2026-11-01 (10:55): **POS fix 1 #1264** - round 12 fixed (all checks green, 20,831 tests); **money review 13: BLOCK**, 21 problems confirmed. Every round-12 fix held. The new problems:
- mostly the new 'Complete record' step: no screen yet, no shop check, dated 'now' (would move an earlier day's cash refund into today's drawer), an unpaid cash part dropped instead of recorded as owed;
- a stuck refund hold with nothing paid had no way out;
- the till's single automatic retry could deduct store credit or a voucher twice.

Being fixed now. Decisions: the retry is only for cash/card/UPI parts; a stuck hold raises a task and is never released automatically; a small 'Needs completing' panel on the Returns page for Accounts and Admin. Live check: the website/app (Vercel) and the server (Railway) both have every change merged tonight. Earlier, 2026-11-01 (10:00): **#1315 merged** - money, GST and accounts now read business dates in IST. It found more than the night-time problem: a code path had silently stopped working, so the **Tally B2B and sales exports, P&L and reconciliation date ranges used UTC days at every hour** (each day's export ran from 05:30 to 05:30 IST); and **'this month's expenses'** on Cash flow and the owner dashboard counted the previous month's last day, every day. Also fixed: promotions switch on/off on the IST date; the Finance dashboard's default year and period-lock month; GSTR-1/3B open on this month; day-end, petty-cash, cash-reconciliation, journal and expense default dates. The clock-sweep work is done apart from the date stamp in RET-/SHP-/VR- numbers (after #1264) and the two owner questions. **An internet outage (~09:20)** killed one helper in each running job; all were resumed, and the stock transfers (S3) builder's unsaved work was saved first and is being continued. **Website menu #1280:** six review rounds; four small items left, a seventh round is running. Earlier, 2026-11-01 (09:10): **#1314 merged and live** - operational screens now read business dates in IST (from the clock sweep): workshop counts, follow-up overdue/due-today, rosters, analytics 'today', staff self-service months, Reports hub ranges, activity log, order/transfer number stamps, contact-lens expiry. Three one-line date fixes inside POS screens are held (POS is ask-first; the files are also in #1264). One owner question added to section 5 (does a contact-lens batch's expiry day count as expired?). The money / GST / accounts half (#1315) is in review. Earlier, 2026-11-01 (08:45): **#1309 merged and live** (build 2d06ed38, healthy) - stock redesign **S5, one Reorder list** at Stock & supply > Reorder. Buy Desk, Replenishment and Low stock now open it. One rule for how many to buy: the shop's own minimum level if set, otherwise the last 30 days' sales, minus stock and orders already sent. It says 'transfer instead of buying' when another shop has spares, lists what it does not suggest and why, and makes one draft purchase order per supplier. One owner question added to section 5 (automatic reordering is off by default). Earlier, 2026-11-01 (08:30): **Three owner questions added to section 5** (shipping refunds at the counter - default NO; two average-cost rules for S4 - recommendations given). **S4 #1303, one average cost:** final check BLOCK - the catalogue edit screen could overwrite a product's average with a typed cost (products made through the normal create door slipped past its guard), plus a second copy of the catalogue-copy rule; fix round running. **GST 17(5) #1302:** fourth fix round (the GSTIN of a piece written off mid-transfer, the month of a late bill's reversal, multi-bill receipts, re-claimed credit never netted negative into 4(B)(1)). Earlier, 2026-11-01 (07:45): **The clock sweep finished.** It looked everywhere for the night-time mistake behind #1306 (a business day or month taken from the UTC clock instead of IST). **#1312 merged** (test-only): three more tests would have broken on 31 Mar 2027. In the product itself it found **25 real places**, each confirmed by an independent check. None has touched real money yet (not billing live), but each would once the shops are. The serious ones:
- the **Tally B2B and sales-journal exports** put bills made between 00:00 and 05:30 IST into the previous day's export;
- **promotions** switched on and off by the UTC date, so an expired offer applied to bills until 05:30;
- the **Finance dashboard's** default dates, including the month the period-lock button locks, were a day (or a month) behind at night.

Also: month-to-date expenses, the GSTR-1/3B default month, day-end / petty-cash / cash-reconciliation / journal / expense default dates, workshop and follow-up counts, analytics 'today', rosters, staff self-service months, contact-lens expiry. **Two fix PRs are in build:** lane A (money, GST, accounts; strictest review) and lane B (operational). The date stamp in RET-/SHP-/VR- numbers waits for #1264 (same files). **Website menu #1280**: the round-3 fixes passed CI; a final review round is running before merge and the one-time live setup. Earlier, 2026-11-01 (07:15): **#1299 merged and live** (build 48001dfa, healthy) - stock redesign **S2b, the Receive follow-ups**:
- a delivery-challan receipt needs its photo (refused without it, and the photo is now kept);
- a cash memo with no number is fine but its photo is required;
- a damaged piece marked 'Credit' closes that much of the purchase order short;
- each damaged piece gets a photo, kept on the receipt, the quarantined piece and the supplier-return draft;
- managers can print red DO NOT SHELVE labels for the quarantined pieces right from Receive.

Still in build: **GST 17(5) #1302** - third fix round: pieces refused at the receipt were counted when splitting a walk-in bill's tax, and one rule needed a test. **S4 #1303**, one average cost: fix round after review. **S5 #1309**, one Reorder list: opened. **Website menu #1280**: fix round 3. **POS fix 1 #1264**: round-12 fixes. Earlier, 2026-11-01 (06:50): **POS fix 1 #1264, money review 12: BLOCK** (the deploy-scripts reviewer approved). The round-11 fixes are in (all checks green, 20,739 tests). Review 12 confirmed 15 more problems; nearly all are one kind - a money action whose answer is lost and is then pressed again:
- the Returns screen sent no repeat-protection key, so a re-press after a lost answer paid twice on a multi-piece line;
- the manual credit screen's key was not tied to the amount;
- a voided superadmin credit note could never be issued again;
- two crash windows: a voided credit's pieces released twice, and a refund hold lapsing after the money moved;
- the till's payment legs carried no key;
- the general error told staff to 'try again' on saves that may have gone through.

Being fixed now with one rule: every money save carries a key that a re-press reuses; the server refuses the same key with different content; staff are told 'may already be saved - check before pressing again'. Also built: a small Accounts/Admin step to complete a return record when the credit landed but the record did not, with a person confirming whether the cash part was paid. **GST 17(5) #1302**: final fix round running (paisa-exact split for walk-in bills and transfers). Earlier, 2026-11-01 (04:15): **#1300 and #1301 merged and live** (build 1cee8100, healthy). #1300: the till now refuses to sell more than is in stock, including products the shop never held and the last piece sold at two tills at once. #1301: bulk price changes now reach the website (from the next sync, 09:00 IST), and archived products can never be re-listed. Wave 2: 6 of 7 merged; GST 17(5) #1302 is in review. **POS fix 1 #1264, money review 11: BLOCK.** 20 problems confirmed, none refuted, now being fixed:
- a second counter return on a line with several pieces (contact lens boxes, solutions) could pay the credit twice;
- the app silently re-sent a failed save up to 3 times, so an 'unclear' credit was redone automatically - fixed once for every screen;
- the superadmin credit note and the manual credit screen could pay twice;
- the section 34 deadline was not applied on website refunds;
- a credit that landed without a return record raised no task for anyone.

The deploy-scripts and #1294 reviewers approved. **Owner decision, default set (change it if you disagree):** the counter never refunds a website order's shipping charge; shipping is refunded only through Shopify's refund, which IMS mirrors. Earlier, 2026-11-01 (03:50): **#1306 merged and live** (build 3189dc34, healthy). Main's backend checks failed every night between 00:00 and 05:30 IST: two GST tests asked a report for 'today' and 'this month' by the UTC date, while the reports (correctly) use IST days. Only the tests changed; the product was never affected. With the fix, the whole backend suite was run to the end inside the night window (03:10 IST, across the Oct->Nov month end), and every test passed. **POS fix 1 #1264**: the round-10 fixes are pushed. When a store credit cannot be confirmed, staff see 'do not redo, it settles by itself'; the returned pieces stay claimed, so a redo is refused. Money review 11 is running with five reviewers, one of them on how refunds and returns interact with #1294's new shipping line. A sweep for the same clock mistake (year, financial-year and quarter ends, and product code) is **paused for memory** (the PC reached 95.7%); it found product places that read a business date off the UTC clock, which are being checked, and resumes when memory has room. Earlier, 2026-11-01 (02:15): **four more merged and live** (build a5997374, healthy): **#1290** a made-to-order lens is priced once at the till (it was billed double), **#1292** a Suggest button gives a colour name from the product's own photo (Accept / Change, nothing saved until Save), **#1295** the review follow-ups from #1274-#1277/#1284 and plain wording on the Shopify pages, **#1294** online orders now match what Shopify charged (discount codes, shipping line, shipping address kept, a simulated courier booking never marks an order shipped). New draft PRs waiting on their checks: #1301 website bulk prices + archived never re-listed, #1302 GST 17(5) blocked credit, #1303 stock S4 one average cost. In review/fix: POS 4 #1300, menu #1280 (round 2 found 2 blocking + 12 should-fix items; fix round 2 running), S2b #1299, POS fix 1 #1264 (catching up with today's merges, then money review 11). Earlier, 2026-10-31 (night): **#1273 delete archived products merged and live.** POS fix 1 #1264 money review 10 blocked on one shared 'unclear credit' problem (fix in build). Earlier (evening): wave 2 (7 jobs) and wave 3 (the stock redesign, 6 phases in 3 lanes) running; POS fix 1 in money review 10. Earlier (later): **#1286 merged** (Images page retired, archived products leave the publish queue); delete archived products #1273 passed its final check and is being updated with this week's merges before merge. Earlier 2026-10-31: merged and live since the last note - **#1277, #1275, #1276** (10-29), **#1274** and **#1284** (10-31). On 10-29 evening the helper agents lost access for a while (account access message), which stopped three reviews; restarted 10-31 from where they stopped. In progress: delete archived products #1273 (final check), the menu #1280 (fix round), the Images-page cleanup #1286, POS fix 1 #1264 (catching up with main, then money review 10). Earlier, 2026-10-29 (late evening): delete archived products passed review 3 (final fixes in build); POS fix 1 needs a credit-posting redesign after money review 9; barcode follow-up #1284 approved. Earlier (evening): **#1271 duplicate fix merged and live; #1278 Codex catalogue fixes merged and live.** Earlier 2026-10-29: work resumed (section 4ah). 628 of 633 colours/sizes now on the website (the RB3025 size was filled). Codex built 5 PRs (#1274-#1278), all under review. New owner asks: a self-maintaining website menu generated from collections with products (in build), remove the Images page, fix Store Health's counts. Earlier, 2026-10-24 (afternoon): **#1269** catalogue form helpers merged and deployed. Website parity rechecked (section 4ag): 610 of 633 colours/sizes are online; 15 Ray-Ban RB3025 colours wait on one missing lens size, 6 Carrera wait on the duplicate fix (in build). Unilens bridge set to NA on prod. Duplicate fix is PR #1271 (review blocked, being fixed). Owner asked for permanent delete of archived products (in build). POS fix 1 (#1264) in its 7th money review. Earlier today: merged and deployed **#1260** S1 stock count, **#1267** catalogue Save & next, **#1261** online refund store-credit cap. Earlier note (early 10-24): **S1 stock count merged (#1260) and deployed**; the owner approved two catalogue speed-up builds from the usage review (section 4af). Before that, 2026-10-23 (late): S1 round-2 fixes pushed with CI green, POS fix 1 is PR #1264, the CA's 19 answers recorded (section 4ae), a Fable review of real staff usage started (section 4af). Earlier the same evening: **21 PRs merged since the 2026-10-19 morning note** (section 4aa): step A finished (#1236 till discount approver, #1238 GST input credit, #1239 GST document numbers, #1241 publish prep, #1242 top-menu scroll, #1243 model name), the website work (#1244 page types, #1245 brand filters + Never sell online, #1251 auto first-publish 100 per sync, #1252 + #1254 never-sell take-down, #1257 listing quality, #1258 re-queue stamp, #1255 + #1256 weight on the product and sent to Shopify in grams, #1248 + #1262 the "could not be read" website labels), GST (#1247 margins without GST, #1253 GSTR-1 batch 1), the ZEISS lens master (#1246), two flaky tests (#1249, #1250) and **S2 of the stock redesign: one Receive screen (#1259)**. **Deployed 2026-10-23 16:06 and 18:01** after Railway's build pause (platform security incident 10-21); live build 8a21fd3c, healthy. On prod (owner approved): page types switched on; the 23 live listings re-sent with the new copy and weights; 47 new products first-published by two manual syncs (425 waiting, 100 per scheduled run); per-colour photos attached on 11 listings; an archived product taken out of the queue. Secrets rotated after the incident: MongoDB and Redis passwords (now variable references), the login and seed secrets, the Anthropic key (old key deleted by the owner); still to rotate: Shopify app token, credential-encryption key, PageSpeed, old e-commerce keys, marketing site password. **The owner approved the stock & inventory redesign** (14-screen canvas, section 4ab) and **a Fable 5.1 review of 8 workflows** found 34 confirmed problems (section 4ac); fixes are in build, the POS ones owner-approved. Still nothing sells online: website stock is 0 until opening stock / purchases are entered in IMS.

Updated **2026-10-19** (morning). Step A of the agreed order (section 4x) is mostly merged: **9 more PRs merged since the 2026-10-18 evening note** (section 4z) - one product per model in IMS (#1226), no duplicate products on Shopify (#1227), the visibility sweep (#1232), 16-character tax invoice numbers BOK01/2627/00001 (#1231), one website listing per model with colour/size choices (#1233), supplier payments pick their bill (#1230), who sees which shop (#1235), no doubled brand in titles (#1237) and supplier returns that name every piece (#1234). On prod (owner approved): the model-family move was applied (96 models, 149 linked, reversible) and the Shopify unique product id was set up. Read-only checks: the 01:02 IST sync ran clean with the Pune error gone; the website-listing dry run shows 0 price changes on the 10 live Carrera models and none of the 25 live listings queued; every staff login has a shop; there are no supplier returns on prod; no product has a selling price below MRP. In review: the till discount approver (#1236), GST input credit (#1238), GST document numbers (#1239). **The owner answered all five questions the same morning (section 5):** offers within caps, the June Pune test bills cancelled, 83 doubled brand names cleaned up, a return with no credit-note number refused, and Better Vision Opticals Pvt Ltd is over Rs 5 crore (e-invoicing needed). Still nothing sells online: products have 0 stock in IMS until it is received through purchasing.

Updated **2026-10-18** (evening). The largest day so far: Codex finished the paused wave and the 13-finding audit, and Claude took over as lead. **35 PRs merged since 2026-10-13** (section 4w): the paused procurement/online wave (#1163, #1166, #1171, #1181, #1196-#1204), all 13 original audit findings (#1205-#1220), and today's batch (#1221 collections push, #1222 failed Shopify notifications retried, #1223 GSTR-3B month-end, #1224 stock-write ordering, #1225 every size priced before publish, #1228 status page inside Shopify admin). On prod (owner approved): Pune linked to its Shopify location (all 4 shops linked); the IMS Shopify app's permissions fixed (menus and pages now readable); the live-data checks needed no change (barcodes 0, reorder levels 0, customers over Rs 1,50,000 credit 0). A full sweep of all 295 audit documents (2,801 items) was reconciled against the code; its confirmed open items are in section 4y. **The agreed order of work is section 4x.** Still nothing sells online: all 601 ready products have 0 stock in IMS until it is received through purchasing.

Updated **2026-10-13** (evening). Seven more merged 2026-10-13 (section 4v): the owner's Catalog page asks (**#1192**: recently-edited first, filters incl. who added it and a date range, rows per page, tick-select to flag or archive), photo order (**#1189**), cost hidden from counter staff everywhere incl. the 3 approved till places (**#1191**), the counter stock lookup (**#1180**), the valued inter-company challan (**#1185**), Add product follows the owner rulings (**#1174**) and the design-photo publish rebuild (**#1193**, replaces #1152). On prod (owner approved): the product identity keys were rebuilt (499 of 499, a fresh trial run finds nothing left) and the Brand Master website switch is ON for all 22 product brands (19 switched today). Still in build, none merged: #1153, #1163, #1165, #1166, #1171, #1181, #1183, #1184, #1188, own-task list #1194, what-we-pay admin/accounts only, round-off, reverse charge.

Updated **2026-10-13**. Three more merged 2026-10-08 .. 10-12 (section 4u): the purchase-order form (**#1170**), counter staff locked out of supplier and purchase data (**#1161**), and the till / counter lookup finding multi-word names (**#1187**, owner-approved POS change). In build, none merged: the 2026-10-12 owner asks - Catalog page sort / filters / who added it / select to flag or archive / rows per page / date range, photo order (#1189), cost hidden from counter staff everywhere (incl. 3 approved till places), each person sees only their own tasks - and the procurement-audit PRs #1165, #1166, #1171, #1174, #1180, #1183, #1184, #1185, #1188, plus #1153, #1163, #1181. Owner rulings 2026-10-12 recorded: what we pay (purchase prices and product cost) is admin / superadmin / accounts only; PO line prices fill in from the last price paid; walk-in dealers are added as suppliers; the catalog opens recently-edited first.

Updated **2026-10-08**. Four more merged 2026-10-01 .. 10-08 (section 4t): per-shop reorder levels (**#1179**), multi-location **PR 4 of 6 (#1160)**, purchase bills that match their form (**#1167**) and the stock-screens fixes (**#1169**). On prod: the July pooled stock-parity task is closed; the trial runs of the reorder-level move and the product switched-on backfill both found nothing to change, so neither was applied. Still in build, none merged: purchase-order edit/cancel #1165, purchase-order form #1170, off-catalogue items #1171, counter stock lookup #1180, unit barcodes #1166, counter-role purchase access #1161, add-product #1174, the purchases report #1184, bought-without-PO #1183, the valued challan #1185, multi-location PR 5 #1163, online order status #1153, the till records the scanned unit #1181, and the owner's 2026-10-08 answers (multi-word search, round-off, default credit limit, reverse charge, admin-controls clean-up, the #1152/#1172 rebuilds).

Updated **2026-10-01**. Seven more merged 2026-09-29 .. 10-01 (section 4s): the owner-approved till stock badge (**#1173**), unit labels for the TSC TE244 (**#1164**), four Wave 6 page splits built and reviewed in the cloud (settings admin editors **#1175**, payroll **#1176**, demand forecast **#1177**, loyalty **#1178**), and two CI/tooling fixes (**#1162**, **#1168**). Still in build, none merged: the procurement-audit fixes (purchase bills #1167, purchase-order edit/cancel #1165, purchase-order form #1170, off-catalogue items #1171, per-shop reorder levels #1179, counter stock lookup #1180, unit barcodes #1166, stock screens #1169, counter-role purchase access #1161, the purchases report, bought-without-PO and the valued inter-company challan), multi-location PRs 4 and 5 (#1160, #1163) and the online order status rules (#1153). Note: Vercel's free plan hit its daily deploy limit on 2026-10-01, so #1178 is merged but reaches the live site with the next deploy.

Updated **2026-09-29**. Eight more merged 2026-09-27/28 (section 4r): Wave 6 items A1, A3, A4, A6 and the clinical and workshop router packages (**#1150, #1151, #1155-#1158**), multi-location **PR 3 of 6 (#1154)**, and this file (#1149). The procurement audit (catalogue → purchase order → goods receipt → purchase invoice → stock, walked as each role on an isolated copy of the database) is **DELIVERED**: 118 confirmed findings, 3 of them blockers; the owner answered every question it raised (three multiple-choice rounds, 2026-09-28/29). Its fixes are **IN BUILD, none merged**: purchase-order edit/cancel/send, counter-role access to purchase data, stock-side bugs with real unit labels for the TSC TE244, unit barcodes without hyphens and the approved till stock badge. Also in build: multi-location PRs 4 and 5, the online order status rules, and the #1141 follow-ups.

Updated **2026-09-27**. **#1141 MERGED** — the per-store website stock writer (multi-location PR 2 of 6): each shop's own quantity goes to its own Shopify location through one rule and one writer, after 13+ rounds of adversarial review and a clean final recheck (section 4p). **#1148 MERGED** — the backend's Railway sleep setting is now OFF (owner ruling 2026-09-27; the app never actually slept) and the keepalive service it needed is removed. Wave 6 of the modularization work is in build; nothing of it is merged yet (section 4q).

Updated **2026-09-07**. **THE CATALOGUE WAS RESET TO EMPTY ON 2026-09-07 (section 4o).** On the owner's ruling every product was deleted: 43 listings removed outright from Shopify and every product, twin, variant, stock unit and product-image record removed from IMS. bettervision.in serves no products; IMS holds none. Orders (934), customers, stores, entities, users and the audit trail are untouched, and the historical GST invoices are intact because every order line carries its own snapshot of name, SKU, price and tax. The owner re-adds the catalogue fresh through IMS, which makes IMS the only source from the first product on. Every product count, product name and "live on the site" statement written below is history, not current state. All the sync code shipped on 2026-09-06 stands and applies to whatever is added next.

Updated **2026-09-06**. Thirteen more squash-merged on 2026-09-06 (section 4l), and with them **all five waves of the 2026-08-30 page-split plan are on main**: **#1110** sales roles book eye tests (closes the last section-5 decision), **#1111** + **#1112** the Wave 1/2 loose ends (last `?tab=` links gone; recalls at `/customers/recalls`), **#1113-#1115** the Wave 3 file diets (QuickAdd, Jarvis, Workshop pages), **#1116-#1122** the Wave 5 backend packages (finance, vendors, reports, inventory, orders, rbac_policy, shopify_push — every one a pure move, byte-identical API, 1,313 routes throughout). On prod (read-only, 2026-09-06): the 6 live products are untracked on Shopify and on sale at quantity 0, 0 Shopify orders since the 27 Aug cut-over, all 11 webhooks point at IMS — the sync-path audit's five gaps are the next queue, website stock first (section 4b). Then three more on 2026-09-06: **#1124** the scheduled live sync + manual button + Superadmin settings, **#1125** website stock made real, **#1126** delist on retire (section 4m). The first "Push stock" press is **NOT done** — it is blocked on two owner decisions (section 5): Shopify has two locations fulfilling online orders, and IMS's ledger holds 1 unit against 49 on Shopify, so a press today would mark the whole website sold out. Then **#1128** photos and the title follow IMS onto live products, **#1129** tags ownership, **#1130** missed-order catch-up — so **all five sync-audit gaps the owner ordered are on main** (section 4m). Then **#1132**: a failed price push keeps the product queued and says so (audit item 7). Then the evening's owner rulings turned into work (section 4n): **#1134** + **#1137** adopt the photos of products already live into the media map (35 of 42 now follow IMS), **#1135** + **#1136** a Shopify location per shop (PR 1 of the multi-location plan; 3 of 4 stores mapped, Pune deliberately last). On prod the same evening: 38 opening-stock units entered at Pune through the app door, and two Shopify locations created for the Dhanbad shops. In flight: the variant-of rule for the 11 Large-size smartglass units (no PR yet). The multi-location stock writer (PR 2 of 6) is designed, not built. The 09-05/06 wave (#1094, #1102-#1108, sections 4j-4k) is all on main. Section 5: nothing owed. This file is the single place to see what is done, what
is in flight, and what is waiting on a decision. Claude keeps it current; if it
disagrees with reality, the file is wrong and should be fixed.

Status key: **DONE** merged · **PR** open, awaiting merge · **WIP** agent working
· **BLOCKED** waiting on the owner · **TODO** not started

---

## 1. POS switchover — all five blockers closed

The new till at `/pos/new`, `/pos/counter`, `/pos/delivery` could not replace the
classic one until these were fixed. Every one deleted the classic till's copy
rather than duplicating it, so both tills now run the same code.

| # | Blocker | Status |
|---|---|---|
| 1 | New screens in no menu — reachable only by typing the address | **MERGED #1085** |
| 2 | A first-time customer could not be billed at all | **MERGED #1085** |
| 3 | Hold / recall a bill (the till auto-parks on idle — that work was unrecoverable) | **MERGED #1085** |
| 4 | Made-to-order lens could not be rung up (no barcode to scan) | **MERGED #1085** |
| 5 | Paper prescription could not be entered at the till | **MERGED #1085** |

**Two reported blockers were checked and disproved** — no code was written for
them: the workshop job is created from the *items* and is `sale_type`-agnostic;
and `is_advance_payment` never reaches the server, so part-payment already works.

### Delivery counter (owner review of the live screen, 2026-09-03)
| Item | Status |
|---|---|
| Split payment + credit-delivery options missing | **DONE** |
| Match the POS design language | **DONE** |
| Log which salesperson handed the goods over | **DONE** |
| 30-day window on pending deliveries (admin/superadmin exempt) | **DONE** |
| Search by customer name and phone, not just job card | **DONE** |

Root cause of the first three: the delivery screen hand-rolled its own
three-button payment UI instead of rendering the shared `StepPayment` the other
two tills use. That copy is deleted; reusing the real one restored split tender,
credit, vouchers, loyalty, EMI and cash denominations — and the design language —
in one move, and every guard now runs verbatim instead of in a weaker
transcription.

The search used to fetch the newest twenty orders of any status and match the
number exactly, client-side — so a customer who had lost their job card could not
be served. It now searches the delivery queue by number, name or phone; several
matches are chosen from, never guessed.

**Found while verifying, since fixed — MERGED #1093:** `find_overdue` bound a
datetime against `expected_delivery`, which is stored as an ISO string, so
`/orders/overdue/list` had been returning nothing in production. One
`iso_date_window` helper now serves the orders, workshop and prescription windows.

---

## 2. Wave 2 page splits — tabs become addresses

Owner's order: POS → Clinical → Inventory → Reports → Tasks → Customers → HR.
Sequence adjusted with reasons (Tasks needed decisions, HR was smallest).

| Module | What was wrong | Status |
|---|---|---|
| **Reports** | 5 sections in one 1,345-line page; 16 data calls before any click; GST returns were pop-ups | **MERGED #1086** |
| **HR** | 7 tabs, one URL; salary screens open to 5 roles against admin-only endpoints | **MERGED #1088** |
| **Tasks** | Two rival pages with opposite permissions; fabricated SOPs; 50-task blindness | **DONE** |
| **Customers** | 10 finished screens in no menu; no address for a customer profile | **DONE** — **found 2026-09-06 (#1111):** the "split" had only moved the route registry; recalls was still an in-page tab. **MERGED #1112**: `/customers/recalls` is a real address (same `RecallManager`, in-page branch deleted, nav row "Sales floor -> Recalls") |
| **Clinical** | 5 hidden tabs, two rival prescription doors | **DONE** — weaker Rx door deleted; the eye examination is its own page, **MERGED #1099** |
| **Inventory** | 19 sections in one file, 3 in the menu | **DONE** — 17 real pages |
| **Catalog** | Mostly split already; needs the review queue + photo work | **MERGED #1098** (review queue at `/catalog/review`, photo column, `/catalog/missing-photos`) + **#1096** (Add-a-Product) |

**Wave 1/2 loose ends closed — MERGED #1111 + #1112 (2026-09-06):** the last
`?tab=` links became real addresses (QuickAdd x2 -> `/inventory/stock?search=`,
reports -> `/inventory/stock` and `/inventory/transfers`), `pages/catalogue`
folded into `pages/catalog`, stale-doc banners on `docs/reference/*Feature_Status*`
and `IMS2_COMPLETE_FEATURE_LIST.md`. The app now has ZERO live `?tab=`
navigations — the mentions that remain are the forwarding shims in
`routes/*Routes.tsx` and comments. Waves 3 and 5 (the file diets) are in
section 4l; **all five waves of the 2026-08-30 page-split plan are on main.**

---

## 3. Security — found while doing the above

| Finding | Severity | Status |
|---|---|---|
| Churn-risk list returned 500 **complete customer records** to any signed-in user | High | **DONE** |
| RFM report published company-wide average customer value to any signed-in user | Medium | **DONE** |
| **13 of 19 customer doors trusted an id as authority — 4 moved money** | High | **MERGED #1089** |
| Login limiter locked out a **whole shop** on five typos across three people | High | **DONE** |
| Two IP readers, one spoofable and one returning a constant | Medium | **DONE** |
| Salary screens reachable by 3 roles the endpoints refuse | Medium | **MERGED #1088** |

---

## 4. Data integrity — found and fixed

| Finding | Status |
|---|---|
| Optometrist's Rx remarks discarded on save; the **patient's complaint printed in their place** | **DONE** |
| Three prescription date filters compared datetimes against strings — matched nothing, silently | **DONE** |
| One frame written five ways became **four products**; `identity_key` unique index did not exist in production | **DONE** |
| Discount cap bypassable by adding the item after saving the draft; lens lines exempt entirely | **DONE** |
| GSTR-1 and GSTR-3B disagreed on the tax head for the same refund | **DONE** |
| A refund spanning a month boundary reversed tax **twice** | **DONE** |

---

## 4b. Still to build (nothing is blocked on a decision)

| Work | State |
|---|---|
| **Catalog** — review queue + the photo work | **MERGED #1098** + **#1096** (section 4i); MEASURED + REPAIRED on prod 2026-09-05: twins with a usable photo 6 -> 70 (64 repaired, nothing queued, publish still manual); 37 never-pushed products now qualify to go online; only 7 products have no photo anywhere; **first 6 pushed LIVE 2026-09-06** (section 4k) |
| **Staff sign-in gate** — approved devices (WebAuthn passkeys); ADMIN/SUPERADMIN never gated | **BUILT, shipped OFF** — owner arms it |
| **Website stock made real** — sync-audit gap 1: the 6 live products are untracked on Shopify and on sale at quantity 0 (section 4k) | **MERGED #1125** (section 4m). The one-time "Push stock" press is NOT done — blocked on the owner (section 5) |
| **Scheduled live sync** — products already on Shopify re-push on change at 01:00 and 09:00 IST, a "Sync live products now" button, Superadmin settings (owner ruling 09-06, section 6). First publish of a never-pushed product stays a human press | **MERGED #1124** (section 4m) |
| **Sync-audit gaps 2-5**, in the owner's order: (2) delete/archive takes the product off sale; (3) photo + name changes reach live products; (4) tags; (5) missed-webhook catch-up | (2) **MERGED #1126**; (3) **MERGED #1128**; (4) **MERGED #1129**; (5) **MERGED #1130** — all five done (section 4m). Follow-ups logged, waiting on the owner's pick: adopt the products live before #1128 (no photo map, hands-off until a one-off script on an explicit id list); sweep missed *status* webhooks on already-booked orders; the design-queue photo re-press duplicates media + 24 orphan photo rows; parity check blind to untracked variants + a July drift task stuck ESCALATED; variants immutable via IMS; take-down never exercised live |
| Three screens each re-map the store's staff list their own way (`NewTaskModal`, `SalespersonPicker`, the new `useStoreStaff`) | small follow-up |

## 4c. Found and NOT fixed — recorded so they are not lost

| Finding | Why it is still open |
|---|---|
| `find_overdue` binds a datetime against `expected_delivery`, stored as a string, so **`/orders/overdue/list` returns nothing in production** | **MERGED #1093** — the field stays an ISO string; `iso_date_window` binds the window as strings, and the workshop and prescription windows use the same helper |
| The 30-day clamp on the customer list | Owner chose "clamp on last activity" — no last-activity field exists yet |

## 4d. The money-path audit (2026-09-03) - 20 raised, 8 killed, 12 confirmed

Five independent lenses swept POS -> payment -> delivery -> invoice; every
finding was then attacked by three separate refuters, majority required to kill.
A completeness critic then asked what all five had missed.

| Confirmed finding | Status |
|---|---|
| Refused handover charged the customer TWICE (delivery counter) | **DONE** |
| Goods left unpaid via the pickup scan, workshop status, courier booking - and a 4th, lab-routing | **DONE** - one shared gate, four doors |
| Credit note reversed under the wrong tax head; GSTR-1 and 3B disagreed | **DONE** |
| Credit note booked under the cashier's store -> one refund, two GSTINs | **DONE** |
| In-store return backed out GST at one dominant rate | **DONE** - exact per-line engine, copy deleted |
| Discount caps silently no-opped on add-item-to-order | **DONE** |
| Promo clamp read a merchandising label, not the discount tier | **DONE** |
| Loyalty points burned AND the customer still billed | **DONE** |
| Fabricated invoice number on printed paper | **DONE** - legacy invoice retired |
| Cashiers could not submit the blind day-end count | **DONE** |
| Store credit issued with no way to spend it | **WIP** |
| Cancelling an order with an advance has no door to return the cash | **TODO** |

## 4e. POS surfaces - owner review 2026-09-03

"Complete sale" on the general counter did nothing: `try/finally` with no
`catch`, so anything that threw vanished silently. **DONE**, both surfaces, and
that screen had NO tests at all before today.

An evidence-backed comparison found the counter is not a different design - it
is billing minus four capabilities, plus a legitimately re-shaped browse grid
whose card was re-typed and has drifted five ways. The cart is genuinely ONE
shared component; it only looks different because counter carts never hold
optical lines. **WIP**: all four gaps, the shared product card, 44px cart
controls, per-sale receipt choice, and customer edit at the till.

**Retiring the legacy till** (owner: "retire old pos") - **DONE**: retired in
**#1088** (`POSLayout` deleted, `/pos` redirects), salvage triage finished in
**#1095**. Nothing was deleted until it was proven replaced.

## 4f. Owner rulings 2026-09-04 - in build

| Decision | Status |
|---|---|
| Counter ALWAYS mints the tax invoice; per-sale receipt chooser dropped (no numberless receipt exists) | **WIP** |
| Store credit enabled at the delivery counter | **WIP** |
| Family-member number at create: BLOCK, with promote-to-own-account / open-existing popup | **DONE** |
| Two find-or-create implementations collapsed into one; race on the unique index returns 409 not 500 | **DONE** |
| Email-only web buyers wrote `mobile: ""` (indexed; 2nd one becomes a phantom customer) | **DONE** |
| Cashiers + sales staff: full customer edit incl. phone + GSTIN, from both tills | **DONE** |
| Counter sale marked DELIVERED at completion, cashier as handover (owner 09-04) -- through the existing /ready + /deliver doors; a HOME-DELIVERY counter bill is deliberately NOT stamped (parcel still on the packing desk) | **DONE** |
| Optical till: 'Order receipt (A4)' relabelled 'Tax invoice (A4)' -- serial mints at sale (owner 09-04) | **DONE** |
| Reverse split: adding a family member whose number is already a customer -- BLOCKED at all 3 doors; link-existing NOT built (cannot be truthful: POS Rx gate + clinical readers key on customer id); popup opens their own account | **DONE** |
| Member-on-two-accounts (child on both parents') -- BLOCK, one household account (owner 09-04) | **MERGED #1090** |
| Member rows minted in 3 places with drifting `relation` defaults and no `created_at` | **TODO** (one-rule-two-implementations) |
| Legacy till RETIRED: /pos redirects to /pos/new (query kept), POSLayout deleted, 8 legacy-vehicle tests re-pointed, a recorded-axis-0 bug caught on the new picker. Follow-ups: Playwright specs re-pointed (DONE); POSReceipt.tsx orphan deleted (**#1095**); PIXEL audit list still names /pos (follows redirect) | **DONE** |

Production check 2026-09-04 (read-only): `customers.mobile` unique index EXISTS; 779
customers; 0 duplicate top-level numbers; an early probe said 9 family members also exist as their own
customer, but it counted holders' own Self rows -- the committed report finds 0 real splits; 1 null + 1 empty mobile (one row away from an index collision).

## 4g. Found, not fixed (this wave) - recorded so they are not lost

| Finding | Where | Why open |
|---|---|---|
| Repair-portal DELIVERED is unbilled BY DESIGN | `repair_portal.py` | revisit before repairs carry charges at go-live |
| COD booking sends `sub_total = grand_total`, not `balance_due` (over-collects on a partly-paid COD) | `services/shiprocket.py` | **MERGED #1094**: COD sends `balance_due`; two adversarial rounds (39 probes kept). Round 2 fixed: `gate_credit_delivery` reads through the shared `order_balance_due` (a balance-less legacy row no longer reads Rs 0); a SIMULATED dry-run booking no longer 409-locks the order; the shipping card stops guessing a missing balance and offers "Book again anyway" on a 409; non-finite amounts refused. Left by its author: the duplicate-shipment lookup fails soft (no DB, no guard); a booking is not amended when the balance is paid at the counter afterwards; a COD REMITTANCE door is an owner decision (section 5), not built |
| lab-routing scan reply lacks a PAYMENT_DUE sentence (blocks correctly, says only the code) | `services/lab_routing.py` | cosmetic |
| `find_overdue` datetime vs string `expected_delivery` -> `/orders/overdue/list` empty in prod | `order_repository.py` | **MERGED #1093** — the string stays; one `iso_date_window` helper for the orders, workshop and prescription windows |
| Optical SALE-stage WhatsApp `ORDER_CONFIRMED` now seeded; owner must map the real approved template name before arming | `notification_templates.py` | owner paperwork (DLT) |
| Stale worktrees: inventory delivered; owner ruled REMOVE ALL 15 (msgv verified as a stale 08-27 snapshot, nothing unmerged) -- 15 removed, 0 failed | git worktrees | **DONE** |
| `POSReceipt.tsx` orphaned after the till retirement; `calculateGST`/`calculateIGST` in `constants/gst.ts` have zero callers | frontend | **MERGED #1095** (salvage look first): POSReceipt + ReceiptPreview (thermal; no live screen rendered it -- both surfaces' Receipt button is `window.print()`; owner ruled A4 + WhatsApp) + `utils/receiptFormat.ts` (client copy of `portal._describe_for_customer`, pin ported to `test_portal.py`) + both GST calculators DELETED. Guards in `ServerInvoiceOnly.test.tsx` (thermal shape, GST-extraction shape, export check); z-layer test re-pointed at WorkshopJobCardPrint. Left: PrintPage's "Thermal receipt" template card + `thermal_receipt` override key describe a document nothing renders; posStore's on-screen cart GST extraction is a live second copy of split_gst (display only) |
| Pune store's `store_id` is a UUID (`4dc49c44-...`) while every other store uses its code (`BV-DHN-02`, `WIZ-DHN-01`...) as the id; `store_code` is `BV-PUN-01` | `stores` collection (prod) | possibly related to the open "Pune 2 orphan units"; needs a look before any store-id join |
| PIXEL audit list still names `/pos` (follows the redirect) | `agents/implementations/pixel.py:107` | harmless |
| Member rows minted in 3 places, drifting `relation` defaults, no `created_at` | `customers.py` / `customer_service.py` | **MERGED #1090**: one `make_patient_row`, five sites, default `Other` (a silent `Self` would make the member the holder) |
| `GET /incentive/points/settings/eligibility` (and `/settings/effective`, a second leak) revealed per-person commission weight/bonus % | `points.py` | **MERGED #1091** |

## 4h. Closed by owner statement

| Item | Status |
|---|---|
| BV-ONLINE-01 GSTIN (was 'owner owes', hard-blocking web-order invoices) | **CLOSED 09-04** -- verified in prod: identical to the Pune store's (same entity, state 27); no write needed |

## 4i. Designed screens landed 2026-09-04 (all squash-merged to main)

| Screen | What changed | Status |
|---|---|---|
| **Add-a-Product** | The eleven designed tweaks: still-missing chips, section error counts, HSN inline error, touch targets, GST as text, chips with words, review-card real labels, Online un-muted, action-first inventory, header wrap + tile floor, tag label. Field order, section order, the 12 categories and the POSTed payload are unchanged. Found: the app-wide `.input-field` is **36px**, not the 44px the design claimed -- raising it is a separate decision (section 5) | **MERGED #1096** |
| **Store picker** `/select-store` | Names wrap, grouped by `store.brand`, online stores in their own section with live/dark from the storefront posture, "Where you were" + Enter, filter, digit picks. Presentation only -- same `setActiveStore` path | **MERGED #1097** |
| **Catalog review + photos** | Review queue at its own address `/catalog/review` (old `?segment=review` links forward); photo column + filter; `/catalog/missing-photos` work list; `has_photo` / `online` computed on the server from the push's own predicate; counts from the server. **Real bug fixed:** `_build_pim_doc` never projected the spine's `images` onto the catalog twin, so catalogued products were refused as "no photograph"; `scripts/backfill_twin_images.py` (dry-run by default) repairs existing twins. **Correction:** the earlier reading that photography is the go-live bottleneck came from this projection bug, not from a photo shortage; MEASURED + REPAIRED on prod 2026-09-05: twins with a usable photo 6 -> 70 (64 repaired, nothing queued, publish still manual); 37 never-pushed products now qualify to go online; only 7 products have no photo anywhere | **MERGED #1098** |
| **Eye examination** | Its own page at `/clinical/test/:entryId` (and `/clinical/test/amend/:testId`); the `EyeTestForm` modal is DELETED; a staff-only internal note kept off print, portal and wire. **Correction:** the split did NOT widen `/clinical/conversion` -- optometrists always had it and the backend deliberately serves them a revenue-stripped row; the gate is now pinned both ways by test | **MERGED #1099** |

## 4j. Merged 2026-09-05/06 (all squash-merged to main)

| PR | What changed | Status |
|---|---|---|
| **#1094** courier COD | Collects `balance_due`, not `grand_total` — detail in section 4g | **MERGED #1094** |
| **#1102** layout gate | `.ims-anim-page` entry animation no longer leaves a transform on the page wrapper (fill `both` -> `backwards`), so `fixed inset-0` overlays anchor to the viewport again; the e2e probe now treats closed `<details>` content as hidden. KNOWN_BROKEN shrank by two: `hr salary-config` (genuinely fixed) and `online-store new-smart-collection` (a probe artefact). The other too-wide rows are real width problems, unchanged | **MERGED #1102** |
| **#1103** top menu | Owner could not scroll the Stock & supply submenu on a tablet: `vh` sized the dropdown under the browser toolbar. Dropdowns now size to the visible viewport (`dvh`) and stay finger-scrollable (`touch-action: pan-y`) | **MERGED #1103** |
| **#1104** leaderboards | Everyone below ADMIN/SUPERADMIN sees their own row + rank only — one trim on `/incentive`, the `/payroll` commission leaderboard + summary, and the analytics-v2 staff leaderboard, which had **no gate at all** and leaked every colleague's revenue to any signed-in role. SALES_STAFF/CASHIER admitted to `/incentive/leaderboard`; NOT to `/hr/leaderboard` (the payroll mount is finance-only, pinned by test). Closes the section-5 leaderboard row | **MERGED #1104** |
| **#1105** Shopify push | `PUBLISH_SCOPE_MISSING` code + a plain message when the publish step is denied (the product create had already succeeded); `productOptions` sent on CREATE only (Shopify rejects it on update); failed pushes show the server's message on `/online-store/products` and `/online-store/shopify` | **MERGED #1105** |
| **#1106** POS customer panel | The four widget tiles (Family Rx, Dues, Offers & loyalty, My day) open a slide-over on the till (bottom sheet on phones) instead of navigating; "Bill for <member>" keeps the cart; money is read-only in the panel; one-tap "Book eye test" into today's clinical queue (reads the queue first, never adds twice); WhatsApp recall reminder queued via flow key `PRESCRIPTION_EXPIRY`; new `GET /incentive/points/my-day` (own data only). Owner decisions for all six behaviours recorded 09-06. **Open:** the server refuses "Book eye test" for SALES roles (section 5) | **MERGED #1106** |
| **#1107** catalog twin | Spine edits now mirror brand/category/attributes/tags/gtin onto the catalog twin the push reads — vendor, metafields and tags no longer go stale after an edit. **Measured first (prod, read-only):** the "Shopify did not get the descriptions" report is NOT a push fault — only 10 of 76 products have a description in IMS at all; the `ims.*` attribute metafields DO reach Shopify but need theme blocks to render | **MERGED #1107** |
| **#1108** push timeout | The shared axios 10 s timeout produced a FALSE "Network error" on a push that had succeeded. Push requests now get a sweep-sized timeout (180 s sweep, 60 s single entity) and an honest "still running on the server, do not press again" message | **MERGED #1108** |

## 4k. Storefront — what happened on prod (dated facts)

| Date | Event |
|---|---|
| 2026-09-05 | Catalog twins' `images` backfilled from the spine (dry-run measured first): twins with a usable photo **6 -> 70**; 37 never-pushed products now qualify; 7 have no photo anywhere; nothing queued |
| 2026-09-06 | Root cause of every failed LIVE push: the installed Shopify app "BV Inventory-1" had 23 scopes and NO `write_publications` / `read_publications` — the store had never been asked to accept the July app version's scopes (legacy install flow). Fixed by opening the app's own OAuth authorize URL in the owner's admin; owner pressed Update; 25 scopes verified; backend redeployed to drop its cached token |
| 2026-09-06 22:24 UTC | Owner authorised the assistant to press the Products "Push": **6 IMS products went LIVE on bettervision.in** (verified ACTIVE + published on Shopify), 4 refused for no photo — the first IMS-originated products on the storefront |
| 2026-09-06 | Sync-path audit, read-only: all 6 IMS-pushed products are `tracked=false` on Shopify and on sale at quantity 0; **0 Shopify orders** since the 27 Aug webhook cut-over; 11 webhook subscriptions, all pointing at the IMS receiver. The audit's five gaps are the next queue, in the owner's order (section 4b) |
| 2026-09-06 (after #1125 deployed) | Read-only probe before the authorised "Push stock" press: Shopify has **two** active locations that fulfil online orders (Better Vision Sector 4, Bokaro: ships nothing, 0 units; Gangadham Pune: the only shipping location, all **49** available units); 42 IMS products carry a Shopify id, 53 variants (47 tracked, all DENY). IMS's own ledger holds **1 unit** for those products (the Gucci) — the Ray-Ban Meta quantities were typed into Shopify by the connector and never entered in IMS. **Press NOT done**: it would set 46 variants to 0. Shopify's per-variant quantities were snapshotted first. Decision returned to the owner (section 5) |

## 4l. Merged 2026-09-06 — Waves 1-5 of the page-split plan closed (all squash-merged to main)

| PR | What changed | Status |
|---|---|---|
| **#1110** clinical queue | Sales roles (`SALES_STAFF`, `SALES_CASHIER`; not bare `CASHIER`) may add a customer to today's eye-test queue (`_QUEUE_ADD_ROLES` in `clinical.py`); Rx create/edit stays closed to them. Closes the section-5 "Book eye test for sales roles" decision | **MERGED #1110** |
| **#1111** Wave 1/2 loose ends | Last `?tab=` links -> real addresses; `pages/catalogue` folded into `pages/catalog`; stale-doc banners on the two reference lists. **Finding:** the Customers "split" had only moved the route registry — recalls was still an in-page tab | **MERGED #1111** |
| **#1112** recalls | `/customers/recalls` is a real address (same `RecallManager`; in-page branch deleted; campaigns redirect moved into the shim table; nav row "Sales floor -> Recalls"). Zero live `?tab=` navigations remain — Wave 2 genuinely complete | **MERGED #1112** |
| **#1113** Wave 3 | `QuickAddPage.tsx` 3,212 -> 229 lines; 12 block files under `pages/catalog/quickadd/`. Proof: unchanged tests, POSTed payloads identical key-for-key, rendered HTML byte-identical, line-multiset audit | **MERGED #1113** |
| **#1114** Wave 3 | `JarvisPage.tsx` 2,215 -> 103 lines (11 files); same proof standard | **MERGED #1114** |
| **#1115** Wave 3 | `WorkshopPage.tsx` 2,011 -> 114 lines (12 files); same proof standard. **Wave 3 complete** | **MERGED #1115** |
| **#1116** Wave 5 | `routers/finance.py` 6,604 lines -> a 20-file package | **MERGED #1116** |
| **#1117** Wave 5 | `routers/vendors.py` 6,850 -> 21 files | **MERGED #1117** |
| **#1118** Wave 5 | `routers/reports.py` 6,483 -> 17 files | **MERGED #1118** |
| **#1119** Wave 5 | `routers/inventory.py` 5,893 -> 23 files | **MERGED #1119** |
| **#1120** Wave 5 | `routers/orders.py` 6,649 -> 21 files — the POS/money door, split with the owner's approval (section 5) | **MERGED #1120** |
| **#1121** Wave 5 | `services/rbac_policy.py` 7,839 -> 22 files; 1,303 policy rows, registry identical | **MERGED #1121** |
| **#1122** Wave 5 | `services/shopify_push.py` 3,499 -> 13 files. **Wave 5 complete — all five waves done** | **MERGED #1122** |

Every Wave 5 split is a pure move: byte-identical API paths, an empty
whole-app OpenAPI schema diff (1,084 paths), **1,313 routes** before and after
(re-measured on main 2026-09-06), route tables in registration order, AST /
line-multiset equality, no test assertion edited (only the BUG-104 allow-list
paths re-pointed where a moved line sat), package-split tripwire tests
(`test_finance_package_split.py`, `test_inventory_package_split.py`,
`test_orders_package_split.py`, `test_shopify_push_package_split.py`), and
`__init__.py` files that re-export the flat surface and forward monkeypatch
writes into the sub-modules. The largest backend file is now
`routers/returns.py` at 3,754 lines.

## 4m. Merged 2026-09-06 (later) — the Shopify sync gaps (all squash-merged to main)

| PR | What | State |
|---|---|---|
| **#1124** | Scheduled live-product sync at 01:00 and 09:00 IST: re-pushes only products already on Shopify whose IMS copy changed; "Sync live products now" button on the sync page; SUPERADMIN Settings section (times, on/off, per-run cap); every run audited. First publish of a never-pushed product stays a human press | **MERGED #1124** |
| **#1125** | Website stock made real (gap 1): one pooled-quantity rule (on-hand across physical stores minus the safety buffer); online location auto-resolved (env pin -> stored -> the single online-fulfilling location; ambiguous or none -> refused with a code, never a guess); every push sets tracked + policy DENY + real quantities before publish; `POST /online-store/push/stock` + "Push stock" button; the all-pending press runs it too | **MERGED #1125** — the one-time press is blocked on the owner (section 5) |
| **#1126** | Delist on retire (gap 2): deleting or deactivating a product takes it off Shopify (product -> DRAFT, Shopify id kept, never deleted); hooked on the delete door and the three is-active doors; a failed delist shows a "Still live" chip; reactivation queues a republish | **MERGED #1126** |
| **#1128** | Photos and the title follow IMS (gap 3): a media diff on every live push — attach, then delete, then reorder — managing only media IMS itself attached (recorded per product), never a hand upload; a tombstone before every delete; the 250-media cap refused up front; a rename mirrors the title; the twice-daily sync now runs the stock pass too (a no-op until the online location is pinned) | **MERGED #1128** |
| **#1129** | Tags ownership (gap 4): one field for the tags IMS sends (the review box and the add-product door both land there; the dead top-level copy is no longer written); a push records the tags it wrote and on update adds or removes only those, so tags typed into Shopify admin survive; products with no record are adopted add-only | **MERGED #1129** |
| **#1132** | Silent price-push failure (audit item 7): when the variant price write fails after a product went live, the product stays queued for the next press or the twice-daily sync instead of being marked clean; the result, the audit row, the sync-page row, the toast, the sweep card and the last-run line all say "at the OLD price" | **MERGED #1132** |
| **#1130** | Missed-order catch-up (gap 5): the hourly Shopify pull now feeds any order IMS never received through the same mapper the webhook uses (deduped on the Shopify order id; window = the last successful pull or 48 h, whichever is older; capped per run); pulled orders land in the same inbox so the FAILED queue and Re-map see them; the sweep only books in live dispatch mode. Fact recorded: the webhook path itself has no dark-mode switch and books in every mode | **MERGED #1130** |

## 4n. Merged 2026-09-06 (evening) — owner rulings turned into work (all squash-merged to main)

| PR | What | State |
|---|---|---|
| **#1134** | Runbook script that adopts a live product's Shopify photos into IMS's media map by exact identity only (file name equals the IMS image id); run on prod for the 6 IMS-pushed products | **MERGED #1134** — 6 products adopted |
| **#1137** | Adoption round 2 on the owner's word: an opt-in rule for photos the connector named after the product's own Shopify id (23 Ray-Ban Meta products), and replace-then-adopt for 6 old-app products whose IMS photos were stale screenshots; reversal file written before any write | **MERGED #1137** — 29 more adopted; 35 of 42 live products now carry a map; 3 have no IMS photo; 4 stay hands-off |
| **#1135** | Multi-location PR 1 of 6: a Shopify location per shop — the mapping field on the store record with one validator (not on an online store, one shop per location), a locations read for the Organization page's store editor with an exact-name preselect, a migration script that refuses to map Pune until told | **MERGED #1135** — no push or stock behaviour changed |
| **#1136** | The migration script's final-review fixes: a duplicate location in one run is refused, an identical re-apply is reported as such, a refused write stops before the registry clean-up | **MERGED #1136** |

Prod events the same evening (dated facts, section 4k): 38 opening-stock units committed at Gangadham Pune (batch OSB-D531651BAE) through the app's own door after the owner approved the list; the 11 Large-size units wait for the variant-of rule. Two Shopify locations created through the connector on the owner's instruction (HIRAPUR-DHN, Saraidhela- WizOpt); Bokaro, Dhanbad and WizOpt mapped to their IMS stores; Pune left unmapped until the per-store writer exists and the Large units are in.

## 4o. 2026-09-07 — the catalogue reset, and the rules that survive it

The owner asked what should happen to seven listings whose photos could not be matched and to two stray stock units, and answered both with the same instruction: delete everything and re-add fresh from IMS. Pinned by follow-up questions: the entire catalogue, not just the seven; deleted outright on Shopify rather than archived, with no image export (the consequence was stated and chosen); the stock goes with the products and is re-entered later; and the eleven Large-size products are not created until the fresh catalogue exists.

| What | Measured before → after |
|---|---|
| Shopify products | 43 → **0** (41 active, 1 archived, 1 draft; deleted with IMS's own app token). The storefront answers normally and lists nothing |
| IMS products / twins / variants | 76 / 77 / 84 → **0** |
| Stock units, product images, collection rows | 41 / 24 / 9 → **0** |
| Orders, customers, stores, audit rows | 934 / 779 / 6 / 15,464 — **untouched** |

A hard delete was necessary rather than a soft one: the product-creation door blocks a duplicate SKU, so a deactivated product would have blocked the owner from re-adding the same item. A snapshot of all 311 IMS records was taken first, and an audit row records the reset as owner-authorised. What this voids: the six products that went live on 5 September, the 38 opening-stock units entered at Pune, the photo adoption on 35 products, and both long-standing orphan-record items. The code that did all of that is on main and applies to the new catalogue.

| Merged the same day | What |
|---|---|
| **#1139** | The variant-of rule: a size of a style is its own product in IMS, and it pushes only its own price, barcode and stock to the parent's listing, never the listing's own fields; retiring a size takes only that size off sale. The runbook it ships for the eleven Large smartglasses is paused, its input list having been deleted with the products |

**MERGED #1142 (2026-09-12)** — `scripts/delete_catalog_products.py`, the recorded, scoped version of this reset, for when a product must go so the SAME item can be catalogued again. It exists because the app's Delete button is a SOFT delete and the `identity_key` unique index on `products` is not filtered by `is_active`, so a soft-deleted product keeps owning its brand+model+colour+size identity and Add-a-Product 409s on the re-add — the trap this reset had to work around. Removes the twin, the spine and the variant / image / collection / stock-unit rows under BOTH ids; refuses the whole batch if any target is on Shopify, on an order line, or holds a stock unit that is not plain AVAILABLE. Dry run by default; `--all` needs `--expect N`; a JSON snapshot (the undo) is written before the first delete; one `audit_logs` row per run. NOT a replacement for the Delete button, which also takes the listing off sale on the website.

Also ruled 2026-09-07: the accountant **confirmed the GST rates as applied** (5% on frames, optical lenses and contact lenses; 18% on sunglasses, watches and accessories), closing a long-open question; the accountant **confirmed invoicing from the shipping shop's own GSTIN**, which unblocks the last multi-location piece; and the take-down button is **not** to be tested on a live listing.

## 4p. Merged 2026-09-17 .. 2026-09-27 (all squash-merged to main)

| PR | What it does |
|---|---|
| **#1141** | Multi-location PR 2 of 6: per-store website stock. Each shop's own quantity is written to its own Shopify location by ONE writer from ONE quantity rule; an unmapped shop or an unreadable stock count fails loud instead of writing a guess; a sale, a receipt, a transfer or a delist all feed the same writer. 13+ adversarial review rounds; the final three-reviewer recheck (oversell, one-rule, first push) was clean. No effect on the website yet: none of the 121 products is published. Pune stays unmapped on purpose until its opening stock is entered with the live sync off. |
| **#1145 / #1146** | A keepalive cron service to wake the sleeping backend before each scheduled job. #1146's repo-root `railway.json` broke the backend deploy (Railway applies a root config to every service). |
| **#1147** | Moved that config into `ops/keepalive/`; backend deploys healthy again. |
| **#1148** | Removed the keepalive service: measured on 2026-09-27, the backend never slept (it always holds database connections, which Railway counts as activity), and the owner turned sleep off. |

## 4q. Wave 6 of the modularization work — IN BUILD, part merged (section 4r)

What the five page-split waves left, measured 2026-09-27 (owner page "Wave Six"). Owner rulings the same day: the finance dashboard's cash-flow and budgets become links to the existing standalone pages (one door); the customers list KEEPS its in-place panel; the Payroll module's Run is the one payroll door and the older HR generator is deleted; the classic goods-receipt screen retires once Receive Goods covers a delivery challan without an invoice.

| Item | Status |
|---|---|
| A3 purchase-invoices screen split into files · A4 add-product rules moved to one package | **MERGED #1150 · #1151** |
| A6 delete dead endpoints (HR payroll generator, fake-success transfer stubs, duplicate routes) | **MERGED #1155** |
| A1 expenses page → nine addressed sections | **MERGED #1156** |
| Clinical and workshop routers become packages | **MERGED #1157 · #1158** |
| A2 finance dashboard → addressed sections · A10 QC queue gets its own address | **WIP** |
| A9 products router + product service become packages | **WIP** (queued) |
| A8 GST/money rule convergence · A11 retire the classic goods-receipt screen | **TODO** — the audit report is in; queued behind its fixes |
| A7 one timestamp rule for the backend | **TODO** — DECIDED 2026-09-28: save every time with its time zone, show it in IST |

## 4r. Merged 2026-09-27 .. 2026-09-28 (all squash-merged to main)

| PR | What it does |
|---|---|
| **#1149** | This file: #1141 and #1148 recorded. |
| **#1150** | Wave 6 A3: the 1,765-line purchase-invoices screen becomes a folder of files. Pure move; the screen is unchanged. |
| **#1151** | Wave 6 A4: the add-product rules (1,580 lines) move into one package. Pure move. |
| **#1154** | Multi-location PR 3 of 6: deletes the leftover second copies of the website stock rule and writer, so #1141's writer is the only one. |
| **#1155** | Wave 6 A6 (owner ruling 2026-09-27): deletes the old HR payroll generator (Payroll's Run is the one door), the inventory transfer stubs that reported success without doing anything, a duplicate optometrist-stats route, a shadowed audit-log summary and an unused product API. |
| **#1156** | Wave 6 A1: the expenses page becomes nine sections, each with its own address and its own role list. |
| **#1157** | Wave 6: the 3,158-line clinical router becomes a package. Byte-identical API. |
| **#1158** | Wave 6: the 3,528-line workshop router becomes a package. Byte-identical API. |

## 4s. Merged 2026-09-29 .. 2026-10-01 (all squash-merged to main)

| PR | What it does |
|---|---|
| **#1162** | Local test runs use 2 workers, so a test run no longer exhausts the office PC's memory; CI keeps its default. |
| **#1168** | CI installs mongomock, which the newer backend tests need. |
| **#1173** | Owner-approved till change: product tiles show this shop's stock ("8 in stock" / "Out of stock") and the colour, and adding an unavailable frame warns at once. The Complete-sale guard is unchanged. |
| **#1164** | Unit labels: one 100 x 15 mm label per unit on the TSC TE244, MRP printed exactly (never rounded), a per-computer print offset, and one "in the shop" rule for the ledger count and the labels dialog. |
| **#1175** | Wave 6 B21: the four admin editors (modules, permissions, discount caps, rules) get their own SUPERADMIN-only addresses with an unsaved-changes warning; every SUPERADMIN-only settings page is now refused to ADMIN by address too. Owner question: nothing reads what these editors save - connect them or remove them? |
| **#1176** | Wave 6 B14: payroll's salary sheet, advances and payslips get their own addresses inside HR; salary stays ADMIN/SUPERADMIN only. |
| **#1177** | Wave 6 B13: the demand forecast's three panels get their own addresses; the reorder panel is kept (different data and rule from Inventory > Reorders). |
| **#1178** | Wave 6 B12: loyalty's overview, tiers and rewards get their own addresses; four tabs that showed no real data are removed (checked one by one first). |

## 4t. Merged 2026-10-01 .. 2026-10-08 (all squash-merged to main)

| PR | What it does |
|---|---|
| **#1179** | Reorder levels are per shop: each shop sets its own level, and an unset level means no low-stock alert (owner ruling D12). Prod trial run of the move script: 0 products, 0 shop levels - nothing to move. |
| **#1160** | Multi-location PR 4 of 6: the nightly stock check compares each shop with its own Shopify location and raises one drift task per shop. The old pooled July task was closed on prod after a trial run. |
| **#1167** | A booked purchase bill matches its form: IGST stays IGST, the approve buttons work, a bill made from a receipt arrives with its lines filled, and every bill counts in GSTR-3B input credit. |
| **#1169** | Stock screens: receipts show on Movements, low stock names the product, opening stock ages from the day it was entered (owner ruling 2026-10-08), stock-count times show in IST, a refused receipt takes no number, and purchase tasks go to a named person. Prod trial run of its backfill: 0 rows - nothing to write. |

## 4u. Merged 2026-10-08 .. 2026-10-12 (all squash-merged to main)

| PR | What it does |
|---|---|
| **#1170** | The purchase-order form finds products by model number, shows the last price really paid to this supplier (accepted receipts first, never drafts), and a tapped quantity box takes the number typed. |
| **#1161** | Counter staff (sales, cashier, optometrist, workshop) can no longer read supplier details, purchase orders, bills or what the business owes through the API; workshop sees vendor returns without prices. |
| **#1187** | Owner-approved POS change: the till and the counter lookup find multi-word names such as Air Optix or Ray Ban Aviator; what the till found before always comes first, in the same order. The till's UPI error names the Organization screen. |

## 4v. Merged 2026-10-13 (all squash-merged to main)

| PR | What it does |
|---|---|
| **#1192** | Catalog page: sort (recently edited first by default, newest / oldest added), filters for brand, category, colour, who added it, a date range and open flags; rows per page; admins, superadmins and store managers tick products to flag them (needs revision with a note, photo redo, price check, wrong category / duplicate) or archive them; cataloguers filter by what needs redoing. |
| **#1189** | Product photos can be put in order (drag or arrows); photo 1 is the main photo, an angle guide shows Front, Angled, Side, Back, Folded, and the website follows the order. |
| **#1191** | Counter staff no longer see cost anywhere: stock counts, stock value, alerts, workshop remake cost and (owner-approved POS change) order lines, the till scan reply and the below-cost message. Workshop may log vendor returns; supplier-paid totals are admin and accounts only. |
| **#1180** | A read-only stock lookup for counter staff: this shop and other shops, no cost or supplier figures. |
| **#1185** | Transfers between two of our companies carry a valued delivery challan with both GSTINs. |
| **#1174** | Add product follows the owner rulings: the brand default decides the website, readable SKUs, reorder level starts as not set, Save + New keeps what was typed. |
| **#1193** | Pressing Publish on a design photo twice puts it up once; replacing a photo swaps it (simple rebuild of #1152, which is closed). |

## 4w. Merged 2026-10-14 .. 2026-10-18 (all squash-merged to main)

| PR | What it does |
|---|---|
| **#1171** | Items ordered outside the catalogue reach the shelf: the cataloguer is told, finishing the product releases the units, no hidden duplicates. |
| **#1166** | New unit barcodes are letters and digits only; a product barcode is only the maker's UPC/GTIN. |
| **#1181** | The till records the exact frame it scanned; sale and return keep that unit. |
| **#1196** | A new size on a live listing is never sold untracked (simple rebuild of #1172). |
| **#1197** | Reverse-charge purchase bills: the supplier is owed the taxable value, the shop pays the GST itself. |
| **#1198** | Every bill rounds to the nearest rupee with a Round off line. |
| **#1163** | Multi-location PR 5 of 6: an online order is claimed, fulfilled and invoiced by the shop Shopify assigned, from that shop's own GSTIN. |
| **#1199** | Purchase cost and prices are for accounts and admins only. |
| **#1200** | Product lists refresh across shops after a write. |
| **#1201** | Purchasing completed: shop-scoped reports, bought-without-PO receipts, extra deliveries (replaces #1183 and #1184). |
| **#1202** | Historical returns can never create stock without evidence. |
| **#1203** | Manager deadline controls, an explicit Cashier role, unused Feature Toggles removed. |
| **#1204** | Credit limits enforced on every sale and unpaid handover. |
| **#1205-#1220** | All 13 original audit findings: quarantine race (A02), GSTR-3B registration (A10), live session authority (A06), customer-return scope (A07), last price paid (A11), catalogue identity + product-import retirement (A05), task recipients, GST cards per registration (A04), RBAC document parity, finished-product category guard, rejected-goods custody (A08), landscape-tablet checks, unknown supplier cost (A09), original-bill provenance + supplier-confirmed credits (A03/A01), advances kept separate (A12), explicit event times (A13). |
| **#1221** | Collections reach Shopify (91 of 95 had failed on the sort order). |
| **#1222** | A failed Shopify notification is retried and finished, never marked done; nothing is recorded twice. |
| **#1223** | GSTR-3B counts the last moment of the month; one month rule for GST-1/3B, payroll commission, budgets, growth. |
| **#1224** | An older stock count can never overwrite a newer one on Shopify. |
| **#1225** | A first publish is refused until every size has a price (the message names the SKUs). |
| **#1228** | The IMS 2.0 SYNC page inside Shopify admin shows a welcome and the connection status (Shopify-signed loads only). |

## 4z. Merged 2026-10-18 (late) .. 2026-10-19 (all squash-merged to main)

| PR | What it does |
|---|---|
| **#1226** | One product per model in IMS: colours and sizes of the same model number are linked to the first-catalogued product. Applied on prod 2026-10-18 (96 models, 149 linked, 52 items took the lead's shape/gender; reversible by run id). |
| **#1227** | No duplicate products on Shopify after a lost reply; a catalogue save never wipes the Shopify link; Shopify API pinned to 2026-01; every product carries a unique IMS id on Shopify (set up on prod). |
| **#1232** | Visibility sweep: the Tally journal export, bill scans and journal entries for accounts only; cost hidden on more screens; other shops' frames hidden from scans. |
| **#1231** | Tax invoice numbers fit GST's 16 characters: BOK01/2627/00001, one series per shop per year, never two numbers for one bill, historical orders never numbered. |
| **#1233** | One website listing per model with colour and size choices, model-level title / description / SEO, per-colour photos and stock; unfinished colours wait; MRP required. Nothing changed on the site at merge (0 of 25 live listings queued). |
| **#1230** | Supplier payments pick their bill; advances applied to bills later; a retried payment is never recorded twice; closed months stay closed; petty cash paid out once. |
| **#1235** | Who sees which shop: an accountant with a shop sees only it on the Tally sales JV, B2B invoice book, GST cross-check and bank statements; a staff login with no shop sees no orders or customer lists; an admin with no shop picked reads all shops (screens show it once the 'All stores' picker ships); Pune's PO and receipt numbers say BV-PUN-01. |
| **#1237** | The brand is no longer doubled in titles ('Carrera 3044/S'). A clean-up script for the 83 existing doubled names is ready (dry run shown to the owner, not applied). |
| **#1234** | Supplier returns name every piece and take it off sale at once, ship it to the supplier on Ship and put it back on Cancel; a damaged customer return sets that piece aside with a 7-day task; stock-count write-offs list each piece. |

In review (not merged): **#1236** till discount approver from the manager's own login + a manager-approval step for over-limit discounts (waits on owner question 1); **#1238** GST input credit (dashboard GST table from the GSTR-3B rule per GSTIN, unclaimed GST into stock cost, section 17(5) tick); **#1239** GST document numbers (transfer tax invoices BOK02/T2627/0001 with an e-way data page, debit and credit notes in the same format; waits on owner question 4).

**Next GST follow-up (before the first GSTR-1 with a move between GSTINs):** file transfers in the month they ship, not the month they are completed; flag a transfer invoice missing from every return; a return paid partly in store credit must file its whole tax reversal; GSTR-3B always covers every shop on a GSTIN.

## 4aa. Merged 2026-10-19 (late) .. 2026-10-23 (all squash-merged to main)

| PR | What it does |
|---|---|
| **#1236** | The till's discount approver is decided by the server; over-limit discounts go through a manager's approval |
| **#1238** | One input-credit rule: GST table per GSTIN, unclaimed GST into stock cost, section 17(5) tick, supplier note month |
| **#1239** | Transfer tax invoices, debit and credit notes fit GST's 16 characters |
| **#1241** | Website publish prep: cost-exempt colours, collections fill, brand collections |
| **#1242** | Top-menu dropdowns scroll again |
| **#1243** | A frame's Model Name is what staff typed, never a copy of the Model Number |
| **#1244** | Website page type per product (Standard / Pre-order / Preview only); switched ON on prod 10-23 |
| **#1245** | Brand Master filter bar + SUPERADMIN "Never sell online" block |
| **#1246** | ZEISS April 2026 lens master + dry-run import (applied on prod, 735 lines) |
| **#1247** | Every margin compares cost and price both without GST |
| **#1248**, **#1262** | The website line on Add/Edit product (#1248) and on the Catalogue list (#1262) no longer say "could not be read" |
| **#1249**, **#1250** | Two tests no longer fail around IST midnight |
| **#1251** | The scheduled sync (01:00 / 09:00 IST) first-publishes up to 100 queued products per run (owner 10-20; about 15 Dec switch to all-at-once) |
| **#1252**, **#1254** | "Never sell online" takes the brand's live listings down, with its own per-run cap; a failed read is reported |
| **#1253** | GST batch 1: GSTR-1 rows correct (fail loudly, B2CL inter-state only, per-rate items), credit-note place of supply from the sale, an invoiced or shipped VOID is still a sale |
| **#1255**, **#1256** | Weight: one value on the product, shown on every screen, sent to Shopify in grams |
| **#1257** | Listing quality before auto-publish: no doubled brand, alt text on every photo, no "100% genuine" promise, collection SEO, New Arrivals tags, mm on sizes, "polarized" only when true |
| **#1258** | Re-queue writers stamp a fresh revision, so a queued edit is never lost |
| **#1259** | **Stock redesign S2 - one Receive screen** at /purchase/receive: order receipt, bought without PO, delivery challan, extra goods, cancelled orders; per-line "I counted this"; damaged piece inline -> Quarantine + draft debit note; shelf code; contact-lens batch + expiry; labels after receiving; stuck-receipt recovery; no prices for the manager (the accountant types bill prices); /purchase/grn forwards. 3 review rounds |

## 4ab. Stock & inventory redesign (owner approved 2026-10-23)

Design: the 14-board canvas "IMS Stock & Inventory Redesign" (claude.ai artifact BEBdK16Xgjyv41LbCogZi2). It follows the page-split rules: one top menu, every screen its own address, no hidden tabs, one door per job, old addresses forward. The Stock & supply menu goes from 19 links to 10 (Catalogue stays inside it as one link with its own section links; stock reports move to Analysis; Workshop to Operations; Online Stock to Growth; Opening Stock stays).

| Phase | Work | State |
|---|---|---|
| S1 | Blind scan count (each scan +1, typed qty per style incl. 0, nothing expected shown until Finish), count result, write-off of the EXACT unscanned pieces, every loss door admin-only on the server (incl. lens reserve/commit/bulk import, mark-sold needs a real order line), zone counts never write off, 'None found' styles write-off-able by an admin only at >= 90% coverage (SUPERADMIN setting) | **DONE #1260** (merged + deployed 2026-10-24; 4 review rounds) |
| S2 | One Receive screen | **DONE #1259** |
| S2b | Server side of receiving: "credit" closes the order line short (D9); damaged-piece photo (accounts open it from the supplier-return draft: "Damage photo"); challan photo REQUIRED (owner 10-23); red DO NOT SHELVE label from receive (one label door shared with the Quarantine queue); two small preview fixes | draft PR #1299 (review fixes in). Follow-ups, not in #1299: (a) a credit chosen LATER on a pre-S2 return draft does not close the order line (the Receive screen now refuses a damaged line without a choice); (b) a "Damage photo" link on the receipt detail too; (c) a photo field on the Quarantine queue's manual "Mark quarantine" (scope to confirm) |
| S3 | Transfers tracked piece by piece: scan out / scan in, missing on the way, ship marks the packed barcodes, GST supply dated at ship, inter-GSTIN valued challan | TODO |
| S4 | One average cost per product (re-averaged only when the accountant accepts a bill) and one stock-value formula; on-hold bills never rewrite cost/MRP; lens-lab challan bills no longer double-counted | TODO (money review) |
| S5 | One Reorder list (shop level if set, else 30-day sales); Buy Desk, Replenishment and Low stock forward to it | TODO |
| S6 | Menu 19 -> 10, Find a product (ledger + lookup merged), Catalogue section links, Approvals carries write-off and missing-on-the-way requests | TODO |
| S7 | Stock today landing page | TODO |

Owner answers 2026-10-23: one average cost; the ACCOUNTANT types bill prices; count blind until Finish; reorder = shop level, else 30-day sales; challan photo required; cash-memo bill number optional; "None found" styles: an admin may write them off (whole-shop or whole-category counts only).

## 4ac. Fable 5.1 workflow review (2026-10-23) and the fixes

8 workflows reviewed by Claude Fable 5.1 in one batch ($19.64 of the $200 API credit that expires 31 Oct); the 43 critical/high leads were verified against the code: **34 confirmed, 8 partly, 1 false** (1 critical and 20 high after checking).

| Fix | Problems | State |
|---|---|---|
| M1 | Confirming an online refund review on a never-paid (COD) order minted store credit; credit is now capped at money collected minus everything already given back (Shopify refunds incl. money-only, counter returns, hand credits), one refund at a time per order, untied hand credit sends the refund to a person | **DONE #1261** (merged + deployed 2026-10-24; 5 review rounds; prod audit after deploy: 0 rows) |
| POS 1 | "Paid by loyalty" without points + no role gate on payments; return store credit to another customer; refunds of any size by a cashier (**owner 10-23: above Rs 2,000 needs a manager**, fail closed, split refunds counted together); a paid/invoiced order cancelled with no refund or credit note (owner answers: whole-invoice credit note + refund only the advance; non-cash advances back as store credit; Refund-advance button Accounts/Admin/Superadmin only; accountants may take payments) | **PR #1264** (draft). Six money-review rounds so far, each fix tested first: GST reversed twice on a cancelled invoiced bill; a refunded order still deliverable (incl. via workshop/shipping); cancelled orders leaking into sales; crash recovery; refund after handover; a stalled press paying an advance twice; one store-credit balance rule. Round 7 review running. After merge: a dry-run then backed-up fill of the missing store-credit field (768 of 779 customers, all balance 0) |
| POS 2 | Tax invoice number minted at the moment of sale (not when the PDF is opened); HSN and GST class from the product (also fixes website orders' GST guess) | TODO |
| POS 3 | Made-to-order lens price doubled at the till | TODO |
| POS 4 | Oversell not blocked for never-stocked products and the last-unit race | TODO (after S1) |
| Website 1 | Invoice total checked against what Shopify charged (discount codes, shipping); shipping address stored on the order; no fake "shipped" from a simulated courier booking | TODO |
| Website 2 | Bulk price changes reach bettervision.in; archived products cannot be re-listed | TODO |
| Inside S1/S3/S4 | Count scan "1" instead of "+1", lens stock lowered without a reason, transfer GST month, a random frame marked shipped, a partial transfer strands pieces, an on-hold bill rewrites cost, challan double count | in those phases |
| Returns (lower) | A failed return save swallowed after its side effects; a credit-note number burned on failure; a bank deposit missing from the shared till session (false SHORT) | TODO |

## 4ad. Small screen fixes queued (owner asks 2026-10-23)

- The shared error display: 18 screens read the server's refusal from the wrong place, so a "not allowed" shows as a red error (Durga's "Couldn't check Shopify sync status", "Failed to create GRN").
- Out-of-date "dry-run unless armed" texts on the Shopify pages; photos still processing shown as amber "waiting", not red crosses; a size variant's "push the parent" message in plain words.
- Brand form: ticking "Never sell online" greys out and unticks "Sync to Shopify by default".
- Catalogue list: "Edited by" / "Added" on each colour and size row, not only the model.
- Website "Type: Sunglass" -> "Sunglasses".
- **Colour names**: the 630 drafted names (sheet sent to the owner, $8.58) are confirmed by the cataloguer, then loaded into IMS and synced; then a permanent "Suggested colour name" on each new product (from its own photo, Accept / Change on the edit screen).
- Catalogue data for the cataloguer: two products entered twice (Carrera 3001/S Havana 54, Flag Lab 16 KY21V), a Polaroid PLD 2141/S colour without a price, 163 colours whose catalogue lens/frame colour contradicts the photo (flagged in the sheet).

## 4ae. CA answers (owner on call, 2026-10-23)

The 19 questions in `CA_Questions_GST.md` (project folder) were answered; the answers table is at the end of that file. What changes in IMS:

| Answer | Effect |
|---|---|
| Stock moved BVOPL Jharkhand -> M/s Better Vision, Bokaro (different firm): **tax invoice at cost** | IMS prints a valued delivery challan today; becomes a tax invoice at cost (S3), before any such move |
| BVOPL Maharashtra <-> Jharkhand: **tax invoice at cost**; reported in the **dispatch month**; short delivery = **value received + credit note** | S3 |
| B2CL above **Rs 1 lakh**; Table 12 split B2B/B2C with quantities; Table 13 mandatory | remaining GST batches |
| Hearing aids exempt; **hearing-aid spares and repair 18%**; eye test exempt (3.1(c)) | check IMS rates |
| Tally: website receipts to a **Shopify/Razorpay receivable** ledger (not Suspense); a **Round Off** ledger in each company | Tally export |
| E-invoice (BVOPL): B2B invoices, B2B credit/debit notes, MH-JH transfer invoices | step A2 |
| Confirmed as built/planned: counter sale to an out-of-state walk-in = CGST+SGST; COD cancelled = credit note; website order invoiced by the shipping shop's GSTIN; store-credit GST treatment; no reverse charge on unregistered optical purchases; unclaimed GST into stock cost; ITC on GSTR-2B | none |
| Section 17(5): owner wrote "all can be there, non blocked" - read as: every expense type can carry the blocked tick, default not blocked | to confirm with the owner before building |

## 4af. How staff really use IMS - Fable review (in progress, 2026-10-23)

Owner ask: "without removing anything, make things simpler, faster and easier". A read-only digest of every write request real staff made (from the audit log; names replaced by role labels) plus the code of the screens they use went to Claude Fable 5.1 (batch, about $3). The main cataloguer's 20 working days in October show: the SKU preview called 4,185 times (about 9 per product, mostly quick repeats), photos uploaded one at a time (about 5 per product), the same product saved 4+ times in a row 427 times. Every Fable suggestion is checked against the code before it reaches the owner as a ranked list to approve.

**Result (verified against the code, 2026-10-23):** of 28 proposals, 8 kept, 7 kept with a change, 12 dropped (false or already built). The owner approved:
- **PR1 Edit flow - DONE #1267** (merged + deployed 2026-10-24): "Save & next" through the filtered catalogue list with Skip and "Item N of M"; the edit page loads the product once; the SKU check runs only when an SKU field changes; plain Save lands on the Catalogue.
- **PR3 Form helpers - DONE #1269** (merged + deployed 2026-10-24): a "website still needs" line (never cost), copy specs from the last product, AI description drafted when empty, soft warnings that never block a save, publish counter text.
- **PR2 Photos: not now** (owner).

Fable credit used so far: about $31 of $200 (code review $19.64, colour names $8.58, this review about $3).

## 4ag. Website parity recheck (2026-10-24, read-only on prod)

The diagnostics card reads "469 / 633". That compares **website pages** (one page holds every colour and size of a model) with **IMS products** (one per colour + size), so it looks far worse than it is. Measured per colour/size: **610 of 633 are on the website; 23 are not.**

| Not on the website | Count | Why | Fix |
|---|---|---|---|
| Ray-Ban RB3025 (every colour not yet live) | 15 | One colour, SGRAYBANRB3025004/R6, has **no lens size**, so Shopify refuses the whole RB3025 page every run (OPTION_MISSING) | Cataloguer fills the size; the next sync publishes all 15 |
| Carrera Flag Lab 16 | 5 | Two colours were entered twice (old SKU style with the brand inside the model number), so two choices clash (OPTION_CLASH) | Duplicate fix in build (below) |
| Carrera 3001/S 086HA | 1 | Second copy of a colour already live | Same duplicate fix archives the copy that is not live |
| Archived on purpose | 2 | Idee SB2382 C4, Maui Jim S3357 | none |

Queued from this: the card should count colours/sizes ("610 of 633 online, 469 pages"); one colour with a missing field should not hold back its whole family (push the rest, keep refusing the broken one); SGRAYBANRB3025004004 has the colour inside its model number and sits on its own page instead of the RB3025 page (cataloguer to check).

**Duplicate fix - PR #1271 (draft, review BLOCKED 2026-10-24, being fixed):** the duplicate check ignores the brand typed at the start of the model number ("CARRERA 3001/S" = "3001/S"; brand only - stripping sub-brands too would have refused real frames like BOSS ORANGE 0123 vs BOSS 0123 and broken the Ray-Ban Meta size variants); a one-off script cleans the 7 affected model numbers and archives one copy of each duplicate pair, choosing by rule (live on the website > has stock or orders > more photos > newer), never a live one and never an archived one. The prod dry run (read-only) picked correctly: Carrera 3001/S keeps the live copy; Flag Lab 16 White/Black keep the newer copies (Black gets size 99). Order: re-review -> merge -> deploy -> apply.

**Delete archived products (owner ask 2026-10-24, in build, not merged):** today Delete only archives. New: a Superadmin-only "Delete permanently" on products that are already archived. Refused, and the product stays archived, if it has any sales, stock, purchase, receipt, transfer, return or count history, is still on the website, or leads a colour family. A full backup is written before anything is removed.

**Unilens bridge = "NA" (owner 2026-10-24, applied):** the "01" printed on Unilens frames is a placeholder, not a bridge size. All 25 Unilens products (lens size 99) and Carrera CA PRW 2/S/IN now read bridge "NA" (23 changed, backed up; 14 live listings re-queued). Cataloguers enter NA from now on.

**Section 17(5) (owner: "check yourself"):** default **blocked** for food and drinks, motor vehicles, gifts and free samples, club / gym / personal, shop construction and fit-out, staff holiday travel; **claimable** for rent, utilities, supplies, marketing, repairs, stock and business travel. Gap found: written-off stock needs its input credit reversed (17(5)(h)); not built, added to the GST batch.

## 4ah. 2026-10-29 - work resumed after the owner's pause (24-28 Oct)

**Website today (read-only, prod):** the missing lens size on SGRAYBANRB3025004/R6 was filled on 10-26, so the whole Ray-Ban RB3025 family went online: **628 of 633 colours/sizes are on the website** (610 on 10-24). Every scheduled sync since has run with 0 failures. The last 5: 2 archived on purpose, 3 Carrera duplicates waiting for PR #1271.

**Codex built the five 10-24 prompts as PRs (none merged; each under independent review with every serious finding re-checked):** #1274 server error messages kept on ~18 screens + 3 test fixes; #1275 sync diagnostics count colours/sizes and compare content (not timestamps) for drift; #1276 one incomplete colour no longer holds back its model's website page; #1277 "photos still attaching" shown amber, not as a failure; #1278 brand form contradiction, "Edited by" per colour row, "Sunglasses" label, clearer photo counter.

**Owner asks 2026-10-29:**

| Ask | What it really is | State |
|---|---|---|
| "Collections and menus need to be made and pushed now" + "a self-maintaining menu system in IMS that generates menus directly from collections that have products, rather than static pushes" | The live main menu was hand-edited on Shopify: Shop by Brand lists only Carrera, By Shape 4 of 13 shapes, By Price 2 of 4 bands; IMS holds no menu at all. The 49 collections exist (22 brands, 13 shapes, gender, price, polarized, new arrivals, eyeglasses) but IMS's own product-per-collection count reads 0 for all of them | **In build** (branch feat/self-maintaining-menus): the approved Gem-style menu kept in IMS as a template; items generated from collections that have products online (empty ones hidden, they reappear when stocked); regenerated after every scheduled sync and pushed only when it changed; adopts the live main menu (backed up first); footer menus untouched; the Menus page shows the template, a live preview with counts and what is hidden. One rule for "which products are in a collection", fixing the broken count |
| /online-store/images "not required - editor and cataloguer work from the same catalog page" | A separate photo-approval page | Queued: remove the page and its menu link (old address forwards to the catalogue) |
| Store Health "Fixes needed" (ignore barcode) | "162 not mapped to Shopify" = the same lead-only counting error as 10-24 (really 5); "529 not in any collection" = the broken collection count above; "2 missing an image" = real | Queued after #1275 (one counting rule for both pages); the 2 products to be named for the cataloguers |

**Codex PR reviews (2026-10-29; each serious finding re-checked by a second agent):**

| PR | Verdict | What happens next |
|---|---|---|
| #1278 catalogue small fixes | Approved | **DONE #1278** (merged + deployed 2026-10-29, build d2f346a1): Never-sell-online and Sync-to-website can no longer both be on; "Edited by" per colour row; "Sunglasses" label; clearer photo counter |
| #1277 photos still attaching | Approved after fixes | **DONE #1277** (merged + deployed 2026-10-29): photos Shopify is still processing show amber and finish on the next sync; real photo failures stay red with Shopify's reason; escalation counts sync runs, not presses |
| #1276 one bad colour does not block the page | Approved after fixes; rebased onto #1277 and the resolution re-checked | **DONE #1276** (merged + deployed 2026-10-29, build 394b8966): a model's complete colours go online and an incomplete one is left out with a per-SKU warning (it stays queued); a live listing is no longer re-queued every run |
| #1274 error messages | Approved after fixes; owner OK'd the wording change on POS screens (2026-10-29) | **DONE #1274** (merged 2026-10-31, 80bc042d): staff see the server's real reason on ~95 screens through one shared helper, never raw developer text; 3 tests made reliable |
| #1275 colour counts + content drift | Approved after fixes | **DONE #1275** (merged + deployed 2026-10-29): sync diagnostics count each colour/size (one shared 'is this colour online' rule, also used by Store Health, archived products left out); drift compares the content IMS owns, never Shopify's timestamp |

Fixed one PR at a time (this PC runs short of memory with parallel test runs), each re-reviewed before merge. Then one small PR: remove the Images page (old address forwards to the catalogue) and take archived products out of the publish queue (Maui Jim's red line on every sync).

Codex PRs: all five closed out - #1278, #1277, #1275, #1276 (2026-10-29) and #1274 (2026-10-31) merged and live. **DONE #1286** (merged 2026-10-31, c6a0a871): the Online Store > Images page is retired (its old address opens the Catalog; a design manager lands on Online Store); the go-live push no longer sends photos from the retired approval queue (product photos still go with each product); archiving takes a product out of the website publish queue and the sync skips archived products quietly (Maui Jim's red line every run ends).

**Duplicate fix #1271:** re-review 2026-10-29 BLOCKED again (11 confirmed): the clean-up script could guess a wrong eye size when the same frame exists in another size; restoring two archived copies could bring back a duplicate; editing an old-style product skipped the duplicate check. Prod dry run changed: the two newer Flag Lab 16 copies were archived by hand, so the older copies are live; the script will also clean live copies whose twin was archived by hand. Being fixed.

**In build since resume (2026-10-29, latest first):**

| PR | Latest review | State |
|---|---|---|
| Night test clock #1306 | Found 2026-11-01 03:00 IST: main's backend checks were red. The same two GST tests failed on main before tonight's merges too. Cause: the tests took 'today' and 'this month' from the UTC clock while the reports use IST days. Proof: the file passes with the clock frozen at a daytime hour and on the last night of a month, a year and a financial year; the old file fails at night. A full-suite run inside the night window found no other affected test | **DONE #1306** (merged 2026-11-01, 3189dc34; test-only) |
| POS fix 1 #1264 | Money review 9 led to one credit-posting design (states, at-most-once balance move, a 10-minute sweeper). Money review 10 (2026-10-31): the clean-up scripts reviewer APPROVES (running them before the merge is safe; the sweeper's first live run changes nothing); three reviewers BLOCK on one shared problem - when a credit save comes back 'unclear', staff were told 'nothing recorded, try again', so a redo could give double credit. Fix: say 'may already be recorded - do not redo, it settles by itself', keep the return held, a re-press finishes the same credit line. Plus should-fix items (website refunds count in-progress credits, the section 34 deadline on the website refund door, goodwill credits are not GST credit notes, screen messages). **Round 10 fixed (2026-11-01, b5f9702d, caught up with #1294):** 'could not be confirmed - do NOT redo, it settles by itself in 10 minutes'; the returned pieces and the refund hold stay, so a redo is refused; the 10-minute sweeper settles the credit either way; one credit note for a part-cash part-credit return; goodwill credits never GST credit notes; the Rs 2,000 rule can only be made stricter (Superadmin); fix forward only, never roll back past this deploy. 22 new tests (backend/tests/test_pos_money_doors_r10.py); undoing each fix turns them red for 23 of 24 backend fixes and all 3 screen fixes; full checks 20,664 passed, the 2 failures being the night clock tests fixed in #1306 **Money review 11 (2026-11-01): BLOCK, 20 confirmed** - a second counter return on a multi-piece line could pay twice; the shared app client re-sent failed saves 3 times; the superadmin credit note and the manual credit door were not resumable; section 34 not applied on the live website refund door; a credit that landed without a return record raised no task; plus 11 should-fix items. The deploy-scripts reviewer APPROVED and wrote the exact production run order **Round 11 fixed** (c6e6a352, all checks green). **Money review 12: BLOCK, 15 distinct problems** - the lost-answer re-press class: Returns screen key, manual credit key tied to the amount, voided superadmin note re-issue, two crash windows, till payment-leg keys, error wording; plus a latent double pay on pre-#1264 ledger rows **Round 12 fixed** (99ec2fee, 20,831 tests green). **Money review 13: BLOCK, 21 confirmed** - mostly the new Complete-record step (no screen, no shop check, dated 'now', unpaid cash part dropped), dead-end stuck holds, and the till retry deducting store credit/vouchers twice | round-13 fixes in build; then money review 14; then the two clean-up scripts on prod BEFORE the merge |
| Delete archived products #1273 | Three review rounds + a final check + a rebase check | **DONE #1273** (merged + deployed 2026-10-31, build df6038f7): Superadmin-only "Delete permanently" in the Catalog's Archived view; anything with sales/stock/purchase history stays archived; a website-linked product is first removed from Shopify after IMS reads Shopify itself and confirms everything there is this one product's SKU (a colour inside a model's page removes only that colour); a full backup is written first and scripts/restore_deleted_product.py --backup-id puts a deleted product back. Follow-up **DONE #1296** (merged 2026-10-31): if reviving a discarded draft is interrupted halfway, it is put back so the next order revives it cleanly |
| Duplicate fix #1271 | **DONE #1271** (merged + deployed 2026-10-29, build 84ad7908; 5 review rounds + a final check). On prod (run 20261029103835-20e33b, applied just before the merge): the extra Carrera 3001/S Havana copy archived; 7 "CARRERA ..." model numbers cleaned and re-keyed, incl. the two live Flag Lab 16 rows whose hand-archived twins gave up their duplicate keys; check after deploy: 0 left. The 3 live website listings pick up the cleaned model tag on their next push. A brand glued to the model ("RAYBANRB2140") is not folded - prod has none | follow-up PR: Restore's barcode check also sees the public barcode field |
| Restore barcode check #1284 (follow-up to #1271) | Two review rounds + two final checks | **DONE #1284** (merged 2026-10-31, 4fded152): Restore refuses only when a LIVE product already holds the same public barcode (checked on the product and its catalogue copy, with the website push's own barcode rule, so junk and internal 20-29 codes never block); the dry run and the real restore give the same answer |
| Self-maintaining menu #1280 | Review round 1 (three reviewers) BLOCKED on 5: an unreadable catalogue could have been read as 'no products' and pushed a menu with only About Us; a collection that cannot be checked could stop every product push; plus untested push paths. Fix round was interrupted on 10-29 (agent access outage) | **DONE #1280** (merged 2026-11-01, 8943c605; 10 review rounds). Template seeded on prod (nothing pushed); first push = owner's press after the Menus page deploys |

**Wave 2 started 2026-10-31** (owner: "run more tasks in parallel", memory ceiling raised to 94%). Seven builds in four lanes, each built, reviewed by two independent reviewers, every serious finding re-checked, fixed and re-reviewed before it can merge (four merged so far, listed under the table):

| Lane | Work | Owner ask / source |
|---|---|---|
| 1 | POS 3: a made-to-order lens priced twice at the till; then POS 4: selling more than is in stock refused (never-stocked products, last unit sold twice) | POS fixes approved 2026-10-23; oversell BLOCKS (2026-08-25) |
| 2 | Website 1: online orders match what Shopify charged (discount codes, shipping), keep the shipping address, never marked "shipped" by a simulated courier booking; then Website 2: bulk price changes reach bettervision.in, archived products can never be re-listed | 2026-10-23 review |
| 3 | Review follow-ups from #1274-#1277/#1284 and plain wording on the Shopify pages; then GST: section 17(5) default blocked expense types + input credit reversed on written-off stock (GSTR-3B 4(B)(1)) | 4ad; owner "check yourself" on 17(5) |
| 4 | Suggested colour name from each product's own photo, with Accept / Change | owner 2026-10-23 |

**Wave 2 merged (2026-11-01 02:10, live on build a5997374):**

| PR | What changed |
|---|---|
| **DONE #1290** (e64f3e1c) | POS 3: the Add Lens window asks every price per lens; the pair total and the bill now agree (example: Rs 3,700 a lens = Rs 7,400 the pair; before, Rs 8,800 was billed) |
| **DONE #1292** (cfbc71b9) | Suggested colour name on Add product, Edit and Review: Suggest reads photo 1, Accept or Change puts it in the field, nothing is saved until Save; capped at 30 suggestions per 10 minutes per person; cost of each call logged |
| **DONE #1295** (65e664d0) | Follow-ups: a photo Shopify refuses shows red with Shopify's reason (not 'still attaching'); a press held back for a catalogue reason is counted as its own line; the sync screen gives the same verdicts as the push; a barcode stored as a number gets its leading zero back; the Shopify pages say writes are live (not 'dry run') |
| **DONE #1294** (a5997374) | Website 1: the online invoice equals what Shopify charged (discount codes, a shipping line), any difference raises a task for an admin, the shipping address is kept for the courier and the invoice, a simulated courier booking never tells Shopify 'shipped'. Orders already in IMS keep their old totals (no backfill) |
| **DONE #1300** (1cee8100) | POS 4: the till refuses to sell more than is on the shelf. A product the shop never held can no longer be billed from an empty shelf, and two tills can no longer both sell the last piece (each piece is claimed before the bill is saved). Services, eye tests, made-to-order lenses and lens-grid lines are never blocked; a cancelled sale puts the piece back |
| **DONE #1301** (c7da473e) | Website 2: a bulk price change now reaches bettervision.in (from the next sync) and an archived product can never be re-listed |

Still in wave 2: GST 17(5) #1302 (blocked input credit on section 17(5) expense types; credit reversed on written-off stock) in review.

**Wave 3 started 2026-10-31: the stock & inventory redesign the owner approved on 2026-10-23** (built from the 14-board canvas; S1 #1260 and S2 #1259 already merged). Three lanes, each phase built, reviewed by two independent reviewers, findings re-checked, fixed and re-reviewed before merge (none merged yet):

| Lane | First | Then |
|---|---|---|
| A | S4: one average cost per product, re-averaged when a bill is accepted; one stock-value formula; cost history on the product page; an on-hold bill no longer rewrites cost; a delivery-challan bill no longer counted twice | S3: transfers tracked piece by piece (ask, area-manager approval, scan out, courier, scan in, damaged to quarantine, "missing on the way"); between different GSTINs a TAX INVOICE AT COST in the dispatch month, short delivery = received + credit note (CA answers) |
| B | S2b: challan photo required at receipt; cash-memo bill number optional (photo still required); a supplier credit closes a PO line short; damaged-piece photo; red DO NOT SHELVE label | S5: one Reorder list (shop minimum level if set, else 30-day sales); Buy Desk, Replenishment and Low stock forward to it |
| C | S7: "Stock today" page for each shop | S6: the Stock & supply menu cut from about 19 links to about 10; stock reports move to Analysis (same addresses); old addresses keep working |

**Wave 3 paused 2026-10-31 20:40** (the PC's memory reached 96.9%, owner ceiling 94%) and **resumed 20:48** with at most two lanes at a time (A and B first, then C). **DONE #1299** (merged 2026-11-01, 48001dfa): S2b receive follow-ups - challan photo required and kept, cash memo number optional with photo, a 'Credit' damaged piece closes the order line short, a damaged-piece photo, red DO NOT SHELVE labels from Receive. Draft PRs: S7 Stock today #1297 (built), S2b receive follow-ups #1299 (review found 2 blocking + several should-fix items, being re-checked), S4 one average cost #1303 (opened, checks running). **DONE #1309** (merged 2026-11-01, 2d06ed38): S5 one Reorder list.

POS 2 (invoice number at the moment of sale, HSN from the product) waits for POS fix 1 #1264 to merge (same invoice code). Returns (lower) waits for #1264 too.

**Deploy rule from 2026-10-29: Railway now deploys main automatically on every merge.** Any live-data step that must run before new code (e.g. #1264's two clean-up scripts) runs before the merge, from the PR's own copy.

## 4x. The agreed order of work (owner 2026-10-18)

| Step | Work | State |
|---|---|---|
| **A** | In-flight fixes: one product per model in IMS (**#1226**, draft); no duplicate products on Shopify + catalogue saves never wipe the Shopify link (**#1227**, draft); tax invoice numbers fit GST's 16 characters, format **BOK01/2627/00001**; supplier payments pick their bill + advances applied later + a payment can never apply twice; shop scope on the category P&L, cost writes for accounts only, bill scans and journal entries for accounts only, other shops' frames hidden from scans; stock (supplier returns name their frames and leave stock when shipped, damaged returns set aside, stock-count write-offs per frame); till discount approver from the manager's own login + an over-limit approval step; "who sees which shop" (accountant own shop on four finance views, no-shop login sees no sales at the till, admin with no shop picked sees all shops everywhere) | **DONE** (#1226 #1227 #1230 #1231 #1232 #1234 #1235, section 4z; #1236 #1238 #1239, section 4aa) |
| **A2** | E-invoicing for Better Vision Opticals Pvt Ltd (over Rs 5 crore): an IRN and signed QR on its B2B tax invoices, B2B credit/debit notes and its transfer tax invoices between the Maharashtra and Jharkhand GSTINs, before the first such document is issued. Route to choose: IMS prepares the e-invoice file for upload on the government portal and stores the IRN/QR it returns, or a direct link through a GST provider | **new 2026-10-19 - design first** |
| **B1-5** | Products live on bettervision.in: grouped website listings (one per model with colour/size choices, model-level title, description and SEO, all photos, new colours added to the 25 live listings); set up the unique product id on Shopify; move the 601 products into model families (dry run shown to the owner first); publish ~451 listings, then collections, then rebuild the menu; one real test order end to end | listings code **merged #1233**; auto first-publish **#1251** (100 per scheduled run; 72 published, 425 waiting on 10-23); collections pushed; next: menu rebuild once products are live, one real test order, website stock |
| **C (Batch 2)** | Loyalty redemption and its record saved together (also adjustments, expiry, referrals); promotional messages re-check opt-out and quiet hours just before sending | approved |
| **B6-8** | Look and buying: theme check, mock-ups, home / collection / product pages, checkout; traffic: Google Shopping, Facebook and Instagram shop, SEO, WhatsApp order updates; a daily visitors / carts / orders dashboard | after C |
| **Rest of C** | Shopify housekeeping (5 unused notifications after a safety check, remove the dead old key, prepare the next API version); recovery tools for rare stuck states; audit leftovers (section 4y) | later |
| **F** | Features planned in June but never built - owner picks: consignment, spare parts, cannibalisation report, ageing auto-clearance, network-restricted attendance, own-use ledger, B2B bulk orders, lookbooks, profile merge, benefits-expiry campaign; screens missing for endless aisle, repair portal, bank reconciliation, rebate tracker, landed cost | owner to choose |
| **G** | This file kept current; Wave 6 tidy-up; ~40 stale work folders; 994 advisory type warnings | last |

**Owner and team tasks (not code):** stock for the best sellers through purchasing (nothing sells online until it is in); the accountant's "Round Off" ledger in each Tally company and one real supplier return -> credit note -> payment -> Tally import; label printer and scanner on a tablet in a shop; a backup restore drill; staff sign-in by approved devices (built, off - arm after the web address is final); WhatsApp template approval (DLT) before messaging is switched on.

**For the CA:** the new invoice-number series starting mid-year; services bills whose credit cannot be claimed (section 17(5)); GST added to stock cost when credit is not claimed on PO bills; place of supply, credit-note timing, 2B matching, reverse charge on unregistered purchases, GSTIN-to-GSTIN transfers, set-off; hearing-aid GST.

## 4y. Audit sweep 2026-10-18 - confirmed open items (all 295 documents, 2,801 items reconciled against main)

Independently verified with tests on the real code. Items in step A above are not repeated.

| Item | What is wrong | Plan |
|---|---|---|
| GST table on the Finance dashboard | Input credit taken from purchase-order tax, pooled per company, so "net payable" can be wrong | step A (with the payments fix) or remove the wrong columns |
| Unclaimable GST into stock cost | Only walk-in bills add it; PO bills booked with credit off do not | CA first |
| Services bills (food, vehicles) | No switch to mark section 17(5) credit as not claimable on the Cash Flow bill | CA first |
| Recovery tools | A rejected-goods return that crashed mid-issue, and a supplier-credit confirmation stuck mid-save, have no clean-up tool | rest of C |
| Time-zone leftovers | Three reports, AP date fallback and manual prescription dates strip the zone | rest of C |
| Stock-cost edge cases | Old stock with no recorded cost; partial/unprovable bill allocations; a fail-soft valuation write | rest of C |
| Category fields | A few contact-lens fields differ between screen and server | rest of C |
| Pending POs | Legacy "partly received" status not counted | rest of C |
| Free items | A free-of-charge purchase line cannot be entered at Rs 0 | rest of C |
| Shopify | Products with more than 100 sizes; a hand-uploaded Shopify photo can outrank IMS photo 1; collection/menu/photo creates have no automatic lookup after a lost reply | rest of C |
| Not a problem (tested) | A cashier cannot approve their own big discount; payroll "lock" marks paid by design; GST "blocked credit" flag is never set; the purchase-recommendations report is protected; the old setup script is not used by any deploy | closed |

The full table is kept with the handover (`audit_master_1018.csv`).

## 5. Waiting on the owner

| Question | Why it matters |
|---|---|
| **COD remittance door** — a courier's collection is never recorded as a payment, so a COD order stays unpaid in IMS after the customer has paid at the door | **DECIDED 2026-09-06: NOT NOW.** Courier COD orders stay unpaid in IMS until reconciled by hand; acceptable while there are no courier orders. `cod_amount` on the shipment doc is the figure a future reconcile would net against. Do not re-raise unless courier orders start |
| **36px or 44px controls** — the app-wide `.input-field` is 36px; the Add-a-Product design assumed 44px touch targets | **DECIDED 2026-09-06: LEAVE AT 36px** until staff report the tablets are fiddly. Do not re-raise |
| **"Book eye test" from the POS customer panel, for sales roles** — the one-tap booking calls `POST /clinical/queue`, which the server gates to ADMIN/STORE_MANAGER/OPTOMETRIST | **DECIDED 2026-09-06: LET SALES STAFF BOOK TOO.** Only the add-to-queue door opens to sales roles; Rx create/edit stays closed to them (control test). **MERGED #1110** |
| **Split `orders.py` in Wave 5** — the POS/money door; POS work is ask-first | **DECIDED 2026-09-06: approved.** **MERGED #1120** — pure move, byte-identical API (section 4l) |
| **Website stock: which location, and whose numbers?** — (a) Shopify lists two locations that fulfil online orders; the sync refuses to guess. Pin Gangadham Pune (where all 49 units sit and the only one that ships) or un-tick Sector 4 in Shopify admin > Settings > Locations. (b) IMS holds 1 unit for the 41 mapped products; Shopify holds 49 (typed in by the connector). Pressing "Push stock" now would mark the website sold out. Options: enter the 49 units into IMS as opening stock at Pune, then press; press now and accept sold-out until stock is entered; or leave website stock un-synced for now | **OPEN 2026-09-06.** Until answered the location stays unpinned, so neither the button nor the 01:00/09:00 sync writes any quantity |
| **Re-add the catalogue through IMS** — the store has no products until he does; first publish of each stays a human press | **OPEN 2026-09-07** — his own work, on the Add-a-Product screen |
| **The multi-location stock writer (PR 2 of 6)** — the money PR: per-store quantities to each shop's own Shopify location, one writer, every on-hand door feeding it; adversarial review before it leaves draft | **MERGED #1141 (2026-09-27)** — see section 4p. PRs 3 -> 4, then 5 (claim at Shopify's assigned shop + invoice from the shipping shop's GSTIN, the owner's ruling) are not built |
| **One real test order on bettervision.in before live selling** — the whole web-order path (webhook → mapper → IMS order with a GST invoice → unit claim) has never run live, and #1130's catch-up should then count it once as already-in-IMS | **OPEN 2026-09-06** — an owner action, not code |
| **`app.uniparallel.com` move** | Passkeys bind to the web address; the device gate must not be enrolled before a domain change |
| **Commission Leaderboard** shows per-staff revenue and commission in rupees to 5 roles | **RULED 09-03**: ADMIN/SUPERADMIN see all; everyone else, managers included, sees own figures + rank. **MERGED #1104** — one trim (`points.self_only_rows`) on `/payroll/commission/leaderboard`, `/payroll/commission/summary`, `/analytics-v2/staff-leaderboard` (which had NO gate) and the `/incentive/points` boards; SALES_STAFF/CASHIER can now open `/incentive/leaderboard` for their own standing, still not `/hr/leaderboard` (finance-only mount) |
| **Apply-for-leave has no screen** — nobody in the company can request leave | **BUILT — MERGED #1088**: apply form on `/my-work` (open to every operational role), feeding the approve/reject chain that was already running |
| **Thermal or A4** receipts at the counter? | **RULED 09-03: A4 + WhatsApp, not thermal** — this unblocked the POS switchover; the thermal renderers went in #1095 |
| Is *"every bill needs a customer"* deliberate? | **RULED 09-03: yes** — no anonymous walk-in sale, no amount threshold; the till already assumed it |
| **GST summary: own shop or the whole company?** (purchases-report branch #1184, 2026-10-12) — Finance > GST summary now shows a store manager or accountant who holds a shop that shop's sales tax (admins still see every shop). Before, every role read the whole company's figure. The 2026-10-12 rulings (R1-R9) do not cover this | **DECIDED 2026-10-18:** the stricter R1 of 2026-10-07 stands (managers: no GST summary, no GSTR-3B; admins + accountant only). On main the GSTR-3B menu entry is accounts-only; the server gate is re-checked in step A. Accountants: own shop. |
| **Org-wide finance views for an accountant who holds a shop** — the Tally sales JV, B2B invoice book, GST cross-check and bank statements read every shop for any accountant; only a login with NO shop is refused (R4). (The per-entity GST reconciliation is no longer one of them: on draft #1184 (not merged) it reads the accountant's own shop, the same scope as the GST summary cards above it.) | **DECIDED 2026-10-18: ONLY THEIR SHOP** on the Tally sales JV, B2B invoice book, GST cross-check and bank statements; company-wide needs an admin. In build (section 4x step A). |
| **A login with no shop at the till** — `GET /orders` still lists every shop's sales for a non-admin login with no shop (naming a shop is already a 403). Outside the till, the reads #1184 touched give such a login no shop (Purchase, Finance, GST, stock, reports, HR, tasks, workshop, the Hub widgets). Routers #1184 did not touch (analytics v2, marketing, campaigns) still keep their own copy of the shop rule and are not checked for a login with no shop | **DECIDED 2026-10-18: FIX IT** (owner approved this till change): a non-admin login with no shop sees no sales. In build (section 4x step A). |
| **"No shop picked" means two things for an admin** — Purchase and Finance read every shop (with a shop filter); reports, HR, tasks, workshop and inventory read the admin's topbar shop (`store_in_view`) | **DECIDED 2026-10-18: ALL SHOPS WHEN NONE PICKED** on every screen, with a shop filter. In build (section 4x step A). |

| **Selling prices below MRP set by the catalogue team** (#1236) - the till now measures a discount from the HQ offer, so an offer sale needs no manager | **DECIDED 2026-10-19: WITHIN CAPS.** A selling price below MRP must stay within the category and brand caps when it is set (checked at every door); the till sells at it with no manager; any further discount on an offer item is refused; deeper clearance goes through Promotions |
| **Two June Pune counter bills with no invoice number** (ORD-...C34069 Rs 880 paid, ORD-...BEB310 Rs 1,980 part-paid) | **DECIDED 2026-10-19: TEST BILLS - CANCELLED** on prod the same morning through the app's cancel door (no invoice number ever issued) |
| **Apply the clean-up of 83 doubled product names** (61 Carrera, 22 Marc Jacobs) | **DECIDED 2026-10-19: CLEAN THEM UP - APPLIED** on prod (83 renamed, recheck 0 left; reversible by run id 8ea873ee2eac) |
| **A customer return whose credit note cannot be numbered** (#1239, till) | **DECIDED 2026-10-19: REFUSE IT** (nothing saved, staff retry), like invoices. In #1239 |
| **Is any company's turnover over Rs 5 crore?** | **ANSWERED 2026-10-19: YES - Better Vision Opticals Pvt Ltd** (WizOpt is its subsidiary; M/s Better Vision, Bokaro, is not). Its B2B invoices, B2B credit/debit notes and its transfer tax invoices between its Maharashtra and Jharkhand GSTINs need an e-invoice IRN. New plan item (section 4x, step A2): IMS does not do this yet |
| **Website orders: can the shop counter refund the shipping charge?** (POS fix 1 #1264, 2026-11-01) - a website order now carries a shipping line (#1294); the counter could hand it back on its own, and a later Shopify shipping refund would reverse its GST a second time | **DECIDED 2026-11-01 (MCQ): NO** - the counter never refunds a website order's shipping charge; shipping goes back only through Shopify's refund, which IMS copies. Built into POS fix 1 #1264 |
| **Average cost (S4 #1303): pieces sold before their supplier bill arrives** - when the bill comes, are those already-sold pieces averaged into the cost of the stock still on the shelf (today), or only the pieces still held, with the rest booked as cost of goods sold? | **DECIDED 2026-11-01 (MCQ): ONLY THE PIECES STILL HELD** are averaged at the bill price; the difference on pieces already sold goes to cost of goods sold. To be built into #1303 before it merges; the one-time backfill runs under this rule |
| **Average cost (S4 #1303): walk-in purchases** - today a walk-in bill REPLACES the product's average with the latest price paid (ruling 2026-10-12); under 'one average cost per product' should it be averaged in like every other bill? | **DECIDED 2026-11-01 (MCQ): AVERAGE IT IN** like every other purchase bill (replaces the 2026-10-12 'latest price' rule for walk-ins). To be built into #1303 |
| **Reorder list (S5 #1309): automatic reordering is OFF by default** - so most products show under 'Not suggested - auto-reorder off'. Turn it on for all products, or for some kinds only? | **DECIDED 2026-11-01 (MCQ): LEAVE ALL OFF** - shops switch it on product by product. No change |
| **Contact-lens expiry day (#1314)** - a batch that expires TODAY now shows 'near expiry, 0 days left' all day. Before, it showed as expired during the day (an accident of the old arithmetic; between 00:00 and 05:30 it showed 0) | **DECIDED 2026-11-01 (MCQ): THE EXPIRY DATE ITSELF COUNTS AS EXPIRED. DONE #1325** (merged, 10e36162) - one rule for the inventory screen, the till, the near-expiry filter, the expiring report and the receive warning |
| **Three POS screens still take 'today' from the UTC clock** (lens fitting form, prescription form, the till's order date) - each a one-line switch to the IST day; POS is ask-first | **DECIDED 2026-11-01 (MCQ): YES, FIX THEM** - right after POS fix 1 #1264 merges (one of the files is in it) |
| **Merge bar for money / GST builds** - every should-fix fixed before merge (slow, 9-14 rounds each), or merge once nothing blocks and no money can go wrong twice, with rare cases as follow-ups? | **DECIDED 2026-11-01 (MCQ): KEEP ZERO SHOULD-FIX.** Speed comes from fewer rebases, parallel re-checks, a scope freeze and nits as follow-ups |
| **GST 17(5) #1302 after nine rounds** - the two remaining cases need a shop with no GSTIN, a locked month and a piece found again | **DECIDED 2026-11-01 (MCQ): ONE MORE ROUND, THEN MERGE** (further rare cases become follow-ups) |
| **Can workshop staff record a payment at handover?** (#1264 removed it) | **DECIDED 2026-11-01 (MCQ): NO - workshop staff never record payments.** #1264 keeps them out of the payments door |

---

## 6. Decided — recorded so they are not re-litigated

| Decision (2026-09-27) | Ruling |
|---|---|
| Railway "sleep when inactive" on the backend | **OFF.** The app never slept (it holds database connections); the keepalive service is cancelled. Supersedes the 2026-09-17 "leave it on" ruling |
| Wave 6: finance dashboard cash-flow and budgets | **One door**: links to the standalone Cash Flow and Budgeting pages |
| Wave 6: customers list detail | **Keep the in-place panel** |
| Wave 6: payroll | **Payroll module's Run is the one door**; the HR generator is deleted |
| Wave 6: classic goods receipt | **Retire it** once Receive Goods covers a delivery challan without an invoice |

| Decision (2026-09-06 evening) | Ruling |
|---|---|
| Which Shopify location the website sells from | **One Shopify location per IMS physical store**; the site ships from whichever shop holds the unit. Supersedes the single pooled location of #1125 |
| Whose stock numbers are true | **IMS.** The 49 units typed into Shopify are entered into IMS first (38 done; 11 Large after the variant-of rule), then the first stock push |
| Seller of record when a Jharkhand shop ships a website order | **The shipping shop's own GSTIN** — invoice series, seller state, returns all follow the shop. A money change; accountant to confirm before it goes live |
| Which stores get a location | **All physical stores**, WizOpt's shop included — its stock sells on bettervision.in too |
| Sector 4 ticked to fulfil online orders while holding 0 units | **Leave it ticked** |
| Sizes of one style | IMS keeps one product per SKU (size is inside the SKU); a **variant-of rule** lets a size product push only its own price, barcode and stock to the parent listing |
| Photo adoption for products live before the media map | Exact matches, the connector-name rule for the Meta range, and replace-then-adopt for the 6 old-app products — all **yes** |

- **30-day browse horizon** applies to browsing, never to work in hand. Task
  queues and unpaid balances are exempt. Aged debt stays visible at any age.
- **Follow-ups** keep pending items visible at any age — owner weighed the
  browse risk and chose business value.
- **Team tasks**: managers and above only.
- **Fabricated SOPs**: deleted; the owner supplies the real procedures.
- **Customer list**: clamp on last *activity*, not registration date. Deferred —
  no activity field exists yet, and production has had no orders in 30 days
  because the app is not yet billing live.
- **Staff sign-in**: approved devices only, SUPERADMIN approves from his phone,
  ADMIN and SUPERADMIN never device-gated, no off-site path.
- **Catalog entry**: type in capitals, server stores the right case. Codes stay
  uppercase; a value already mixed-case is never rewritten.
- **Tasks and SOPs go to a PERSON, not a job title** (owner, 2026-09-03). A role
  on an SOP template resolves to the people holding it in that store, so
  "Cashier" becomes Sameer's task and Rupesh's task by name. Every generated
  task used to go to whoever pressed the button, and the template's assignee
  list was read by nothing.
- **POS customer panel** (owner, recorded 2026-09-06): the widget tiles open a
  slide-over on the till, never navigate; "Bill for <member>" keeps the cart;
  money is read-only in the panel; "Book eye test" is one tap onto today's
  queue; the WhatsApp reminder is the household recall (`PRESCRIPTION_EXPIRY`);
  "My day" shows the signed-in person's own figures only.
- **Products already on Shopify sync automatically** (owner, 2026-09-06): a
  scheduled run at 01:00 and 09:00 IST re-pushes every already-pushed product
  that changed, plus a "Sync live products now" button. The FIRST publish of a
  never-pushed product stays a human press. Build in flight on
  `feat/shopify-live-sync` — not merged (section 4b).
- **Sync-path audit order** (owner, 2026-09-06): website stock first, then
  delete/archive takes the product off sale, then photo + name changes reach
  live products, then tags, then missed-webhook catch-up (section 4b).
