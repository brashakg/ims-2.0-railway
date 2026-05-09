// POST /api/collections/auto-generate
//
// Generates the per-category smart-collection plan from
// PER_CATEGORY_PLAN + the AttributeOption table, then upserts the rows
// into the local Collection table. Pushing to Shopify is deferred to a
// follow-up endpoint — this one only writes locally.
//
// Body:
//   {
//     category?: string         // limit to one category, e.g. "SUNGLASSES"
//     brand?: string            // limit brand-dimension to a single brand
//     dryRun?: boolean          // when true, no DB writes — return plan
//   }
//
// Response:
//   {
//     success: true,
//     summary: { created, updated, skipped },
//     created: CollectionPlan[],
//     updated: CollectionPlan[],
//     skipped: CollectionPlan[],
//   }
//
// Idempotency: the upsert is keyed on Collection.autoSource. Re-running
// the endpoint never creates duplicates and only touches collections
// that didn't already exist with the same lineage key.
//
// Auth: ADMIN only.

import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import {
  generateCollectionsForCategory,
  generateCollectionsForAllCategories,
  type CollectionPlan,
} from "@/lib/collections/ruleGenerator";
import {
  PER_CATEGORY_PLAN,
  type PlanDimension,
} from "@/lib/collections/perCategoryPlan";

interface AutoGenerateBody {
  category?: string;
  brand?: string;
  dryRun?: boolean;
}

interface AutoGenerateSummary {
  created: number;
  updated: number;
  skipped: number;
  totalPlanned: number;
  dryRun: boolean;
}

interface AutoGenerateResponse {
  success: true;
  summary: AutoGenerateSummary;
  created: CollectionPlan[];
  updated: CollectionPlan[];
  skipped: CollectionPlan[];
}

/**
 * Look up the live attribute option values keyed by attribute-type name.
 * The AttributeType.name column historically uses lowercase keys
 * (e.g. "brand", "shape", "framematerial"), so we normalise the dim
 * key the same way.
 *
 * The result map is keyed by PlanDimension.key (e.g. "brand", "shape",
 * "frameMaterial") so callers can look up dim.key directly.
 */
async function loadAttributeValuesForCategory(
  category: string
): Promise<Record<string, string[]>> {
  const dims = PER_CATEGORY_PLAN[category] ?? [];
  if (dims.length === 0) return {};

  // Only dimensions that don't have fixedValues need DB lookups.
  const dynamicDims = dims.filter(
    (d: PlanDimension) =>
      !d.fixedValues &&
      d.key !== "bestSellers" &&
      d.key !== "sale" &&
      d.key !== "featured"
  );
  if (dynamicDims.length === 0) return {};

  // Map dim.key → attributeType name. Most match by lowercase, a few
  // need explicit overrides because the seeded AttributeType.name in
  // the DB diverged historically (e.g. "framematerial" not
  // "frameMaterial"). Mirrors AttributeMeta.attributeTypeName.
  const nameOverrides: Record<string, string> = {
    frameMaterial: "framematerial",
    contactLensMaterial: "contactlensmaterial",
    accessoryType: "accessorytype",
    blueLightProtection: "bluelightprotection",
    antiGlareCoating: "antiglarecoating",
    visionType: "visiontype",
    wearSchedule: "wearschedule",
    lensType: "lenstype",
    useCase: "usecase",
    colorFamily: "lenscolour",
  };
  const dimNames = dynamicDims.map((d) => ({
    dimKey: d.key,
    typeName: nameOverrides[d.key] ?? d.key.toLowerCase(),
  }));
  const typeNames = dimNames.map((d) => d.typeName);

  const types = await prisma.attributeType.findMany({
    where: { name: { in: typeNames } },
    include: { options: { orderBy: { value: "asc" } } },
  });

  const out: Record<string, string[]> = {};
  for (const dn of dimNames) {
    const t = types.find((x) => x.name === dn.typeName);
    if (!t) {
      out[dn.dimKey] = [];
      continue;
    }
    out[dn.dimKey] = t.options.map((o) => o.value);
  }
  return out;
}

/**
 * Filter brand values down to a single brand if `brand` is supplied
 * in the body. Useful when a merchant adds a new brand and only wants
 * collections for THAT brand to be auto-generated.
 */
function applyBrandFilter(
  values: Record<string, string[]>,
  brand: string | undefined
): Record<string, string[]> {
  if (!brand) return values;
  const trimmed = brand.trim();
  if (!trimmed) return values;
  return { ...values, brand: [trimmed] };
}

