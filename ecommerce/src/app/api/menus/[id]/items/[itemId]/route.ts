import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";

interface RouteParams {
  params: Promise<{ id: string; itemId: string }>;
}

const VALID_TYPES = new Set([
  "COLLECTION",
  "COLLECTIONS",
  "PRODUCT",
  "PAGE",
  "BLOG",
  "ARTICLE",
  "FRONTPAGE",
  "CATALOG",
  "SEARCH",
  "HTTP",
  "SHOP_POLICY",
  "METAOBJECT",
]);

// GET — single item read. Mostly here for the editor's deep-linking
// "open by URL" behaviour.
export async function GET(_request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;
    const { id, itemId } = await params;

    const item = await prisma.menuItem.findUnique({
      where: { id: itemId },
      include: { children: true },
    });

    if (!item || item.menuId !== id) {
      return NextResponse.json(
        { success: false, error: "Menu item not found" },
        { status: 404 }
      );
    }

    return NextResponse.json({ success: true, data: item });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}

// PUT — update an item. The reorder endpoint handles position/parent
// changes for drag-drop; this one updates the item's content fields.
export async function PUT(request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;
    const { id, itemId } = await params;

    const existing = await prisma.menuItem.findUnique({
      where: { id: itemId },
    });
    if (!existing || existing.menuId !== id) {
      return NextResponse.json(
        { success: false, error: "Menu item not found" },
        { status: 404 }
      );
    }

    const body = await request.json();
    const data: Record<string, unknown> = {};

    if (typeof body.title === "string" && body.title.trim()) {
      data.title = body.title.trim();
    }
    if (typeof body.itemType === "string") {
      const itemType = body.itemType.toUpperCase();
      if (!VALID_TYPES.has(itemType)) {
        return NextResponse.json(
          {
            success: false,
            error: `itemType must be one of: ${Array.from(VALID_TYPES).join(", ")}`,
          },
          { status: 400 }
        );
      }
      data.itemType = itemType;
    }
    if (body.url !== undefined) data.url = body.url || null;
    if (body.resourceId !== undefined) data.resourceId = body.resourceId || null;
    if (body.tagsFilter !== undefined) data.tagsFilter = body.tagsFilter || null;
    if (body.iconUrl !== undefined) data.iconUrl = body.iconUrl || null;
    if (body.bannerUrl !== undefined) data.bannerUrl = body.bannerUrl || null;
    if (body.badgeText !== undefined) data.badgeText = body.badgeText || null;
    if (body.badgeColor !== undefined) data.badgeColor = body.badgeColor || null;
    if (typeof body.pinnedToTop === "boolean") {
      data.pinnedToTop = body.pinnedToTop;
    }
    if (typeof body.position === "number" && body.position >= 0) {
      data.position = body.position;
    }

    // Allow reparenting through PUT for one-off moves. The drag-drop
    // editor uses the bulk /reorder endpoint instead; this exists so a
    // direct API caller (script, API explorer) can move an item too.
    if (body.parentId !== undefined) {
      const newParentId: string | null = body.parentId || null;
      if (newParentId) {
        if (newParentId === itemId) {
          return NextResponse.json(
            { success: false, error: "Item cannot be its own parent" },
            { status: 400 }
          );
        }
        const parent = await prisma.menuItem.findUnique({
          where: { id: newParentId },
        });
        if (!parent || parent.menuId !== id) {
          return NextResponse.json(
            { success: false, error: "parentId not found in this menu" },
            { status: 400 }
          );
        }
      }
      data.parentId = newParentId;
    }

    const updated = await prisma.menuItem.update({
      where: { id: itemId },
      data,
    });

    await prisma.menu.update({
      where: { id },
      data: { locallyModified: true },
    });

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "UPDATE",
      entity: "MENU_ITEM",
      entityId: itemId,
      details: `Updated menu item: ${updated.title}`,
    });

    return NextResponse.json({ success: true, data: updated });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}

// DELETE — remove an item and (via Prisma cascade) its descendants.
export async function DELETE(_request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;
    const { id, itemId } = await params;

    const existing = await prisma.menuItem.findUnique({
      where: { id: itemId },
    });
    if (!existing || existing.menuId !== id) {
      return NextResponse.json(
        { success: false, error: "Menu item not found" },
        { status: 404 }
      );
    }

    await prisma.menuItem.delete({ where: { id: itemId } });
    await prisma.menu.update({
      where: { id },
      data: { locallyModified: true },
    });

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "DELETE",
      entity: "MENU_ITEM",
      entityId: itemId,
      details: `Deleted menu item: ${existing.title}`,
    });

    return NextResponse.json({ success: true });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
