import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";

export async function GET() {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const [totalCustomers, withOrders, acceptsMarketing, totalSpentResult, avgSpentResult] = await Promise.all([
      prisma.customer.count(),
      prisma.customer.count({ where: { ordersCount: { gt: 0 } } }),
      prisma.customer.count({ where: { acceptsMarketing: true } }),
      prisma.customer.aggregate({ _sum: { totalSpent: true } }),
      prisma.customer.aggregate({ _avg: { totalSpent: true } }),
    ]);

    return NextResponse.json({
      success: true,
      data: {
        totalCustomers,
        withOrders,
        acceptsMarketing,
        totalRevenue: totalSpentResult._sum.totalSpent || 0,
        avgOrderValue: avgSpentResult._avg.totalSpent || 0,
      },
    });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 }
    );
  }
}
