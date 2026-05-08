import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";

interface RouteParams {
  params: Promise<{ id: string; variantId: string }>;
}

// GET single variant
export async function GET(request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;
    const { id, variantId } = await params;

    const variant = await prisma.productVariant.findFirst({
      where: { id: variantId, productId: id },
      include: {
        images: true,
        locations: { include: { location: true } },
      },
    });

    if (!variant) {
      return NextResponse.json(
        { success: false, error: "Variant not found" },
        { status: 404 }
      );
    }

    return NextResponse.json({ success: true, data: variant });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}

// PUT — update a variant
export async function PUT(request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth(["ADMIN", "CATALOG_MANAGER"]);
    if (!auth.authorized) return auth.response!;
    const { id, variantId } = await params;

    const body = await request.json();

    const existing = await prisma.productVariant.findFirst({
      where: { id: variantId, productId: id },
    });

    if (!existing) {
      return NextResponse.json(
        { success: false, error: "Variant not found" },
        { status: 404 }
      );
    }

    // Allowlist — defensive against unknown keys from the form. New
    // variant-level fields added in round 2 mapping (storeBarcode, power,
    // packSize, cylinder, axis, strapColor, caseSize, dialColor, extras)
    // are accepted here.
    const ALLOWED_VARIANT_FIELDS = [
      "colorCode", "colorName", "frameColor", "templeColor", "frameSize",
      "bridge", "templeLength", "weight", "lensColour", "tint",
      "mrp", "discountedPrice", "compareAtPrice", "sku", "title",
      "barcode", "storeBarcode",
      "power", "packSize", "cylinder", "axis",
      "strapColor", "caseSize", "dialColor", "extras",
      "shopifyVariantId", "shopifyInventoryItemId",
    ] as const;
    const updateData: Record<string, unknown> = {};
    for (const k of ALLOWED_VARIANT_FIELDS) {
      if (Object.prototype.hasOwnProperty.call(body, k)) {
        updateData[k] = (body as Record<string, unknown>)[k];
      }
    }
    // Always re-derive title from colour-code + size so it stays canonical.
    updateData.title = `${body.colorCode || existing.colorCode}${
      (body.frameSize || existing.frameSize)
        ? " / " + (body.frameSize || existing.frameSize)
        : ""
    }`;

    const variant = await prisma.productVariant.update({
      where: { id: variantId },
      data: updateData,
      include: {
        images: true,
        locations: { include: { location: true } },
      },
    });

    return NextResponse.json({ success: true, data: variant });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}

// DELETE — remove a variant
export async function DELETE(request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth(["ADMIN", "CATALOG_MANAGER"]);
    if (!auth.authorized) return auth.response!;
    const { id, variantId } = await params;

    const existing = await prisma.productVariant.findFirst({
      where: { id: variantId, productId: id },
    });

    if (!existing) {
      return NextResponse.json(
        { success: false, error: "Variant not found" },
        { status: 404 }
      );
    }

    await prisma.productVariant.delete({
      where: { id: variantId },
    });

    return NextResponse.json({
      success: true,
      message: "Variant deleted successfully",
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
