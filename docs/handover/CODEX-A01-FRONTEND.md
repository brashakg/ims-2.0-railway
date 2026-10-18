# A01 supplier-confirmation frontend

Owner-approved scope: the existing Vendor Returns Issue Credit action opens a
form for the supplier's actual note number/date and credited amounts, original
bill selection when ambiguous, partial credits and pending missing evidence.
Only the backend's verified POSTED event may change AP/GST.

This first frontend phase adds an isolated form, typed API client and 15
behavior tests. It is not yet wired into VendorReturns and must not be released
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
Those integration changes and their checks are the next phase. Root owns this
frontend branch; the supplier-accounting agent owns backend routes/readers.
