import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";

// Strip every non-alphanumeric character and lowercase. Used to tolerate
// variant spellings on brand and model number lookups:
//   "Ray-Ban" <-> "Ray Ban" <-> "RAYBAN" <-> "rayban"
//   "RB 3025" <-> "RB3025" <-> "rb-3025"
function normalize(s: string): string {
  return s.trim().toLowerCase().replace(/[^a-z0-9]/g, "");
}

// GET /api/products/check-duplicate?brand=Ray+Ban&modelNo=RB+3025
//                                  &colorCode=002/32&frameSize=58
//
// Returns existing product(s) whose brand + modelNo match, tolerant of
// punctuation / spacing differences between the query and stored values.
//
// When `colorCode` and/or `frameSize` are also supplied, we additionally
// scan the matched products' variants for an exact (normalized) match
// on those two fields. If found, the variant is returned in
// `exactVariant` so the wizard can route directly to "Update stock" for
// that specific item instead of the parent-product "Add as variant"
// branch.
export async function GET(request: NextRequest) {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const { searchParams } = new URL(request.url);
    const brand = searchParams.get("brand");
    const modelNo = searchParams.get("modelNo");
    const colorCode = searchParams.get("colorCode") || "";
    const frameSize = searchParams.get("frameSize") || "";

    if (!brand || !modelNo) {
      return NextResponse.json(
        { success: false, error: "brand and modelNo are required" },
        { status: 400 }
      );
    }

    // Stage 1: cheap exact case-insensitive match (hits most cases).
    let matchMode: "exact" | "normalized" | "none" = "none";
    let existing = await prisma.product.findMany({
      where: {
        brand: { equals: brand, mode: "insensitive" },
        modelNo: { equals: modelNo, mode: "insensitive" },
      },
      include: {
        variants: {
          include: {
            images: { orderBy: { position: "asc" }, take: 1 },
            locations: { include: { location: true } },
          },
          orderBy: [{ colorCode: "asc" }, { frameSize: "asc" }],
        },
        images: { orderBy: { position: "asc" }, take: 1 },
      },
    });
    if (existing.length > 0) matchMode = "exact";

    // Stage 2: normalized CONTAINS match. Many products in this catalog
    // have modelNo = full product title (pull fallback when the Shopify
    // model_no metafield is empty), so a strict equality check fails even
    // when the user's short model number ("RB3025") appears inside the
    // stored value ("Ray Ban RB 3025 002/32 58 Black Sunglass Eyewear").
    //
    // Strategy: normalize both sides (strip non-alphanumerics, lowercase),
    // then require the stored brand normalized to equal the query brand
    // normalized, AND the stored modelNo OR title normalized to CONTAIN
    // the query modelNo normalized.
    if (existing.length === 0) {
      const normBrand = normalize(brand);
      const normModel = normalize(modelNo);
      if (normBrand && normModel) {
        const brandHead = normBrand.slice(0, Math.min(3, normBrand.length));
        const candidates = await prisma.product.findMany({
          where: {
            brand: { contains: brandHead, mode: "insensitive" },
          },
          include: {
            variants: {
              include: {
                images: { orderBy: { position: "asc" }, take: 1 },
                locations: { include: { location: true } },
              },
              orderBy: [{ colorCode: "asc" }, { frameSize: "asc" }],
            },
            images: { orderBy: { position: "asc" }, take: 1 },
          },
          take: 500,
        });
        existing = candidates.filter((p) => {
          if (normalize(p.brand || "") !== normBrand) return false;
          const m = normalize(p.modelNo || "");
          const t = normalize(p.title || "");
          return m.includes(normModel) || t.includes(normModel);
        });
        if (existing.length > 0) matchMode = "normalized";
      }
    }

    // Optional 4-field exact-variant match. We only return a hit when
    // both colorCode AND frameSize are supplied — partial input would
    // be ambiguous (e.g., colour matches but size is wrong).
    let exactVariant: {
      id: string;
      productId: string;
      colorCode: string;
      frameSize: string | null;
      sku: string | null;
      barcode: string | null;
    } | null = null;

    if (existing.length > 0 && (colorCode || frameSize)) {
      const normColor = normalize(colorCode);
      const normSize = normalize(frameSize);
      // We require both to be present for a true variant-level hit.
      // (Just colour or just size still leaves us at parent-level.)
      if (normColor && normSize) {
        outer: for (const p of existing) {
          for (const v of p.variants) {
            if (
              normalize(v.colorCode || "") === normColor &&
              normalize(v.frameSize || "") === normSize
            ) {
              exactVariant = {
                id: v.id,
                productId: p.id,
                colorCode: v.colorCode,
                frameSize: v.frameSize,
                sku: v.sku,
                barcode: v.barcode,
              };
              break outer;
            }
          }
        }
      }
    }

    return NextResponse.json({
      success: true,
      found: existing.length > 0,
      matchMode,
      exactVariant,
      products: existing.map((p) => ({
        id: p.id,
        title: p.title,
        brand: p.brand,
        subBrand: p.subBrand,
        modelNo: p.modelNo,
        category: p.category,
        status: p.status,
        mrp: p.mrp,
        sku: p.sku,
        image: p.images[0]?.url || null,
        variants: p.variants.map((v) => ({
          id: v.id,
          colorCode: v.colorCode,
          colorName: v.colorName,
          frameColor: v.frameColor,
          frameSize: v.frameSize,
          sku: v.sku,
          barcode: v.barcode,
          mrp: v.mrp,
          discountedPrice: v.discountedPrice,
          image: v.images[0]?.url || null,
          locations: v.locations.map((vl) => ({
            id: vl.id,
            locationId: vl.locationId,
            locationName: vl.location.name,
            quantity: vl.quantity,
          })),
          totalStock: v.locations.reduce((s, vl) => s + vl.quantity, 0),
        })),
        variantCount: p.variants.length,
      })),
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
