# Feature #28: Geofenced & IP-Restricted Attendance
META: effort=M days=4 risk=MED roi=4 quickwin=no deps=none phase=3

## Existing overlap
IMS already has a working geo-fence attendance system. The current implementation (backend/api/routers/hr.py lines 772-905, backend/api/services/attendance_engine.py) enforces a **500m GPS radius** for roles 4-7, using haversine distance against a per-store lat/lng. Check-in/check-out stamps land on the `attendance` collection with `geo_verified` boolean. The `evaluate_geofence` function in attendance_engine.py is the exact hook to extend. There is **no IP allowlist** path today — this is the only genuine gap.

## Reuse (extend, don't rebuild)
- `backend/api/routers/hr.py` — extend the POST `/attendance/check-in` and `/attendance/check-out` handlers; the geo-fence block already exists, add an IP-bypass branch before the geo reject
- `backend/api/services/attendance_engine.py` — extend `evaluate_geofence()` to return a richer `AttendanceAuthResult` carrying the method used (GPS_PASS / IP_PASS / GPS_FAIL_IP_FAIL) rather than a plain boolean
- `attendance` collection (backend/database/schemas.py) — add fields, no new collection needed
- `stores` collection — add `allowed_ip_ranges` array field (CIDR strings) — per-store allowlist owned by ADMIN
- Settings page `frontend/src/pages/settings/` — extend Store Settings section with an "Allowed IPs" list editor (same restrained table pattern as other settings)

## Data model
No new collection. Extend existing documents:

**`stores` collection — new fields:**
```
allowed_ip_ranges: List[str]   # CIDR blocks e.g. ["203.0.113.0/24"]; empty = IP check disabled for that store
ip_fence_enabled: bool          # master toggle per store; default false
geofence_radius_m: int          # currently hardcoded 500; move to per-store; default 500
```

**`attendance` collection — new fields:**
```
auth_method: str    # "GPS" | "IP" | "GPS+IP" | "OVERRIDE" — how the clock-in was authorised
client_ip: str      # requester IP at check-in time (for audit; never shown in UI)
ip_matched_range: str  # which CIDR matched, if IP auth used
```

## Backend

- **`GET /stores/{store_id}/ip-fence`** (ADMIN/SUPERADMIN) — read current `allowed_ip_ranges` + `ip_fence_enabled` + `geofence_radius_m` for a store
- **`PUT /stores/{store_id}/ip-fence`** (ADMIN/SUPERADMIN) — write the three fields; validate each entry is parseable as IPv4/IPv6 CIDR (Python `ipaddress.ip_network(strict=False)`); reject private RFC-1918 ranges that would let anyone on any home network pass (configurable guard)
- **`attendance_engine.evaluate_geofence()`** — refactor signature to `evaluate_attendance_auth(lat, lng, client_ip, store) -> AttendanceAuthResult`. Logic: (1) if GPS within `store.geofence_radius_m` → GPS_PASS; (2) else if `store.ip_fence_enabled` and client_ip matches any CIDR in `store.allowed_ip_ranges` → IP_PASS; (3) else → FAIL with reason. Return dataclass with `allowed: bool`, `method: str`, `matched_range: str | None`.
- **`POST /attendance/check-in`** and **`check-out`** (hr.py) — extract real client IP via `request.headers.get("X-Forwarded-For", request.client.host).split(",")[0].strip()` (Railway sits behind a proxy); pass to `evaluate_attendance_auth()`; stamp `auth_method` + `client_ip` + `ip_matched_range` on the attendance doc; log to `audit_logs` with the auth method used.
- **`GET /attendance/auth-log`** (STORE_MANAGER/ADMIN/SUPERADMIN, store-scoped) — returns attendance rows with `auth_method` filter, for review of IP-only clock-ins (the "suspicious" list)

## Frontend

- **Store Settings → IP Fence section** (extend existing settings page, restrained table UI):
  - Toggle: "Enable WiFi IP allowlist" (maps to `ip_fence_enabled`)
  - Text input + Add button: enter a CIDR or single IP; validated client-side with a simple regex, confirmed server-side
  - Table of current allowed ranges with a Remove button per row
  - Geofence radius input (metres, numeric, 50–5000 range slider or number field)
  - Save button — single PUT to `/stores/{id}/ip-fence`

- **Attendance page** (frontend/src/pages/attendance/AttendancePage.tsx) — extend the clock-in confirmation card:
  - Show the auth method badge: "GPS verified" (green) / "WiFi verified" (blue) / "GPS + WiFi" (green) — colour has semantic meaning only
  - On failed auth: show which method was attempted and failed ("Outside store zone and not on store WiFi")

- **Manager attendance grid** — add an "Auth Method" column (GPS / IP / GPS+IP / OVERRIDE) so managers can spot IP-only patterns at a glance

