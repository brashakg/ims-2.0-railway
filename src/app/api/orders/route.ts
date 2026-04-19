import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";

export async function GET(request: NextRequest) {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const { searchParams } = new URL(request.url);
    const page = parseInt(searchParams.get("page") || "1");
    const limit = parseInt(searchParams.get("limit") || "20");
    const status = searchParams.get("status");
    const segment = searchParams.get("segment") || "all";
    const search = searchParams.get("search");

    const skip = (page - 1) * limit;
    const andClauses: Record<string, unknown>[] = [];

    if (status && status !== "All") andClauses.push({ orderStatus: status });

    if (segment === "unfulfilled") {
      andClauses.push({ fulfillmentStatus: { not: "fulfilled" } });
      andClauses.push({ orderStatus: { not: "CANCELLED" } });
    } else if (segment === "unpaid") {
      // Prisma's `in` doesn't accept null — split into OR.
      andClauses.push({
        OR: [
          { financialStatus: { in: ["pending", "authorized"] } },
          { financialStatus: null },
        ],
      });
      andClauses.push({ orderStatus: { not: "CANCELLED" } });
    } else if (segment === "open") {
      andClauses.push({ orderStatus: "OPEN" });
    } else if (segment === "closed") {
      andClauses.push({ orderStatus: "CLOSED" });
    } else if (segment === "cancelled") {
      andClauses.push({ orderStatus: "CANCELLED" });
    }
    if (search) {
      andClauses.push({
        OR: [
          { name: { contains: search, mode: "insensitive" } },
          { email: { contains: search, mode: "insensitive" } },
          { orderNumber: { contains: search, mode: "insensitive" } },
        ],
      });
    }

    const where: Record<string, unknown> =
      andClauses.length > 0 ? { AND: andClauses } : {};

    const [orders, total] = await Promise.all([
      prisma.order.findMany({
        where,
        skip,
        take: limit,
        include: {
          customer: true,
          lineItems: true,
        },
        orderBy: { createdAt: "desc" },
      }),
      prisma.order.count({ where }),
    ]);

    return NextResponse.json({
      success: true,
      data: orders,
      pagination: { page, limit, total, pages: Math.ceil(total / limit) },
    });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 }
    );
  }
}
