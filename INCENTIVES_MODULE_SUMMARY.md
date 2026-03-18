# Staff Incentive Tracking System - IMS 2.0

## Overview
A comprehensive incentive tracking system for optical retail staff, enabling sales target management, kicker program tracking, and real-time incentive calculations.

## Backend Implementation

### Router: `/sessions/blissful-brave-rubin/ims-audit/backend/api/routers/incentives.py`

**Endpoints:**

1. **GET `/api/v1/incentives/dashboard`**
   - Get incentive summary for current staff member
   - Returns: current month achievement, base incentive, kicker status, Google review bonus, total earned
   - Query params: `month` (optional), `year` (optional)

2. **GET `/api/v1/incentives/targets/{staff_id}`**
   - Get monthly sales targets for a staff member
   - Query params: `month` (optional), `year` (optional)

3. **POST `/api/v1/incentives/targets`** (Admin only)
   - Set/update monthly sales target for a staff member
   - Body: `{ staff_id, target_amount, month, year, description? }`

4. **GET `/api/v1/incentives/leaderboard`**
   - Get staff ranking by achievement percentage for current store
   - Returns: ranked list with achievement %, sales, and total incentive
   - Query params: `month` (optional), `year` (optional), `limit` (1-100, default 50)

5. **POST `/api/v1/incentives/kickers`** (Admin or self)
   - Record a kicker sale (Zeiss/Safilo brands)
   - Body: `{ brand, product_name?, sale_amount, sale_date? }`
   - Query params: `staff_id` (optional, defaults to current user)

6. **GET `/api/v1/incentives/kickers/{staff_id}`**
   - Get kicker summary for a staff member
   - Returns: kicker count, sales, bonus, brand breakdown, recent sales
   - Query params: `month` (optional), `year` (optional)

### Incentive Calculation Logic

```python
Achievement % = (actual_sales / target) * 100

Incentive Slabs:
- < 80%: No incentive (status: "Below Target")
- 80-99%: 0.8% of sales (status: "Qualified")
- 100-119%: 1% of sales (status: "Exceeded")
- 120%+: 1.5% of sales (status: "Exceeded")

Kicker Bonus: ₹200 per kicker (minimum 3 kickers to qualify)
Google Review Bonus: ₹25 per review (₹50 for ratings ≥ 4.5)

Total Incentive = Base Incentive + Kicker Bonus + Google Review Bonus
```

### Database Collections

**incentive_targets**
```json
{
  "target_id": "uuid",
  "staff_id": "user_id",
  "target_amount": 100000,
  "month": 3,
  "year": 2026,
  "description": "March monthly target",
  "store_id": "store_id",
  "created_by": "admin_id",
  "created_at": "2026-03-18T10:30:00"
}
```

**kicker_sales**
```json
{
  "kicker_id": "uuid",
  "staff_id": "user_id",
  "staff_name": "John Doe",
  "brand": "Zeiss SmartLife",
  "product_name": "Anti-reflective coating",
  "sale_amount": 5000,
  "sale_date": "2026-03-18",
  "store_id": "store_id",
  "created_by": "staff_id",
  "created_at": "2026-03-18T14:22:00"
}
```

## Frontend Implementation

### Component: `/sessions/blissful-brave-rubin/ims-audit/frontend/src/pages/hr/IncentiveDashboard.tsx`

**Features:**

1. **Stats Cards** (4 columns)
   - Achievement % with progress bar
   - Base Incentive with slab indicator
   - Kicker Bonus count and status
   - Total Incentive with status badge

2. **Sales Target Progress Section**
   - Main progress bar with gradient
   - Slab indicators (80%, 100%, 120%)
   - Incentive rate labels
   - Next milestone information

3. **Kicker Tracker**
   - Summary: Total kickers, Status, Bonus amount
   - Brand breakdown by count
   - Recent kicker sales table (last 5)
   - Visual status indicator (✓ if 3+)

