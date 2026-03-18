# Staff Incentive Tracking - Quick Reference

## Access
**URL:** `/hr/incentives` (in HR module sidebar)
**Required Role:** SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT

## Key Files
- **Backend:** `backend/api/routers/incentives.py` (600 lines)
- **Frontend:** `frontend/src/pages/hr/IncentiveDashboard.tsx` (559 lines)
- **API Service:** `frontend/src/services/api.ts` (incentivesApi)

## API Endpoints

### Get Dashboard (Personal)
```
GET /api/v1/incentives/dashboard?month=3&year=2026
```
Response includes current achievement, incentive breakdown, status

### Set Sales Target (Admin)
```
POST /api/v1/incentives/targets
{
  "staff_id": "user123",
  "target_amount": 100000,
  "month": 3,
  "year": 2026,
  "description": "Q1 target"
}
```

### Get Leaderboard
```
GET /api/v1/incentives/leaderboard?month=3&year=2026&limit=50
```
Returns ranked list of staff by achievement %

### Record Kicker Sale (Self or Admin)
```
POST /api/v1/incentives/kickers?staff_id=user123
{
  "brand": "Zeiss SmartLife",
  "product_name": "Progressive",
  "sale_amount": 5000,
  "sale_date": "2026-03-18"
}
```

### Get Kicker Summary
```
GET /api/v1/incentives/kickers/user123?month=3&year=2026
```

## Frontend Components

### Stats Cards (Top Section)
- Achievement % - color-coded progress bar
- Base Incentive - slab indicator
- Kicker Bonus - with min 3 qualifier status
- Total Incentive - highlighted in yellow

### Sales Target Progress
- Main progress bar (gradient colors)
- Slab indicators at 80%, 100%, 120%
- Next milestone info

### Kicker Tracker
- Total count, status, bonus amount
- Brand breakdown table
- Recent sales (last 5)

### Leaderboard
- Top 10 staff (expandable to 50)
- Medals for top 3 (🥇🥈🥉)
- Color-coded achievement %
- Formatted sales (1.2L format)

### Period Selector
- Month dropdown (Jan-Dec)
- Year dropdown (2024-2026)
- Auto-refreshes on change

## Incentive Calculation

```
Base Incentive = Sales × Rate
  Rate = 0.008 (80%+) | 0.010 (100%+) | 0.015 (120%+)

Kicker Bonus = ₹200 × count (if count >= 3)

Google Review = ₹25 per review

Total = Base + Kickers + Reviews
```

## Database Schema

**incentive_targets**
- target_id (uuid)
- staff_id, target_amount, month, year
- store_id, created_by, created_at

**kicker_sales**
- kicker_id (uuid)
- staff_id, brand, product_name, sale_amount
- sale_date, store_id, created_at

## Common Tasks

### As Store Manager
1. Navigate to HR → Incentive Tracking
2. Set staff targets: Click period, set targets via API
3. Review leaderboard: See achievement rankings
4. Approve kickers: Verify brand sales are recorded

### As Sales Staff
1. Navigate to HR → Incentive Tracking
2. View dashboard: Check current achievement
3. Record kickers: Log Zeiss/Safilo sales
4. Check leaderboard: See ranking position

## Color Scheme
- Green (#22c55e): Exceeded (100%+)
- Yellow (#eab308): High achiever (120%+)
- Blue (#3b82f6): Qualified (80%+)
- Red (#ef4444): Below target (<80%)
- Gray backgrounds: Dark theme

## Testing
- DB collections: `incentive_targets`, `kicker_sales`
- Seed data: Create targets first via admin
- Mock data: Sample kickers and leaderboard
- Period selector: Test month/year switching
