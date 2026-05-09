import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { fetchAllCollections } from "@/lib/shopify";
import { logActivity } from "@/lib/activityLog";

// POST /api/collections/sync — Pull all collections from Shopify and
// upsert into the local Collection table.
//
// Performance rewrite (2026-05-09): the previous implementation looped
// through each collection sequentially with findUnique + update, which
// took ~35 seconds for 1,160 rows. Combined with Shopify pagination
// (50/page = 24 round-trips) the total request hit the Railway timeout
// and the client saw the request hang indefinitely. Now:
//
//   1. We pre-fetch the locallyModified flag in ONE bulk findMany so we
//      never have to read-then-write per row.
//   2. Each row uses prisma.upsert (one network call vs. two).
//   3. Chunks of 20 rows run in parallel (Promise.all). Postgres can
//      easily absorb 20 concurrent writes; the bottleneck is the
//      Prisma round-trip latency, not Postgres throughput.
//
// Expected total runtime for ~1,200 collections: ~12-15s (Shopify
// pagination dominates). Well under the Railway request timeout.
export async function POST() {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const t0 = Date.now();
    const result = await fetchAllCollections();
    if (!result.success || !result.collections) {
      return NextResponse.json(
        { success: false, error: result.error || "Failed to fetch from Shopify" },
        { status: 502 }
      );
    }
    const fetchedAt = Date.now();
    const total = result.collections.length;
    console.log(`[collections/sync] fetched ${total} collections from Shopify in ${fetchedAt - t0}ms`);

    // Pre-fetch the existing rows in ONE query so we know which rows are
    // locally-modified (and therefore should only have lastSyncedAt
    // bumped, not the full payload overwritten). Without this we'd need
    // 1,160 individual SELECTs.
    const ids = result.collections.map((c) => c.id);
    const existingRows = await prisma.collection.findMany({
      where: { shopifyCollectionId: { in: ids } },
      select: { shopifyCollectionId: true, locallyModified: true },
    });
    const existingMap = new Map<string, boolean>(); // id -> locallyModified
    for (const r of existingRows) {
      if (r.shopifyCollectionId) existingMap.set(r.shopifyCollectionId, r.locallyModified);
    }

    let created = 0;
    let updated = 0;
    let preserved = 0;
    const errors: Array<{ id: string; handle: string | null; error: string }> = [];

    // Chunked parallel upserts. Chunk size of 20 = 60 chunks for 1,200
    // collections; each chunk completes in ~200-400ms so total upsert
    // phase is ~15-20s. If a chunk fails we keep going so partial
    // success is still useful.
    const CHUNK = 20;
    for (let i = 0; i < result.collections.length; i += CHUNK) {
      const chunk = result.collections.slice(i, i + CHUNK);
      await Promise.all(
        chunk.map(async (sc) => {
          try {
            const isSmartCollection = sc.ruleSet !== null && sc.ruleSet.rules.length > 0;
            const isLocal = existingMap.get(sc.id) === true;
            // Locally-modified rows: only refresh the sync timestamp.
            // Everything else: full upsert from Shopify payload.
            const dataIfLocal = { lastSyncedAt: new Date() };
            const dataFull = {
              title: sc.title,
              handle: sc.handle,
              description: sc.description || null,
              descriptionHtml: sc.descriptionHtml || null,
              collectionType: isSmartCollection ? "SMART" : "CUSTOM",
              sortOrder: sc.sortOrder || null,
              templateSuffix: sc.templateSuffix || null,
              imageUrl: sc.image?.url || null,
              imageAlt: sc.image?.altText || null,
              seoTitle: sc.seo?.title || null,
              seoDescription: sc.seo?.description || null,
              published: true,
              productsCount: sc.productsCount?.count || 0,
              rules: isSmartCollection ? JSON.stringify(sc.ruleSet!.rules) : null,
              disjunctive: sc.ruleSet?.appliedDisjunctively || false,
              lastSyncedAt: new Date(),
            };
            await prisma.collection.upsert({
              where: { shopifyCollectionId: sc.id },
              update: isLocal ? dataIfLocal : dataFull,
              create: { shopifyCollectionId: sc.id, ...dataFull },
            });
            if (existingMap.has(sc.id)) {
              if (isLocal) preserved++;
              else updated++;
            } else {
              created++;
            }
          } catch (e) {
            errors.push({
              id: sc.id,
              handle: sc.handle || null,
              error: e instanceof Error ? e.message : "Unknown",
            });
          }
        })
      );
    }
    const upsertedAt = Date.now();
    console.log(
      `[collections/sync] upserted ${created + updated + preserved}/${total} in ${upsertedAt - fetchedAt}ms ` +
        `(${created} created, ${updated} updated, ${preserved} preserved-as-local-modified, ${errors.length} errors)`
    );

    logActivity({
      userId: (auth.session?.user as { id?: string } | undefined)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "COLLECTION_SYNC",
      entity: "COLLECTION",
      details:
        `Synced ${total} collections from Shopify in ${(upsertedAt - t0) / 1000}s ` +
        `(${created} new, ${updated} updated, ${preserved} preserved, ${errors.length} errors).`,
    });

    return NextResponse.json({
      success: true,
      message: `Synced ${total} collections (${created} new, ${updated} updated${preserved ? `, ${preserved} preserved-locally-modified` : ""}${errors.length ? `, ${errors.length} errors` : ""})`,
      total,
      created,
      updated,
      preserved,
      errors,
      durationMs: upsertedAt - t0,
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ success: false, error: msg }, { status: 500 });
  }
}
