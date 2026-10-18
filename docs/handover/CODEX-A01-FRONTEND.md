# A01 supplier-confirmation frontend

Owner-approved scope: the existing Vendor Returns Issue Credit action opens a
form for the supplier's actual note number/date and credited amounts, original
bill selection when ambiguous, partial credits and pending missing evidence.
Only the backend's verified POSTED event may change AP/GST.

The first frontend phase adds the form, typed API client and 15 behavior
tests. The second phase wires the existing Vendor Returns action into it and
adds the approved Cash Flow choice/handoff. These changes must not be released
separately from the backend routes, canonical posting and accounting readers.
The agreed implementation contract is in the external audit-fix workspace's
A01-CREDIT-CONFIRMATION-API-CONTRACT.md.

The form uses the actual supplier identity/date, validates integer-paise
allocation totals and original caps, keeps reverse-charge tax separate from
supplier credit, selects original bill/unit evidence, resumes pending notes,
and retries uncertain responses with exactly the same UUID/payload. It refuses
stale failed verification and discards money when the return or permission
changes. Non-Accounts roles neither fetch nor render the form.

Verification: 15 focused Vitest behavior tests; TypeScript compilation; ESLint
with no warnings; Vite production build (2,997 modules, 59.72 seconds, existing
unrelated CSS-comment warning). No production transaction was executed.

Integration also includes the owner's later-approved Cash Flow credit-type
choice: returned-goods credits go through Vendor Returns confirmation, while
scheme, discount and quality adjustments retain their existing manual path.
Cash Flow requires an explicit adjustment type, sends it for scheme/discount/
quality adjustments and offers a link to this supplier's returns for returned
goods. The existing shop scope remains in force. Vendor Returns uses the
actual confirmation response for pending/partial/full feedback, without a
direct credit_issued status patch or an invented supplier note number/date.
Missing prices can retain actual evidence as pending; internal note issuance
still requires its existing pricing step.

Integrated verification: 140 related tests across 15 files; forced TypeScript
and Vite production build (2,999 modules); ESLint has no errors and eight
warnings in existing page/API sections. The standalone form/API module has
no lint warnings. An offline real-component browser probe with synthetic
responses and the production CSS/fonts passes the existing layout audit at
all eight sizes, and submits a partial note with its actual number/date,
original unit selection and amount allocations. Tablet and phone screenshots
were inspected. This probe does not call a real confirmation backend or prove
accounting integration. External evidence is a01-frontend-related.log,
a01-integration-build.log and supplier-credit-form-browser.json/png.

Root owns this frontend branch; the supplier-accounting agent owns backend
routes/readers. Independent review of the combined accounting release and
fresh hosted CI are still required. No production transaction occurred.

Independent review corrections: when loading a pending note fails, the next
successful refresh replaces number, date, amount and request identity with
the selected note; switching back to a new note also clears the old identity
after recovery. The Cash Flow and Vendor Returns links use app navigation,
preserving the in-memory shop selection. Three additional regressions cover
both recovery directions and the real router/shop handoff. The focused run
passes 47 tests. Independent offline browser probes confirm both recovery
directions submit the correct existing or fresh request identity.
Forced TypeScript checking, Vite production build and focused ESLint also
pass after these corrections; the pre-existing CSS-comment warning remains.
