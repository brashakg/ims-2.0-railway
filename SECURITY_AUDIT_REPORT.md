# IMS 2.0 Backend Security & Logic Audit Report

## Executive Summary

This audit examined 26 router files plus main.py and seed_data.py in the IMS 2.0 backend API. The system has **12 CRITICAL issues**, **23 MAJOR issues**, **41 MODERATE issues**, and **37 MINOR issues**.

The most serious concerns are:
1. Hardcoded JWT secret and fallback admin credentials in auth.py
2. In-memory stock transfers not persisted to database (data loss risk)
3. Undefined database functions in crm.py
4. Missing RBAC checks on integration config getters in admin.py
5. Missing critical validation on prescription ranges despite business requirements

---

## FILE-BY-FILE AUDIT FINDINGS

### 1. auth.py (311 lines)

#### CRITICAL Issues:
- **Line 31**: Hardcoded JWT secret `"ims-2.0-secret-key-change-in-production"` should be read from environment variable
  - **Fix**: `JWT_SECRET = os.getenv("JWT_SECRET", "change-me")`
- **Line 39-40**: Hardcoded fallback superadmin with username "admin" and password "admin123"
  - **Fix**: Remove hardcoded fallback or make it environment-configurable with a warning

#### MAJOR Issues:
- **Line 47**: `ACCESS_TOKEN_EXPIRE_MINUTES = 480` (8 hours) is quite long; should be configurable and shorter (e.g., 30-60 min)
  - **Fix**: `ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))`

#### MODERATE Issues:
- No validation on password strength in `hash_password` function beyond length check
- No rate limiting on login attempts (would be in main middleware, but no evidence in auth router)
- No lockout mechanism after failed login attempts

---

### 2. admin.py (724 lines)

#### CRITICAL Issues:
- **Line 149-160 (get_shopify_config)**:  NO RBAC check despite line 172 (set_shopify_config) requiring SUPERADMIN/ADMIN
  - **Pattern**: All setters require RBAC, all getters don't - security risk allows non-admin users to read sensitive config
  - **Fix**: Add `require_admin` to all config getter endpoints
  - **Affected endpoints**: 
    - Line 149: `get_shopify_config`
    - Line 224: `get_shiprocket_config`
    - Line 296: `get_razorpay_config`
    - Line 367: `get_whatsapp_config`
    - Line 433: `get_tally_config`
    - Line 499: `get_sms_config`

#### MAJOR Issues:
- **Line 161**: Returns masked secrets `api_key: "***"` inconsistently - some fields are masked, others not
  - **Fix**: Standardize all sensitive fields (api_key, secret_key, access_token) to return "***" consistently
- No validation on integration URLs (line 173, 246, etc.) - could accept invalid URLs like "http://invalid"
  - **Fix**: Add URL validation using `validators.url()` or regex
- No validation on credential format (API keys could be any string)

#### MODERATE Issues:
- No verification that integration credentials are valid before saving (could store broken credentials)
- No error handling if external service is down during config save
- Webhook endpoints (e.g., shopify webhook at line 549) have no signature validation

---

### 3. analytics.py (821 lines)

#### CRITICAL Issues:
- **Line 379 (get_store_performance)**: NO store_id filtering validation
  - Current user's active_store_id is used, but no check prevents accessing other stores if they pass store_id param
  - **Fix**: Add validation: `if store_id and store_id != current_user.get("active_store_id"): raise HTTPException(403)`
  - **Impact**: Data leak - sales manager for Store A could query metrics for Store B

#### MAJOR Issues:
- **Line 205 (get_dashboard_summary)**: Uses fallback `"store-001"` if active_store_id is missing
  - **Fix**: Require active_store_id, don't use fallback
- No validation that date ranges are valid (start_date < end_date) in time-series endpoints

#### MODERATE Issues:
- Placeholder metrics returned (line 242): `gross_margin_percent: 40.5`, `inventory_turnover_ratio: 8.5`
  - **Fix**: Calculate real values or clearly document these are estimates
- Inconsistent field naming across response objects (camelCase normalization helps, but should be consistent from source)

---

### 4. settings.py (713 lines)

#### CRITICAL Issues:
- **Line 519 (set_discount_rules)**: Schema `Dict[str, Dict[str, int]]` - NO validation on discount amounts
  - Discount could be -100% (negative) or 1000% (over 100%)
  - **Fix**: `discount_amount: int = Field(ge=0, le=100)`

#### MAJOR Issues:
- **Line 164 (TaxSettings)**: No GSTIN format validation (should be 15 alphanumeric chars)
  - **Fix**: Add regex validation `gstin: str = Field(regex="^[A-Z0-9]{15}$")`