## Business rules
- GPS check is always attempted first; IP is a fallback, not a replacement — a device inside the GPS radius is always GPS_PASS regardless of IP
- If both GPS and IP fail, clock-in is hard-blocked for roles 4-7 (same as today)
- `client_ip` is extracted server-side from X-Forwarded-For; the client app never sends it — prevents spoofing
- Private RFC-1918 IPs (10.x, 172.16-31.x, 192.168.x) must be explicitly allowed by an admin (they are not auto-rejected, but the settings UI warns: "This is a private IP range; any device on a similarly-configured network could match")
- `auth_method=OVERRIDE` reserved for future SUPERADMIN manual override with audit reason; not wired in this phase
- All clock-in attempts (pass or fail) are written to `audit_logs` with `client_ip`, `lat/lng`, `auth_method`, `store_id`
- `ip_matched_range` is stored on the attendance doc but masked from non-admin UI (security: don't expose internal IP topology to sales staff)
- Changing `allowed_ip_ranges` or `ip_fence_enabled` writes an audit row (before/after) — same pattern as credential changes in settings.py

## RBAC
- **SUPERADMIN / ADMIN**: read + write IP fence config for any store
- **STORE_MANAGER**: read IP fence config for their store only (cannot edit — prevents self-grant)
- **AREA_MANAGER**: read IP fence config for stores in their area; no write
- **OPTOMETRIST / SALES_CASHIER / SALES_STAFF / CASHIER / WORKSHOP_STAFF** (roles 4-7): see only their own clock-in auth method badge; no config access
- Roles 1-3 are geo-exempt (unchanged from current code); IP fence does not apply to them

## Integrations
- None required. No MSG91/Shopify/Razorpay/Tally/Jarvis dependency.
- JARVIS/ORACLE could later flag stores with a high ratio of IP-only clock-ins as an anomaly signal — hook point exists via `dispatch_event("attendance.ip_only_ratio_high", ...)` from TASKMASTER's 5-min tick, but not in scope for this phase.

## Risk notes
- **Proxy IP trust**: Railway runs behind a load balancer; X-Forwarded-For must be trusted correctly. The leftmost IP in the chain is the client; the rightmost is the Railway proxy. A misconfigured extraction would let anyone spoof their IP. Mitigation: use a hardened helper that validates the chain length matches Railway's known proxy count (RAILWAY_PROXY_HOPS env, default 1).
- **Dynamic IPs / ISP rotation**: Store WiFi on a residential/dynamic IP plan will have its IP change periodically. Admin must update the allowlist when ISP changes IP. Mitigation: CIDR range entry (e.g. /28 block) rather than single IPs; no auto-detection.
- **VPN bypass risk**: A staff member on a VPN that exits from an allowed IP range would pass the IP check. Cannot prevent without device-level MDM. This is an accepted residual risk; audit logs will surface suspicious IP-only clock-ins.
- **No POS or money impact** — this feature touches only the attendance collection and store settings. Zero POS/order/payment code is modified. No feature flag needed for the backend logic, but the `ip_fence_enabled` per-store toggle is itself the rollout flag.
- **geofence_radius_m migration**: currently 500m is hardcoded in attendance_engine.py. Moving it to per-store DB field requires a one-time migration to set `geofence_radius_m: 500` on all existing store docs; straightforward upsert script.

## Recommendation
Build later — the existing GPS geo-fence already covers the core time-theft risk for the current 6-store footprint. IP allowlist adds meaningful value when stores have stable business-grade internet with fixed IPs. Confirm WiFi setup (below) before building.

## Owner decisions
- Q: Do any of the 6 stores currently have a fixed/static IP from their ISP, or are they on residential dynamic-IP plans? | Why: If all stores are on dynamic IPs, the IP allowlist is unmanageable without a CIDR range that is too broad to be useful. If even 1-2 stores have static IPs, ship for those first. | Options: a) static IP at most/all stores — build now; b) dynamic IP — defer until stores upgrade to business broadband with a static IP; c) unknown — ask store manager to check router WAN IP stability over 7 days before committing
- Q: Should a STORE_MANAGER be able to add/remove allowed IPs for their own store, or is that ADMIN-only? | Why: Allowing store managers to self-configure the IP fence means a colluding manager could add their home IP and clock in from home. ADMIN-only is the safer default but adds friction. | Options: a) ADMIN/SUPERADMIN only (recommended — prevents self-grant); b) STORE_MANAGER can edit but every change triggers an alert to ADMIN; c) STORE_MANAGER read-only, ADMIN write-only
- Q: What should happen when a staff member's phone GPS is off/denied but they are on the store WiFi — allow clock-in or block? | Why: Some staff disable location permissions. IP-only clock-in is less trustworthy but still reduces buddy-punching (phone must be on the store network). | Options: a) allow IP-only clock-in (permissive — reduces friction, some anti-buddy-punch value); b) require GPS even if IP matches (strict — forces location permission on); c) allow IP-only but flag the attendance row for manager review
- Q: What geofence radius do you want per store — keep 500m for all, or set it differently per location? | Why: A mall store with 6 other optical shops within 500m might want a tighter 100m radius; a standalone store in a commercial area might be fine with 500m. | Options: a) keep 500m globally; b) set per-store (requires the migration); c) tighten globally to 100–200m