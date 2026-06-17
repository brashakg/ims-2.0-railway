// ============================================================================
// IMS 2.0 - Capability helpers (frontend mirror of backend capabilities.py)
// ============================================================================
// The per-user permissions layer (council ruling sec.2) keys overrides on
// CAPABILITY strings (`<module>:read|write`). The frontend needs two small,
// purely client-side helpers:
//
//   1. permissionToCapability -- bridge the LEGACY dotted permission namespace
//      used by AuthContext.hasPermission ("pos.create", "reports.view") to the
//      capability namespace the override is stored under. Only the permissions
//      that an owner can actually toggle need a mapping; everything else returns
//      null and hasPermission stays DARK (role baseline unchanged).
//
//   2. isUngrantableCapability -- jarvis:* is never grantable via the per-user
//      layer (mirrors the backend ungrantable set; the FE never offers it as a
//      toggle, and hasPermission refuses to honour a forged grant of it).
//
// This is a UI hint layer ONLY. The authoritative enforcement is the backend
// middleware + the in-route gates; the frontend never grants access the server
// would deny. Keeping the mapping deliberately small avoids inventing a
// permission effect that did not exist before (DARK by default).

/** Map a legacy dotted permission to its capability key, or null when there is
 *  no override-relevant mapping (then hasPermission keeps the role baseline). */
export function permissionToCapability(permission: string): string | null {
  if (!permission) return null;
  // Direct capability form already (defensive -- callers may pass "orders:write").
  if (permission.includes(':')) return permission;

  // Curated bridge for the dotted permissions an owner can actually toggle.
  const MAP: Record<string, string> = {
    'pos.create': 'orders:write',
    'pos.discount': 'orders:write',
    'reports.view': 'reports:read',
    'finance.view': 'finance:read',
    'clinical.view': 'clinical:read',
    'inventory.view': 'inventory:read',
    'inventory.transfer': 'transfers:write',
    'customers.view': 'customers:read',
    'orders.view': 'orders:read',
  };
  if (MAP[permission]) return MAP[permission];

  // Generic "<cat>.<verb>" -> "<cat>:read|write" fallback for view/read/write.
  const [cat, verb] = permission.split('.');
  if (cat && verb) {
    if (verb === 'view' || verb === 'read') return `${cat}:read`;
    if (verb === 'write' || verb === 'edit' || verb === 'create' || verb === 'manage') {
      return `${cat}:write`;
    }
  }
  return null;
}

/** True when a capability can never be granted via the per-user layer
 *  (jarvis/AI is SUPERADMIN-only, non-negotiable). Mirrors backend
 *  capabilities.is_ungrantable for the jarvis class (the FE never sees the full
 *  SUPERADMIN-only set, and would never offer those toggles anyway). */
export function isUngrantableCapability(cap: string): boolean {
  return cap.startsWith('jarvis:');
}