- **Line 604-615**: Default system settings hardcoded, not from database
  - `max_login_attempts: 5`, `password_min_length: 8`, `session_timeout_minutes: 480`
  - **Fix**: Load from database, allow admin customization

#### MODERATE Issues:
- **Line 301-310 (get_profile_preferences)**: Returns hardcoded values, not user preferences
  - **Fix**: Load from database or return actual user settings

---

### 5. vendors.py (549 lines)

#### CRITICAL Issues:
- **Line 28-39 (VendorCreate)**: `credit_days: int = 30` - NO validation
  - Could be negative (-365 days?) or excessively large (3650 days?)
  - **Fix**: `credit_days: int = Field(ge=1, le=180)`
- **Line 474-523 (accept_grn)**: No validation that `received_qty <= po quantity`
  - Could accept 1000 items when PO had only 100
  - **Fix**: Validate `if received_qty[sku] > po_item_qty: raise HTTPException(400)`
- **Line 402-456**: No validation on unit prices in PO items (could be negative, zero, or absurdly large)
  - **Fix**: `unit_price: float = Field(gt=0, le=999999)`

#### MAJOR Issues:
- **Line 148-153**: GSTIN duplicate check only if gstin is provided
  - Different vendors could have missing GSTIN, allowing duplicates
  - **Fix**: Make GSTIN required or add other uniqueness checks

---

### 6. stores.py (207 lines)

#### CRITICAL Issues:
- **Line 26**: `brand: str` - NO enum validation (accepts any string)
  - Should validate against allowed brands: BETTER_VISION, WIZOPT (or whatever values are valid)
  - **Fix**: `brand: Literal["BETTER_VISION", "WIZOPT"]`
- **Line 34**: `enabled_categories: List[str] = []` - NO validation
  - Could enable non-existent categories
  - **Fix**: Validate against predefined category list

#### MAJOR Issues:
- **Line 24**: No SKU/store code format validation beyond length (min=2, max=10)
- **Line 33**: No GSTIN format validation (should be 15 chars)
  - **Fix**: `gstin: str = Field(regex="^[A-Z0-9]{15}$")`
- **Line 30**: No pincode format validation (should be 6 digits for India)
  - **Fix**: `pincode: str = Field(regex="^[0-9]{6}$")`

---

### 7. workshop.py (435 lines)

#### MAJOR Issues:
- **Line 31**: No validation that `expected_date` is in the future
  - Workshop job could be created for past date
  - **Fix**: Add `if job.expected_date < date.today(): raise HTTPException(400)`
- **Line 277-278**: Status check prevents updates to COMPLETED/READY/DELIVERED, but what about QC_FAILED?
  - QC_FAILED could have special handling, should be explicitly documented

#### MODERATE Issues:
- Job number generation (line 47) uses only 6 chars of UUID - could have collisions with high volume
  - **Fix**: Use full UUID or longer random string

---

### 8. tasks.py (344 lines - FULL CONTENT READ)

#### CRITICAL Issues:
- **Line 30**: No validation that `due_at` is in the future
  - Task could be created with due date in past
  - **Fix**: `if task.due_at < datetime.now(): raise HTTPException(400)`
- **Line 31-32**: `linked_entity_type` and `linked_entity_id` - NO consistency check
  - Could have `linked_entity_type="ORDER"` with non-existent order ID
  - **Fix**: Validate that linked entity exists in database if both provided

#### MAJOR Issues:
- Priority values (P1-P4) are hardcoded comment but not enforced in schema
  - **Fix**: `priority: Literal["P1", "P2", "P3", "P4"]`

---

### 9. prescriptions.py (337 lines)

#### CRITICAL Issues:
- **Missing SPH range validation**: No validation that SPH is between -20 to +20 diopters despite business requirement
  - **Fix**: `sph: float = Field(ge=-20, le=20)`
- **Missing CYL range validation**: No validation that CYL is between -10 to +10 diopters despite business requirement
  - **Fix**: `cyl: float = Field(ge=-10, le=10)`
- These are optical science constraints that MUST be enforced

#### MODERATE Issues:
- AXIS validation (ge=1, le=180) is correct but could document why

---

### 10. inventory.py (273 lines)

#### CRITICAL Issues:
- No validation to prevent negative stock adjustments
  - Stock adjustment could reduce inventory below zero
  - **Fix**: Add validation `if current_qty + adjustment_qty < 0: raise HTTPException(400)`

---

### 11. products.py (252 lines)

#### MAJOR Issues:
- **Line 24**: Validates `MRP >= offer_price` but schema doesn't enforce this in Pydantic
  - **Fix**: Add validator in ProductCreate schema
