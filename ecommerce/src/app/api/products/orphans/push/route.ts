import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";
import { validateOrphanForPush } from "@/lib/orphanValidation";
import {
  fetchProductsForPush,
  pushProductsToShopify,
} from "@/lib/shopifyPush";

interface PushRequest {
  limit?: number;
  productIds?: string[];
}

export async function POST(request: NextRequest) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const body: PushRequest = await request.json().catch(() => ({}));
    const limit = Math.min(Math.max(body.limit ?? 50, 1), 100);

    let candidateIds: string[] = [];

    if (body.productIds && body.productIds.length > 0) {
      const specified = await prisma.product.findMany({
        where: {
          id: { in: body.productIds },
          shopifyProductId: null,
        },
        select: {
          id: true,
          brand: true,
          modelNo: true,
          title: true,
          mrp: true,
          variants: { select: { mrp: true, discountedPrice: true } },
          images: { select: { id: true } },
        },
      });
      candidateIds = specified
        .filter((p) => validateOrphanForPush(p).pushable)
        .map((p) => p.id);
    } else {
      const orphans = await prisma.product.findMany({
        where: { shopifyProductId: null },
        select: {
          id: true,
          brand: true,
          modelNo: true,
          title: true,
          mrp: true,
          variants: { select: { mrp: true, discountedPrice: true } },
          images: { select: { id: true } },
        },
        orderBy: { createdAt: "desc" },
      });
      candidateIds = orphans
        .filter((p) => validateOrphanForPush(p).pushable)
        .map((p) => p.id)
        .slice(0, limit);
    }

    if (candidateIds.length === 0) {
      const remainingPushable = await countPushableOrphans();
      return NextResponse.json({
        success: true,
        results: [],
        summary: { total: 0, success: 0, failed: 0, skipped: 0 },
        remainingPushable,
      });
    }

    const products = await fetchProductsForPush(candidateIds);
    const { results, summary } = await pushProductsToShopify(products);

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "ORPHAN_PUSH",
      entity: "PRODUCT",
      details: `Orphan push: ${summary.success} succeeded, ${summary.failed} failed, ${summary.skipped} skipped (of ${summary.total})`,
    });

    const remainingPushable = await countPushableOrphans();

    return NextResponse.json({
      success: true,
      results,
      summary,
      remainingPushable,
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

async function countPushableOrphans(): Promise<number> {
  const rows = await prisma.product.findMany({
    where: { shopifyProductId: null },
    select: {
      brand: true,
      modelNo: true,
      title: true,
      mrp: true,
      variants: { select: { mrp: true, discountedPrice: true } },
      images: { select: { id: true } },
    },
  });
  return rows.filter((p) => validateOrphanForPush(p).pushable).length;
}
