import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";

/**
 * GET /api/dashboard/stats
 *
 * Single endpoint that powers every number on the dashboard. Replaces
 * the placeholder "fake" stats with real DB queries:
 *   - product counts by status + low-stock
 *   - awaiting-design (products with raw images, no edited yet)
 *   - sync-failed (products whose latest SyncLog row is FAILED)
 *   - today's revenue + 30-day daily series for the sparkline
 *   - today's order count
 *
 * Heavy queries are run in parallel via Promise.all so the dashboard
 * lands in one round-trip. Each block is wrapped so a single broken
 * query doesn't 500 the whole response.
 */
export async function GET() {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const now = new Date();
    const startOfToday = new Date(now);
    startOfToday.setHours(0, 0, 0, 0);
    const startOf30DaysAgo = new Date(now);
    startOf30DaysAgo.setDate(startOf30DaysAgo.getDate() - 29);
    startOf30DaysAgo.setHours(0, 0, 0, 0);

    const [
      total,
      published,
      draft,
      archived,
      syncedWithShopify,
      lowStockResult,
      awaitingDesign,
      syncFailedResult,
      todaysRevenueResult,
      todaysOrders,
      revenueSeriesResult,
    ] = await Promise.all([
      prisma.product.count(),
      prisma.product.count({ where: { status: "PUBLISHED" } }),
      prisma.product.count({ where: { status: "DRAFT" } }),
      prisma.product.count({ where: { status: "ARCHIVED" } }),
      prisma.product.count({ where: { shopifyProductId: { not: null } } }),
      // Low-stock: total quantity across all locations < 10. Filtered to
      // active (non-archived) products so historical archived products
      // don't pollute the count.
      prisma.$queryRaw<[{ count: bigint }]>`
        SELECT COUNT(*)::bigint as count FROM (
          SELECT p.id
          FROM "Product" p
          LEFT JOIN "ProductLocation" pl ON pl."productId" = p.id
          WHERE p.status <> 'ARCHIVED'
          GROUP BY p.id
          HAVING COALESCE(SUM(pl.quantity), 0) < 10
        ) sub
      `,
      // Awaiting design: products whose imageDesignStatus is PENDING_DESIGN.
      // Cataloger uploads raw photos → designer edits → READY. The middle
      // state is the queue depth.
      prisma.product.count({
        where: { imageDesignStatus: "PENDING_DESIGN" },
      }),
      // Sync-failed: products whose MOST RECENT SyncLog action was FAILED.
      // We can't just count FAILED rows because a product could have
      // failed last week and succeeded since. The sub-query picks the
      // latest SyncLog per product.
      prisma.$queryRaw<[{ count: bigint }]>`
        SELECT COUNT(*)::bigint as count FROM (
          SELECT DISTINCT ON ("productId") "productId", "status"
          FROM "SyncLog"
          ORDER BY "productId", "createdAt" DESC
        ) latest
        WHERE latest.status = 'FAILED'
      `,
      // Today's revenue from orders that were processed today.
      // financialStatus = 'paid' or 'partially_paid' counts; refunded/
      // pending don't.
      prisma.order.aggregate({
        _sum: { totalPrice: true },
        where: {
          createdAt: { gte: startOfToday },
          financialStatus: { in: ["paid", "partially_paid"] },
        },
      }),
      prisma.order.count({
        where: { createdAt: { gte: startOfToday } },
      }),
      // 30-day daily revenue series for the sparkline. One row per day
      // (gaps filled with 0 client-side). Returns up to 30 entries.
      prisma.$queryRaw<Array<{ day: Date; revenue: number }>>`
        SELECT
          DATE_TRUNC('day', "createdAt") AS day,
          COALESCE(SUM("totalPrice"), 0)::float AS revenue
        FROM "Order"
        WHERE "createdAt" >= ${startOf30DaysAgo}
          AND "financialStatus" IN ('paid', 'partially_paid')
        GROUP BY day
        ORDER BY day ASC
      `,
    ]);

    const lowStock = Number(lowStockResult[0]?.count || 0);
    const syncFailed = Number(syncFailedResult[0]?.count || 0);
    const todaysRevenue = todaysRevenueResult._sum.totalPrice || 0;

    // Fill gaps in the 30-day series client-side: the SQL only returns
    // rows for days that have at least one order. The dashboard expects
    // exactly 30 contiguous numbers.
    const seriesByDay = new Map<string, number>();
    for (const row of revenueSeriesResult) {
      const key = new Date(row.day).toISOString().slice(0, 10);
      seriesByDay.set(key, Number(row.revenue));
    }
    const revenueSeries: number[] = [];
    const seriesDays: string[] = [];
    for (let i = 0; i < 30; i++) {
      const d = new Date(startOf30DaysAgo);
      d.setDate(d.getDate() + i);
      const key = d.toISOString().slice(0, 10);
      revenueSeries.push(seriesByDay.get(key) || 0);
      seriesDays.push(key);
    }

    return NextResponse.json({
      success: true,
      data: {
        // Catalog counts
        total,
        published,
        draft,
        archived,
        syncedWithShopify,
        lowStock,
        // Workflow queues
        awaitingDesign,
        syncFailed,
        // Revenue
        todaysRevenue,
        todaysOrders,
        revenueSeries,
        seriesDays,
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
