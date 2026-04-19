# IMS 2.0 — Continuation Prompt for Next Session

Copy-paste this into a new conversation to continue where we left off.

---

## PROMPT:

I'm continuing work on IMS 2.0, a retail operating system for my Indian optical store chain (Better Vision Optics / WizOpt). Here's the full context:

### Stack
- **Frontend:** React + TypeScript + Tailwind + Vite, deployed on Vercel at `ims-2-0-railway.vercel.app`
- **Backend:** FastAPI + Python + MongoDB, deployed on Railway at `ims-20-railway-production.up.railway.app/api/v1`
- **Auth:** JWT with bcrypt, 10 roles (SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT, CATALOG_MANAGER, OPTOMETRIST, SALES_CASHIER, SALES_STAFF, WORKSHOP_STAFF)
- **Test login:** admin/admin123
- **Git:** Single `main` branch on `github.com/brashakg/ims-2.0-railway`, PAT: `<redacted — PAT rotated; ask user for current token if needed>`
- **Toast system:** Uses custom `useToast()` from `context/ToastContext` (NOT react-toastify). Methods: `toast.success()`, `toast.error()`, `toast.warning()`, `toast.info()`
- **DB helper pattern:** Backend routers use `_get_db()` with lazy import: `def _get_db(): from database.connection import get_db; return get_db().db`
- **User type:** `user?.id` (not user_id), `user?.roles` (array, not singular role)
- **Theme:** Dark navy/slate throughout (bg-gray-800/900, text-white, border-gray-700)
- **GST:** 5% frames/lenses/contacts, 18% sunglasses/watches/accessories
- **tsconfig:** Strict mode with `noUnusedLocals`, `noUnusedParameters` — CI enforces this

### Current CI Status
- ✅ Frontend Tests & Build — PASSING (zero TS errors)
- ✅ Backend Tests & Build — PASSING
- ✅ Railway deploy (divine-heart) — SUCCESS
- ❌ Deploy to Production workflow — fails because GitHub Secrets not configured (VERCEL_TOKEN, RAILWAY_TOKEN, SLACK_WEBHOOK_URL). Vercel/Railway deploy via their native GitHub integrations instead.
- Vercel deploy check may fail independently — needs investigation

### What's Been Built (across 10 phases this session)

**Phase 1-2:** Fixed loading spinners (Clinical/Workshop/HR), Settings API paths, Reports API params, unified colour scheme to dark theme across 9 pages

**Phase 3:** Mark Order as Delivered + confirmation modal, Order Status Timeline component, Target vs Achievement meter on dashboard, Workshop status fix

**Phase 4:** Vendor Returns workflow (create→approve→ship→credit/replace), Staff Incentive Tracking (3-tier slabs 0.8%/1%/1.5%, kicker system, leaderboard), Expense Tracker with approval workflow

**Phase 5:** Customer Follow-up Automation (eye test reminders, frame replacement, order delivery), PO Print template (A4), GRN Print template (A4)

**Phase 6:** Payroll module (Indian salary structure Basic+HRA+PF+ESI+PT+TDS, advance tracking, payslips), 12 Report types (sales comparison, MoM/YoY, P&L, staff ranking, stock count, eye tests, etc.), Tasks/SOPs/Escalation engine (P0-P4, daily checklists, 2-level auto-escalation)

**Phase 7:** 5 Print templates (eye test token, workshop job card, delivery challan, credit note, estimate/quotation), Workshop completions (QC, print), Clinical completions (token, eye test count), 12 Dashboard role-specific widgets, 6 Inventory advanced features (non-moving stock, stock count scanner, contact lens expiry, SPH×CYL power grid, sell-through %, overstock analysis)

**Phase 8:** POS credit billing + voucher redemption + loyalty points + previous Rx + last bought display, HR monthly attendance grid + employee self-service, CRM customer purchase history + Rx QR code + OTP verification

**Phase 9:** Finance & Accounting (revenue tracking, P&L, GST management, outstanding receivables, vendor payments, cash flow, period locking, budgets, reconciliation), Store Setup deep config, Exchange flow in POS

**Phase 10:** Fixed 95 TypeScript strict mode errors across 30 files, added tsconfig.json, Store Setup components (FeatureToggles, AuditLogViewer, IntegrationSettings, StoreSetupWizard), Clinical components (PrescriptionCard with A5 print, FamilyPrescriptionsView, AbuseDetection), Expense components (ExpenseBillUpload with hash dedup)

### Updated Feature Count
- **~210 features built** (up from 117 at start of session)
- **~85 remaining** (excluding integrations, marketplace, AI/ML, training which user deferred)

### What's Still NOT Built (user explicitly deferred items 2-5)
1. ~~Third-party Integrations~~ (deferred — Razorpay, WhatsApp, Shiprocket, Tally, GST Portal, Google/Meta Ads)
2. ~~Marketplace/E-commerce~~ (deferred — Amazon/Flipkart/Shopify unified)
3. ~~AI Intelligence~~ (deferred — ML predictions, NLP queries, purchase advisor)
4. ~~Training & Rollout~~ (deferred — curriculum, in-app help)

### Remaining items that SHOULD still be built:
- Some Store Setup components created but not yet integrated into SettingsPage.tsx (FeatureToggles, AuditLogViewer, IntegrationSettings, StoreSetupWizard need wiring)
- Some Clinical components created but not yet integrated (AbuseDetection, FamilyPrescriptionsView, PrescriptionCard)
- Some Expense components created but not integrated (ExpenseBillUpload)
- EMI payment component may need integration into POSLayout
- Notification mock for Orders (component may exist but needs wiring)
- Order tracking QR code
- Credit note balance tracking per customer
- Bulk CSV product import
- Store-wise product activation toggles
- Geo-fence check-in enforcement
- Week-off swap with manager approval
- LWP auto-deduction logic

### Key File Locations
- Frontend pages: `frontend/src/pages/`
- Frontend components: `frontend/src/components/`
- Backend routers: `backend/api/routers/`
- API services: `frontend/src/services/api.ts`
- Auth context: `frontend/src/context/AuthContext.tsx`
- Toast context: `frontend/src/context/ToastContext.tsx`
- Module config: `frontend/src/context/ModuleContext.tsx`
- Routes: `frontend/src/App.tsx`
- Router registry: `backend/api/routers/__init__.py`
- Router mount: `backend/api/main.py`

### Important Patterns
- All routers use `_get_db()` lazy import pattern
- All pages use dark theme (bg-gray-800/900)
- Frontend build: `cd frontend && npx vite build`
- TS check: `cd frontend && npx tsc --noEmit`
- Backend check: `python3 -m py_compile backend/api/routers/<file>.py`
- Full backend test: `python3 -c "import sys; sys.path.insert(0, 'backend'); from api.main import app; print(len(app.routes))"`

### The codebase is at:
- Local: `/sessions/blissful-brave-rubin/ims-audit` (or clone from GitHub)
- Repo: `github.com/brashakg/ims-2.0-railway`
- The user's workspace folders are mounted at `/sessions/blissful-brave-rubin/mnt/`

### What to do next:
Integrate all created-but-not-wired components into their parent pages, then continue building the remaining ~85 features. After that, test the full app on the live Vercel deployment.
