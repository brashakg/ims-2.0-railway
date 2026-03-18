# Vendor Returns / Debit Notes Workflow - IMS 2.0

## Overview
Complete vendor returns workflow for optical retail, enabling stores to manage defective product returns, track credit notes, and request replacements from vendors.

---

## Backend Implementation

### File: `/backend/api/routers/vendor_returns.py`

**Core Features:**
- RESTful API endpoints for vendor return management
- MongoDB integration with vendor_returns collection
- Status workflow with validation (created → approved → shipped → received_by_vendor → credit_issued/replaced)
- Automatic credit note number generation
- Complete audit trail with status_history

**Endpoints:**

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/vendor-returns/` | List returns with optional filtering by store_id, vendor_id, status |
| `POST` | `/api/v1/vendor-returns/` | Create new vendor return |
| `GET` | `/api/v1/vendor-returns/{return_id}` | Get return details |
| `PATCH` | `/api/v1/vendor-returns/{return_id}/status` | Update return status |

**Database Schema (MongoDB collection: vendor_returns):**

```javascript
{
    "return_id": "VR-20240318-A1B2C3D4",
    "vendor_id": "V001",
    "vendor_name": "Optical Frames Ltd",
    "store_id": "STORE001",
    "items": [
        {
            "product_id": "PROD001",
            "product_name": "UV Protected Frames",
            "quantity": 5,
            "reason": "defective",
            "unit_price": 500
        }
    ],
    "return_type": "credit_note",  // or "replacement"
    "status": "credit_issued",
    "total_value": 2500,
    "credit_note_number": "CN-240318A1B2-ABC123",
    "credit_note_amount": 2500,
    "notes": "Defective frames due to poor quality control",
    "created_at": "2024-03-18T10:30:00Z",
    "created_by": "user_id",
    "status_history": [
        {
            "status": "created",
            "timestamp": "2024-03-18T10:30:00Z",
            "changed_by": "user_id",
            "notes": "Return created"
        },
        {
            "status": "approved",
            "timestamp": "2024-03-18T10:45:00Z",
            "changed_by": "manager_id",
            "notes": ""
        }
        // ... more status changes
    ]
}
```

**Status Workflow:**
```
created ──> approved ──> shipped ──> received_by_vendor ──> credit_issued
                                                          └──> replaced
         └──> cancelled (at any point)
```

**Return Reasons:**
- defective
- wrong_item
- expired
- damaged_in_transit
- quality_issue
- not_as_ordered
- other

**Return Types:**
- credit_note: Vendor issues a credit memo
- replacement: Vendor sends replacement items

---

## Frontend Implementation

### File: `/frontend/src/pages/purchase/VendorReturns.tsx`

**Component Features:**
- Summary dashboard with key metrics
- Tab navigation: Active Returns | History
- Create Return modal with vendor selection and item management
- Expandable return cards showing full details
- Status-aware action buttons
- Dark theme (bg-gray-800/900, text-white, border-gray-700)

**Key Metrics (Summary Cards):**
1. Total Returns - Total number of returns created
2. Pending Credits - Returns awaiting credit issue (approved + received_by_vendor)
3. Credit Value - Total credit value from issued credit notes
4. This Month - Returns created this month

**Tab Views:**
- **Active Returns**: Shows returns not yet credited or replaced
- **History**: Shows completed returns (credited, replaced, cancelled)

**Modal: Create Return**
- Vendor selection dropdown (populated from vendors API)
- Item entry with product search and quantity selection
- Return reason selection (defective, wrong_item, etc.)
- Return type toggle (Credit Note / Replacement)
- Dynamic total calculation
- Additional notes field

**Return Cards Display:**
- Vendor name with return ID
- Status badge with color coding
- Return type indicator (Credit Note/Replacement)
- Total return value and item count
- Expandable details showing:
  - Line items with quantity, reason, and subtotal
  - Notes and credit note number (if issued)
  - Status-appropriate action buttons:
    - Approve (from 'created')
    - Mark as Shipped (from 'approved')
    - Received by Vendor (from 'shipped')
    - Issue Credit / Mark as Replaced (from 'received_by_vendor')

**Status Color Coding:**
- created: Blue
- approved: Cyan
- shipped: Purple
- received_by_vendor: Orange
- credit_issued: Green
- replaced: Green
- cancelled: Red

**Integration with Purchase Management Page:**
- Added as tab in PurchaseManagementPage
- Accessible via `/purchase?tab=vendor-returns`
- Shares same navigation structure with Purchase Orders and Suppliers

---

## Integration Points

### Backend Routes Registered
File: `/backend/api/main.py`
```python
app.include_router(vendor_returns_router, prefix="/api/v1/vendor-returns", tags=["Vendor Returns"])
```

### Router Exports
File: `/backend/api/routers/__init__.py`
- Added: `from .vendor_returns import router as vendor_returns_router`
- Added to `__all__`: "vendor_returns_router"

### Frontend Integration
File: `/frontend/src/pages/purchase/PurchaseManagementPage.tsx`
- Added: `import { VendorReturns } from './VendorReturns';`
- Updated TabType to include 'vendor-returns'
- Added tab button with AlertTriangle icon
- Integrated VendorReturns component render

---

## API Usage Examples

### Create a Vendor Return
```bash
POST /api/v1/vendor-returns/
Content-Type: application/json

