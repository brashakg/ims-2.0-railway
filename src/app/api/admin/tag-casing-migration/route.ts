// POST /api/admin/tag-casing-migration
//
// Round 2 mapping C6 ("Both — write a migration"). Two-phase endpoint:
//   { dryRun: true }  (default) → returns the plan, doesn't touch Shopify
//   { commit: true }            → re-builds the plan AND applies it
//
// Always re-builds the plan immediately before committing so we don't
// race against admin-side edits in Shopify between plan + commit.
//
// Admin-only. Logs to ActivityLog so we have an audit trail of when the
// migration ran and by whom.

import { NextRequest, NextResponse } from "next/server";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";
import {
  buildMigrationPlan,
  commitMigration,
} from "@/lib/collections/tagCasingMigration";

export async function POST(request: NextRequest) {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const body = (await request.json().catch(() => ({}))) as {
      commit?: boolean;
      dryRun?: boolean;
    };
    const commit = body.commit === true;

    const plan = await buildMigrationPlan();

    if (!commit) {
      return NextResponse.json({
        success: true,
        dryRun: true,
        ...plan,
      });
    }

    // Apply.
    const result = await commitMigration(plan);

    logActivity({
      userId: (auth.session?.user as { id?: string } | undefined)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "TAG_CASING_MIGRATION",
      entity: "COLLECTION",
      details:
        `Tag-casing migration committed: ${result.applied}/${result.collectionsWithChanges} ` +
        `collections updated, ${result.totalRuleChanges} rule conditions migrated, ` +
        `${result.failed.length} failures.`,
    });

    return NextResponse.json({
      success: result.failed.length === 0,
      dryRun: false,
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
