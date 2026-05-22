// POST /api/shopify/sync-history
//
// Clears stale SyncLog rows so the Shopify Sync header doesn't keep
// showing yesterday's failure batch as today's "99 Failed".
//
// Body shape:
//   { mode: "failed-older-than", days?: number }
//      Deletes FAILED rows older than `days` (default 1).
//   { mode: "all-older-than", days?: number }
//      Deletes EVERY SyncLog row older than `days` (default 30).
//   { mode: "all-failed" }
//      Nukes every FAILED row regardless of age. Aggressive — use only
//      when you've confirmed the failures are stale (post-hotfix).
//
// Admin-only. Logs to ActivityLog so the action is auditable.

import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";

interface ClearBody {
  mode?: "failed-older-than" | "all-older-than" | "all-failed";
  days?: number;
}

export async function POST(request: NextRequest) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const body = (await request.json().catch(() => ({}))) as ClearBody;
    const mode = body.mode || "failed-older-than";
    const days = typeof body.days === "number" && body.days > 0 ? body.days : (mode === "all-older-than" ? 30 : 1);
    const cutoff = new Date(Date.now() - days * 24 * 60 * 60 * 1000);

    let where: Record<string, unknown>;
    let label: string;
    switch (mode) {
      case "failed-older-than":
        where = { status: "FAILED", createdAt: { lt: cutoff } };
        label = `failed rows older than ${days}d`;
        break;
      case "all-older-than":
        where = { createdAt: { lt: cutoff } };
        label = `all rows older than ${days}d`;
        break;
      case "all-failed":
        where = { status: "FAILED" };
        label = `all failed rows (no age filter)`;
        break;
      default:
        return NextResponse.json(
          { success: false, error: `Unknown mode: ${mode}` },
          { status: 400 }
        );
    }

    const before = await prisma.syncLog.count({ where });
    const result = await prisma.syncLog.deleteMany({ where });

    logActivity({
      userId: (auth.session?.user as { id?: string } | undefined)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "SYNC_HISTORY_CLEARED",
      entity: "SYNC_LOG",
      details: `Cleared ${result.count} SyncLog rows (${label}).`,
    });

    return NextResponse.json({
      success: true,
      mode,
      days: mode !== "all-failed" ? days : null,
      matched: before,
      deleted: result.count,
      message: `Deleted ${result.count} ${label}.`,
    });
  } catch (e) {
    return NextResponse.json(
      {
        success: false,
        error: e instanceof Error ? e.message : "Unknown error",
      },
      { status: 500 }
    );
  }
}
