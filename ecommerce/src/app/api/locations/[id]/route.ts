import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";

// DELETE /api/locations/[id]
// Admin-only. Only deletes locations that (a) are NOT linked to Shopify and
// (b) have no inventory rows pointing at them. Locations synced from Shopify
// must be deleted in Shopify — we don't want drift.
export async function DELETE(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;
    const { id } = await params;

    const location = await prisma.location.findUnique({
      where: { id },
    });

    if (!location) {
      return NextResponse.json(
        { success: false, error: "Location not found" },
        { status: 404 }
      );
    }

    if (location.shopifyLocationId) {
      return NextResponse.json(
        {
          success: false,
          error:
            "This location is linked to Shopify. Delete it in Shopify admin to keep both systems in sync, then re-sync.",
        },
        { status: 400 }
      );
    }

    const [productLocCount, variantLocCount] = await Promise.all([
      prisma.productLocation.count({ where: { locationId: id } }),
      prisma.variantLocation.count({ where: { locationId: id } }),
    ]);

    if (productLocCount > 0 || variantLocCount > 0) {
      return NextResponse.json(
        {
          success: false,
          error: `Cannot delete: ${productLocCount} product and ${variantLocCount} variant inventory rows reference this location. Reassign or clear inventory first.`,
        },
        { status: 400 }
      );
    }

    await prisma.location.delete({ where: { id: id } });

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "DELETE",
      entity: "LOCATION",
      entityId: id,
      details: `Deleted local-only location: ${location.name} (${location.code})`,
    });

    return NextResponse.json({ success: true });
  } catch (error) {
    return NextResponse.json(
      {
        success: false,
        error: error instanceof Error ? error.message : "Unknown error",
      },
      { status: 500 }
    );
  }
}
