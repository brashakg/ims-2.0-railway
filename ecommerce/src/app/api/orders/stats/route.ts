import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";

export async function GET() {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const [totalOrders, openOrders, closedOrders, cancelledOrders, revenueResult, todayOrders] = await Promise.all([
      prisma.order.count(),
      prisma.order.count({ where: { orderStatus: "OPEN" } }),
      prisma.order.count({ where: { orderStatus: "CLOSED" } }),
      prisma.order.count({ where: { orderStatus: "CANCELLED" } }),
      prisma.order.aggregate({ _sum: { totalPrice: true } }),
      prisma.order.count({
        where: {
          createdAt: {
            gte: new Date(new Date().setHours(0, 0, 0, 0)),
          },
        },
      }),
    ]);

    return NextResponse.json({
      success: true,
      data: {
        totalOrders,
        openOrders,
        closedOrders,
        cancelledOrders,
        totalRevenue: revenueResult._sum.totalPrice || 0,
        todayOrders,
      },
    });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 }
    );
  }
}
