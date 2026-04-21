import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";

/**
 * Normalize brand / subBrand inputs: trim, store as null if empty so the
 * category-only rule has a stable key (brand=null, subBrand=null).
 */
function normalize(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const t = v.trim();
  return t ? t : null;
}

export async function GET() {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const rules = await prisma.discountRule.findMany({
      orderBy: [
        { category: "asc" },
        // Most-specific first within a category (for UI readability):
        // brand+subBrand rows first, brand-only next, category-only last.
        { brand: { sort: "asc", nulls: "last" } },
        { subBrand: { sort: "asc", nulls: "last" } },
      ],
    });

    return NextResponse.json({ success: true, data: rules });
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

export async function POST(request: NextRequest) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const body = await request.json();
    const { category, discountPercentage, brand, subBrand } = body;

    if (!category || discountPercentage === undefined) {
      return NextResponse.json(
        {
          success: false,
          error: "category and discountPercentage are required",
        },
        { status: 400 }
      );
    }

    const pct = parseFloat(discountPercentage);
    if (isNaN(pct) || pct < 0 || pct > 100) {
      return NextResponse.json(
        {
          success: false,
          error: "discountPercentage must be between 0 and 100",
        },
        { status: 400 }
      );
    }

    const cat = String(category).toUpperCase().trim();
    const br = normalize(brand);
    const sb = normalize(subBrand);

    // Guard: subBrand requires brand (the rule wouldn't apply otherwise).
    if (sb && !br) {
      return NextResponse.json(
        {
          success: false,
          error:
            "subBrand requires a brand — rules are scoped category > brand > subBrand.",
        },
        { status: 400 }
      );
    }

    // Composite-key upsert via findFirst (Prisma can't express a unique
    // constraint that treats NULLs as equal in Postgres without
    // NULLS NOT DISTINCT, which isn't surfaced through @@unique today).
    const existing = await prisma.discountRule.findFirst({
      where: { category: cat, brand: br, subBrand: sb },
    });

    const rule = existing
      ? await prisma.discountRule.update({
          where: { id: existing.id },
          data: { discountPercentage: pct },
        })
      : await prisma.discountRule.create({
          data: {
            category: cat,
            brand: br,
            subBrand: sb,
            discountPercentage: pct,
          },
        });

    return NextResponse.json({ success: true, data: rule });
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

export async function DELETE(request: NextRequest) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const { searchParams } = new URL(request.url);
    const id = searchParams.get("id");

    if (!id) {
      return NextResponse.json(
        { success: false, error: "id is required" },
        { status: 400 }
      );
    }

    await prisma.discountRule.delete({ where: { id } });
    return NextResponse.json({ success: true });
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