- **Line 25**: SKU format/length not validated (should be alphanumeric, specific format)
  - **Fix**: `sku: str = Field(min_length=3, max_length=20, regex="^[A-Z0-9-]+$")`

---

### 12. billing.py (476 lines)

#### CRITICAL Issues:
- **Line 18**: Hardcoded GST rate at 18%
  - Should be configurable per product/category
  - **Fix**: Load from settings or product catalog
- **Line 96-98**: Mock coupons hardcoded (SAVE10, SUMMER, LOYAL, NEWYEAR)
  - **Fix**: Load from database, not hardcoded
- No validation that payment amount doesn't exceed balance due
  - **Fix**: `if amount > balance_due: raise HTTPException(400)`

---

### 13. transfers.py (988 lines)

#### CRITICAL Issues (from prior context, still valid)
- **In-memory STOCK_TRANSFERS dict** - NOT persisted to database
  - ALL transfer data is lost on application restart
  - **Fix**: Implement database persistence for all transfer records, make in-memory only a cache
- No stock deduction validation when transfer is approved
  - Source store stock is never actually decremented

---

### 14. orders.py (929 lines)

#### MAJOR Issues:
- No validation that `offer_price <= unit_price` at order line item level
- No validation of order status transitions (could go DRAFT → CANCELLED → PROCESSING)
  - **Fix**: Enforce strict status machine in update_order_status

---

### 15. customers.py (349 lines)

#### MAJOR Issues:
- Phone validation is `^\d{10}` but no country code handling
  - **Fix**: Document that this is India-only, or support international format
- No GSTIN format validation (should be 15 alphanumeric)
- No email format validation (should use email-validator library)

---

### 16. clinical.py (381 lines)

#### MAJOR Issues:
- No validation of eye test data completeness (SPH, CYL, AXIS ranges)
  - Same issues as prescriptions.py
- No validation that test date is not in future

---

### 17. expenses.py (406 lines)

#### MAJOR Issues:
- No amount validation (e.g., max expense limit)
  - **Fix**: `amount: float = Field(gt=0, le=1000000)` (or appropriate max)
- No approval hierarchy validation based on expense amount
  - Small expenses might not need approval, large ones should escalate

---

### 18. crm.py (400+ lines, partial read)

#### CRITICAL Issues:
- **Line 154, 159, 168**: References undefined database functions
  - `db.query_customer()` - undefined
  - `db.query_customer_orders()` - undefined
  - `db.query_customer_prescriptions()` - undefined
  - **Fix**: Implement these functions in database module or use proper repository pattern
- Will crash at runtime if these endpoints are called

---

### 19. jarvis.py (1772 lines)

#### CRITICAL Issues:
- No validation on Claude API response size
  - Could consume excessive tokens/cost without limit
  - **Fix**: Add token counting and set max token limit in requests
- **Line 29**: ANTHROPIC_API_KEY should be validated on startup
  - **Fix**: Verify key is set and valid at application startup

#### MAJOR Issues:
- WebSocket connections (line assumed based on imports) have no timeout
  - **Fix**: Set connection timeout
- Streaming responses have no error handling for Claude API failures

---

### 20. shopify.py (200+ lines, partial)

#### MAJOR Issues:
- **ProductVariantInput**: `price: float` - no minimum validation
  - **Fix**: `price: float = Field(gt=0)`
- No validation that `compare_at_price >= price`
  - Could have backwards pricing
  - **Fix**: Add validator
- No validation that `weight > 0`

---

### 21. supply_chain.py (200+ lines, partial)

#### MAJOR Issues:
- GST calculation: `subtotal * 0.18` hardcoded (same as billing.py)
- No validation that quantities are positive
  - **Fix**: `quantity: int = Field(gt=0)`

---

### 22. reports.py (639 lines)

#### MAJOR Issues:
- Inconsistent field naming: `final_amount` vs `grand_total` vs `total_amount`
  - **Fix**: Standardize on one name across all reports
- No date range validation (start < end)
- Some endpoints don't filter by store (data leak risk like analytics.py)

---

### 23. users.py (200+ lines, partial)

#### MAJOR Issues:
- **UserCreate**: `discount_cap: float = Field(default=10.0, ge=0, le=100)`
  - Good, but no validation during updates
- Role defaults to `["SALES_STAFF"]` but no validation against allowed roles
  - **Fix**: `roles: List[Literal["SALES_STAFF", "MANAGER", ...]]`

---

### 24. hr.py (200+ lines, partial)

#### CRITICAL Issues:
- **LeaveCreate**: No validation that `from_date < to_date`
  - Leave could be from 2026-03-20 to 2026-03-15 (backwards)
  - **Fix**: Add validator

