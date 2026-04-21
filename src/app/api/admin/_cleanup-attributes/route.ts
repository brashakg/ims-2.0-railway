// ONE-SHOT: normalize typos and duplicate spellings in Product and
// ProductVariant string columns. Admin runs this once after resync-
// attributes; the endpoint is removed from the repo in a follow-up
// commit.
//
// Conservative: only maps obvious typos + casing/spacing duplicates.
// Leaves brand-specific or ambiguous values untouched (e.g.
// "Mauievalution" — probably a real Maui Jim trademark).
import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";

type Map = Record<string, string>;

const PRODUCT_COLUMN_MAPS: Record<string, Map> = {
  gender: {
    Male: "Men",
    Female: "Women",
  },
  countryOfOrigin: {
    Madeinchina: "Made in China",
    Madeinindia: "Made in India",
    Madeinitaly: "Made in Italy",
    Madeinjapan: "Made in Japan",
    Madeinthailand: "Made in Thailand",
    "Made In China": "Made in China",
    "Made In India": "Made in India",
    "Made In Italy": "Made in Italy",
  },
  frameMaterial: {
    Accetate: "Acetate",
    Acetae: "Acetate",
    Betatitanium: "Beta Titanium",
    Carbonfiber: "Carbon Fiber",
    Carbanfiber: "Carbon Fiber",
    Stainlesssteel: "Stainless Steel",
    "Stainless steel": "Stainless Steel",
    "Acetate Stainlesssteel": "Acetate + Stainless Steel",
    Acetatewithmetal: "Acetate + Metal",
    Memorymetal: "Memory Metal",
  },
  templeMaterial: {
    Accetate: "Acetate",
    Acetatewithmetal: "Acetate + Metal",
    Aluminum: "Aluminium",
    Betatitanium: "Beta Titanium",
    Carbanfiber: "Carbon Fiber",
    Carbonfiber: "Carbon Fiber",
  },
  frameType: {
    Fillframe: "Full Frame",
    Fullfarme: "Full Frame",
    Fullmetal: "Full Frame",
    Fullrim: "Full Frame",
    Halfframe: "Half Frame",
    Halfrim: "Half Frame",
    "Half Rim": "Half Frame",
    // These are materials incorrectly entered in the frameType column
    // — wipe them so the column only contains real frame types.
    Acetate: "",
    Acetatesupra: "",
    Metal: "",
  },
  shape: {
    Avaitor: "Aviator",
    Avitor: "Aviator",
    Cateeye: "Cateye",
    Cateyes: "Cateye",
    Catseye: "Cateye",
  },
  warranty: {
    "1 Years": "1 Year",
    "2 Year": "2 Years",
  },
  polarization: {
    Polarised: "Polarized",
    Nonpolarised: "Non Polarized",
    "Non-Polarized": "Non Polarized",
  },
  uvProtection: {
    "100 Uv Protection": "100% UV Protection",
  },
  lensMaterial: {
    "Cr 39": "CR 39",
    Poliammide: "Polyamide",
  },
};

const VARIANT_COLUMN_MAPS: Record<string, Map> = {
  // Variant-level columns currently have very few populated values —
  // leaving this minimal. Add entries here if future data needs it.
};

export async function POST() {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const results: Array<{
      table: "Product" | "ProductVariant";
      column: string;
      from: string;
      to: string;
      rowsAffected: number;
    }> = [];

    for (const [column, map] of Object.entries(PRODUCT_COLUMN_MAPS)) {
      for (const [from, to] of Object.entries(map)) {
        if (from === to) continue;
        const updated = await prisma.product.updateMany({
          where: { [column]: from } as never,
          data: { [column]: to || null } as never,
        });
        if (updated.count > 0) {
          results.push({
            table: "Product",
            column,
            from,
            to: to || "(cleared)",
            rowsAffected: updated.count,
          });
        }
      }
    }

    for (const [column, map] of Object.entries(VARIANT_COLUMN_MAPS)) {
      for (const [from, to] of Object.entries(map)) {
        if (from === to) continue;
        const updated = await prisma.productVariant.updateMany({
          where: { [column]: from } as never,
          data: { [column]: to || null } as never,
        });
        if (updated.count > 0) {
          results.push({
            table: "ProductVariant",
            column,
            from,
            to: to || "(cleared)",
            rowsAffected: updated.count,
          });
        }
      }
    }

    const totalUpdates = results.reduce((s, r) => s + r.rowsAffected, 0);

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "ATTRIBUTES_CLEANUP",
      entity: "PRODUCT",
      details: `One-shot cleanup: ${totalUpdates} row(s) updated across ${results.length} mapping(s).`,
    });

    return NextResponse.json({
      success: true,
      summary: {
        totalRowsUpdated: totalUpdates,
        mappingsApplied: results.length,
      },
      details: results,
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
