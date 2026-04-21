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
          include: {
            images: { orderBy: { position: "asc" }, take: 1 },
            locations: { include: { location: true } },
          },
          orderBy: [{ colorCode: "asc" }, { frameSize: "asc" }],
        },
        images: { orderBy: { position: "asc" }, take: 1 },
      },
    });

    return NextResponse.json({
      success: true,
      found: existing.length > 0,
      products: existing.map((p) => ({
        id: p.id,
        title: p.title,
        brand: p.brand,
        subBrand: p.subBrand,
        modelNo: p.modelNo,
        category: p.category,
        status: p.status,
        mrp: p.mrp,
        sku: p.sku,
        image: p.images[0]?.url || null,
        variants: p.variants.map((v) => ({
          id: v.id,
          colorCode: v.colorCode,
          colorName: v.colorName,
          frameColor: v.frameColor,
          frameSize: v.frameSize,
          sku: v.sku,
          barcode: v.barcode,
          mrp: v.mrp,
          discountedPrice: v.discountedPrice,
          image: v.images[0]?.url || null,
          locations: v.locations.map((vl) => ({
            id: vl.id,
            locationId: vl.locationId,
            locationName: vl.location.name,
            quantity: vl.quantity,
          })),
          totalStock: v.locations.reduce((s, vl) => s + vl.quantity, 0),
        })),
        variantCount: p.variants.length,
      })),
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
