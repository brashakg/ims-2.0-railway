import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";
import { fetchAllMenus, type ShopifyMenuItem } from "@/lib/shopifyMenus";

interface RouteParams {
  params: Promise<{ id: string }>;
}

// Sync ONE menu from Shopify into our local DB.
//
// This is a destructive overwrite of the local item tree: we delete
// every MenuItem belonging to this menu and re-create them from
// Shopify's payload. Doing it in a transaction is important because
// MenuItem.shopifyItemId is unique and a partial replay would hit
// duplicate constraints. Round 2 mapping M2 (auto-bucket new brands)
// is NOT applied here — sync just mirrors Shopify; the bucket logic
// runs when the Brands menu auto-adds a new brand from the bv-app
// product flow.
export async function POST(_request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;
    const { id } = await params;

    const localMenu = await prisma.menu.findUnique({ where: { id } });
    if (!localMenu) {
      return NextResponse.json(
        { success: false, error: "Menu not found" },
        { status: 404 }
      );
    }

    // We always pull all menus and pick the matching one by handle (or
    // shopifyMenuId if we have it). Pulling all is cheap because there
    // are typically <10 menus and Shopify's per-menu query doesn't
    // exist via Admin API; the storefront API returns single menus
    // but with a different shape we don't want to plumb here.
    const fetchResult = await fetchAllMenus();
    if (!fetchResult.success || !fetchResult.menus) {
      return NextResponse.json(
        {
          success: false,
          error: fetchResult.error || "Failed to fetch from Shopify",
        },
        { status: 502 }
      );
    }

    const remote = fetchResult.menus.find(
      (m) =>
        (localMenu.shopifyMenuId && m.id === localMenu.shopifyMenuId) ||
        m.handle === localMenu.handle
    );

    if (!remote) {
      return NextResponse.json(
        {
          success: false,
          error: `Menu "${localMenu.handle}" not found on Shopify`,
        },
        { status: 404 }
      );
    }

    // Walk the Shopify tree depth-first to flatten into a list, then
    // bulk-replace the local items. We DO NOT preserve local-only
    // fields (iconUrl, badgeText, etc.) on existing rows because the
    // sync's purpose is to mirror remote state. If a user edited
    // iconUrl locally and then synced from Shopify, the icon is lost
    // — that's intended; the user should push first if they have
    // pending local edits, and the menu.locallyModified flag tells
    // them when that's true. We surface a warning in the response.
    interface FlatNew {
      shopifyItemId: string;
      title: string;
      itemType: string;
      url: string | null;
      resourceId: string | null;
      tagsFilter: string | null;
      parentTempIdx: number | null; // index into the same flat list
      position: number;
    }

    const flat: FlatNew[] = [];

    const flatten = (
      nodes: ShopifyMenuItem[],
      parentIdx: number | null,
      level: number
    ): void => {
      void level;
      nodes.forEach((node, idx) => {
        flat.push({
          shopifyItemId: node.id,
          title: node.title,
          itemType: node.type,
          url: node.url,
          resourceId: node.resourceId,
          tagsFilter:
            node.tags && node.tags.length > 0 ? node.tags.join(",") : null,
          parentTempIdx: parentIdx,
          position: idx,
        });
        const myIdx = flat.length - 1;
        if (node.items && node.items.length > 0) {
          flatten(node.items, myIdx, level + 1);
        }
      });
    };
    flatten(remote.items, null, 0);

    // Try to keep local iconUrl/bannerUrl/badgeText/badgeColor/pinnedToTop
    // by mapping existing rows by shopifyItemId. We re-write the rest
    // from the fresh Shopify payload.
    const existingItems = await prisma.menuItem.findMany({
      where: { menuId: id, shopifyItemId: { not: null } },
      select: {
        shopifyItemId: true,
        iconUrl: true,
        bannerUrl: true,
        badgeText: true,
        badgeColor: true,
        pinnedToTop: true,
      },
    });
    const localOverlay = new Map(
      existingItems
        .filter((i) => i.shopifyItemId)
        .map((i) => [i.shopifyItemId as string, i])
    );

    let warning: string | null = null;
    if (localMenu.locallyModified) {
      warning =
        "Local edits to this menu were overwritten by the Shopify sync. " +
        "Push from your editor before syncing next time to preserve them.";
    }

    await prisma.$transaction(async (tx) => {
      // Replace items: delete + recreate. We keep parent relationships
      // by inserting in tree order and using the auto-generated cuids
      // as we go.
      await tx.menuItem.deleteMany({ where: { menuId: id } });

      const idByIdx: string[] = [];
      for (let i = 0; i < flat.length; i++) {
        const f = flat[i];
        const overlay = localOverlay.get(f.shopifyItemId);
        const created = await tx.menuItem.create({
          data: {
            menuId: id,
            shopifyItemId: f.shopifyItemId,
            parentId:
              f.parentTempIdx !== null ? idByIdx[f.parentTempIdx] : null,
            position: f.position,
            title: f.title,
            itemType: f.itemType,
            url: f.url,
            resourceId: f.resourceId,
            tagsFilter: f.tagsFilter,
            iconUrl: overlay?.iconUrl || null,
            bannerUrl: overlay?.bannerUrl || null,
            badgeText: overlay?.badgeText || null,
            badgeColor: overlay?.badgeColor || null,
            pinnedToTop: overlay?.pinnedToTop || false,
          },
          select: { id: true },
        });
        idByIdx.push(created.id);
      }

      await tx.menu.update({
        where: { id },
        data: {
          shopifyMenuId: remote.id,
          title: remote.title,
          handle: remote.handle,
          isDefault: remote.isDefault,
          lastSyncedAt: new Date(),
          locallyModified: false,
        },
      });
    });

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "SYNC_PULL",
      entity: "MENU",
      entityId: id,
      details: `Synced menu ${remote.handle} from Shopify (${flat.length} items)`,
    });

    return NextResponse.json({
      success: true,
      itemCount: flat.length,
      warning,
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
