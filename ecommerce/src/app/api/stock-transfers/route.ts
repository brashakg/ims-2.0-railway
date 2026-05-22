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

    const skip = (page - 1) * limit;
    const where: Record<string, unknown> = {};
    if (status && status !== "All") where.status = status;

    const [transfers, total] = await Promise.all([
      prisma.stockTransfer.findMany({
        where,
        skip,
        take: limit,
        include: { items: true },
        orderBy: { createdAt: "desc" },
      }),
      prisma.stockTransfer.count({ where }),
    ]);

    const locationIds = new Set<string>();
    transfers.forEach((t) => {
      locationIds.add(t.fromLocationId);
      locationIds.add(t.toLocationId);
    });

    const locations = await prisma.location.findMany({
      where: { id: { in: Array.from(locationIds) } },
    });
    const locationMap = new Map(locations.map((l) => [l.id, l]));

    const enriched = transfers.map((t) => ({
      ...t,
      fromLocation: locationMap.get(t.fromLocationId),
      toLocation: locationMap.get(t.toLocationId),
    }));

    return NextResponse.json({
      success: true,
      data: enriched,
      pagination: { page, limit, total, pages: Math.ceil(total / limit) },
    });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 }
    );
  }
}

export async function POST(request: NextRequest) {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const body = await request.json();
    const { fromLocationId, toLocationId, items, note } = body;

    if (!fromLocationId || !toLocationId || !items?.length) {
      return NextResponse.json(
        { success: false, error: "Missing required fields" },
        { status: 400 }
      );
    }

    if (fromLocationId === toLocationId) {
      return NextResponse.json(
        { success: false, error: "Source and destination must be different" },
        { status: 400 }
      );
    }

    const count = await prisma.stockTransfer.count();
    const transferNumber = `ST-${String(count + 1).padStart(5, "0")}`;

    const transfer = await prisma.stockTransfer.create({
      data: {
        transferNumber,
        fromLocationId,
        toLocationId,
        note: note || undefined,
        items: {
          create: items.map((item: any) => ({
            productId: item.productId,
            variantId: item.variantId || undefined,
            productTitle: item.productTitle || undefined,
            variantTitle: item.variantTitle || undefined,
            quantity: item.quantity,
          })),
        },
      },
      include: { items: true },
    });

    return NextResponse.json({ success: true, data: transfer }, { status: 201 });
  } catch (error) {
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 }
    );
  }
}
