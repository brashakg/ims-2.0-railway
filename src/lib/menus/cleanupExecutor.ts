// Menu cleanup executor — applies CL1-CL7 from cleanupTasks.ts to the
// live Shopify storefront via shopifyMenus.ts helpers.
//
// Strategy:
//   1. Fetch every live menu state from Shopify.
//   2. Group cleanup ops by menuHandle.
//   3. For each menu, walk a deep clone of its item tree and apply the
//      ops in declared order (TRIM, UPDATE, MOVE, REPLACE, DELETE).
//   4. Push the modified tree wholesale via pushMenuToShopify.
//   5. For DEACTIVATE_MENU ops (M8), call deleteShopifyMenu (skips
//      isDefault menus).
//
// Each op is recorded with applied/skipped/failed status so the caller
// (the admin endpoint) can show an honest report. The execution is
// resilient: a single op failure inside one menu doesn't abort the
// other menus.

import {
  fetchAllMenus,
  pushMenuToShopify,
  deleteShopifyMenu,
  type ShopifyMenu,
  type ShopifyMenuItem,
  type MenuItemTreeInput,
} from "@/lib/shopifyMenus";
import { makeGraphQLRequest } from "@/lib/shopify";
import {
  MENU_CLEANUP_TASKS,
  type MenuCleanupTask,
  type MenuOp,
  type MenuOpDeleteItem,
  type MenuOpUpdateItem,
  type MenuOpMoveItem,
  type MenuOpReplaceItem,
  type MenuOpTrim,
  type MenuOpDeactivateMenu,
} from "./cleanupTasks";

export interface OpResult {
  op: MenuOp;
  status: "applied" | "skipped" | "failed";
  detail: string;
}

export interface TaskResult {
  taskId: string;
  taskTitle: string;
  ops: OpResult[];
}

export interface CleanupResult {
  dryRun: boolean;
  /** Snapshot of live menus before any op was applied. */
  snapshot: ShopifyMenu[];
  tasks: TaskResult[];
  /** Menus that were actually pushed back to Shopify (only relevant
   *  when dryRun=false). */
  pushedMenus: string[];
  /** Menus that were deleted from Shopify. */
  deletedMenus: string[];
}

/* ------------------------------------------------------------------
 * Top-level executor
 * ------------------------------------------------------------------ */
export async function executeCleanup(opts: {
  dryRun: boolean;
  /** Override the cleanup spec — used by the menu-cleanup route to pass
   *  in resolved (or unresolved, on dry-run) collection GIDs.
   *  Defaults to MENU_CLEANUP_TASKS unmodified. */
  tasks?: MenuCleanupTask[];
}): Promise<CleanupResult> {
  const fetched = await fetchAllMenus();
  if (!fetched.success || !fetched.menus) {
    throw new Error(
      `Could not fetch live menus from Shopify: ${fetched.error || "unknown error"}`
    );
  }
  const snapshot: ShopifyMenu[] = fetched.menus;
  const taskSpec = opts.tasks ?? MENU_CLEANUP_TASKS;

  // Build a working copy of the menus, keyed by handle, that ops will
  // mutate. Deep-clone via JSON to keep the snapshot pristine for the
  // response.
  const working: Record<string, ShopifyMenu> = {};
  for (const m of snapshot) {
    working[m.handle] = JSON.parse(JSON.stringify(m)) as ShopifyMenu;
  }

  const tasks: TaskResult[] = [];
  const dirtyHandles = new Set<string>();
  const handlesToDelete = new Set<string>();

  for (const task of taskSpec) {
    const taskResult: TaskResult = {
      taskId: task.id,
      taskTitle: task.title,
      ops: [],
    };

    for (const op of task.ops) {
      const opResult = applyOpToWorking(op, working, handlesToDelete);
      taskResult.ops.push(opResult);
      if (
        opResult.status === "applied" &&
        op.type !== "DEACTIVATE_MENU" &&
        "menuHandle" in op
      ) {
        dirtyHandles.add(op.menuHandle);
      }
    }

    tasks.push(taskResult);
  }

  const pushedMenus: string[] = [];
  const deletedMenus: string[] = [];

  if (!opts.dryRun) {
    // Apply pushes — one per dirty menu.
    for (const handle of dirtyHandles) {
      const menu = working[handle];
      if (!menu) continue;
      const pushed = await pushMenuToShopify({
        shopifyMenuId: menu.id,
        title: menu.title,
        handle: menu.handle,
        items: toPushInput(menu.items),
      });
      if (pushed.success) {
        pushedMenus.push(handle);
      } else {
        // Mark the FIRST applied op for this menu as failed retroactively
        // so the user sees it. This is approximate — Shopify rejected
        // the whole tree. Better detail belongs in the activity log.
        for (const t of tasks) {
          for (const o of t.ops) {
            if (
              o.status === "applied" &&
              "menuHandle" in o.op &&
              o.op.menuHandle === handle
            ) {
              o.status = "failed";
              o.detail = `Shopify push failed: ${pushed.message}`;
              break;
            }
          }
        }
      }
    }

    // Apply deletes.
    for (const handle of handlesToDelete) {
      const menu = working[handle];
      if (!menu) continue;
      if (menu.isDefault) continue; // can't delete default menus
      const deleted = await deleteShopifyMenu(menu.id);
      if (deleted.success) {
        deletedMenus.push(handle);
      }
    }
  }

  return {
    dryRun: opts.dryRun,
    snapshot,
    tasks,
    pushedMenus,
    deletedMenus,
  };
}

