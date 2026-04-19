import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";

// POST /api/admin/migrate-legacy-variant-fields
// One-shot backfill: for every Product that has no ProductVariant rows yet
// but has legacy variant-level data (colorCode, frameSize, frameColor, etc.)
// on the Product row itself, create a single default ProductVariant from
// those fields so the data survives when the legacy columns are dropped.
//
// Products that already have variants AND have populated legacy fields are
// flagged via SyncLog (action=MIGRATION, status=SKIPPED) for manual review.
//
// Idempotent: running a second time is a no-op because every migrated
// Product now has a variant and no longer matches the `variants: none` filter.
export async function POST() {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const legacyFilterOR = [
      { colorCode: { not: null } },
      { frameColor: { not: null } },
      { frameSize: { not: null } },
      { templeColor: { not: null } },
      { bridge: { not: null } },
      { templeLength: { not: null } },
      { weight: { not: null } },
      { lensColour: { not: null } },
      { tint: { not: null } },
    ];

    const candidates = await prisma.product.findMany({
      where: {
        variants: { none: {} },
        OR: legacyFilterOR,
      },
      select: {
        id: true,
        sku: true,
        mrp: true,
        discountedPrice: true,
        compareAtPrice: true,
        colorCode: true,
        frameColor: true,
        templeColor: true,
        frameSize: true,
        bridge: true,
        templeLength: true,
        weight: true,
        lensColour: true,
        tint: true,
      },
    });

    let created = 0;
    let failed = 0;
    const errors: string[] = [];

    for (const p of candidates) {
      try {
        await prisma.productVariant.create({
          data: {
            productId: p.id,
            colorCode: p.colorCode || "DEFAULT",
            frameSize: p.frameSize || null,
            frameColor: p.frameColor || null,
            templeColor: p.templeColor || null,
            bridge: p.bridge || null,
            templeLength: p.templeLength || null,
            weight: p.weight || null,
            lensColour: p.lensColour || null,
            tint: p.tint || null,
            mrp: p.mrp || 0,
            discountedPrice: p.discountedPrice || p.mrp || 0,
            compareAtPrice: p.compareAtPrice || p.mrp || 0,
            sku: p.sku ? `${p.sku}-DEFAULT` : null,
            title:
              [p.colorCode, p.frameSize].filter(Boolean).join(" / ") ||
              "Default",
          },
        });
        created++;
      } catch (e) {
        failed++;
        errors.push(`${p.id}: ${e instanceof Error ? e.message : "Unknown"}`);
      }
    }

    // Flag conflicts: Products with variants AND legacy data populated.
    const conflicts = await prisma.product.findMany({
      where: {
        variants: { some: {} },
        OR: legacyFilterOR,
      },
      select: { id: true },
    });
    let conflictsLogged = 0;
    for (const c of conflicts) {
      try {
        await prisma.syncLog.create({
          data: {
            productId: c.id,
            action: "MIGRATION",
            status: "SKIPPED",
            message:
              "Product has both variants and legacy data; reconcile manually before dropping legacy columns.",
          },
        });
        conflictsLogged++;
      } catch {
        // ignore
      }
    }

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "LEGACY_FIELD_MIGRATION",
      entity: "PRODUCT",
      details: `Created ${created} default variant(s) from legacy Product fields; flagged ${conflictsLogged} conflict(s) to SyncLog; ${failed} failure(s).`,
    });

    return NextResponse.json({
      success: true,
      summary: {
        candidates: candidates.length,
        variantsCreated: created,
        failures: failed,
        conflictsFlagged: conflictsLogged,
      },
      errors: errors.slice(0, 10),
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