{
    "vendor_id": "V001",
    "vendor_name": "Optical Frames Ltd",
    "store_id": "STORE001",
    "items": [
        {
            "product_id": "PROD001",
            "product_name": "UV Protected Frames",
            "quantity": 5,
            "reason": "defective",
            "unit_price": 500
        }
    ],
    "return_type": "credit_note",
    "notes": "Defective frames due to poor quality control"
}
```

### List Returns with Filters
```bash
GET /api/v1/vendor-returns/?store_id=STORE001&status=approved&limit=20
```

### Update Return Status
```bash
PATCH /api/v1/vendor-returns/VR-20240318-A1B2C3D4/status
Content-Type: application/json

{
    "status": "credit_issued",
    "notes": "Credit note issued"
}
```

---

## Use Cases

### Case 1: Defective Product Return (Credit Note)
1. Store receives defective frames from vendor
2. Creates vendor return with reason "defective"
3. Sets return_type to "credit_note"
4. Status progresses: created → approved → shipped → received_by_vendor → credit_issued
5. Credit note automatically generated when transitioning to credit_issued

### Case 2: Damaged in Transit (Replacement)
1. Store receives damaged lenses
2. Creates vendor return with reason "damaged_in_transit"
3. Sets return_type to "replacement"
4. Status progresses: created → approved → shipped → received_by_vendor → replaced
5. Vendor sends replacement items

### Case 3: Wrong Item Returned
1. Store creates return for wrong item
2. Updates status through workflow
3. If resolved with credit, transitions to credit_issued
4. If resolved with replacement, transitions to replaced

---

## Data Validation

**Status Transition Rules:**
- created → approved, cancelled
- approved → shipped, cancelled
- shipped → received_by_vendor
- received_by_vendor → credit_issued, replaced
- credit_issued → (no transitions)
- replaced → (no transitions)
- cancelled → (no transitions)

**Required Fields for Creation:**
- vendor_id, vendor_name
- store_id
- items (non-empty array with product_id, product_name, quantity, reason, unit_price)
- return_type (credit_note or replacement)

---

## Future Enhancements

1. **Integration with Inventory:**
   - Automatically reverse stock for returned items
   - Update product status when replacements are received

2. **Vendor Dashboard:**
   - Vendors can view their pending returns
   - Automatic notification when return status changes

3. **Financial Reconciliation:**
   - Link credit notes to vendor account balances
   - Generate reconciliation reports

4. **Debit Note Support:**
   - For vendor billing adjustments
   - Separate workflow if needed

5. **Batch Returns:**
   - Combine multiple returns into single shipment
   - Batch credit note processing

6. **Return Inspection:**
   - Add inspection checklist before accepting returned items
   - Photo evidence support

7. **Analytics:**
   - Return rate by vendor
   - Most common failure reasons
   - Credit note trends

---

## Files Created/Modified

### Created:
- `/backend/api/routers/vendor_returns.py` - Backend router (9.3 KB)
- `/frontend/src/pages/purchase/VendorReturns.tsx` - Frontend component (25.8 KB)

### Modified:
- `/backend/api/routers/__init__.py` - Added vendor_returns_router import
- `/backend/api/main.py` - Added vendor_returns_router import and registration
- `/frontend/src/pages/purchase/PurchaseManagementPage.tsx` - Added VendorReturns tab and import

---

## Deployment Checklist

- [x] Backend router created and registered
- [x] Frontend component created and integrated
- [x] Tab navigation added to PurchaseManagementPage
- [ ] MongoDB index creation for vendor_returns collection
- [ ] Test create vendor return endpoint
- [ ] Test status update workflow
- [ ] Test filtering by store_id, vendor_id, status
- [ ] Test frontend modal and return cards
- [ ] Integration testing with vendors API
- [ ] Update API documentation
- [ ] User acceptance testing

---

**Status:** Ready for testing and integration
**Last Updated:** 2024-03-18
