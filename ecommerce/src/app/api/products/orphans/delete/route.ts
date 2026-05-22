import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";

interface DeleteRequest {
  productIds: string[];
  mode: "archive" | "hard";
}

export async function POST(request: NextRequest) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const body: DeleteRequest = await request.json();

    if (
      !body.productIds ||
      !Array.isArray(body.productIds) ||
      body.productIds.length === 0
    ) {
      return NextResponse.json(
        { success: false, error: "productIds array is required" },
        { status: 400 }
      );
    }

    if (body.mode !== "archive" && body.mode !== "hard") {
      return NextResponse.json(
        { success: false, error: "mode must be 'archive' or 'hard'" },
        { status: 400 }
      );
    }

    const orphans = await prisma.product.findMany({
      where: {
        id: { in: body.productIds },
        shopifyProductId: null,
      },
      select: { id: true, title: true },
    });

    if (orphans.length === 0) {
      return NextResponse.json(
        {
          success: false,
          error:
            "No matching orphan products found. Only products with shopifyProductId IS NULL can be touched here.",
        },
        { status: 404 }
      );
    }

    const orphanIds = orphans.map((p) => p.id);
    const skippedCount = body.productIds.length - orphanIds.length;

    let successCount = 0;
    let errorCount = 0;
    const errors: string[] = [];

    if (body.mode === "archive") {
      const result = await prisma.product.updateMany({
        where: { id: { in: orphanIds } },
        data: { status: "ARCHIVED" },
      });
      successCount = result.count;
    } else {
      for (const id of orphanIds) {
        try {
          await prisma.product.delete({ where: { id } });
          successCount++;
        } catch (e) {
          errorCount++;
          errors.push(`${id}: ${e instanceof Error ? e.message : "Unknown"}`);
        }
      }
    }

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: body.mode === "hard" ? "ORPHAN_HARD_DELETE" : "ORPHAN_ARCHIVE",
      entity: "PRODUCT",
      details: `Orphan ${body.mode}: ${successCount} succeeded, ${errorCount} failed, ${skippedCount} skipped (non-orphan)`,
    });

    return NextResponse.json({
      success: true,
      summary: {
        requested: body.productIds.length,
        succeeded: successCount,
        failed: errorCount,
        skippedNonOrphan: skippedCount,
      },
      errors: errors.slice(0, 10),
    });
  } catch (error) {
    const errorMessage =
      error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { success: false, error: errorMessage },
      { status: 500 }
    );
  }
}
