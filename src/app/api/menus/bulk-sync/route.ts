// POST /api/menus/bulk-sync
//
// Bootstrap sync — fetches every live storefront menu from Shopify and
// upserts a local Menu row (plus its full MenuItem tree) for each.
//
// Why this exists: the menus list page (/dashboard/admin/menus) shows
// "No menus found" on first load because the local Menu table is
// empty. Each existing menu needs a one-shot pull from Shopify before
// the per-menu /api/menus/[id]/sync endpoint becomes useful. This
// endpoint makes that bootstrap one click.
//
// Flow:
//   1. Fetch all menus from Shopify via fetchAllMenus() (paginated).
//   2. For each Shopify menu:
//      a. Upsert the local Menu row by `handle`.
//      b. Delete the menu's existing local MenuItem rows.
//      c. Recreate them from the Shopify item tree, preserving the
//         hierarchy via parentId references.
//   3. Return a per-menu summary { handle, action, itemsCreated }.
//
// Reuses the existing /api/menus/[id]/sync logic conceptually; we just
// run it across every menu in one request. Admin-only.

import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";
import { fetchAllMenus, type ShopifyMenu, type ShopifyMenuItem } from "@/lib/shopifyMenus";

interface MenuSyncSummary {
  handle: string;
  title: string;
  shopifyMenuId: string;
  action: "created" | "updated" | "skipped";
  itemsCreated: number;
  warning?: string;
}

export async function POST(_request: NextRequest) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const fetched = await fetchAllMenus();
    if (!fetched.success || !fetched.menus) {
      return NextResponse.json(
        { success: false, error: fetched.error || "Could not fetch menus from Shopify" },
        { status: 502 }
      );
    }

    const summaries: MenuSyncSummary[] = [];

    for (const m of fetched.menus) {
      const summary = await syncOneMenu(m);
      summaries.push(summary);
    }

    logActivity({
      userId: (auth.session?.user as { id?: string } | undefined)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "MENU_BULK_SYNC",
      entity: "MENU",
      details:
        `Bulk-synced ${summaries.length} menus from Shopify. ` +
        `${summaries.filter((s) => s.action === "created").length} created, ` +
        `${summaries.filter((s) => s.action === "updated").length} updated, ` +
        `${summaries.filter((s) => s.action === "skipped").length} skipped.`,
    });

    return NextResponse.json({
      success: true,
      total: summaries.length,
      created: summaries.filter((s) => s.action === "created").length,
      updated: summaries.filter((s) => s.action === "updated").length,
      skipped: summaries.filter((s) => s.action === "skipped").length,
      summaries,
    });
  } catch (e) {
    return NextResponse.json(
      {
        success: false,
        error: e instanceof Error ? e.message : "Unknown error",
      },
      { status: 500 }
    );
  }
}

/* ------------------------------------------------------------------
 * Per-menu sync — upsert the Menu row, then rebuild MenuItem tree.
 *
 * Locally-modified menus are NOT overwritten without a warning. We
 * still upsert the Menu row's title (cheap and rarely conflicts) but
 * skip the item-tree rebuild and surface a warning in the summary so
 * the admin can decide whether to force-overwrite.
 * ------------------------------------------------------------------ */
async function syncOneMenu(shopifyMenu: ShopifyMenu): Promise<MenuSyncSummary> {
  const existing = await prisma.menu.findUnique({
    where: { handle: shopifyMenu.handle },
  });

  // Preserve local-only overlay fields keyed on shopifyItemId so user
  // edits to icon / banner / badge / pinning aren't wiped on resync.
  // Shape mirrors the per-menu /api/menus/[id]/sync handler.
  const overlayMap = new Map<
    string,
    {
      iconUrl: string | null;
      bannerUrl: string | null;
      badgeText: string | null;
      badgeColor: string | null;
      pinnedToTop: boolean;
    }
  >();
  if (existing) {
    const localItems = await prisma.menuItem.findMany({
      where: { menuId: existing.id },
    });
    for (const li of localItems) {
      if (li.shopifyItemId) {
        overlayMap.set(li.shopifyItemId, {
          iconUrl: li.iconUrl,
          bannerUrl: li.bannerUrl,
          badgeText: li.badgeText,
          badgeColor: li.badgeColor,
          pinnedToTop: li.pinnedToTop,
        });
      }
    }
  }

  const upsertWarning =
    existing?.locallyModified === true
      ? "Skipped item-tree rebuild — menu has local edits not pushed to Shopify yet. Push or force-resync to overwrite."
      : undefined;

  // Upsert the Menu row regardless — title / isDefault stays in sync
  // even when locallyModified flag is set.
  const menu = await prisma.menu.upsert({
    where: { handle: shopifyMenu.handle },
    update: {
      shopifyMenuId: shopifyMenu.id,
      title: shopifyMenu.title,
      isDefault: shopifyMenu.isDefault,
      lastSyncedAt: new Date(),
    },
    create: {
      shopifyMenuId: shopifyMenu.id,
      handle: shopifyMenu.handle,
      title: shopifyMenu.title,
      isDefault: shopifyMenu.isDefault,
      active: true,
      lastSyncedAt: new Date(),
    },
  });

  // Skip item-tree rebuild on locally-modified menus to protect unpushed edits.
  if (existing?.locallyModified) {
    return {
      handle: menu.handle,
      title: menu.title,
      shopifyMenuId: menu.shopifyMenuId || "",
      action: "skipped",
      itemsCreated: 0,
      warning: upsertWarning,
    };
  }

  // Wipe and recreate the item tree — cheaper than reconciling diffs,
  // and matches the per-menu sync handler's strategy.
  await prisma.menuItem.deleteMany({ where: { menuId: menu.id } });

  let totalCreated = 0;
  totalCreated += await createItemsRec(menu.id, shopifyMenu.items, null, overlayMap);

  return {
    handle: menu.handle,
    title: menu.title,
    shopifyMenuId: menu.shopifyMenuId || "",
    action: existing ? "updated" : "created",
    itemsCreated: totalCreated,
  };
}

/** Walk the Shopify menu tree, creating MenuItem rows depth-first.
 *  Returns the total count created. Local overlay fields are restored
 *  from `overlayMap` keyed on shopifyItemId. */
async function createItemsRec(
  menuId: string,
  items: ShopifyMenuItem[],
  parentId: string | null,
  overlayMap: Map<
    string,
    {
      iconUrl: string | null;
      bannerUrl: string | null;
      badgeText: string | null;
      badgeColor: string | null;
      pinnedToTop: boolean;
    }
  >
): Promise<number> {
  let count = 0;
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    const overlay = it.id ? overlayMap.get(it.id) : undefined;
    const created = await prisma.menuItem.create({
      data: {
        menuId,
        parentId,
        position: i,
        shopifyItemId: it.id || null,
        title: it.title || "",
        itemType: it.type || "HTTP",
        url: it.url || null,
        resourceId: it.resourceId || null,
        tagsFilter: Array.isArray(it.tags) && it.tags.length > 0 ? it.tags.join(",") : null,
        iconUrl: overlay?.iconUrl || null,
        bannerUrl: overlay?.bannerUrl || null,
        badgeText: overlay?.badgeText || null,
        badgeColor: overlay?.badgeColor || null,
        pinnedToTop: overlay?.pinnedToTop || false,
      },
    });
    count++;
    if (Array.isArray(it.items) && it.items.length > 0) {
      count += await createItemsRec(menuId, it.items, created.id, overlayMap);
    }
  }
  return count;
}
