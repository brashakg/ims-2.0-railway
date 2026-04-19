import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";

// GET /api/products/check-duplicate?brand=BOSS&modelNo=1234
// Returns existing product(s) that share the same brand + model no
export async function GET(request: NextRequest) {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const { searchParams } = new URL(request.url);
    const brand = searchParams.get("brand");
    const modelNo = searchParams.get("modelNo");

    if (!brand || !modelNo) {
      return NextResponse.json(
        { success: false, error: "brand and modelNo are required" },
        { status: 400 }
      );
    }

    const existing = await prisma.product.findMany({
      where: {
        brand: { equals: brand, mode: "insensitive" },
        modelNo: { equals: modelNo, mode: "insensitive" },
      },
      include: {
        variants: {
          select: {
            id: true,
            colorCode: true,
            colorName: true,
            frameSize: true,
            sku: true,
          },
          orderBy: [{ colorCode: "asc" }, { frameSize: "asc" }],
        },
        images: { take: 1 },
      },
    });

    return NextResponse.json({
      success: true,
      found: existing.length > 0,
      products: existing.map((p) => ({
        id: p.id,
        title: p.title,
        brand: p.brand,
        modelNo: p.modelNo,
        category: p.category,
        status: p.status,
        mrp: p.mrp,
        sku: p.sku,
        image: p.images[0]?.url || null,
        variants: p.variants,
        variantCount: p.variants.length,
      })),
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
