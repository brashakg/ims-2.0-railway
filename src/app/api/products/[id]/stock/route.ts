import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { updateInventory } from "@/lib/shopify";

interface UpdateStockRequest {
  locationId: string;
  quantity: number;
}

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const auth = await requireAuth(["ADMIN", "CATALOG_MANAGER"]);
    if (!auth.authorized) return auth.response!;
    const { id } = await params;

    const body: UpdateStockRequest = await request.json();

    const product = await prisma.product.findUnique({
      where: { id },
      include: { locations: true },
    });

    if (!product) {
      return NextResponse.json(
        { success: false, error: "Product not found" },
        { status: 404 }
      );
    }

    const existingLocation = product.locations.find(
      (l) => l.locationId === body.locationId
    );

    if (!existingLocation) {
      return NextResponse.json(
        { success: false, error: "Product not available at this location" },
        { status: 404 }
      );
    }

    const updatedLocation = await prisma.productLocation.update({
      where: { id: existingLocation.id },
      data: { quantity: body.quantity },
    });

    if (product.shopifyProductId && product.status === "PUBLISHED") {
      const shopifyResult = await updateInventory(
        product.shopifyProductId,
        body.locationId,
        body.quantity - existingLocation.quantity
      );

      await prisma.syncLog.create({
        data: {
          productId: id,
          action: "STOCK_UPDATE",
          status: shopifyResult.success ? "SUCCESS" : "FAILED",
          message: shopifyResult.message,
        },
      });
    }

    return NextResponse.json(
      {
        success: true,
        data: updatedLocation,
        message: "Stock updated successfully",
      },
      { status: 200 }
    );
  } catch (error) {
    const errorMessage =
      error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { success: false, error: errorMessage },
      { status: 500 }
    );
  }
}
