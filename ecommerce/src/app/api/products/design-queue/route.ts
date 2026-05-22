import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";

// GET /api/products/design-queue
// Lists products the designer still needs to edit images for.
// Shows raw images for download + editing.
export async function GET(request: NextRequest) {
  try {
    const auth = await requireAuth(["ADMIN", "DESIGN_MANAGER"]);
    if (!auth.authorized) return auth.response!;

    const { searchParams } = new URL(request.url);
    const page = Math.max(parseInt(searchParams.get("page") || "1"), 1);
    const limit = Math.min(
      Math.max(parseInt(searchParams.get("limit") || "20"), 1),
      100
    );
    const search = searchParams.get("search") || "";

    const where: Record<string, unknown> = {
      imageDesignStatus: "PENDING_DESIGN",
    };
    if (search) {
      where.OR = [
        { title: { contains: search, mode: "insensitive" } },
        { brand: { contains: search, mode: "insensitive" } },
        { modelNo: { contains: search, mode: "insensitive" } },
      ];
    }

    const [rows, total] = await Promise.all([
      prisma.product.findMany({
        where,
        skip: (page - 1) * limit,
        take: limit,
        include: {
          images: { orderBy: { position: "asc" } },
        },
        orderBy: { createdAt: "asc" }, // oldest first — FIFO
      }),
      prisma.product.count({ where }),
    ]);

    return NextResponse.json({
      success: true,
      data: rows.map((p) => ({
        id: p.id,
        title: p.title,
        brand: p.brand,
        modelNo: p.modelNo,
        category: p.category,
        shopifyProductId: p.shopifyProductId,
        createdAt: p.createdAt,
        rawImages: p.images
          .filter((img) => img.role === "RAW")
          .map((img) => ({
            id: img.id,
            url: img.url,
            originalUrl: img.originalUrl,
            position: img.position,
          })),
        editedImages: p.images
          .filter((img) => img.role === "EDITED")
          .map((img) => ({ id: img.id, url: img.url })),
      })),
      pagination: {
        page,
        limit,
        total,
        pages: Math.max(Math.ceil(total / limit), 1),
      },
    });
  } catch (error) {
    return NextResponse.json(
      {
        success: false,
        error: error instanceof Error ? error.message : "Unknown error",
      },
      { status: 500 }
    );
  }
}