4. **Incentive Calculation Breakdown**
   - Sales-based incentive with rate
   - Kicker bonus with minimum threshold info
   - Google review bonus (if any)
   - Total incentive display

5. **Staff Leaderboard**
   - Rank with medals (🥇🥈🥉)
   - Staff name
   - Achievement % (color-coded)
   - Actual sales (formatted as 1.2L)
   - Total incentive earned
   - Top 10 displayed (expandable to 50)

6. **Period Selector**
   - Month dropdown (Jan-Dec)
   - Year dropdown (2024-2026)
   - Real-time data refresh on change

### Dark Theme Styling
- Background: `bg-gray-800/900`
- Cards: Gradient backgrounds with borders
- Text: White headings, gray-400 labels
- Accents: Yellow (₹), Green (achieved), Blue (qualified), Red (below target)
- Icons: Lucide React icons with appropriate colors

### API Integration
```typescript
// incentivesApi methods:
- getDashboard(month?, year?)
- getLeaderboard(month?, year?, limit?)
- getStaffTargets(staffId, month?, year?)
- setTargets(data)
- recordKicker(data, staffId?)
- getKickers(staffId, month?, year?)
```

## Navigation Integration

### HR Module Sidebar Updated
Path: `/sessions/blissful-brave-rubin/ims-audit/frontend/src/context/ModuleContext.tsx`

New sidebar item added to HR module:
```typescript
{ id: 'hr-incentives', label: 'Incentive Tracking', path: '/hr/incentives' }
```

### Route Added
Path: `/sessions/blissful-brave-rubin/ims-audit/frontend/src/App.tsx`

New route:
```tsx
<Route
  path="hr/incentives"
  element={
    <ProtectedRoute
      allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}
    >
      <IncentiveDashboard />
    </ProtectedRoute>
  }
/>
```

## Access Control

**Allowed Roles:** SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT

**Admin-only Endpoints:**
- POST /incentives/targets (set targets)
- POST /incentives/kickers (record for other staff)

**Self-service:**
- GET /incentives/dashboard (own dashboard)
- GET /incentives/leaderboard (store-wide)
- POST /incentives/kickers (own sales)

## Key Features

✓ Real-time incentive calculations
✓ Dynamic slab-based commission rates
✓ Kicker program tracking (Zeiss, Safilo brands)
✓ Google review incentives
✓ Staff leaderboard with rankings
✓ Monthly period selection
✓ Color-coded progress indicators
✓ Admin target management
✓ Dark theme UI
✓ Mobile responsive layout

## File Changes Summary

### Created Files
1. `/backend/api/routers/incentives.py` (600 lines)
2. `/frontend/src/pages/hr/IncentiveDashboard.tsx` (559 lines)

### Modified Files
1. `/backend/api/routers/__init__.py` - Added incentives_router import
2. `/backend/api/main.py` - Added incentives_router registration
3. `/frontend/src/context/ModuleContext.tsx` - Added Incentive Tracking to HR sidebar
4. `/frontend/src/App.tsx` - Added IncentiveDashboard import and route
5. `/frontend/src/services/api.ts` - Added incentivesApi with 6 methods

## Testing Checklist

- [ ] Backend: All 6 endpoints respond with correct status codes
- [ ] Frontend: Dashboard loads with mock data
- [ ] Frontend: Period selector updates data
- [ ] Frontend: Leaderboard displays ranked staff
- [ ] Frontend: Kicker table shows recent sales
- [ ] Frontend: Responsive design on mobile/tablet
- [ ] Auth: Access control verified for all endpoints
- [ ] Database: Collections created and indexed

## Notes

- All dates stored as ISO 8601 strings in MongoDB
- Achievement % calculations use decimal arithmetic (not integer)
- Kicker bonus requires minimum 3 qualifying sales
- Leaderboard filtered by active store
- API responses include camelCase conversion for frontend
