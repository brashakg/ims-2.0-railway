import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";
import { createShopifyMenu } from "@/lib/shopifyMenus";

// GET /api/menus — list menus.
//
// By default returns active menus only (per round 2 M8 we mark
// additional-pages and desktop-megamenu-eyewear as active=false; the
// editor should hide them so the user can't accidentally push). Pass
// ?includeInactive=1 from an admin tool to see everything.
export async function GET(request: NextRequest) {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const { searchParams } = new URL(request.url);
    const includeInactive = searchParams.get("includeInactive") === "1";

    const where: Record<string, unknown> = {};
    if (!includeInactive) where.active = true;

    const menus = await prisma.menu.findMany({
      where,
      orderBy: [{ isDefault: "desc" }, { handle: "asc" }],
      include: {
        _count: { select: { items: true } },
      },
    });

    return NextResponse.json({ success: true, data: menus });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}

// POST /api/menus — create a new menu (admin only).
//
// Creating a menu locally also creates it on Shopify so the new menu
// has a shopifyMenuId from the moment it exists. If the Shopify call
// fails we still keep the local row but flag it as locallyModified
// so a later sync/push can recover.
export async function POST(request: NextRequest) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const body = await request.json();
    const title = (body.title || "").trim();
    const handle = (body.handle || "").trim().toLowerCase();

    if (!title || !handle) {
      return NextResponse.json(
        { success: false, error: "title and handle are required" },
        { status: 400 }
      );
    }

    if (!/^[a-z0-9-]+$/.test(handle)) {
      return NextResponse.json(
        {
          success: false,
          error: "handle must be lowercase letters, digits, and hyphens only",
        },
        { status: 400 }
      );
    }

    // Reject duplicates upfront so the unique constraint doesn't bubble
    // up as a 500.
    const existing = await prisma.menu.findUnique({ where: { handle } });
    if (existing) {
      return NextResponse.json(
        { success: false, error: `Menu with handle "${handle}" already exists` },
        { status: 409 }
      );
    }

    // Try to create on Shopify first. If that fails (e.g. handle
    // collides with an existing Shopify menu we never pulled), surface
    // the error and bail before writing the local row.
    let shopifyMenuId: string | null = null;
    if (body.pushToShopify !== false) {
      const shopifyResult = await createShopifyMenu({ title, handle });
      if (!shopifyResult.success) {
        return NextResponse.json(
          {
            success: false,
            error: `Shopify create failed: ${shopifyResult.message}`,
          },
          { status: 502 }
        );
      }
      shopifyMenuId = shopifyResult.shopifyMenuId || null;
    }

    const menu = await prisma.menu.create({
      data: {
        title,
        handle,
        isDefault: body.isDefault === true,
        active: body.active !== false,
        shopifyMenuId,
        lastSyncedAt: shopifyMenuId ? new Date() : null,
      },
    });

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "CREATE",
      entity: "MENU",
      entityId: menu.id,
      details: `Created menu: ${title} (${handle})`,
    });

    return NextResponse.json({ success: true, data: menu }, { status: 201 });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
