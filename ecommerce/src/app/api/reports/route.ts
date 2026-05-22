import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";

export async function GET(request: NextRequest) {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const { searchParams } = new URL(request.url);
    const report = searchParams.get("type") || "overview";

    if (report === "overview") {
      const [
        totalProducts,
        totalOrders,
        totalCustomers,
        totalRevenue,
        productsByCategory,
        productsByStatus,
        topBrands,
        recentOrders,
        ordersByMonth,
      ] = await Promise.all([
        prisma.product.count(),
        prisma.order.count(),
        prisma.customer.count(),
        prisma.order.aggregate({ _sum: { totalPrice: true } }),
        prisma.product.groupBy({ by: ["category"], _count: { id: true } }),
        prisma.product.groupBy({ by: ["status"], _count: { id: true } }),
        prisma.product.groupBy({
          by: ["brand"],
          _count: { id: true },
          orderBy: { _count: { id: "desc" } },
          take: 10,
        }),
        prisma.order.findMany({
          take: 10,
          orderBy: { createdAt: "desc" },
          include: { customer: true, lineItems: true },
        }),
        prisma.$queryRaw`
          SELECT
            TO_CHAR("createdAt", 'YYYY-MM') as month,
            COUNT(*)::int as count,
            COALESCE(SUM("totalPrice"), 0)::float as revenue
          FROM "Order"
          WHERE "createdAt" >= NOW() - INTERVAL '12 months'
          GROUP BY TO_CHAR("createdAt", 'YYYY-MM')
          ORDER BY month ASC
        `,
      ]);

      return NextResponse.json({
        success: true,
        data: {
          totalProducts,
          totalOrders,
          totalCustomers,
          totalRevenue: totalRevenue._sum.totalPrice || 0,
          productsByCategory,
          productsByStatus,
          topBrands,
          recentOrders,
          ordersByMonth,
        },
      });
    }

    if (report === "sales") {
      const [
        ordersByMonth,
        topProducts,
        avgOrderValue,
        ordersByStatus,
      ] = await Promise.all([
        prisma.$queryRaw`
          SELECT
            TO_CHAR("createdAt", 'YYYY-MM') as month,
            COUNT(*)::int as count,
            COALESCE(SUM("totalPrice"), 0)::float as revenue
          FROM "Order"
          WHERE "createdAt" >= NOW() - INTERVAL '12 months'
          GROUP BY TO_CHAR("createdAt", 'YYYY-MM')
          ORDER BY month ASC
        `,
        prisma.$queryRaw`
          SELECT
            li.title,
            SUM(li.quantity)::int as total_sold,
            SUM(li.price * li.quantity)::float as total_revenue
          FROM "OrderLineItem" li
          GROUP BY li.title
          ORDER BY total_sold DESC
          LIMIT 20
        `,
        prisma.order.aggregate({ _avg: { totalPrice: true } }),
        prisma.order.groupBy({ by: ["financialStatus"], _count: { id: true } }),
      ]);

      return NextResponse.json({
        success: true,
        data: {
          ordersByMonth,
          topProducts,
          avgOrderValue: avgOrderValue._avg.totalPrice || 0,
          ordersByStatus,
        },
      });
    }

    if (report === "inventory") {
      const [
        totalStock,
        productsByCategory,
        lowStockProducts,
        outOfStockCount,
      ] = await Promise.all([
        prisma.productLocation.aggregate({ _sum: { quantity: true } }),
        prisma.product.groupBy({ by: ["category"], _count: { id: true } }),
        prisma.$queryRaw`
          SELECT p.id, p.title, p.brand, p.category, COALESCE(SUM(pl.quantity), 0)::int as stock
          FROM "Product" p
          LEFT JOIN "ProductLocation" pl ON pl."productId" = p.id
          GROUP BY p.id, p.title, p.brand, p.category
          HAVING COALESCE(SUM(pl.quantity), 0) > 0 AND COALESCE(SUM(pl.quantity), 0) < 10
          ORDER BY stock ASC
          LIMIT 20
        `,
        prisma.$queryRaw<[{ count: bigint }]>`
          SELECT COUNT(*)::bigint as count FROM (
            SELECT p.id
            FROM "Product" p
            LEFT JOIN "ProductLocation" pl ON pl."productId" = p.id
            GROUP BY p.id
            HAVING COALESCE(SUM(pl.quantity), 0) = 0
          ) sub
        `.then((r) => Number(r[0]?.count || 0)),
      ]);

      return NextResponse.json({
        success: true,
        data: {
          totalStock: totalStock._sum.quantity || 0,
          productsByCategory,
          lowStockProducts,
          outOfStockCount,
        },
      });
    }

    return NextResponse.json(
      { success: false, error: "Unknown report type" },
      { status: 400 }
    );
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 }
    );
  }
}
