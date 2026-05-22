import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";
import { attachMediaToProduct, updateProduct } from "@/lib/shopify";

interface DesignUploadRequest {
  images: Array<{ url: string; originalUrl?: string; alt?: string }>;
  removeRaw?: boolean; // default true — drop the RAW placeholders once we have EDITED
}

// POST /api/products/[id]/design-upload
// Called by the designer after they've edited the raw images and want to
// attach the edited versions to the product. Pushes the images to Shopify,
// flips the product status to PUBLISHED (Shopify ACTIVE), and marks the
// design workflow as READY.
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const auth = await requireAuth(["ADMIN", "DESIGN_MANAGER"]);
    if (!auth.authorized) return auth.response!;
    const { id } = await params;

    const body: DesignUploadRequest = await request.json();

    if (!body.images || body.images.length === 0) {
      return NextResponse.json(
        { success: false, error: "At least one edited image is required" },
        { status: 400 }
      );
    }

    const product = await prisma.product.findUnique({
      where: { id: id },
      include: { images: true },
    });
    if (!product) {
      return NextResponse.json(
        { success: false, error: "Product not found" },
        { status: 404 }
      );
    }
    if (product.imageDesignStatus !== "PENDING_DESIGN") {
      return NextResponse.json(
        {
          success: false,
          error: `Product is not awaiting design (current status: ${product.imageDesignStatus || "not in workflow"}).`,
        },
        { status: 400 }
      );
    }

    // Persist the edited images as ProductImage rows with role=EDITED.
    const startPosition = product.images.length;
    const createdRows = await Promise.all(
      body.images.map((img, idx) =>
        prisma.productImage.create({
          data: {
            productId: product.id,
            url: img.url,
            originalUrl: img.originalUrl || img.url,
            role: "EDITED",
            position: startPosition + idx,
          },
        })
      )
    );

    // Optionally remove RAW placeholders from the DB now that we have
    // EDITED versions. Raw files themselves are in whatever storage the
    // cataloger uploaded to (separate from this call).
    const shouldRemoveRaw = body.removeRaw !== false;
    let removedRawCount = 0;
    if (shouldRemoveRaw) {
      const result = await prisma.productImage.deleteMany({
        where: { productId: product.id, role: "RAW" },
      });
      removedRawCount = result.count;
    }

    let shopifyMessage = "Product not on Shopify yet — local-only publish.";
    let shopifyOk = true;

    if (product.shopifyProductId) {
      // Attach edited images to the Shopify product.
      const attachResult = await attachMediaToProduct(
        product.shopifyProductId,
        body.images
          .filter((img) => img.url.startsWith("http"))
          .map((img) => ({ src: img.url, alt: img.alt || product.title || "" }))
      );
      if (!attachResult.success) {
        shopifyOk = false;
        shopifyMessage = `Failed to attach images to Shopify: ${attachResult.message}`;
      } else {
        // Flip Shopify status to ACTIVE now that images are in place.
        const statusResult = await updateProduct(product.shopifyProductId, {
          status: "ACTIVE",
        });
        if (!statusResult.success) {
          shopifyOk = false;
          shopifyMessage = `Images attached but failed to activate on Shopify: ${statusResult.message}`;
        } else {
          shopifyMessage = `Attached ${attachResult.mediaIds?.length || 0} image(s) and activated on Shopify.`;
        }
      }
    }

    // Update local product status regardless — the designer did their job.
    const updatedProduct = await prisma.product.update({
      where: { id: id },
      data: {
        imageDesignStatus: "READY",
        status: shopifyOk ? "PUBLISHED" : product.status,
      },
    });

    // Log for audit + sync history
    await prisma.syncLog.create({
      data: {
        productId: id,
        action: "DESIGN_UPLOAD",
        status: shopifyOk ? "SUCCESS" : "FAILED",
        message: shopifyMessage,
      },
    });

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "DESIGN_UPLOAD",
      entity: "PRODUCT",
      entityId: id,
      details: `Designer uploaded ${createdRows.length} edited image(s); removed ${removedRawCount} raw; ${shopifyMessage}`,
    });

    return NextResponse.json({
      success: true,
      message: shopifyMessage,
      data: {
        product: updatedProduct,
        editedImagesAdded: createdRows.length,
        rawImagesRemoved: removedRawCount,
        shopifyOk,
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
