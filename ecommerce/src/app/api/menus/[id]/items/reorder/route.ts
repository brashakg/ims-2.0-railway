import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";

interface RouteParams {
  params: Promise<{ id: string }>;
}

// Body shape:
// { items: [{ id: string, position: number, parentId: string | null }] }
//
// The drag-drop editor saves the entire affected level (or the entire
// tree) on every drop so we don't have to track operation deltas.
// The endpoint accepts a partial update — only the items in the body
// are reindexed; everything else is left alone. Wrapping the writes
// in a transaction keeps the tree consistent if any single update
// fails (e.g. a parentId points to a now-deleted node).
//
// We deliberately don't validate cycles strictly: if the editor tries
// to move a node into its own descendant it'll either silently land
// somewhere odd or hit the menuId mismatch check below. The Shopify
// push reapplies tree shape from scratch so a brief "weird state"
// can't escape the local DB.
export async function POST(request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;
    const { id } = await params;

    const body = await request.json();
    const items: Array<{
      id: string;
      position: number;
      parentId: string | null;
    }> = body.items || [];

    if (!Array.isArray(items) || items.length === 0) {
      return NextResponse.json(
        { success: false, error: "items array is required" },
        { status: 400 }
      );
    }

    const menu = await prisma.menu.findUnique({ where: { id } });
    if (!menu) {
      return NextResponse.json(
        { success: false, error: "Menu not found" },
        { status: 404 }
      );
    }

    // Single fetch for ALL items in this menu so we can validate
    // membership, parentage, and self-reference without N+1.
    const all = await prisma.menuItem.findMany({
      where: { menuId: id },
      select: { id: true, parentId: true },
    });
    const memberIds = new Set(all.map((i) => i.id));

    for (const change of items) {
      if (!change.id || typeof change.position !== "number") {
        return NextResponse.json(
          {
            success: false,
            error: "each item must have id and numeric position",
          },
          { status: 400 }
        );
      }
      if (!memberIds.has(change.id)) {
        return NextResponse.json(
          {
            success: false,
            error: `item ${change.id} not found in this menu`,
          },
          { status: 400 }
        );
      }
      if (change.parentId) {
        if (change.parentId === change.id) {
          return NextResponse.json(
            {
              success: false,
              error: `item ${change.id} cannot be its own parent`,
            },
            { status: 400 }
          );
        }
        if (!memberIds.has(change.parentId)) {
          return NextResponse.json(
            {
              success: false,
              error: `parentId ${change.parentId} not found in this menu`,
            },
            { status: 400 }
          );
        }
      }
    }

    // Single transaction — if any single write fails we want to leave
    // the original positions in place rather than half-applying the
    // reorder.
    await prisma.$transaction(
      items.map((change) =>
        prisma.menuItem.update({
          where: { id: change.id },
          data: {
            position: change.position,
            parentId: change.parentId || null,
          },
        })
      )
    );

    await prisma.menu.update({
      where: { id },
      data: { locallyModified: true },
    });

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "UPDATE",
      entity: "MENU",
      entityId: id,
      details: `Reordered ${items.length} item(s) in menu ${menu.handle}`,
    });

    return NextResponse.json({ success: true, updated: items.length });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