/* ------------------------------------------------------------------
 * Op application — mutates the working menu tree in place.
 * ------------------------------------------------------------------ */
function applyOpToWorking(
  op: MenuOp,
  working: Record<string, ShopifyMenu>,
  handlesToDelete: Set<string>
): OpResult {
  try {
    switch (op.type) {
      case "TRIM_TITLE":
        return applyTrim(op, working);
      case "DELETE_ITEM":
        return applyDelete(op, working);
      case "UPDATE_ITEM":
        return applyUpdate(op, working);
      case "MOVE_ITEM":
        return applyMove(op, working);
      case "REPLACE_ITEM":
        return applyReplace(op, working);
      case "DEACTIVATE_MENU":
        return applyDeactivate(op, working, handlesToDelete);
    }
  } catch (e) {
    return {
      op,
      status: "failed",
      detail: e instanceof Error ? e.message : String(e),
    };
  }
}

function applyTrim(op: MenuOpTrim, working: Record<string, ShopifyMenu>): OpResult {
  const menu = working[op.menuHandle];
  if (!menu) return { op, status: "skipped", detail: `Menu ${op.menuHandle} not found` };
  const found = findItemByPath(menu.items, op.path, /*looseWhitespace*/ true);
  if (!found) return { op, status: "skipped", detail: `Item not found at ${op.path.join(" → ")}` };
  if (found.title === op.newTitle) return { op, status: "skipped", detail: "Already trimmed" };
  found.title = op.newTitle;
  return { op, status: "applied", detail: `'${op.matchTitle}' → '${op.newTitle}'` };
}

function applyDelete(
  op: MenuOpDeleteItem,
  working: Record<string, ShopifyMenu>
): OpResult {
  const menu = working[op.menuHandle];
  if (!menu) return { op, status: "skipped", detail: `Menu ${op.menuHandle} not found` };
  const removed = removeItemByPath(menu.items, op.path);
  return removed
    ? { op, status: "applied", detail: `Deleted ${op.path.join(" → ")}` }
    : { op, status: "skipped", detail: `Item not found at ${op.path.join(" → ")}` };
}

function applyUpdate(
  op: MenuOpUpdateItem,
  working: Record<string, ShopifyMenu>
): OpResult {
  const menu = working[op.menuHandle];
  if (!menu) return { op, status: "skipped", detail: `Menu ${op.menuHandle} not found` };
  const found = findItemByPath(menu.items, op.path, /*looseWhitespace*/ true);
  if (!found) return { op, status: "skipped", detail: `Item not found at ${op.path.join(" → ")}` };
  if (op.changes.title !== undefined) found.title = op.changes.title;
  if (op.changes.itemType !== undefined) found.type = op.changes.itemType;
  if (op.changes.url !== undefined) found.url = op.changes.url ?? null;
  if (op.changes.resourceId !== undefined) found.resourceId = op.changes.resourceId;
  return { op, status: "applied", detail: `Updated ${op.path.join(" → ")}` };
}

