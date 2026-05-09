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
      dryRun?: boolean;
    };
    // Hotfix H1/H2 (security audit) — explicit dryRun:true wins over commit.
    const dryRun = body.dryRun === true || body.commit !== true;

    // Hotfix H2 — resolveCollectionRefs has a side-effect (creates the
    // 'cases-bags' collection on Shopify when missing). That MUST NOT
    // run during a dry-run. Skip the resolution pass when dryRun is on
    // so the response carries unresolved (null) resourceIds; the UI
    // shows them as such and the user explicitly commits to actually
    // create the collection + apply the cleanup.
    const tasks = dryRun
      ? MENU_CLEANUP_TASKS
      : await resolveCollectionRefs(MENU_CLEANUP_TASKS);

    const result = await executeCleanup({ dryRun, tasks });

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
