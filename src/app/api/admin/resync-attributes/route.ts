import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";

// POST /api/admin/resync-attributes
// Wipes AttributeType.options and repopulates them from the real
// distinct values present in the Product + ProductVariant tables.
// Call once after a seed + Shopify pull. Admin only.
//
// The attribute types themselves (rows in AttributeType) are created if
// missing, keyed by the attribute name below. Existing AttributeType
// rows keep their id, label, sortOrder — only their child options are
// replaced.
export async function POST() {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    // Attribute name -> where its values live (table + column).
    // Names match what getAttributeOptions() looks up from the frontend.
    const productColumns: Array<{
      name: string;
      label: string;
      column: keyof PrismaProductSelect;
    }> = [
      { name: "brand",            label: "Brand",              column: "brand" },
      { name: "subbrand",         label: "Sub Brand",          column: "subBrand" },
      { name: "shape",            label: "Shape",              column: "shape" },
      { name: "framematerial",    label: "Frame Material",     column: "frameMaterial" },
      { name: "templematerial",   label: "Temple Material",    column: "templeMaterial" },
      { name: "frametype",        label: "Frame Type",         column: "frameType" },
      { name: "gender",           label: "Gender",             column: "gender" },
      { name: "countryoforigin",  label: "Country Of Origin",  column: "countryOfOrigin" },
      { name: "warranty",         label: "Warranty",           column: "warranty" },
      { name: "lensmaterial",     label: "Lens Material",      column: "lensMaterial" },
      { name: "lensUSP",          label: "Lens USP",           column: "lensUSP" },
      { name: "polarization",     label: "Polarization",       column: "polarization" },
      { name: "uvprotection",     label: "UV Protection",      column: "uvProtection" },
      { name: "productusp",       label: "Product USP",        column: "productUSP" },
    ];

    const variantColumns: Array<{
      name: string;
      label: string;
      column: keyof PrismaVariantSelect;
    }> = [
      { name: "framecolor",  label: "Frame Color",  column: "frameColor" },
      { name: "templecolor", label: "Temple Color", column: "templeColor" },
      { name: "lenscolour",  label: "Lens Colour",  column: "lensColour" },
      { name: "tint",        label: "Tint",         column: "tint" },
    ];

    let attrTypesTouched = 0;
    let optionsCreated = 0;
    let optionsRemoved = 0;

    async function repopulate(
      name: string,
      label: string,
      values: string[]
    ) {
      const clean = Array.from(
        new Set(
          values
            .map((v) => (v || "").trim())
            .filter((v) => v.length > 0)
        )
      ).sort((a, b) => a.localeCompare(b));

      const type = await prisma.attributeType.upsert({
        where: { name },
        update: { label },
        create: { name, label },
      });

      const removed = await prisma.attributeOption.deleteMany({
        where: { attributeTypeId: type.id },
      });
      optionsRemoved += removed.count;

      if (clean.length === 0) return;

      await prisma.attributeOption.createMany({
        data: clean.map((value) => ({
          value,
          attributeTypeId: type.id,
        })),
      });
      optionsCreated += clean.length;
      attrTypesTouched++;
    }

    for (const col of productColumns) {
      const rows = await prisma.product.findMany({
        distinct: [col.column as never],
        select: { [col.column]: true } as never,
      });
      const values = rows
        .map((r: Record<string, unknown>) => r[col.column as string])
        .filter((v): v is string => typeof v === "string");
      await repopulate(col.name, col.label, values);
    }

    for (const col of variantColumns) {
      const rows = await prisma.productVariant.findMany({
        distinct: [col.column as never],
        select: { [col.column]: true } as never,
      });
      const values = rows
        .map((r: Record<string, unknown>) => r[col.column as string])
        .filter((v): v is string => typeof v === "string");
      await repopulate(col.name, col.label, values);
    }

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "ATTRIBUTES_RESYNC",
      entity: "ATTRIBUTE_TYPE",
      details: `Resynced ${attrTypesTouched} attribute type(s) from real product data. Removed ${optionsRemoved} old option(s); created ${optionsCreated} from DB.`,
    });

    return NextResponse.json({
      success: true,
      summary: {
        attrTypesTouched,
        optionsRemoved,
        optionsCreated,
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

// Minimal helper types to satisfy TS on dynamic key indexing. We don't
// need full Prisma types here — the findMany calls above pass the
// column name as a string, and we read back the row as a record.
type PrismaProductSelect = {
  brand: boolean;
  subBrand: boolean;
  shape: boolean;
  frameMaterial: boolean;
  templeMaterial: boolean;
  frameType: boolean;
  gender: boolean;
  countryOfOrigin: boolean;
  warranty: boolean;
  lensMaterial: boolean;
  lensUSP: boolean;
  polarization: boolean;
  uvProtection: boolean;
  productUSP: boolean;
};

type PrismaVariantSelect = {
  frameColor: boolean;
  templeColor: boolean;
  lensColour: boolean;
  tint: boolean;
};
