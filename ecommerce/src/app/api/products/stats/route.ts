import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";

export async function GET() {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const [total, published, draft, archived, syncedWithShopify, lowStockResult] =
      await Promise.all([
        prisma.product.count(),
        prisma.product.count({ where: { status: "PUBLISHED" } }),
        prisma.product.count({ where: { status: "DRAFT" } }),
        prisma.product.count({ where: { status: "ARCHIVED" } }),
        prisma.product.count({
          where: { shopifyProductId: { not: null } },
        }),
        // Low stock: count products where total quantity across all locations < 10
        prisma.$queryRaw<[{ count: bigint }]>`
          SELECT COUNT(*)::bigint as count FROM (
            SELECT p.id
            FROM "Product" p
            LEFT JOIN "ProductLocation" pl ON pl."productId" = p.id
            GROUP BY p.id
            HAVING COALESCE(SUM(pl.quantity), 0) < 10
          ) sub
        `,
      ]);

    const lowStock = Number(lowStockResult[0]?.count || 0);

    return NextResponse.json({
      success: true,
      data: {
        total,
        published,
        draft,
        archived,
        syncedWithShopify,
        lowStock,
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
