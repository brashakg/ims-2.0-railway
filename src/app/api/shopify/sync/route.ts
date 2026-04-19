import { NextRequest, NextResponse } from "next/server";
import { requireAuth } from "@/lib/apiAuth";
import {
  fetchProductsForPush,
  pushProductsToShopify,
} from "@/lib/shopifyPush";

interface SyncRequest {
  productIds: string[];
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

    const { results, summary } = await pushProductsToShopify(products);

    return NextResponse.json({
      success: true,
      data: results,
      summary: {
        total: summary.total,
        success: summary.success,
        failed: summary.failed,
        skipped: summary.skipped,
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
