import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { generateSKU } from "@/lib/autoGenerate";

interface RouteParams {
  params: Promise<{ id: string }>;
}

// GET all variants for a product
export async function GET(request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;
    const { id } = await params;

    const product = await prisma.product.findUnique({
      where: { id },
      include: {
        variants: {
          include: {
            images: true,
            locations: { include: { location: true } },
          },
          orderBy: [{ colorCode: "asc" }, { frameSize: "asc" }],
        },
      },
    });

    if (!product) {
      return NextResponse.json(
        { success: false, error: "Product not found" },
        { status: 404 }
      );
    }

    return NextResponse.json({
      success: true,
      data: product.variants,
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}

// POST — create a new variant (or bulk create)
export async function POST(request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth(["ADMIN", "CATALOG_MANAGER"]);
    if (!auth.authorized) return auth.response!;
    const { id } = await params;

    const body = await request.json();
    const product = await prisma.product.findUnique({
      where: { id },
    });

    if (!product) {
      return NextResponse.json(
        { success: false, error: "Product not found" },
        { status: 404 }
      );
    }

    // Support single variant or array of variants
    const variantsInput = Array.isArray(body.variants)
      ? body.variants
      : [body];

    const discountRules = await prisma.discountRule.findMany();

    const created = [];
    for (const v of variantsInput) {
      if (!v.colorCode) {
        return NextResponse.json(
          { success: false, error: "colorCode is required for each variant" },
          { status: 400 }
        );
      }

      // Auto-generate SKU for variant
      const variantSku = generateSKU({
        category: product.category,
        brand: product.brand,
        modelNo: product.modelNo || "",
        frameSize: v.frameSize || "",
        colorCode: v.colorCode,
      });

      // Calculate discounted price
      const mrp = v.mrp || product.mrp || 0;
      const rule = discountRules.find(
        (r) => r.category.toUpperCase() === product.category.toUpperCase()
      );
      const discountedPrice = rule
        ? Math.round(mrp * (1 - rule.discountPercentage / 100))
        : mrp;

      const variant = await prisma.productVariant.create({
        data: {
          productId: id,
          colorCode: v.colorCode,
          colorName: v.colorName || null,
          frameColor: v.frameColor || null,
          templeColor: v.templeColor || null,
          frameSize: v.frameSize || null,
          bridge: v.bridge || null,
          templeLength: v.templeLength || null,
          weight: v.weight || null,
          lensColour: v.lensColour || null,
          tint: v.tint || null,
          mrp,
          discountedPrice,
          compareAtPrice: mrp,
          sku: variantSku,
          barcode: v.barcode || null,
          title: `${v.colorCode}${v.frameSize ? " / " + v.frameSize : ""}`,
          locations: {
            create: (v.locations || []).map(
              (loc: { locationId: string; quantity: number }) => ({
                locationId: loc.locationId,
                quantity: loc.quantity || 0,
              })
            ),
          },
        },
        include: {
          images: true,
          locations: { include: { location: true } },
        },
      });

      created.push(variant);
    }

    return NextResponse.json(
      { success: true, data: created },
      { status: 201 }
    );
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
