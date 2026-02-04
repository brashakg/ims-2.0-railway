# SYSTEM_INTENT.md - IMS 2.0 Retail Operating System

## Supreme Authority Document

This document defines the NON-NEGOTIABLE business rules that the system MUST enforce.
Any code that violates these rules is INCORRECT.

---

## 1. SYSTEM IDENTITY

**This is NOT a POS. This is a Retail Operating System.**

It governs:
- Sales operations
- Inventory management
- Clinical (optometry) workflows
- HR & payroll
- Finance & accounting
- Customer relationships
- Vendor management
- Multi-store operations
- Task & SOP enforcement
- AI-assisted intelligence (read-only)

---

## 2. CORE PHILOSOPHY

### Control > Convenience
- Every action requires explicit authorization
- No silent defaults
- No hidden automation
- Explicit is better than implicit

### Audit Everything
- Who did what
- When they did it
- Where they did it
- What was the old value
- What is the new value
- Why (if override)

### Fail Loudly
- System failures must be visible
- Never corrupt data silently
- Block operations rather than proceed incorrectly

---

## 3. PRICING LAWS (ABSOLUTE)

### MRP vs Offer Price Rules

```
IF offer_price > mrp:
    → BLOCK (Never allow, database constraint)

IF offer_price < mrp:
    → Product already discounted by HQ
    → NO further discount allowed at store level
    → Only HQ override can add discount

IF offer_price == mrp:
    → Role-based discounts applicable
    → Category-based caps apply
    → Brand-level caps apply (for luxury)
```

### Discount Authority Matrix

| Role | Base Cap | Category Override | Brand Override |
|------|----------|-------------------|----------------|
| Sales Staff | 10% | Category cap if lower | Brand cap if lower |
| Store Manager | 20% | Category cap if lower | Brand cap if lower |
| Area Manager | 25% | Category cap if lower | Brand cap if lower |
| Admin | 100% | Can override | Can override |
| Superadmin | 100% | Can override | Can override |

### Category Discount Caps

| Category | Max Discount | Justification |
|----------|-------------|---------------|
| MASS (Contact lens, Accessories) | 15% | Volume products |
| PREMIUM (Frames, Optical lens) | 20% | Standard margin |
| LUXURY (Watches, Smart glasses) | 5% | Brand protection |
| SERVICE | 10% | Labor cost |
| NON_DISCOUNTABLE | 0% | Fixed price items |

### Luxury Brand Caps (Override Category)

| Brand | Max Discount |
|-------|-------------|
| Cartier | 2% |
| Chopard | 2% |
| Bvlgari | 2% |
| Gucci | 5% |
| Prada | 5% |
| Versace | 5% |
| Burberry | 5% |

---

## 4. PRESCRIPTION RULES

### Mandatory Prescription
- Optical lenses REQUIRE prescription
- Contact lenses REQUIRE prescription
- Frame-only sales do NOT require prescription

### Prescription Validation
- Axis MUST be whole number 1-180
- SPH range: -20.00 to +20.00 (0.25 steps)
- CYL range: -6.00 to +6.00 (0.25 steps)
- ADD range: +0.75 to +3.50 (0.25 steps)

### Prescription Source
- "Tested at store" → Optometrist REQUIRED
- "From doctor" → External doctor name recorded

### Prescription Validity
- Optometrist sets validity based on case
- System warns on expired prescription
- Override requires Store Manager+

---

## 5. STOCK RULES

### Stock Acceptance Flow
1. Stock arrives at store (from HQ or transfer)
2. Stock is in PENDING acceptance status
3. Store Manager verifies count
4. Store Manager assigns location (C1, D2, S3)
5. System generates store-specific barcode
6. Barcode includes location code
7. Store Manager prints barcode
8. Stock becomes ACCEPTED

### Stock Transfer Flow
1. Source store creates transfer
2. Items added with quantities
3. **BARCODES MUST BE REMOVED** before sending
4. Transfer sent (stock reduced at source)
5. Destination receives with quantity check
6. Mismatches escalated to HQ
7. New barcodes printed at destination
8. Stock accepted at destination