function applyMove(
  op: MenuOpMoveItem,
  working: Record<string, ShopifyMenu>
): OpResult {
  const menu = working[op.menuHandle];
  if (!menu) return { op, status: "skipped", detail: `Menu ${op.menuHandle} not found` };
  // Hotfix TS#2 — pre-locate the target BEFORE removing the source so a
  // missing target doesn't leave the source orphaned. Previous version
  // removed first and only checked the target after, causing data loss
  // in the working tree if the path was wrong.
  const target = findItemByPath(menu.items, op.toParentPath, /*looseWhitespace*/ true);
  if (!target) {
    return { op, status: "failed", detail: `Target parent not found at ${op.toParentPath.join(" → ")}` };
  }
  const removed = removeItemByPath(menu.items, op.fromPath);
  if (!removed) return { op, status: "skipped", detail: `Source not found at ${op.fromPath.join(" → ")}` };
  // Cycle guard — refuse to insert a node into itself or its descendants.
  if (containsItemRef(removed, target)) {
    // Re-insert at original parent's end (best effort) since we've
    // already removed it. The source path still resolves the parent.
    const parent = op.fromPath.length > 1
      ? findItemByPath(menu.items, op.fromPath.slice(0, -1), true)
      : null;
    const restoreList = parent ? (parent.items ||= []) : menu.items;
    restoreList.push(removed);
    return { op, status: "failed", detail: `Cycle: target is a descendant of the moved item` };
  }
  if (!Array.isArray(target.items)) target.items = [];
  const pos = Math.max(0, Math.min(op.position, target.items.length));
  target.items.splice(pos, 0, removed);
  return { op, status: "applied", detail: `Moved ${op.fromPath.join(" → ")} → ${op.toParentPath.join(" → ")}` };
}

/** Returns true when `node` is `target` or any descendant of `node`
 *  contains a reference equal to `target`. Used to refuse cyclic moves. */
function containsItemRef(node: ShopifyMenuItem, target: ShopifyMenuItem): boolean {
  if (node === target) return true;
  if (!Array.isArray(node.items)) return false;
  for (const child of node.items) {
    if (containsItemRef(child, target)) return true;
  }
  return false;
}

function applyReplace(
  op: MenuOpReplaceItem,
  working: Record<string, ShopifyMenu>
): OpResult {
  const menu = working[op.menuHandle];
  if (!menu) return { op, status: "skipped", detail: `Menu ${op.menuHandle} not found` };
  const found = findItemByPath(menu.items, op.path, /*looseWhitespace*/ true);
  if (!found) return { op, status: "skipped", detail: `Item not found at ${op.path.join(" → ")}` };
  // Replace contents of `found` in place — preserve the tree slot.
  found.title = op.with.title;
  found.type = op.with.itemType;
  found.url = op.with.url ?? null;
  found.resourceId = op.with.resourceId ?? null;
  // Strip children — replacement items are leaf-replacements unless a
  // future op specifies otherwise.
  found.items = [];
  return { op, status: "applied", detail: `Replaced ${op.path.join(" → ")} with ${op.with.itemType} '${op.with.title}'` };
}

function applyDeactivate(
  op: MenuOpDeactivateMenu,
  working: Record<string, ShopifyMenu>,
  handlesToDelete: Set<string>
): OpResult {
  const menu = working[op.menuHandle];
  if (!menu) return { op, status: "skipped", detail: `Menu ${op.menuHandle} not found` };
  if (menu.isDefault) {
    return { op, status: "skipped", detail: `Menu ${op.menuHandle} is default — can't delete` };
  }
  handlesToDelete.add(op.menuHandle);
  return { op, status: "applied", detail: `Marked ${op.menuHandle} for deletion` };
}

/* ------------------------------------------------------------------
 * Tree walk helpers — find by path, remove by path. Tolerates loose
 * whitespace on titles when looseWhitespace=true (so the cleanup spec
 * paths can refer to "Best Seller" or "Best Seller " interchangeably).
 * ------------------------------------------------------------------ */
function titlesMatch(a: string, b: string, loose: boolean): boolean {
  if (a === b) return true;
  if (!loose) return false;
  return a.trim() === b.trim();
}

function findItemByPath(
  items: ShopifyMenuItem[],
  path: string[],
  looseWhitespace: boolean
): ShopifyMenuItem | null {
  if (path.length === 0) return null;
  for (const it of items) {
    if (titlesMatch(it.title, path[0], looseWhitespace)) {
      if (path.length === 1) return it;
      return findItemByPath(it.items || [], path.slice(1), looseWhitespace);
    }
  }
  return null;
}

function removeItemByPath(
  items: ShopifyMenuItem[],
  path: string[]
): ShopifyMenuItem | null {
  if (path.length === 0) return null;
  if (path.length === 1) {
    const idx = items.findIndex((it) => titlesMatch(it.title, path[0], true));
    if (idx === -1) return null;
    const [removed] = items.splice(idx, 1);
    return removed;
  }
  const parent = items.find((it) => titlesMatch(it.title, path[0], true));
  if (!parent || !Array.isArray(parent.items)) return null;
  return removeItemByPath(parent.items, path.slice(1));
}

