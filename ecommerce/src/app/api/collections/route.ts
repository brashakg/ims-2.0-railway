import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";

// GET /api/collections — List all local collections
export async function GET(request: NextRequest) {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const { searchParams } = new URL(request.url);
    const search = searchParams.get("search");
    const type = searchParams.get("type"); // CUSTOM or SMART
    const page = parseInt(searchParams.get("page") || "1");
    const limit = parseInt(searchParams.get("limit") || "50");

    const where: Record<string, unknown> = {};
    if (type) where.collectionType = type;
    if (search) {
      where.OR = [
        { title: { contains: search } },
        { handle: { contains: search } },
        { description: { contains: search } },
      ];
    }

    const skip = (page - 1) * limit;

    const [collections, total] = await Promise.all([
      prisma.collection.findMany({
        where,
        skip,
        take: limit,
        orderBy: { title: "asc" },
        include: {
          _count: { select: { products: true } },
        },
      }),
      prisma.collection.count({ where }),
    ]);

    return NextResponse.json({
      success: true,
      data: collections,
      pagination: { page, limit, total, pages: Math.ceil(total / limit) },
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
