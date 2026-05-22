import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";

export async function GET() {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    // Check if Shopify credentials are configured (OAuth or legacy)
    const storeUrl = process.env.SHOPIFY_STORE_URL;
    const clientId = process.env.SHOPIFY_CLIENT_ID;
    const clientSecret = process.env.SHOPIFY_CLIENT_SECRET;
    const accessToken = process.env.SHOPIFY_ACCESS_TOKEN;

    const isConfigured = !!storeUrl && ((!!clientId && !!clientSecret) || !!accessToken);

    // Sync statistics — rewritten 2026-05-09 to show meaningful state.
    //
    // Previous implementation queried the LAST 100 SyncLog rows and
    // counted SUCCESS / FAILED ratios. With one bad batch from days ago
    // (yesterday's pre-hotfix push: 99 FAILED + 1 SUCCESS) the header
    // permanently showed "1 Synced / 99 Failed" until 100 new logs
    // pushed the old ones out. Misleading — the actual store state is
    // 4,392 products synced healthily.
    //
    // New shape:
    //   totalSynced       — real count of products with shopifyProductId set
    //                        (matches "Synced with Shopify" card below)
    //   recentFailures    — FAILED SyncLog rows in the last 24 hours only
    //   lastSuccessAt     — most recent SUCCESS sync timestamp
    //   lastAttemptAt     — most recent ANY-STATUS sync timestamp
    //   recentBatch       — counts from the most recent push batch (the
    //                        50 rows around lastAttemptAt) so the user
    //                        can see how the last sync actually went
    const [
      totalSynced,
      lastSuccessLog,
      lastAttemptLog,
      recent24h,
    ] = await Promise.all([
      prisma.product.count({
        where: { shopifyProductId: { not: null } },
      }),
      prisma.syncLog.findFirst({
        where: { status: "SUCCESS" },
        orderBy: { createdAt: "desc" },
      }),
      prisma.syncLog.findFirst({
        orderBy: { createdAt: "desc" },
      }),
      prisma.syncLog.findMany({
        where: {
          createdAt: { gte: new Date(Date.now() - 24 * 60 * 60 * 1000) },
        },
      }),
    ]);

    const recent24hSuccess = recent24h.filter((l) => l.status === "SUCCESS").length;
    const recent24hFailed = recent24h.filter((l) => l.status === "FAILED").length;

    // Last batch = SyncLog rows within ±5 min of the most recent attempt.
    let lastBatchSuccess = 0;
    let lastBatchFailed = 0;
    if (lastAttemptLog) {
      const at = lastAttemptLog.createdAt.getTime();
      const window = 5 * 60 * 1000;
      const batch = await prisma.syncLog.findMany({
        where: {
          createdAt: {
            gte: new Date(at - window),
            lte: new Date(at + window),
          },
        },
      });
      lastBatchSuccess = batch.filter((l) => l.status === "SUCCESS").length;
      lastBatchFailed = batch.filter((l) => l.status === "FAILED").length;
    }

    return NextResponse.json({
      configured: isConfigured,
      storeUrl: storeUrl ? storeUrl.replace(/[^/]/g, (c, i) => i > 8 ? "*" : c) : null,
      stats: {
        // Old shape kept so older clients don't break — but now sourced
        // from real product state, not the SyncLog window.
        totalSynced,
        // Failed count is now scoped to LAST 24 HOURS only. If the
        // dashboard shows "47" it's not cumulative-since-forever, it's
        // failures from today/yesterday.
        failedSyncs: recent24hFailed,
        lastSync: lastAttemptLog?.createdAt || null,
        // New fields the rewritten dashboard reads:
        lastSuccessAt: lastSuccessLog?.createdAt || null,
        lastAttemptAt: lastAttemptLog?.createdAt || null,
        recent24h: { success: recent24hSuccess, failed: recent24hFailed },
        lastBatch: { success: lastBatchSuccess, failed: lastBatchFailed },
      },
    });
  } catch (error) {
    console.error("Error checking Shopify status:", error);
    return NextResponse.json(
      {
        success: false,
        message: "Error checking Shopify status",
      },
      { status: 500 }
    );
  }
}
