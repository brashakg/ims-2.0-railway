import { NextRequest, NextResponse } from "next/server";
import { requireAuth } from "@/lib/apiAuth";
import {
  fetchProductsForPush,
  pushProductsToShopify,
} from "@/lib/shopifyPush";

interface SyncRequest {
  productIds: string[];
  /**
   * "skip" (default) — only create new (unsynced) products. Already-synced
   *                    products are left alone. This is the orphan-push
   *                    semantic and the historic default.
   * "update"         — also re-push edits to already-synced products. Use
   *                    when staff has changed product data locally and
   *                    wants Shopify to reflect the changes.
   */
  syncedMode?: "skip" | "update";
}

export async function POST(request: NextRequest) {
  try {
    const auth = await requireAuth(["ADMIN", "CATALOG_MANAGER"]);
    if (!auth.authorized) return auth.response!;

    const body: SyncRequest = await request.json();

    if (!body.productIds || body.productIds.length === 0) {
      return NextResponse.json(
        { success: false, error: "productIds array is required" },
        { status: 400 }
      );
    }

    const products = await fetchProductsForPush(body.productIds);

    if (products.length === 0) {
      return NextResponse.json(
        { success: false, error: "No products found" },
        { status: 404 }
      );
    }

    const { results, summary } = await pushProductsToShopify(products, {
      syncedMode: body.syncedMode ?? "skip",
    });

    return NextResponse.json({
      success: true,
      data: results,
      summary: {
        total: summary.total,
        success: summary.success,
        failed: summary.failed,
        skipped: summary.skipped,
        aborted: summary.aborted || false,
        abortReason: summary.abortReason,
      },
    });
  } catch (error) {
    const errorMessage =
      error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { success: false, error: errorMessage },
      { status: 500 }
    );
  }
}