### Stock Count Rules
- Daily count for assigned salesperson stock
- Variances logged immediately
- Adjustments require Store Manager approval
- Large variances escalate to Area Manager

---

## 6. PAYMENT RULES

### Accepted Payment Methods
- Cash
- UPI
- Card
- Bank Transfer
- EMI
- Credit (for known customers only)
- Gift Voucher

### Partial Payment (Your Workflow)
1. Customer pays ADVANCE at order confirmation
2. Order confirmed with expected delivery date
3. Customer returns for collection
4. Customer pays FINAL (balance)
5. Order delivered

### Outstanding Tracking
- Credit customers tracked
- Outstanding aging reports
- Follow-up tasks auto-generated

---

## 7. ROLE & ACCESS RULES

### Multi-Role Support
- Single user can have MULTIPLE roles
- Example: Neha = Store Manager + Optometrist + Sales
- Highest role determines approval authority
- Combined permissions from all roles

### Geo-Location Login
- Staff MUST be within store radius to login
- Default radius: 500 meters
- Configurable per store
- HQ roles exempt from geo-check

### Role Hierarchy (1 = highest)
1. Superadmin (CEO)
2. Admin (Director)
3. Area Manager
4. Store Manager / Accountant / Catalog Manager
5. Optometrist
6. Sales Staff / Cashier
7. Fitting Staff

### Approval Rules
- Requester CANNOT approve own request
- Approver MUST be higher in hierarchy
- Approval chain cannot be skipped

---

## 8. AI RULES (CRITICAL)

### AI Access
- Superadmin ONLY
- Read-only by default
- Advisory only
- NO auto-execution

### AI Can Suggest
- Inventory optimization
- Discount abuse patterns
- Sales trends
- Staff performance insights

### AI CANNOT
- Execute changes
- Approve requests
- Block operations
- Override human decisions

### AI Approval Flow (If suggestions implemented)
1. AI generates suggestion
2. Superadmin reviews
3. Superadmin approves/rejects
4. If approved, system executes
5. Full audit trail maintained

---

## 9. GST COMPLIANCE

### GST Rules
- All invoices GST compliant
- HSN codes mandatory
- GSTIN validation via government API
- B2B invoices require GSTIN
- Inter-state = IGST
- Intra-state = CGST + SGST

### Invoice Types
- Tax Invoice (B2B/B2C)
- Delivery Challan
- Credit Note
- Debit Note

---

## 10. FORBIDDEN BEHAVIORS

### System MUST NOT
- Allow offer_price > mrp
- Allow lens sale without prescription
- Allow discount beyond role cap without approval
- Allow login outside geo-radius (for store staff)
- Delete audit logs
- Allow silent data modification
- Bypass approval chains
- Auto-execute AI suggestions

### System MUST
- Log every change
- Enforce pricing rules at database level
- Validate prescriptions before lens orders
- Check role permissions before every action
- Maintain full audit trail
- Escalate unresolved issues

---

## 11. STORE SETUP RULES

### Store Configuration (Superadmin Only)
- Store code (unique)
- Company assignment (Better Vision / WizOpt)
- GST number
- Geo-location (lat/lng)
- Geo-radius
- Opening/closing times
- Product categories enabled
- Hardware counts

### Category Enablement
- Each store can have different categories enabled
- Example: Hearing aids only at 1 store
- Wall clocks only at 2 stores
- Configurable by Superadmin

---

## 12. EMPLOYEE SETUP RULES

### Employee Onboarding
- Employee code (unique)
- Primary store assignment
- Role assignments (multiple allowed)
- Store access grants
- Shift assignment
- Salary structure
- Leave balances

### Employee Offboarding
- Stock count mandatory before leaving
- Access revoked immediately
- Pending tasks reassigned
- Final settlement calculated

---

## END OF SYSTEM_INTENT.md

**This document is the SUPREME AUTHORITY.**
**Any code that violates these rules is INCORRECT.**
**When in doubt, refer to this document.**
