import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";
import { deleteShopifyMenu } from "@/lib/shopifyMenus";

interface RouteParams {
  params: Promise<{ id: string }>;
}

// MenuItem from the Prisma client — we do a flat-then-tree pass below
// instead of relying on Prisma's `include` recursion, which can't
// actually express arbitrary depth (you have to spell out N levels).
interface FlatItem {
  id: string;
  shopifyItemId: string | null;
  menuId: string;
  parentId: string | null;
  position: number;
  title: string;
  itemType: string;
  url: string | null;
  resourceId: string | null;
  tagsFilter: string | null;
  iconUrl: string | null;
  bannerUrl: string | null;
  badgeText: string | null;
  badgeColor: string | null;
  pinnedToTop: boolean;
  createdAt: Date;
  updatedAt: Date;
}

interface NestedItem extends FlatItem {
  children: NestedItem[];
}

/**
 * Build a parent → children tree from a flat MenuItem array.
 * Items at the same level keep their stored `position` order. Pinned
 * items (round 2 M11 — top categories) bubble to the top within their
 * level so the editor renders them above non-pinned siblings.
 */
function buildTree(flat: FlatItem[]): NestedItem[] {
  const byId = new Map<string, NestedItem>();
  for (const item of flat) {
    byId.set(item.id, { ...item, children: [] });
  }

  const roots: NestedItem[] = [];
  for (const node of byId.values()) {
    if (node.parentId) {
      const parent = byId.get(node.parentId);
      if (parent) parent.children.push(node);
      else roots.push(node); // orphan — surface at root so it's visible
    } else {
      roots.push(node);
    }
  }

  const sortNodes = (nodes: NestedItem[]) => {
    nodes.sort((a, b) => {
      if (a.pinnedToTop && !b.pinnedToTop) return -1;
      if (!a.pinnedToTop && b.pinnedToTop) return 1;
      if (a.position !== b.position) return a.position - b.position;
      return a.title.localeCompare(b.title);
    });
    for (const n of nodes) sortNodes(n.children);
  };
  sortNodes(roots);

  return roots;
}

// GET /api/menus/:id — full menu with nested item tree.
export async function GET(_request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;
    const { id } = await params;

    const menu = await prisma.menu.findUnique({
      where: { id },
    });

    if (!menu) {
      return NextResponse.json(
        { success: false, error: "Menu not found" },
        { status: 404 }
      );
    }

    const items = await prisma.menuItem.findMany({
      where: { menuId: id },
      orderBy: [{ pinnedToTop: "desc" }, { position: "asc" }, { title: "asc" }],
    });

    const tree = buildTree(items as FlatItem[]);

    return NextResponse.json({
      success: true,
      data: { ...menu, items: tree, flatItems: items },
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}

// PUT /api/menus/:id — update title / handle / active / isDefault.
//
// We don't push to Shopify here because handle/title changes have
// special restrictions on Shopify (default menus reject handle change).
// The user explicitly clicks "Push to Shopify" from the editor when
// they want the structural push. Mark locallyModified=true so the
// editor surfaces the unsynced banner.
export async function PUT(request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;
    const { id } = await params;

    const body = await request.json();
    const existing = await prisma.menu.findUnique({ where: { id } });
    if (!existing) {
      return NextResponse.json(
        { success: false, error: "Menu not found" },
        { status: 404 }
      );
    }

    const data: Record<string, unknown> = { locallyModified: true };
    if (typeof body.title === "string" && body.title.trim()) {
      data.title = body.title.trim();
    }
    if (typeof body.handle === "string" && body.handle.trim()) {
      const handle = body.handle.trim().toLowerCase();
      if (!/^[a-z0-9-]+$/.test(handle)) {
        return NextResponse.json(
          {
            success: false,
            error: "handle must be lowercase letters, digits, and hyphens only",
          },
          { status: 400 }
        );
      }
      // Reject collisions upfront so the unique constraint doesn't 500.
      if (handle !== existing.handle) {
        const dup = await prisma.menu.findUnique({ where: { handle } });
        if (dup) {
          return NextResponse.json(
            {
              success: false,
              error: `Menu with handle "${handle}" already exists`,
            },
            { status: 409 }
          );
        }
        data.handle = handle;
      }
    }
    if (typeof body.active === "boolean") data.active = body.active;
    if (typeof body.isDefault === "boolean") data.isDefault = body.isDefault;

    const updated = await prisma.menu.update({
      where: { id },
      data,
    });

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "UPDATE",
      entity: "MENU",
      entityId: id,
      details: `Updated menu: ${updated.title} (${updated.handle})`,
    });

    return NextResponse.json({ success: true, data: updated });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}

// DELETE /api/menus/:id — admin only. Refuses to delete the three
// default menus (main-menu, footer, links) since the storefront
// depends on them. Cascades MenuItem rows via Prisma onDelete: Cascade.
export async function DELETE(_request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;
    const { id } = await params;

    const existing = await prisma.menu.findUnique({ where: { id } });
    if (!existing) {
      return NextResponse.json(
        { success: false, error: "Menu not found" },
        { status: 404 }
      );
    }

    if (existing.isDefault) {
      return NextResponse.json(
        {
          success: false,
          error:
            "Default menus (main-menu, footer, links) cannot be deleted. Mark them inactive instead.",
        },
        { status: 400 }
      );
    }

    // Best-effort Shopify delete. If Shopify rejects (e.g. menu still
    // references published collections), we still proceed with the
    // local delete so the editor doesn't get stuck on a half-deleted
    // menu — the failure is surfaced in the response shopifySync field.
    let shopifySync: { success: boolean; message: string } = {
      success: true,
      message: "Not pushed (no Shopify ID)",
    };
    if (existing.shopifyMenuId) {
      shopifySync = await deleteShopifyMenu(existing.shopifyMenuId);
    }

    await prisma.menu.delete({ where: { id } });

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "DELETE",
      entity: "MENU",
      entityId: id,
      details: `Deleted menu: ${existing.title} (${existing.handle})`,
    });

    return NextResponse.json({ success: true, shopifySync });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
