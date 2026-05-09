// Per-user feature catalog. Used to:
//   1. Decide which sidebar items to render for a given user.
//   2. Gate API routes via requireFeature() in apiAuth.ts.
//   3. Power the "Permissions" toggles on the user create / edit form.
//
// User.enabledFeatures (String, optional) holds a comma-separated list
// of FeatureKey values. NULL means "use the role's defaults" — the
// most common case. An explicit list always wins over the role default,
// so an admin can grant a CATALOG_MANAGER access to "reports" without
// changing their role, or revoke "stock_transfers" from a specific
// staff member.

export type Role = "ADMIN" | "DESIGN_MANAGER" | "CATALOG_MANAGER";

export type FeatureKey =
  // Main / catalog
  | "products"
  | "orders"
  | "customers"
  | "collections"
  // Operations
  | "stock_tally"
  | "stock_transfers"
  | "stock_import"
  | "design_queue"
  | "shopify_sync"
  // Insights
  | "reports"
  | "marketing"
  | "store_health"
  // Admin
  | "attributes"
  | "discount_rules"
  | "locations"
  | "users"
  | "images"
  | "activity_logs"
  | "orphan_audit"
  // Round 2 collections + menu rollout (2026-05-09)
  | "storefront_menus"
  | "tag_casing_migration"
  | "auto_collections";

export interface FeatureDef {
  key: FeatureKey;
  label: string;
  description: string;
  group: "main" | "ops" | "insights" | "admin";
  /** Roles this feature defaults ON for. ADMIN always implicitly has all
   *  features; this field controls DESIGN_MANAGER and CATALOG_MANAGER. */
  defaultsFor: Role[];
}

export const FEATURES: FeatureDef[] = [
  // ─── Main ─────────────────────────────────────────
  {
    key: "products",
    label: "Products",
    description: "Browse, create, and edit the product catalog.",
    group: "main",
    defaultsFor: ["DESIGN_MANAGER", "CATALOG_MANAGER"],
  },
  {
    key: "orders",
    label: "Orders",
    description: "View Shopify orders and customer purchases.",
    group: "main",
    defaultsFor: [],
  },
  {
    key: "customers",
    label: "Customers",
    description: "Customer list and analytics.",
    group: "main",
    defaultsFor: [],
  },
  {
    key: "collections",
    label: "Collections",
    description: "Manage Shopify collections and assignments.",
    group: "main",
    defaultsFor: ["CATALOG_MANAGER"],
  },

  // ─── Operations ───────────────────────────────────
  {
    key: "stock_tally",
    label: "Stock tally",
    description: "Barcode-based physical stock counting.",
    group: "ops",
    defaultsFor: ["CATALOG_MANAGER"],
  },
  {
    key: "stock_transfers",
    label: "Stock transfers",
    description: "Move inventory between physical locations.",
    group: "ops",
    defaultsFor: ["CATALOG_MANAGER"],
  },
  {
    key: "stock_import",
    label: "Backup & restore",
    description: "Excel bulk imports and DB backup.",
    group: "ops",
    defaultsFor: [],
  },
  {
    key: "design_queue",
    label: "Design queue",
    description: "Image edit pipeline (raw → edited → approved).",
    group: "ops",
    defaultsFor: ["DESIGN_MANAGER"],
  },
  {
    key: "shopify_sync",
    label: "Shopify sync",
    description: "Push to / pull from Shopify, manage webhooks.",
    group: "ops",
    defaultsFor: [],
  },

  // ─── Insights ─────────────────────────────────────
  {
    key: "reports",
    label: "Reports",
    description: "Sales, inventory, and customer reports.",
    group: "insights",
    defaultsFor: [],
  },
  {
    key: "marketing",
    label: "Marketing",
    description: "Campaign metrics, customer segments.",
    group: "insights",
    defaultsFor: [],
  },
  {
    key: "store_health",
    label: "Store health",
    description: "SEO audit, AI generation, store quality checks.",
    group: "insights",
    defaultsFor: [],
  },

  // ─── Admin ────────────────────────────────────────
  {
    key: "attributes",
    label: "Attributes",
    description: "Manage attribute types and option values.",
    group: "admin",
    defaultsFor: ["CATALOG_MANAGER"],
  },
  {
    key: "discount_rules",
    label: "Discount rules",
    description: "Per-category × brand × sub-brand discounts.",
    group: "admin",
    defaultsFor: [],
  },
  {
    key: "locations",
    label: "Locations",
    description: "Physical store locations.",
    group: "admin",
    defaultsFor: [],
  },
  {
    key: "users",
    label: "Users",
    description: "Add and manage users.",
    group: "admin",
    defaultsFor: [],
  },
  {
    key: "images",
    label: "Images",
    description: "Image library and uploads.",
    group: "admin",
    defaultsFor: ["DESIGN_MANAGER"],
  },
  {
    key: "activity_logs",
    label: "Activity logs",
    description: "Audit trail of all user actions.",
    group: "admin",
    defaultsFor: [],
  },
  {
    key: "orphan_audit",
    label: "Orphan audit",
    description: "Manage products that never reached Shopify.",
    group: "admin",
    defaultsFor: [],
  },
  {
    key: "storefront_menus",
    label: "Storefront menus",
    description: "Edit BetterVision Shopify storefront navigation menus (round 2 mapping M1).",
    group: "admin",
    defaultsFor: [],
  },
  {
    key: "tag_casing_migration",
    label: "Tag casing migration",
    description: "One-shot migration to lowercase Shopify smart-collection rule conditions (round 2 C6).",
    group: "admin",
    defaultsFor: [],
  },
  {
    key: "auto_collections",
    label: "Auto collections",
    description: "Auto-generate brand × category × shape × gender smart collections (round 2 C2/C3/C4).",
    group: "admin",
    defaultsFor: [],
  },
];

