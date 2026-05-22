import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { setInventory, fetchShopifyLocations } from "@/lib/shopify";
import { logActivity } from "@/lib/activityLog";

// POST /api/inventory/reconcile — Push local inventory quantities to Shopify
export async function POST(request: NextRequest) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const body = await request.json();
    const { variantIds, locationId } = body as {
      variantIds?: string[]; // Optional: specific variants to reconcile
      locationId?: string;   // Optional: specific local location
    };

    // Get the Shopify location ID
    // If a local locationId is provided, look up its Shopify mapping
    let shopifyLocationId: string | null = null;

    if (locationId) {
      const location = await prisma.location.findUnique({ where: { id: locationId } });
      if (!location) {
        return NextResponse.json(
          { success: false, error: "Location not found" },
          { status: 404 }
        );
      }
      shopifyLocationId = location.shopifyLocationId || null;
    }

    // If no Shopify location ID mapped, try to fetch and auto-map the primary location
    if (!shopifyLocationId) {
      const locResult = await fetchShopifyLocations();
      if (locResult.success && locResult.locations && locResult.locations.length > 0) {
        const primaryLoc = locResult.locations.find((l) => l.isActive) || locResult.locations[0];
        shopifyLocationId = primaryLoc.id;

        // Auto-map to the SHOPIFY location if it exists
        const shopifyLocation = await prisma.location.findFirst({ where: { code: "SHOPIFY" } });
        if (shopifyLocation && !shopifyLocation.shopifyLocationId) {
          await prisma.location.update({
            where: { id: shopifyLocation.id },
            data: { shopifyLocationId: primaryLoc.id },
          });
        }
      }
    }

    if (!shopifyLocationId) {
      return NextResponse.json(
        { success: false, error: "Could not determine Shopify location ID. Please map your locations first." },
        { status: 400 }
      );
    }

    // Get variants to reconcile
    const whereClause: any = {
      shopifyVariantId: { not: null },
      shopifyInventoryItemId: { not: null },
    };
    if (variantIds && variantIds.length > 0) {
      whereClause.id = { in: variantIds };
    }

    const variants = await prisma.productVariant.findMany({
      where: whereClause,
      include: {
        locations: {
          where: locationId ? { locationId } : {},
        },
        product: { select: { title: true, shopifyProductId: true } },
      },
    });

    let successCount = 0;
    let errorCount = 0;
    let skippedCount = 0;
    const errors: string[] = [];

    for (const variant of variants) {
      if (!variant.shopifyInventoryItemId) {
        skippedCount++;
        continue;
      }

      // Get local quantity (sum across locations if no specific location)
      const localQty = variant.locations.reduce((sum, l) => sum + l.quantity, 0);

      try {
        const result = await setInventory(
          variant.shopifyInventoryItemId,
          shopifyLocationId,
          localQty
        );

        if (result.success) {
          successCount++;
        } else {
          errorCount++;
          errors.push(`${variant.sku || variant.id}: ${result.message}`);
        }
      } catch (e) {
        errorCount++;
        errors.push(`${variant.sku || variant.id}: ${e instanceof Error ? e.message : "Unknown"}`);
      }
    }

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "RECONCILE",
      entity: "INVENTORY",
      details: `Inventory reconciliation: ${successCount} synced, ${errorCount} errors, ${skippedCount} skipped (no inventory item ID)`,
    });

    return NextResponse.json({
      success: true,
      message: `Reconciliation complete: ${successCount} synced, ${errorCount} errors, ${skippedCount} skipped`,
      summary: {
        total: variants.length,
        successCount,
        errorCount,
        skippedCount,
        errors: errors.slice(0, 20),
      },
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    console.error("Inventory reconciliation error:", msg);
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
