import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";

/**
 * POST /api/stock-tally
 * Accepts scanned barcodes + locationId.
 * Looks up variants by `barcode` field (Shopify barcode),
 * compares physical scanned qty vs online (VariantLocation) qty,
 * returns item-level comparison + brand & category breakdowns.
 */
export async function POST(request: NextRequest) {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const body = await request.json();
    const { locationId, barcodes } = body as {
      locationId: string;
      barcodes: { barcode: string; quantity: number }[];
    };

    if (!locationId || !barcodes || barcodes.length === 0) {
      return NextResponse.json(
        { success: false, error: "locationId and barcodes[] are required" },
        { status: 400 }
      );
    }

    // Collect unique barcodes and their scanned quantities
    const barcodeQtyMap = new Map<string, number>();
    for (const item of barcodes) {
      const bc = item.barcode.trim();
      if (!bc) continue;
      barcodeQtyMap.set(bc, (barcodeQtyMap.get(bc) || 0) + (item.quantity || 1));
    }

    const uniqueBarcodes = Array.from(barcodeQtyMap.keys());

    // Look up variants by barcode field, then fallback to SKU for unmatched
    const variantsByBarcode = await prisma.productVariant.findMany({
      where: { barcode: { in: uniqueBarcodes } },
      include: {
        product: {
          select: { id: true, title: true, brand: true, category: true },
        },
        locations: {
          where: { locationId },
          select: { quantity: true },
        },
      },
    });

    // Find barcodes that didn't match — try them as SKUs
    const matchedByBarcode = new Set(variantsByBarcode.map((v) => v.barcode!));
    const unmatchedCodes = uniqueBarcodes.filter((b) => !matchedByBarcode.has(b));

    const variantsBySku = unmatchedCodes.length > 0
      ? await prisma.productVariant.findMany({
          where: { sku: { in: unmatchedCodes } },
          include: {
            product: {
              select: { id: true, title: true, brand: true, category: true },
            },
            locations: {
              where: { locationId },
              select: { quantity: true },
            },
          },
        })
      : [];

    const variants = [...variantsByBarcode, ...variantsBySku];

    // Build comparison items
    interface ComparisonItem {
      variantId: string;
      barcode: string;
      variantTitle: string | null;
      productTitle: string | null;
      brand: string;
      category: string;
      physicalQty: number;
      onlineQty: number;
      difference: number;
      status: "match" | "minor" | "major";
    }

    const comparisonData: ComparisonItem[] = [];
    const matchedBarcodes = new Set<string>();

    for (const v of variants) {
      // Match by barcode or by SKU (for fallback matches)
      const bc = v.barcode && barcodeQtyMap.has(v.barcode) ? v.barcode : v.sku && barcodeQtyMap.has(v.sku) ? v.sku : null;
      if (!bc) continue;
      matchedBarcodes.add(bc);
      const physicalQty = barcodeQtyMap.get(bc) || 0;
      const onlineQty = v.locations.reduce((s, l) => s + l.quantity, 0);
      const diff = physicalQty - onlineQty;

      comparisonData.push({
        variantId: v.id,
        barcode: bc,
        variantTitle: v.title,
        productTitle: v.product.title,
        brand: v.product.brand,
        category: v.product.category,
        physicalQty,
        onlineQty,
        difference: diff,
        status: diff === 0 ? "match" : Math.abs(diff) <= 3 ? "minor" : "major",
      });
    }

    // Unmatched barcodes (scanned but not found in DB)
    const unmatchedBarcodes = uniqueBarcodes.filter((b) => !matchedBarcodes.has(b));

    // Brand breakdown
    const brandMap = new Map<string, { totalPhysical: number; totalOnline: number; excess: number; deficit: number }>();
    for (const item of comparisonData) {
      if (!brandMap.has(item.brand)) {
        brandMap.set(item.brand, { totalPhysical: 0, totalOnline: 0, excess: 0, deficit: 0 });
      }
      const b = brandMap.get(item.brand)!;
      b.totalPhysical += item.physicalQty;
      b.totalOnline += item.onlineQty;
      if (item.difference > 0) b.excess += item.difference;
      else b.deficit += Math.abs(item.difference);
    }

    // Category breakdown
    const categoryMap = new Map<string, { totalPhysical: number; totalOnline: number; excess: number; deficit: number }>();
    for (const item of comparisonData) {
      if (!categoryMap.has(item.category)) {
        categoryMap.set(item.category, { totalPhysical: 0, totalOnline: 0, excess: 0, deficit: 0 });
      }
      const c = categoryMap.get(item.category)!;
      c.totalPhysical += item.physicalQty;
      c.totalOnline += item.onlineQty;
      if (item.difference > 0) c.excess += item.difference;
      else c.deficit += Math.abs(item.difference);
    }

    const summary = {
      totalScanned: barcodes.reduce((s, b) => s + (b.quantity || 1), 0),
      uniqueBarcodes: uniqueBarcodes.length,
      matchedVariants: matchedBarcodes.size,
      unmatchedCount: unmatchedBarcodes.length,
      matchingItems: comparisonData.filter((i) => i.status === "match").length,
      minorDifferences: comparisonData.filter((i) => i.status === "minor").length,
      majorDifferences: comparisonData.filter((i) => i.status === "major").length,
      totalVariance: comparisonData.reduce((s, i) => s + Math.abs(i.difference), 0),
    };

    // Log the tally activity
    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "STOCK_TALLY",
      entity: "STOCK_TRANSFER",
      details: `Stock tally: ${summary.uniqueBarcodes} barcodes scanned, ${summary.matchedVariants} matched, ${summary.unmatchedCount} unmatched, ${summary.majorDifferences} major differences`,
      metadata: {
        locationId,
        totalScanned: summary.totalScanned,
        matchedVariants: summary.matchedVariants,
        unmatchedCount: summary.unmatchedCount,
        totalVariance: summary.totalVariance,
      },
    });

    return NextResponse.json({
      success: true,
      data: {
        comparisonData,
        unmatchedBarcodes,
        brandBreakdown: Array.from(brandMap.entries()).map(([name, v]) => ({ name, ...v })),
        categoryBreakdown: Array.from(categoryMap.entries()).map(([name, v]) => ({ name, ...v })),
        summary,
      },
    });
  } catch (error) {
    console.error("Stock tally error:", error);
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 }
    );
  }
}
