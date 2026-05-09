// Menu cleanup tasks (round 2 mapping CL1-CL8). Declarative spec for the
// audit fixes the user approved. The actual executor wires into the
// menu push helper from src/lib/menus/shopifyMenus.ts (built by the
// menu editor batch). Each task knows what to do and to which item;
// the executor knows how.
//
// Task statuses are tracked via prisma.activityLog so re-runs are
// idempotent (skip already-done).

export interface MenuOpTrim {
  type: "TRIM_TITLE";
  menuHandle: string;
  /** Match by exact (whitespace-included) title in current Shopify state. */
  matchTitle: string;
  /** Path of titles from root to the item being trimmed. */
  path: string[];
  newTitle: string;
}

export interface MenuOpDeleteItem {
  type: "DELETE_ITEM";
  menuHandle: string;
  path: string[];
  reason: string;
}

export interface MenuOpUpdateItem {
  type: "UPDATE_ITEM";
  menuHandle: string;
  path: string[];
  changes: {
    title?: string;
    itemType?: string;
    url?: string;
    resourceId?: string | null;
  };
  reason: string;
}

export interface MenuOpMoveItem {
  type: "MOVE_ITEM";
  menuHandle: string;
  fromPath: string[];
  toParentPath: string[];
  /** New position under target parent (0-indexed). */
  position: number;
  reason: string;
}

export interface MenuOpReplaceItem {
  type: "REPLACE_ITEM";
  menuHandle: string;
  path: string[];
  with: {
    title: string;
    itemType: string;
    url?: string;
    resourceId?: string | null;
  };
  reason: string;
}

export interface MenuOpDeactivateMenu {
  type: "DEACTIVATE_MENU";
  menuHandle: string;
  reason: string;
}

export type MenuOp =
  | MenuOpTrim
  | MenuOpDeleteItem
  | MenuOpUpdateItem
  | MenuOpMoveItem
  | MenuOpReplaceItem
  | MenuOpDeactivateMenu;

export interface MenuCleanupTask {
  id: string; // CL1, CL2, ...
  title: string;
  description: string;
  ops: MenuOp[];
}

/* ------------------------------------------------------------------
 * The plan — drives the cleanup endpoint. Order matters: trims first
 * (cheap, low-risk), then structural moves, then deletions, then
 * type-conversions. CL2 + CL5 are destructive; the endpoint requires
 * { commit: true } before applying any of these.
 * ------------------------------------------------------------------ */
