import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";

interface RouteParams {
  params: Promise<{ id: string }>;
}

// Shopify MenuItemType enum mirror. Anything outside this set is
// rejected up front so we never push an invalid type to Shopify.
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

// GET /api/menus/:id/items — flat list of items for the menu.
// Useful for the resource picker / autocomplete views; the full tree
// is on /api/menus/:id.
export async function GET(_request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;
    const { id } = await params;

    const menu = await prisma.menu.findUnique({ where: { id } });
    if (!menu) {
      return NextResponse.json(
        { success: false, error: "Menu not found" },
        { status: 404 }
      );
    }

    const items = await prisma.menuItem.findMany({
      where: { menuId: id },
      orderBy: [{ parentId: "asc" }, { position: "asc" }],
    });

    return NextResponse.json({ success: true, data: items });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}

// POST /api/menus/:id/items — add a new item.
//
// Body: {
//   title: string,
//   itemType: string,                // COLLECTION | PRODUCT | PAGE | HTTP | ...
//   parentId?: string | null,
//   position?: number,               // defaults to next-available at this level
//   url?: string,                    // for HTTP type
//   resourceId?: string,             // gid://shopify/Collection/123 or local
//   tagsFilter?: string,
//   iconUrl?: string,                // mega-menu thumbnail (M6)
//   bannerUrl?: string,
//   badgeText?: string,
//   badgeColor?: string,
//   pinnedToTop?: boolean,
// }
export async function POST(request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;
    const { id } = await params;

    const menu = await prisma.menu.findUnique({ where: { id } });
    if (!menu) {
      return NextResponse.json(
        { success: false, error: "Menu not found" },
        { status: 404 }
      );
    }

    const body = await request.json();
    const title = (body.title || "").trim();
    const itemType = String(body.itemType || "").toUpperCase();

    if (!title) {
      return NextResponse.json(
        { success: false, error: "title is required" },
        { status: 400 }
      );
    }
    if (!VALID_TYPES.has(itemType)) {
      return NextResponse.json(
        {
          success: false,
          error: `itemType must be one of: ${Array.from(VALID_TYPES).join(", ")}`,
        },
        { status: 400 }
      );
    }

    // HTTP needs a url. Everything else (except FRONTPAGE/CATALOG/SEARCH/SHOP_POLICY)
    // needs a resourceId. We don't enforce hard validation on the
    // resourceId here because the picker/UI may construct GIDs lazily;
    // the Shopify push step revalidates.
    if (itemType === "HTTP" && !body.url) {
      return NextResponse.json(
        { success: false, error: "url is required for HTTP items" },
        { status: 400 }
      );
    }

    const parentId: string | null = body.parentId || null;
    if (parentId) {
      const parent = await prisma.menuItem.findUnique({
        where: { id: parentId },
      });
      if (!parent || parent.menuId !== id) {
        return NextResponse.json(
          { success: false, error: "parentId not found in this menu" },
          { status: 400 }
        );
      }
    }

    // Pick the next position at this level if the caller didn't
    // supply one. We do this in-memory after fetching siblings so the
    // count is stable within the request.
    let position: number;
    if (typeof body.position === "number" && body.position >= 0) {
      position = body.position;
    } else {
      const siblingCount = await prisma.menuItem.count({
        where: { menuId: id, parentId },
      });
      position = siblingCount;
    }

    const item = await prisma.menuItem.create({
      data: {
        menuId: id,
        parentId,
        position,
        title,
        itemType,
        url: body.url || null,
        resourceId: body.resourceId || null,
        tagsFilter: body.tagsFilter || null,
        iconUrl: body.iconUrl || null,
        bannerUrl: body.bannerUrl || null,
        badgeText: body.badgeText || null,
        badgeColor: body.badgeColor || null,
        pinnedToTop: body.pinnedToTop === true,
      },
    });

    // Mark the parent menu as locallyModified so the next push lands.
    await prisma.menu.update({
      where: { id },
      data: { locallyModified: true },
    });

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "CREATE",
      entity: "MENU_ITEM",
      entityId: item.id,
      details: `Added "${title}" (${itemType}) to menu ${menu.handle}`,
    });

    return NextResponse.json({ success: true, data: item }, { status: 201 });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
