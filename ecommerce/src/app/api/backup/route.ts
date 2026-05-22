import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";

/**
 * GET /api/backup
 * Exports all products from the app database as JSON.
 */
export async function GET() {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const products = await prisma.product.findMany({
      include: {
        variants: true,
        images: true,
        locations: {
          include: { location: true },
        },
      },
    });

    const exportData = {
      exportedAt: new Date().toISOString(),
      version: "2.0",
      totalProducts: products.length,
      products: products.map((p) => ({
        sku: p.sku,
        title: p.title,
        productName: p.productName,
        fullModelNo: p.fullModelNo,
        modelNo: p.modelNo,
        brand: p.brand,
        subBrand: p.subBrand,
        label: p.label,
        category: p.category,
        status: p.status,
        mrp: p.mrp,
        discountedPrice: p.discountedPrice,
        compareAtPrice: p.compareAtPrice,
        shape: p.shape,
        frameMaterial: p.frameMaterial,
        templeMaterial: p.templeMaterial,
        frameType: p.frameType,
        gender: p.gender,
        lensUSP: p.lensUSP,
        lensMaterial: p.lensMaterial,
        polarization: p.polarization,
        uvProtection: p.uvProtection,
        productUSP: p.productUSP,
        warranty: p.warranty,
        countryOfOrigin: p.countryOfOrigin,
        gtin: p.gtin,
        upc: p.upc,
        htmlDescription: p.htmlDescription,
        seoTitle: p.seoTitle,
        seoDescription: p.seoDescription,
        pageUrl: p.pageUrl,
        tags: p.tags,
        shopifyProductId: p.shopifyProductId,
        // Solutions-specific
        recommendedFor: p.recommendedFor,
        instructions: p.instructions,
        ingredients: p.ingredients,
        benefits: p.benefits,
        aboutProduct: p.aboutProduct,
        images: p.images.map((img) => ({
          url: img.url,
          originalUrl: img.originalUrl,
          position: img.position,
        })),
        variants: p.variants.map((v) => ({
          sku: v.sku,
          title: v.title,
          colorCode: v.colorCode,
          colorName: v.colorName,
          frameColor: v.frameColor,
          templeColor: v.templeColor,
          frameSize: v.frameSize,
          bridge: v.bridge,
          templeLength: v.templeLength,
          weight: v.weight,
          lensColour: v.lensColour,
          tint: v.tint,
          mrp: v.mrp,
          discountedPrice: v.discountedPrice,
          compareAtPrice: v.compareAtPrice,
          barcode: v.barcode,
          shopifyVariantId: v.shopifyVariantId,
          shopifyInventoryItemId: v.shopifyInventoryItemId,
        })),
        inventory: p.locations.map((pl) => ({
          locationName: pl.location.name,
          locationCode: pl.location.code,
          quantity: pl.quantity,
        })),
      })),
    };

    const json = JSON.stringify(exportData, null, 2);
    return new NextResponse(json, {
      status: 200,
      headers: {
        "Content-Type": "application/json",
        "Content-Disposition": `attachment; filename="bv-backup-${new Date().toISOString().slice(0, 10)}.json"`,
      },
    });
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Backup failed" },
      { status: 500 }
    );
  }
}

/**
 * POST /api/backup
 * Restores product inventory quantities from a previously exported backup JSON.
 * Only updates quantity fields; does NOT overwrite product data.
 */
export async function POST(request: NextRequest) {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const body = await request.json();
    const { products, restoreInventory = true } = body as {
      products: Array<{
        sku: string;
        inventory?: Array<{ locationCode: string; quantity: number }>;
        variants?: Array<{ sku: string; barcode?: string; inventoryQuantity: number }>;
      }>;
      restoreInventory: boolean;
    };

    if (!products || !Array.isArray(products)) {
      return NextResponse.json(
        { error: "Invalid backup file format" },
        { status: 400 }
      );
    }

    let matched = 0;
    let notFound = 0;
    let updated = 0;
    const notFoundSkus: string[] = [];
    const errors: string[] = [];

    for (const item of products) {
      const product = await prisma.product.findFirst({
        where: { sku: item.sku },
      });

      if (!product) {
        notFound++;
        if (notFoundSkus.length < 20) notFoundSkus.push(item.sku);
        continue;
      }

      matched++;

      // Restore variant barcodes if present
      if (item.variants) {
        for (const v of item.variants) {
          if (v.sku && v.barcode) {
            try {
              await prisma.productVariant.updateMany({
                where: { productId: product.id, sku: v.sku },
                data: { barcode: v.barcode },
              });
            } catch { /* skip if variant not found */ }
          }
        }
      }

      if (restoreInventory && item.inventory) {
        for (const inv of item.inventory) {
          try {
            let location = await prisma.location.findFirst({
              where: { code: inv.locationCode },
            });
            if (!location) {
              location = await prisma.location.create({
                data: {
                  name: inv.locationCode,
                  code: inv.locationCode,
                  address: "",
                  isActive: true,
                },
              });
            }
            await prisma.productLocation.upsert({
              where: {
                productId_locationId: {
                  productId: product.id,
                  locationId: location.id,
                },
              },
              update: { quantity: inv.quantity },
              create: {
                productId: product.id,
                locationId: location.id,
                quantity: inv.quantity,
              },
            });
            updated++;
          } catch (err: any) {
            errors.push(`SKU ${item.sku}: ${err.message}`);
          }
        }
      }
    }

    return NextResponse.json({
      success: true,
      summary: {
        totalProducts: products.length,
        matched,
        updated,
        notFound,
        errors: errors.length,
      },
      notFoundSkus: notFoundSkus.slice(0, 20),
      errors: errors.slice(0, 10),
    });
  } catch (err: any) {
    return NextResponse.json(
      { error: err.message || "Restore failed" },
      { status: 500 }
    );
  }
}