/**
 * Local-only collections need a non-null shopifyCollectionId because
 * that column is required + unique on the Collection model. We use a
 * deterministic prefix so a follow-up Shopify push can recognise these
 * rows and replace the placeholder with the real GID once Shopify
 * issues one.
 */
function placeholderShopifyId(handle: string): string {
  return `local:auto:${handle}`;
}

/**
 * Upsert a single CollectionPlan into the DB. Keyed on autoSource so
 * re-runs never duplicate. Returns the bucket the row landed in.
 *
 * Skipped is reserved for collections that exist with the same
 * autoSource but have been locallyModified by a merchant — we leave
 * those alone to preserve hand-edits.
 */
async function upsertCollectionPlan(
  plan: CollectionPlan
): Promise<"created" | "updated" | "skipped"> {
  const existing = await prisma.collection.findFirst({
    where: { autoSource: plan.autoSource },
  });

  const dataCommon = {
    title: plan.title,
    handle: plan.handle,
    collectionType: "SMART" as const,
    sortOrder: plan.sortOrder,
    templateSuffix: plan.themeSuffix,
    rules: JSON.stringify(plan.rules),
    disjunctive: plan.disjunctive,
    bannerImage: plan.bannerImage ?? null,
    shortDescription: plan.shortDescription ?? null,
    sortPriority: plan.sortPriority,
    autoSource: plan.autoSource,
    categoryAnchor: plan.categoryAnchor,
  };

  if (!existing) {
    await prisma.collection.create({
      data: {
        ...dataCommon,
        shopifyCollectionId: placeholderShopifyId(plan.handle),
        published: true,
        productsCount: 0,
        locallyModified: false,
      },
    });
    return "created";
  }

  if (existing.locallyModified) {
    // Don't overwrite a merchant's manual edits — but DO refresh the
    // bookkeeping fields (sortPriority, categoryAnchor, autoSource)
    // because those are app-managed.
    await prisma.collection.update({
      where: { id: existing.id },
      data: {
        sortPriority: plan.sortPriority,
        categoryAnchor: plan.categoryAnchor,
        autoSource: plan.autoSource,
      },
    });
    return "skipped";
  }

  await prisma.collection.update({
    where: { id: existing.id },
    data: dataCommon,
  });
  return "updated";
}

export async function POST(request: NextRequest) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    let body: AutoGenerateBody = {};
    try {
      body = (await request.json()) as AutoGenerateBody;
    } catch {
      // Empty body / invalid JSON is fine — defaults to generate-everything.
      body = {};
    }

    const { category, brand, dryRun = false } = body;

    // Build the plan list.
    let plans: CollectionPlan[] = [];
    if (category) {
      const trimmed = category.trim();
      if (!PER_CATEGORY_PLAN[trimmed]) {
        return NextResponse.json(
          {
            success: false,
            error: `Unknown category "${trimmed}". Expected one of: ${Object.keys(
              PER_CATEGORY_PLAN
            ).join(", ")}.`,
          },
          { status: 400 }
        );
      }
      const attributeValues = applyBrandFilter(
        await loadAttributeValuesForCategory(trimmed),
        brand
      );
      plans = generateCollectionsForCategory(trimmed, attributeValues);
    } else {
      // All categories — load attribute values per category.
      const all: Record<string, Record<string, string[]>> = {};
      for (const cat of Object.keys(PER_CATEGORY_PLAN)) {
        all[cat] = applyBrandFilter(
          await loadAttributeValuesForCategory(cat),
          brand
        );
      }
      plans = generateCollectionsForAllCategories(all);
    }

    // Dry run — short-circuit before any DB writes. Bucket everything
    // into "created" so the merchant sees what would be made.
    if (dryRun) {
      const summary: AutoGenerateSummary = {
        created: plans.length,
        updated: 0,
        skipped: 0,
        totalPlanned: plans.length,
        dryRun: true,
      };
      const resp: AutoGenerateResponse = {
        success: true,
        summary,
        created: plans,
        updated: [],
        skipped: [],
      };
      return NextResponse.json(resp);
    }

    const created: CollectionPlan[] = [];
    const updated: CollectionPlan[] = [];
    const skipped: CollectionPlan[] = [];

    for (const plan of plans) {
      const bucket = await upsertCollectionPlan(plan);
      if (bucket === "created") created.push(plan);
      else if (bucket === "updated") updated.push(plan);
      else skipped.push(plan);
    }

    const summary: AutoGenerateSummary = {
      created: created.length,
      updated: updated.length,
      skipped: skipped.length,
      totalPlanned: plans.length,
      dryRun: false,
    };

    const resp: AutoGenerateResponse = {
      success: true,
      summary,
      created,
      updated,
      skipped,
    };
    return NextResponse.json(resp);
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