export const MENU_CLEANUP_TASKS: MenuCleanupTask[] = [
  {
    id: "CL1",
    title: "Trim whitespace in 4 menu titles",
    description:
      "Round 1 audit A1. Cosmetic but visible: 'By style ', 'Best Seller ', 'RAYBAN ', and ' Michael Kors' have leading or trailing whitespace.",
    ops: [
      {
        type: "TRIM_TITLE",
        menuHandle: "main-menu",
        matchTitle: "By style ",
        path: ["Shop", "By style "],
        newTitle: "By style",
      },
      {
        type: "TRIM_TITLE",
        menuHandle: "main-menu",
        matchTitle: "Best Seller ",
        path: ["Sunglasses", "Best Seller "],
        newTitle: "Best Seller",
      },
      {
        type: "TRIM_TITLE",
        menuHandle: "main-menu",
        matchTitle: "RAYBAN ",
        path: ["Opticals", "Best Seller ", "RAYBAN "],
        newTitle: "RAYBAN",
      },
      {
        type: "TRIM_TITLE",
        menuHandle: "main-menu",
        matchTitle: " Michael Kors",
        path: ["Brands", "M-Q", " Michael Kors"],
        newTitle: "Michael Kors",
      },
      {
        type: "TRIM_TITLE",
        menuHandle: "main-menu",
        matchTitle: "Best Seller ",
        path: ["Opticals", "Best Seller "],
        newTitle: "Best Seller",
      },
    ],
  },
  {
    id: "CL2",
    title: "Drop duplicate Ray-Ban Meta inside Brands → R-Z",
    description:
      "Round 1 audit A2. 'Ray Ban X Meta' lives at top-level AND inside Brands → R-Z (same collection gid 451212902649). Per user CL2 = 'Top-level only — drop the duplicate in Brands'.",
    ops: [
      {
        type: "DELETE_ITEM",
        menuHandle: "main-menu",
        path: ["Brands", "R-Z", "Ray-Ban Meta"],
        reason:
          "Duplicate of top-level 'Ray Ban X Meta'. Keep only the top-level entry.",
      },
    ],
  },
  {
    id: "CL3",
    title: "Convert COLLECTOR'S EDITION from HTTP to COLLECTION type",
    description:
      "Round 1 audit A4. Currently uses link type HTTP but URL is /collections/collectors-edition. Should be COLLECTION type with resourceId so it routes through Shopify's collection picker and gets click tracking.",
    ops: [
      {
        type: "UPDATE_ITEM",
        menuHandle: "main-menu",
        path: ["COLLECTOR'S EDITION"],
        changes: {
          itemType: "COLLECTION",
          // resourceId resolved at runtime via collection-by-handle lookup;
          // the executor fills it in before pushing.
          resourceId: null, // sentinel — executor resolves "collectors-edition" handle
          url: undefined,
        },
        reason:
          "Convert from external HTTP link to native Shopify COLLECTION reference for tracking.",
      },
    ],
  },
  {
    id: "CL4",
    title: "Move Oakley/Carrera/Rayban from Best Seller → TOP BRAND under Opticals",
    description:
      "Round 1 audit A6. Three brand items are nested under Opticals → Best Seller, which is mis-nested. They belong under TOP BRAND.",
    ops: [
      {
        type: "MOVE_ITEM",
        menuHandle: "main-menu",
        fromPath: ["Opticals", "Best Seller", "OAKLEY"],
        toParentPath: ["Opticals", "TOP BRAND"],
        position: 0,
        reason: "Mis-nested. OAKLEY belongs under TOP BRAND.",
      },
      {
        type: "MOVE_ITEM",
        menuHandle: "main-menu",
        fromPath: ["Opticals", "Best Seller", "CARRERA"],
        toParentPath: ["Opticals", "TOP BRAND"],
        position: 1,
        reason: "Mis-nested. CARRERA belongs under TOP BRAND.",
      },
      {
        type: "MOVE_ITEM",
        menuHandle: "main-menu",
        fromPath: ["Opticals", "Best Seller", "RAYBAN"],
        toParentPath: ["Opticals", "TOP BRAND"],
        position: 2,
        reason: "Mis-nested. RAYBAN belongs under TOP BRAND.",
      },
    ],
  },
  {
    id: "CL5",
    title: "Replace 'Eyewear suitcase' product link with 'Cases & Bags' sub-collection",
    description:
      "Round 1 audit A7. Single-product link in nav menu. Per user CL5 = 'Replace with a Cases & Bags sub-collection'. The executor first checks for an existing 'cases-bags' collection; if not present, creates one (a manual collection) and links to it.",
    ops: [
      {
        type: "REPLACE_ITEM",
        menuHandle: "main-menu",
        path: ["Accessories", "Eyewear suitcase"],
        with: {
          title: "Cases & Bags",
          itemType: "COLLECTION",
          // executor resolves/creates 'cases-bags' handle
          resourceId: null,
        },
        reason:
          "Single-product menu links rarely make sense. Replace with a Cases & Bags collection covering the broader category.",
      },
    ],
  },
  {
    id: "CL6",
    title: "Update Track Your Order link from http:// to https://",
    description:
      "Round 1 audit A8. Currently 'http://bettervision.shiprocket.co/'. Mixed-content warning on modern browsers. Switch to https.",
    ops: [
      {
        type: "UPDATE_ITEM",
        menuHandle: "main-menu",
        path: ["Support", "Track Your Order"],
        changes: {
          url: "https://bettervision.shiprocket.co/",
        },
        reason: "https avoids mixed-content browser warnings.",
      },
    ],
  },
  {
    id: "CL7",
    title:
      "Consolidate to 3 menus — drop 'additional-pages' and 'desktop-megamenu-eyewear'",
    description:
      "Round 1 audit A9 + round 2 user M8 = 'Keep 3: main-menu + footer + links'. Mark the two redundant menus as inactive locally; if Shopify allows menuDelete (non-default menus only), the executor deletes them via menuDelete mutation; otherwise we leave them in Shopify but ignore them in our app.",
    ops: [
      {
        type: "DEACTIVATE_MENU",
        menuHandle: "additional-pages",
        reason:
          "Duplicates content already in 'links' menu. User M8 → drop.",
      },
      {
        type: "DEACTIVATE_MENU",
        menuHandle: "desktop-megamenu-eyewear",
        reason:
          "Redundant 3-item mega menu duplicating top-level main-menu items. User M8 → drop.",
      },
    ],
  },
  /* CL8 is NOT in the cleanupTasks array — it's not a menu fix, it's a
   * collection-architecture change ("roll out smart-collection pattern
   * to Sunglass + Frames + Smartwatch"). That work is owned by the
   * smart-collection auto-generator (lib/collections/ruleGenerator.ts).
   * Leaving the user note here so the doc trail is complete. */
];

/* ------------------------------------------------------------------
 * Helper — for endpoint dry-run output. Returns a flat list of
 * human-readable strings describing each op's effect.
 * ------------------------------------------------------------------ */
export function describeCleanupPlan(): Array<{
  taskId: string;
  taskTitle: string;
  ops: string[];
}> {
  return MENU_CLEANUP_TASKS.map((task) => ({
    taskId: task.id,
    taskTitle: task.title,
    ops: task.ops.map((op) => describeOp(op)),
  }));
}

function describeOp(op: MenuOp): string {
  switch (op.type) {
    case "TRIM_TITLE":
      return `[${op.menuHandle}] Trim '${op.matchTitle}' → '${op.newTitle}'`;
    case "DELETE_ITEM":
      return `[${op.menuHandle}] Delete: ${op.path.join(" → ")} (${op.reason})`;
    case "UPDATE_ITEM": {
      const changes = Object.entries(op.changes)
        .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
        .join(", ");
      return `[${op.menuHandle}] Update ${op.path.join(" → ")} { ${changes} }`;
    }
    case "MOVE_ITEM":
      return `[${op.menuHandle}] Move ${op.fromPath.join(" → ")} → ${op.toParentPath.join(" → ")} @ pos ${op.position}`;
    case "REPLACE_ITEM":
      return `[${op.menuHandle}] Replace ${op.path.join(" → ")} with ${op.with.itemType} '${op.with.title}'`;
    case "DEACTIVATE_MENU":
      return `[${op.menuHandle}] Deactivate menu (${op.reason})`;
  }
}
