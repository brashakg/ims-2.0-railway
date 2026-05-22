import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import {
  validateOrphanForPush,
  type OrphanValidationReason,
} from "@/lib/orphanValidation";

const SUMMARY_SELECT = {
  id: true,
  brand: true,
  modelNo: true,
  title: true,
  mrp: true,
  variants: { select: { mrp: true, discountedPrice: true } },
  images: { select: { id: true } },
} as const;

export async function GET(request: NextRequest) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const { searchParams } = new URL(request.url);
    const page = Math.max(parseInt(searchParams.get("page") || "1"), 1);
    const limit = Math.min(
      Math.max(parseInt(searchParams.get("limit") || "50"), 1),
      200
    );
    const filter = (searchParams.get("filter") || "all") as
      | "all"
      | "pushable"
      | "unpushable";
    const search = searchParams.get("search") || "";

    const where: Record<string, unknown> = { shopifyProductId: null };
    if (search) {
      where.OR = [
        { title: { contains: search, mode: "insensitive" } },
        { brand: { contains: search, mode: "insensitive" } },
        { modelNo: { contains: search, mode: "insensitive" } },
        { productName: { contains: search, mode: "insensitive" } },
      ];
    }

    const allOrphans = await prisma.product.findMany({
      where,
      select: SUMMARY_SELECT,
      orderBy: { createdAt: "desc" },
    });

    let pushable = 0;
    let unpushable = 0;
    const reasonBreakdown: Record<OrphanValidationReason, number> = {
      missing_brand: 0,
      missing_modelNo: 0,
      missing_title: 0,
      no_price: 0,
      no_variants_no_images: 0,
    };

    const classified = allOrphans.map((p) => {
      const v = validateOrphanForPush(p);
      if (v.pushable) pushable++;
      else unpushable++;
      for (const r of v.reasons) reasonBreakdown[r]++;
      return { product: p, validation: v };
    });

    const filtered = classified.filter((c) => {
      if (filter === "pushable") return c.validation.pushable;
      if (filter === "unpushable") return !c.validation.pushable;
      return true;
    });

    const total = allOrphans.length;
    const filteredTotal = filtered.length;
    const pageItems = filtered.slice((page - 1) * limit, page * limit);

    const pageProductIds = pageItems.map((c) => c.product.id);
    const pageProducts = pageProductIds.length
      ? await prisma.product.findMany({
          where: { id: { in: pageProductIds } },
          include: {
            images: { orderBy: { position: "asc" }, take: 1 },
            variants: { select: { id: true } },
          },
        })
      : [];

    const productById = new Map(pageProducts.map((p) => [p.id, p]));
    const data = pageItems
      .map((c) => {
        const full = productById.get(c.product.id);
        if (!full) return null;
        return {
          id: full.id,
          brand: full.brand,
          modelNo: full.modelNo,
          title: full.title,
          category: full.category,
          mrp: full.mrp,
          createdAt: full.createdAt,
          variantCount: full.variants.length,
          thumbnail: full.images[0]?.url || null,
          validation: c.validation,
        };
      })
      .filter(Boolean);

    return NextResponse.json({
      success: true,
      summary: {
        total,
        pushable,
        unpushable,
        reasonBreakdown,
      },
      pagination: {
        page,
        limit,
        total: filteredTotal,
        pages: Math.max(Math.ceil(filteredTotal / limit), 1),
      },
      data,
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
