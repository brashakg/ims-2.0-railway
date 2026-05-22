import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";
import { updateInventory, fetchShopifyLocations } from "@/lib/shopify";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const { id } = await params;
    const transfer = await prisma.stockTransfer.findUnique({
      where: { id },
      include: { items: true },
    });

    if (!transfer) {
      return NextResponse.json(
        { success: false, error: "Transfer not found" },
        { status: 404 }
      );
    }

    if (transfer.status !== "PENDING" && transfer.status !== "IN_TRANSIT") {
      return NextResponse.json(
        { success: false, error: `Cannot complete transfer with status: ${transfer.status}` },
        { status: 400 }
      );
    }

    for (const item of transfer.items) {
      await prisma.productLocation.updateMany({
        where: {
          productId: item.productId,
          locationId: transfer.fromLocationId,
        },
        data: { quantity: { decrement: item.quantity } },
      });

      await prisma.productLocation.upsert({
        where: {
          productId_locationId: {
            productId: item.productId,
            locationId: transfer.toLocationId,
          },
        },
        update: { quantity: { increment: item.quantity } },
        create: {
          productId: item.productId,
          locationId: transfer.toLocationId,
          quantity: item.quantity,
        },
      });
    }

    // Also update variant-level inventory if variantId is set
    for (const item of transfer.items) {
      if (item.variantId) {
        // Decrement from source
        await prisma.variantLocation.updateMany({
          where: { variantId: item.variantId, locationId: transfer.fromLocationId },
          data: { quantity: { decrement: item.quantity } },
        });
        // Increment at destination
        await prisma.variantLocation.upsert({
          where: {
            variantId_locationId: {
              variantId: item.variantId,
              locationId: transfer.toLocationId,
            },
          },
          update: { quantity: { increment: item.quantity } },
          create: {
            variantId: item.variantId,
            locationId: transfer.toLocationId,
            quantity: item.quantity,
          },
        });
      }
    }

    const updated = await prisma.stockTransfer.update({
      where: { id },
      data: { status: "COMPLETED", completedAt: new Date() },
      include: { items: true },
    });

    // ── Sync inventory changes to Shopify ──
    // Look up Shopify location IDs for source and destination
    const [fromLoc, toLoc] = await Promise.all([
      prisma.location.findUnique({ where: { id: transfer.fromLocationId } }),
      prisma.location.findUnique({ where: { id: transfer.toLocationId } }),
    ]);

    // Auto-map locations if needed
    if (fromLoc || toLoc) {
      const needsMapping = (fromLoc && !fromLoc.shopifyLocationId) || (toLoc && !toLoc.shopifyLocationId);
      if (needsMapping) {
        const shopifyLocs = await fetchShopifyLocations().catch(() => ({ success: false as const }));
        if (shopifyLocs.success && shopifyLocs.locations) {
          for (const sl of shopifyLocs.locations) {
            // Match by name similarity
            const slName = sl.name.toLowerCase();
            if (fromLoc && !fromLoc.shopifyLocationId && fromLoc.name.toLowerCase().includes(slName)) {
              await prisma.location.update({ where: { id: fromLoc.id }, data: { shopifyLocationId: sl.id } });
              fromLoc.shopifyLocationId = sl.id;
            }
            if (toLoc && !toLoc.shopifyLocationId && toLoc.name.toLowerCase().includes(slName)) {
              await prisma.location.update({ where: { id: toLoc.id }, data: { shopifyLocationId: sl.id } });
              toLoc.shopifyLocationId = sl.id;
            }
          }
        }
      }
    }

    // Sync variant inventory to Shopify for both locations
    let shopifySyncCount = 0;
    for (const item of transfer.items) {
      if (!item.variantId) continue;

      const variant = await prisma.productVariant.findUnique({
        where: { id: item.variantId },
      });
      if (!variant?.shopifyInventoryItemId) continue;

      // Decrement from source Shopify location
      if (fromLoc?.shopifyLocationId) {
        await updateInventory(
          variant.shopifyInventoryItemId,
          fromLoc.shopifyLocationId,
          -item.quantity
        ).catch(() => {});
        shopifySyncCount++;
      }

      // Increment at destination Shopify location
      if (toLoc?.shopifyLocationId) {
        await updateInventory(
          variant.shopifyInventoryItemId,
          toLoc.shopifyLocationId,
          item.quantity
        ).catch(() => {});
        shopifySyncCount++;
      }
    }

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "COMPLETE",
      entity: "STOCK_TRANSFER",
      entityId: id,
      details: `Completed transfer ${transfer.transferNumber}: ${transfer.items.length} items moved${shopifySyncCount > 0 ? `, ${shopifySyncCount} Shopify inventory adjustments` : ""}`,
    });

    return NextResponse.json({ success: true, data: updated });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 }
    );
  }
}
