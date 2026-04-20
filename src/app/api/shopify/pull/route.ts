import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";
import {
  fetchAllProducts,
  fetchProductByShopifyId,
} from "@/lib/shopify";
import {
  upsertShopifyProduct,
  syncShopifyLocationsToMap,
} from "@/lib/shopifyPullHelpers";

// Allow up to 5 minutes for large product pulls (use /api/shopify/pull/chunk
// for resumable chunked pulls that never hit this cap).
export const maxDuration = 300;

// POST /api/shopify/pull — Pull ALL products from Shopify into local DB
export async function POST(request: NextRequest) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const body = await request.json().catch(() => ({}));
    const singleProductId = body.shopifyProductId; // Optional: pull a single product

    let pulledCount = 0;
    let updatedCount = 0;
    let errorCount = 0;
    const errors: string[] = [];

    // Sync real Shopify locations BEFORE products so per-location
    // VariantLocation rows can be populated in a single pass.
    const { locationMap, synced: locationsSynced } =
      await syncShopifyLocationsToMap();

    if (singleProductId) {
      // Pull a single product by Shopify GID
      const result = await fetchProductByShopifyId(singleProductId);
      if (!result.success || !result.product) {
        return NextResponse.json(
          { success: false, error: result.error || "Product not found" },
          { status: 404 }
        );
      }

      try {
        const existing = await prisma.product.findFirst({
          where: { shopifyProductId: singleProductId },
        });
        await upsertShopifyProduct(result.product, locationMap);
        if (existing) updatedCount++;
        else pulledCount++;
      } catch (e) {
        errorCount++;
        errors.push(
          `${result.product.title}: ${e instanceof Error ? e.message : "Unknown error"}`
        );
      }
    } else {
      // Pull ALL products
      const result = await fetchAllProducts();
      if (!result.success || !result.products) {
        return NextResponse.json(
          { success: false, error: result.error || "Failed to fetch from Shopify" },
          { status: 502 }
        );
      }

      for (const sp of result.products) {
        try {
          const existing = await prisma.product.findFirst({
            where: { shopifyProductId: sp.id },
          });
          await upsertShopifyProduct(sp, locationMap);
          if (existing) updatedCount++;
          else pulledCount++;
        } catch (e) {
          errorCount++;
          errors.push(
            `${sp.title}: ${e instanceof Error ? e.message : "Unknown error"}`
          );
        }
      }
    }

    // Log activity
    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "PULL",
      entity: "SHOPIFY",
      details: `Shopify pull: ${pulledCount} new, ${updatedCount} updated, ${errorCount} errors, ${locationsSynced} locations synced`,
    });

    return NextResponse.json({
      success: true,
      message: `Pull complete: ${pulledCount} new, ${updatedCount} updated, ${errorCount} errors`,
      summary: {
        newProducts: pulledCount,
        updatedProducts: updatedCount,
        errors: errorCount,
        errorDetails: errors.slice(0, 20), // Limit error details
      },
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    console.error("Shopify pull error:", msg);
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}

// GET /api/shopify/pull — Get pull status / preview what would be pulled
export async function GET() {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    // Count local products with and without shopify IDs
    const [totalLocal, syncedProducts, unsyncedProducts] = await Promise.all([
      prisma.product.count(),
      prisma.product.count({ where: { shopifyProductId: { not: null } } }),
      prisma.product.count({ where: { shopifyProductId: null } }),
    ]);

    return NextResponse.json({
      success: true,
      data: {
        totalLocalProducts: totalLocal,
        syncedWithShopify: syncedProducts,
        localOnly: unsyncedProducts,
      },
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