/* ------------------------------------------------------------------
 * Tree → Shopify push input. Preserves item IDs where present so
 * Shopify's menuUpdate doesn't recreate the whole tree.
 * ------------------------------------------------------------------ */
function toPushInput(items: ShopifyMenuItem[]): MenuItemTreeInput[] {
  return items.map((it) => ({
    id: it.id || undefined,
    title: it.title,
    type: it.type,
    url: it.url,
    resourceId: it.resourceId,
    tags: Array.isArray(it.tags) ? it.tags : [],
    items: Array.isArray(it.items) && it.items.length > 0
      ? toPushInput(it.items)
      : [],
  }));
}

/* ------------------------------------------------------------------
 * "Cases & Bags" collection helper — CL5 needs a Shopify collection
 * to point the menu item at. If one already exists with handle
 * "cases-bags", reuse its GID. Otherwise create it via collectionCreate
 * (manual / custom collection).
 * ------------------------------------------------------------------ */
export async function ensureCasesAndBagsCollection(): Promise<{
  shopifyCollectionId: string | null;
  created: boolean;
  error?: string;
}> {
  // Try to find existing.
  const findRes = await makeGraphQLRequest<{
    collectionByHandle: { id: string } | null;
  }>(
    `query CasesAndBags { collectionByHandle(handle: "cases-bags") { id } }`,
    {}
  );
  if (findRes.success && findRes.data?.collectionByHandle) {
    return {
      shopifyCollectionId: findRes.data.collectionByHandle.id,
      created: false,
    };
  }

  // Create.
  const createRes = await makeGraphQLRequest<{
    collectionCreate: {
      collection: { id: string } | null;
      userErrors: Array<{ field: string[] | null; message: string }>;
    };
  }>(
    `
      mutation CreateCasesAndBags($input: CollectionInput!) {
        collectionCreate(input: $input) {
          collection { id }
          userErrors { field message }
        }
      }
    `,
    {
      input: {
        title: "Cases & Bags",
        handle: "cases-bags",
        descriptionHtml:
          "Eyewear cases, pouches, and travel bags from Better Vision.",
      },
    }
  );

  const errs = createRes.data?.collectionCreate.userErrors ?? [];
  if (errs.length > 0 || !createRes.data?.collectionCreate.collection) {
    return {
      shopifyCollectionId: null,
      created: false,
      error:
        errs.map((e) => e.message).join("; ") ||
        createRes.error ||
        "Unknown error creating cases-bags collection",
    };
  }

  return {
    shopifyCollectionId: createRes.data.collectionCreate.collection.id,
    created: true,
  };
}

/* ------------------------------------------------------------------
 * For the run-cleanup endpoint — pre-resolves CL3 (collectors-edition)
 * and CL5 (cases-bags) collection GIDs, then injects them into the
 * spec ops before calling executeCleanup. The base spec has these as
 * `resourceId: null` placeholders.
 * ------------------------------------------------------------------ */
export async function resolveCollectionRefs(
  tasks: MenuCleanupTask[]
): Promise<MenuCleanupTask[]> {
  // Look up collectors-edition by handle (CL3).
  const ceRes = await makeGraphQLRequest<{
    collectionByHandle: { id: string } | null;
  }>(
    `query CollectorsEdition { collectionByHandle(handle: "collectors-edition") { id } }`,
    {}
  );
  const collectorsEditionGid =
    ceRes.success && ceRes.data?.collectionByHandle
      ? ceRes.data.collectionByHandle.id
      : null;

  // Ensure cases-bags exists (CL5).
  const cabRes = await ensureCasesAndBagsCollection();
  const casesBagsGid = cabRes.shopifyCollectionId;

  return tasks.map((task) => {
    if (task.id === "CL3") {
      return {
        ...task,
        ops: task.ops.map((op) => {
          if (op.type === "UPDATE_ITEM" && op.path.includes("COLLECTOR'S EDITION")) {
            return {
              ...op,
              changes: {
                ...op.changes,
                resourceId: collectorsEditionGid,
              },
            };
          }
          return op;
        }),
      };
    }
    if (task.id === "CL5") {
      return {
        ...task,
        ops: task.ops.map((op) => {
          if (op.type === "REPLACE_ITEM" && op.path.includes("Eyewear suitcase")) {
            return {
              ...op,
              with: {
                ...op.with,
                resourceId: casesBagsGid,
              },
            };
          }
          return op;
        }),
      };
    }
    return task;
  });
}
