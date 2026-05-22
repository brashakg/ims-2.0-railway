import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { updateCollection as shopifyUpdateCollection } from "@/lib/shopify";

// Next 15+ requires `params` to be a Promise that the handler awaits.
interface RouteParams {
  params: Promise<{ id: string }>;
}

// GET /api/collections/:id — Get a single collection with its products
export async function GET(request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;
    const { id } = await params;

    const collection = await prisma.collection.findUnique({
      where: { id },
      include: {
        products: {
          include: {
            product: {
              select: {
                id: true,
                title: true,
                brand: true,
                modelNo: true,
                category: true,
                status: true,
                mrp: true,
                sku: true,
                shopifyProductId: true,
                images: { take: 1, select: { url: true } },
              },
            },
          },
          orderBy: { position: "asc" },
        },
      },
    });

    if (!collection) {
      return NextResponse.json(
        { success: false, error: "Collection not found" },
        { status: 404 }
      );
    }

    return NextResponse.json({ success: true, data: collection });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}

// PATCH /api/collections/:id — Update collection locally + push to Shopify
export async function PATCH(request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;
    const { id } = await params;

    const body = await request.json();
    const collection = await prisma.collection.findUnique({
      where: { id },
    });

    if (!collection) {
      return NextResponse.json(
        { success: false, error: "Collection not found" },
        { status: 404 }
      );
    }

    // Update locally
    const updateData: Record<string, unknown> = { locallyModified: true };
    if (body.title !== undefined) updateData.title = body.title;
    if (body.description !== undefined) updateData.description = body.description;
    if (body.descriptionHtml !== undefined) updateData.descriptionHtml = body.descriptionHtml;
    if (body.seoTitle !== undefined) updateData.seoTitle = body.seoTitle;
    if (body.seoDescription !== undefined) updateData.seoDescription = body.seoDescription;
    if (body.sortOrder !== undefined) updateData.sortOrder = body.sortOrder;
    if (body.imageUrl !== undefined) updateData.imageUrl = body.imageUrl;
    if (body.imageAlt !== undefined) updateData.imageAlt = body.imageAlt;

    const updated = await prisma.collection.update({
      where: { id },
      data: updateData,
    });

    // Push to Shopify
    let shopifyResult = { success: true, message: "Not pushed (no Shopify ID)" };
    if (collection.shopifyCollectionId && body.pushToShopify !== false) {
      shopifyResult = await shopifyUpdateCollection(
        collection.shopifyCollectionId,
        {
          title: body.title,
          description: body.description,
          descriptionHtml: body.descriptionHtml,
          seoTitle: body.seoTitle,
          seoDescription: body.seoDescription,
          sortOrder: body.sortOrder,
          imageUrl: body.imageUrl,
          imageAlt: body.imageAlt,
        }
      );

      if (shopifyResult.success) {
        await prisma.collection.update({
          where: { id },
          data: { locallyModified: false, lastSyncedAt: new Date() },
        });
      }
    }

    return NextResponse.json({
      success: true,
      data: updated,
      shopifySync: shopifyResult,
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