const ALL_KEYS = FEATURES.map((f) => f.key);

/** Default feature list for a role. ADMIN gets everything implicitly. */
export function defaultFeaturesForRole(role: string | null | undefined): FeatureKey[] {
  if (role === "ADMIN") return ALL_KEYS;
  if (!role) return [];
  return FEATURES.filter((f) => f.defaultsFor.includes(role as Role)).map(
    (f) => f.key
  );
}

/**
 * Resolve the effective feature list for a user. If `enabledFeatures` is
 * an explicit list (non-null, even if empty string), it overrides the
 * role defaults. If null, role defaults apply.
 *
 * Note: ADMIN always has all features even if enabledFeatures is "" — we
 * treat null AND empty as "use role default" for safety, so an admin
 * can never accidentally lock themselves out.
 */
export function effectiveFeatures(user: {
  role?: string | null;
  enabledFeatures?: string | null;
}): FeatureKey[] {
  if (user.role === "ADMIN") return ALL_KEYS;
  const raw = user.enabledFeatures;
  if (raw === null || raw === undefined) return defaultFeaturesForRole(user.role);
  const trimmed = raw.trim();
  if (trimmed === "") return defaultFeaturesForRole(user.role);
  // Filter to known keys so a stale stored value can't grant
  // privileges that no longer exist.
  const set = new Set<string>(ALL_KEYS);
  return trimmed
    .split(",")
    .map((k) => k.trim())
    .filter((k): k is FeatureKey => set.has(k));
}

/** Convenience: does this user have access to feature `key`? */
export function userHasFeature(
  user: { role?: string | null; enabledFeatures?: string | null },
  key: FeatureKey
): boolean {
  return effectiveFeatures(user).includes(key);
}

/** Serialize a feature key list back into the comma-separated form
 *  stored on the User. Always normalizes order so equal sets produce
 *  the same string. */
export function serializeFeatures(keys: FeatureKey[]): string {
  const valid = new Set<string>(ALL_KEYS);
  return Array.from(new Set(keys))
    .filter((k) => valid.has(k))
    .sort()
    .join(",");
}
