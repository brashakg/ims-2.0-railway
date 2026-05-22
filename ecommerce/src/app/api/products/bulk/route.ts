import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { deleteProduct, updateProduct, updateVariantPrice } from "@/lib/shopify";
import { logActivity } from "@/lib/activityLog";
import { calculateDiscountedPrice } from "@/lib/autoGenerate";

// POST /api/products/bulk — Bulk operations on products
export async function POST(request: NextRequest) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const body = await request.json();
    const { action, productIds, data } = body as {
      action: "delete" | "status" | "price" | "sync";
      productIds: string[];
      data?: { status?: string; priceAdjustment?: number; priceType?: "percent" | "fixed" };
    };

    if (!action || !productIds || !Array.isArray(productIds) || productIds.length === 0) {
      return NextResponse.json(
        { success: false, error: "Missing action or productIds" },
        { status: 400 }
      );
    }

    let successCount = 0;
    let errorCount = 0;
    const errors: string[] = [];

    if (action === "delete") {
      // Bulk archive + delete from Shopify
      for (const id of productIds) {
        try {
          const product = await prisma.product.findUnique({ where: { id } });
          if (!product) { errorCount++; continue; }

          await prisma.product.update({
            where: { id },
            data: { status: "ARCHIVED" },
          });

          if (product.shopifyProductId) {
            await deleteProduct(product.shopifyProductId).catch(() => {});
          }
          successCount++;
        } catch (e) {
          errorCount++;
          errors.push(`${id}: ${e instanceof Error ? e.message : "Unknown"}`);
        }
      }

      logActivity({
        userId: (auth.session?.user as any)?.id,
        userName: auth.session?.user?.name,
        userEmail: auth.session?.user?.email,
        action: "BULK_DELETE",
        entity: "PRODUCT",
        details: `Bulk archived ${successCount} products (${errorCount} errors)`,
      });

    } else if (action === "status" && data?.status) {
      // Bulk status change
      const validStatuses = ["DRAFT", "PUBLISHED", "ARCHIVED"];
      if (!validStatuses.includes(data.status)) {
        return NextResponse.json(
          { success: false, error: `Invalid status: ${data.status}` },
          { status: 400 }
        );
      }

      for (const id of productIds) {
        try {
          await prisma.product.update({
            where: { id },
            data: { status: data.status },
          });
          successCount++;
        } catch (e) {
          errorCount++;
          errors.push(`${id}: ${e instanceof Error ? e.message : "Unknown"}`);
        }
      }

      logActivity({
        userId: (auth.session?.user as any)?.id,
        userName: auth.session?.user?.name,
        userEmail: auth.session?.user?.email,
        action: "BULK_STATUS",
        entity: "PRODUCT",
        details: `Bulk status change to ${data.status}: ${successCount} products (${errorCount} errors)`,
      });

    } else if (action === "price" && data?.priceAdjustment !== undefined) {
      // Bulk price update using discount rules
      const discountRules = await prisma.discountRule.findMany();

      for (const id of productIds) {
        try {
          const product = await prisma.product.findUnique({ where: { id } });
          if (!product) { errorCount++; continue; }

          let newMrp = product.mrp;
          if (data.priceType === "percent") {
            newMrp = Math.round(product.mrp * (1 + data.priceAdjustment / 100));
          } else {
            newMrp = product.mrp + data.priceAdjustment;
          }
          if (newMrp < 0) newMrp = 0;

          const newDiscounted = calculateDiscountedPrice(
            newMrp,
            product.category,
            discountRules,
            product.brand,
            product.subBrand,
          );

          await prisma.product.update({
            where: { id },
            data: {
              mrp: newMrp,
              compareAtPrice: newMrp,
              discountedPrice: newDiscounted,
            },
          });

          // Also update all variants for this product
          await prisma.productVariant.updateMany({
            where: { productId: id },
            data: {
              mrp: newMrp,
              compareAtPrice: newMrp,
              discountedPrice: newDiscounted,
            },
          });

          // Sync variant prices to Shopify if published. Bulk-update
          // requires the parent product's Shopify GID (we already have
          // it from the same product row).
          if (product.shopifyProductId && product.status === "PUBLISHED") {
            const variants = await prisma.productVariant.findMany({
              where: { productId: id, shopifyVariantId: { not: null } },
            });
            for (const v of variants) {
              if (v.shopifyVariantId) {
                await updateVariantPrice(
                  product.shopifyProductId,
                  v.shopifyVariantId,
                  String(newDiscounted),
                  String(newMrp)
                ).catch(() => {});
              }
            }
          }

          successCount++;
        } catch (e) {
          errorCount++;
          errors.push(`${id}: ${e instanceof Error ? e.message : "Unknown"}`);
        }
      }

      logActivity({
        userId: (auth.session?.user as any)?.id,
        userName: auth.session?.user?.name,
        userEmail: auth.session?.user?.email,
        action: "BULK_PRICE",
        entity: "PRODUCT",
        details: `Bulk price update (${data.priceType} ${data.priceAdjustment}): ${successCount} products (${errorCount} errors)`,
      });

    } else {
      return NextResponse.json(
        { success: false, error: `Unsupported action: ${action}` },
        { status: 400 }
      );
    }

    return NextResponse.json({
      success: true,
      message: `Bulk ${action}: ${successCount} succeeded, ${errorCount} failed`,
      summary: { successCount, errorCount, errors: errors.slice(0, 10) },
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