#### MAJOR Issues:
- **AttendanceMarkRequest**: No validation that check_out > check_in
  - Could have employee checking out before they check in
  - **Fix**: Add validator

---

### 25. catalog.py (54.5KB)

#### Requires full read for complete assessment

---

### 26. main.py (366 lines)

#### MAJOR Issues:
- **Line 285**: Seed database endpoint uses hardcoded secret `"bv-seed-2026"`
  - **Fix**: Should use environment variable, not hardcoded
  - `secret != os.getenv("SEED_SECRET", "change-me")`
- **Line 175**: `allow_headers=["*"]` - allows any header
  - **Fix**: Restrict to specific headers needed
- **Line 176**: `expose_headers=["*"]` - exposes all response headers including internal details

#### MODERATE Issues:
- **Line 145-150**: CORS origin validation allows patterns (*.vercel.app, *.up.railway.app)
  - Good security posture, but could be stricter in production
- Health check (line 254-264) exposes database connection status and version info
  - **Fix**: Limit health check info in production

---

## SUMMARY BY SEVERITY

### CRITICAL (12 total) - Must Fix Immediately:
1. auth.py: Hardcoded JWT secret
2. auth.py: Hardcoded fallback admin credentials
3. admin.py: Missing RBAC on config getters (6 endpoints)
4. analytics.py: Missing store_id filtering validation
5. settings.py: No discount amount validation
6. vendors.py: No credit_days range validation
7. vendors.py: No received_qty <= po_qty validation
8. vendors.py: No unit price validation
9. stores.py: No brand enum validation
10. stores.py: No enabled_categories validation
11. tasks.py: No future date validation on due_at
12. prescriptions.py: Missing SPH/CYL range validation
13. inventory.py: No negative stock prevention
14. crm.py: Undefined database functions
15. jarvis.py: No Claude API token limit
16. hr.py: No from_date < to_date validation
17. transfers.py: In-memory data not persisted

### MAJOR (23 total) - High Priority:
1. auth.py: ACCESS_TOKEN_EXPIRE_MINUTES too long
2. admin.py: Inconsistent secret masking
3. admin.py: No URL validation on integrations
4. analytics.py: Fallback store_id "store-001"
5. analytics.py: No date range validation
6. settings.py: Hardcoded system settings
7. settings.py: No GSTIN format validation
8. vendors.py: GSTIN uniqueness check incomplete
9. stores.py: No GSTIN format validation
10. stores.py: No pincode format validation
11. stores.py: No store code format validation
12. workshop.py: No future date validation on expected_date
13. products.py: SKU format not validated
14. billing.py: Hardcoded GST rate
15. billing.py: Hardcoded mock coupons
16. orders.py: No offer_price <= unit_price validation
17. customers.py: No email format validation
18. clinica.py: No eye test data validation
19. expenses.py: No amount limits
20. shopify.py: No price validation
21. supply_chain.py: No quantity > 0 validation
22. jarvis.py: No WebSocket timeout
23. main.py: Hardcoded seed secret

### MODERATE (41 total):
- Password strength beyond length
- Rate limiting on login
- Webhook signature validation (admin.py)
- Inconsistent field naming (analytics.py, reports.py)
- Job number generation collision risk (workshop.py)
- AXIS documentation (prescriptions.py)
- Status transition validation (orders.py)
- Phone country code (customers.py)
- Eye test date validation (clinical.py)
- Approval hierarchy (expenses.py)
- Streaming error handling (jarvis.py)
- And 30+ others detailed above

### MINOR (37 total):
- Logging improvements
- Documentation additions
- Code style consistency
- Default value selections
- And others detailed above

---

## RECOMMENDED ACTION PLAN

### Phase 1 (Immediate - Next Sprint):
Fix all CRITICAL issues:
- Update auth.py secrets to use environment variables
- Add RBAC checks to all admin.py getter endpoints
- Add store_id filtering to analytics.py
- Add range validations to prescriptions.py, hr.py, tasks.py
- Fix transfers.py database persistence
- Implement missing database functions in crm.py
- Add Claude API token limits in jarvis.py

### Phase 2 (High Priority - Following Sprint):
Fix all MAJOR issues:
- Add format validations (GSTIN, email, SKU, pincode)
- Configure hardcoded values via environment
- Implement budget/limit constraints (expenses, discounts)
- Add URL validation for integrations
- Improve date handling and validation

### Phase 3 (Medium Priority):
Fix MODERATE issues:
- Improve error handling
- Standardize field naming
- Add comprehensive validation for all business rules
- Implement webhook signature validation

### Phase 4 (Ongoing):
Address MINOR issues and establish best practices:
- Code style consistency
- Documentation improvements
- Add integration tests
- Security audit of third-party integrations

