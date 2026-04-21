import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";

interface StockUpdateRequest {
  locationId: string;
  quantity: number;
}

// POST /api/variants/[variantId]/stock
// Instant-save endpoint for the Update Stock UI. Body is a single
// { locationId, quantity } pair. Upserts the VariantLocation row so
// the cataloger can edit a cell and have it persist on blur.
export async function POST(
  request: NextRequest,
  { params }: { params: { variantId: string } }
) {
  try {
    const auth = await requireAuth(["ADMIN", "CATALOG_MANAGER"]);
    if (!auth.authorized) return auth.response!;

    const body: StockUpdateRequest = await request.json();
    if (!body.locationId) {
      return NextResponse.json(
        { success: false, error: "locationId is required" },
        { status: 400 }
      );
    }
    const qty = Number(body.quantity);
    if (!Number.isFinite(qty) || qty < 0) {
      return NextResponse.json(
        { success: false, error: "quantity must be a non-negative number" },
        { status: 400 }
      );
    }

    const variant = await prisma.productVariant.findUnique({
      where: { id: params.variantId },
      select: { id: true, productId: true, colorCode: true, frameSize: true },
    });
    if (!variant) {
      return NextResponse.json(
        { success: false, error: "Variant not found" },
        { status: 404 }
      );
    }

    const location = await prisma.location.findUnique({
      where: { id: body.locationId },
      select: { id: true, name: true },
    });
    if (!location) {
      return NextResponse.json(
        { success: false, error: "Location not found" },
        { status: 404 }
      );
    }

    const updated = await prisma.variantLocation.upsert({
      where: {
        variantId_locationId: {
          variantId: variant.id,
          locationId: location.id,
        },
      },
      update: { quantity: Math.round(qty) },
      create: {
        variantId: variant.id,
        locationId: location.id,
        quantity: Math.round(qty),
      },
    });

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "STOCK_UPDATE",
      entity: "VARIANT",
      entityId: variant.id,
      details: `Set stock of variant ${variant.colorCode || ""}${variant.frameSize ? "/" + variant.frameSize : ""} at ${location.name} to ${Math.round(qty)}`,
    });

    return NextResponse.json({ success: true, data: updated });
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
