// POST /api/admin/menu-cleanup
//
// Round 2 mapping CL1-CL7. Runs the menu cleanup tasks against live
// Shopify menus. Body:
//   { dryRun: true } (default) → returns the plan + applied/skipped/failed
//                                  status per op without pushing
//   { commit: true }            → resolves collection refs, applies ops,
//                                  pushes affected menus, deletes
//                                  inactive ones (CL7).
//
// Admin-only. Logs to ActivityLog.

import { NextRequest, NextResponse } from "next/server";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";
import {
  executeCleanup,
  resolveCollectionRefs,
} from "@/lib/menus/cleanupExecutor";
import { MENU_CLEANUP_TASKS } from "@/lib/menus/cleanupTasks";

export async function POST(request: NextRequest) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const body = (await request.json().catch(() => ({}))) as {
      commit?: boolean;
    };
    const dryRun = body.commit !== true;

    // Resolve CL3 + CL5 collection GIDs (creates 'cases-bags' if missing).
    const tasks = await resolveCollectionRefs(MENU_CLEANUP_TASKS);

    const result = await executeCleanup({ dryRun });

    if (!dryRun) {
      const opsApplied = result.tasks.flatMap((t) => t.ops).filter((o) => o.status === "applied").length;
      const opsFailed = result.tasks.flatMap((t) => t.ops).filter((o) => o.status === "failed").length;

      logActivity({
        userId: (auth.session?.user as { id?: string } | undefined)?.id,
        userName: auth.session?.user?.name,
        userEmail: auth.session?.user?.email,
        action: "MENU_CLEANUP",
        entity: "MENU",
        details:
          `Menu cleanup committed: ${opsApplied} ops applied, ${opsFailed} failed. ` +
          `Pushed ${result.pushedMenus.length} menu(s), deleted ${result.deletedMenus.length}.`,
      });
    }

    void tasks; // tasks list not surfaced in response; the result.tasks already carries op status

    return NextResponse.json({
      success: true,
      ...result,
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
