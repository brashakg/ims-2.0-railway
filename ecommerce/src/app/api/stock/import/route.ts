import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";

export async function POST(request: NextRequest) {
  try {
    // Audit fix: was completely unauthenticated. Anyone hitting this
    // endpoint could blindly mutate inventory across all variants. Now
    // requires admin role + the stock_import feature toggle.
    const auth = await requireAuth({ roles: "ADMIN", feature: "stock_import" });
    if (!auth.authorized) return auth.response!;

    const body = await request.json();
    const { items, locationId } = body as {
      items: Array<{ sku: string; barcode?: string; quantity: number }>;
      locationId?: string;
    };

    if (!items || !Array.isArray(items) || items.length === 0) {
      return NextResponse.json(
        { error: "items array is required" },
        { status: 400 }
      );
    }

    // Use provided location or default to SHOPIFY
    let location;
    if (locationId) {
      location = await prisma.location.findUnique({ where: { id: locationId } });
    }
    if (!location) {
      location = await prisma.location.findFirst({ where: { code: "SHOPIFY" } });
    }
    if (!location) {
      location = await prisma.location.create({
        data: {
          name: "Shopify Online Store",
          code: "SHOPIFY",
          address: "Online",
          isActive: true,
        },
      });
    }

    let matched = 0;
    let notFound = 0;
    let updated = 0;
    let errors: string[] = [];
    const notFoundSkus: string[] = [];

    // Process in batches
    const BATCH_SIZE = 50;
    for (let i = 0; i < items.length; i += BATCH_SIZE) {
      const batch = items.slice(i, i + BATCH_SIZE);
      const skus = batch.map((item) => item.sku).filter(Boolean);
      const barcodes = batch.map((item) => item.barcode).filter(Boolean) as string[];

      // Search across product SKU, variant SKU, and variant barcode
      const [productsBySku, variantsBySku, variantsByBarcode] = await Promise.all([
        skus.length > 0
          ? prisma.product.findMany({
              where: { sku: { in: skus } },
              select: { id: true, sku: true },
            })
          : [],
        skus.length > 0
          ? prisma.productVariant.findMany({
              where: { sku: { in: skus } },
              select: { id: true, sku: true, productId: true },
            })
          : [],
        barcodes.length > 0
          ? prisma.productVariant.findMany({
              where: { barcode: { in: barcodes } },
              select: { id: true, barcode: true, productId: true },
            })
          : [],
      ]);

      // Build lookup maps
      const skuToProductId = new Map(productsBySku.map((p) => [p.sku, p.id]));
      const skuToVariant = new Map(variantsBySku.map((v) => [v.sku, v]));
      const barcodeToVariant = new Map(variantsByBarcode.map((v) => [v.barcode, v]));

      for (const item of batch) {
        // Try matching in order: variant barcode > variant SKU > product SKU
        const variantByBarcode = item.barcode ? barcodeToVariant.get(item.barcode) : null;
        const variantBySku = skuToVariant.get(item.sku);
        const productId = skuToProductId.get(item.sku);

        if (variantByBarcode) {
          // Match by barcode — update variant-level inventory
          matched++;
          try {
            await prisma.variantLocation.upsert({
              where: {
                variantId_locationId: {
                  variantId: variantByBarcode.id,
                  locationId: location.id,
                },
              },
              update: { quantity: item.quantity },
              create: {
                variantId: variantByBarcode.id,
                locationId: location.id,
                quantity: item.quantity,
              },
            });
            updated++;
          } catch (err: any) {
            errors.push(`Barcode ${item.barcode}: ${err.message}`);
          }
        } else if (variantBySku) {
          // Match by variant SKU — update variant-level inventory
          matched++;
          try {
            await prisma.variantLocation.upsert({
              where: {
                variantId_locationId: {
                  variantId: variantBySku.id,
                  locationId: location.id,
                },
              },
              update: { quantity: item.quantity },
              create: {
                variantId: variantBySku.id,
                locationId: location.id,
                quantity: item.quantity,
              },
            });
            updated++;
          } catch (err: any) {
            errors.push(`SKU ${item.sku}: ${err.message}`);
          }
        } else if (productId) {
          // Fallback: match by product SKU — update product-level inventory
          matched++;
          try {
            await prisma.productLocation.upsert({
              where: {
                productId_locationId: {
                  productId,
                  locationId: location.id,
                },
              },
              update: { quantity: item.quantity },
              create: {
                productId,
                locationId: location.id,
                quantity: item.quantity,
              },
            });
            updated++;
          } catch (err: any) {
            errors.push(`SKU ${item.sku}: ${err.message}`);
          }
        } else {
          notFound++;
          if (notFoundSkus.length < 20) {
            notFoundSkus.push(item.barcode || item.sku);
          }
        }
      }
    }

    return NextResponse.json({
      success: true,
      summary: {
        totalItems: items.length,
        matched,
        updated,
        notFound,
        errors: errors.length,
      },
      notFoundSkus: notFoundSkus.slice(0, 20),
      errors: errors.slice(0, 10),
    });
  } catch (err: any) {
    console.error("Stock import error:", err);
    return NextResponse.json(
      { error: err.message || "Internal server error" },
      { status: 500 }
    );
  }
}
