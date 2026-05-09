import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";
import {
  pushMenuToShopify,
  type MenuItemTreeInput,
  type ShopifyMenuItem,
} from "@/lib/shopifyMenus";

interface RouteParams {
  params: Promise<{ id: string }>;
}

interface FlatItem {
  id: string;
  shopifyItemId: string | null;
  parentId: string | null;
  position: number;
  title: string;
  itemType: string;
  url: string | null;
  resourceId: string | null;
  tagsFilter: string | null;
  pinnedToTop: boolean;
}

interface NestedItem extends FlatItem {
  children: NestedItem[];
}

// Flat → tree, sorted within each level by [pinned, position, title].
function buildTree(flat: FlatItem[]): NestedItem[] {
  const byId = new Map<string, NestedItem>();
  for (const item of flat) {
    byId.set(item.id, { ...item, children: [] });
  }
  const roots: NestedItem[] = [];
  for (const node of byId.values()) {
    if (node.parentId) {
      const p = byId.get(node.parentId);
      if (p) p.children.push(node);
      else roots.push(node);
    } else {
      roots.push(node);
    }
  }
  const sortNodes = (nodes: NestedItem[]) => {
    nodes.sort((a, b) => {
      if (a.pinnedToTop && !b.pinnedToTop) return -1;
      if (!a.pinnedToTop && b.pinnedToTop) return 1;
      return a.position - b.position;
    });
    for (const n of nodes) sortNodes(n.children);
  };
  sortNodes(roots);
  return roots;
}

// Translate our local tree into Shopify's MenuItemUpdateInput shape.
// We pass the existing shopifyItemId for items we previously pushed —
// that tells Shopify "keep the same MenuItem row" rather than
// recreating it. New items don't have an ID; Shopify mints one.
function toShopifyTree(nodes: NestedItem[]): MenuItemTreeInput[] {
  return nodes.map((node) => ({
    id: node.shopifyItemId || null,
    title: node.title,
    type: node.itemType,
    url: node.url || null,
    resourceId: node.resourceId || null,
    tags:
      node.tagsFilter && node.tagsFilter.trim()
        ? node.tagsFilter.split(",").map((t) => t.trim()).filter(Boolean)
        : null,
    items:
      node.children.length > 0 ? toShopifyTree(node.children) : undefined,
  }));
}

// After Shopify confirms the push it returns the canonical item IDs
// in tree order. We walk the local tree and the response tree in
// lockstep to write the new shopifyItemIds back to local rows. This
// keeps subsequent pushes idempotent.
async function writeBackShopifyIds(
  localTree: NestedItem[],
  remoteTree: ShopifyMenuItem[]
): Promise<{ updated: number; mismatched: number }> {
  let updated = 0;
  let mismatched = 0;

  const walk = async (
    locals: NestedItem[],
    remotes: ShopifyMenuItem[]
  ): Promise<void> => {
    const len = Math.min(locals.length, remotes.length);
    if (locals.length !== remotes.length) {
      mismatched += Math.abs(locals.length - remotes.length);
    }
    for (let i = 0; i < len; i++) {
      const l = locals[i];
      const r = remotes[i];
      if (l.shopifyItemId !== r.id) {
        await prisma.menuItem.update({
          where: { id: l.id },
          data: { shopifyItemId: r.id },
        });
        updated += 1;
      }
      if (l.children.length > 0 || (r.items && r.items.length > 0)) {
        await walk(l.children, r.items || []);
      }
    }
  };

  await walk(localTree, remoteTree);
  return { updated, mismatched };
}

// POST /api/menus/:id/push — push local menu to Shopify.
//
// Body (optional): { force?: boolean } — bypasses the
// locallyModified-only guard so you can re-push a menu that's
// already in sync (useful if someone edited Shopify directly).
export async function POST(request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;
    const { id } = await params;

    let force = false;
    try {
      // Body is optional — empty POST is fine.
      const text = await request.text();
      if (text) {
        const body = JSON.parse(text);
        force = body.force === true;
      }
    } catch {
      // ignore parse errors and treat as no-body
    }

    const menu = await prisma.menu.findUnique({ where: { id } });
    if (!menu) {
      return NextResponse.json(
        { success: false, error: "Menu not found" },
        { status: 404 }
      );
    }

    if (!menu.shopifyMenuId) {
      return NextResponse.json(
        {
          success: false,
          error:
            "Menu has no Shopify ID. Sync from Shopify first or create the menu via POST /api/menus.",
        },
        { status: 400 }
      );
    }

    if (!menu.active) {
      return NextResponse.json(
        {
          success: false,
          error: "Menu is marked inactive. Activate it before pushing.",
        },
        { status: 400 }
      );
    }

    if (!menu.locallyModified && !force) {
      return NextResponse.json({
        success: true,
        message: "Menu is already in sync. Pass force=true to push anyway.",
        skipped: true,
      });
    }

    const flatItems = await prisma.menuItem.findMany({
      where: { menuId: id },
    });
    const tree = buildTree(flatItems as FlatItem[]);

    if (tree.length === 0) {
      return NextResponse.json(
        {
          success: false,
          error:
            "Menu has no items to push. Add at least one top-level item first.",
        },
        { status: 400 }
      );
    }

    const shopifyTree = toShopifyTree(tree);
    const pushResult = await pushMenuToShopify({
      shopifyMenuId: menu.shopifyMenuId,
      title: menu.title,
      handle: menu.handle,
      items: shopifyTree,
    });

    if (!pushResult.success) {
      logActivity({
        userId: (auth.session?.user as any)?.id,
        userName: auth.session?.user?.name,
        userEmail: auth.session?.user?.email,
        action: "SYNC_PUSH_FAILED",
        entity: "MENU",
        entityId: id,
        details: `Push to Shopify failed: ${pushResult.message}`,
      });
      return NextResponse.json(
        { success: false, error: pushResult.message },
        { status: 502 }
      );
    }

    let writeBack = { updated: 0, mismatched: 0 };
    if (pushResult.items) {
      // Walk the local tree and Shopify's response in lockstep,
      // updating each row's shopifyItemId. We don't wrap this in a
      // single transaction: each write is independent, and a partial
      // failure mid-walk just means a few rows missed their canonical
      // ID — the next push will fix them up because Shopify uses our
      // tree shape, not the IDs, to identify items.
      writeBack = await writeBackShopifyIds(tree, pushResult.items);
    }

    await prisma.menu.update({
      where: { id },
      data: { locallyModified: false, lastSyncedAt: new Date() },
    });

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "SYNC_PUSH",
      entity: "MENU",
      entityId: id,
      details: `Pushed menu ${menu.handle} to Shopify (${flatItems.length} items, ${writeBack.updated} ID writebacks)`,
    });

    return NextResponse.json({
      success: true,
      message: pushResult.message,
      itemCount: flatItems.length,
      writeBack,
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
