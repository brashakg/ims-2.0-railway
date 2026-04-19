import { NextResponse } from "next/server";
import { requireAuth } from "@/lib/apiAuth";
import { logActivity } from "@/lib/activityLog";
import { recomputeCustomerAggregates } from "@/lib/customerAggregates";

// POST /api/customers/recompute-totals
// Admin-only one-shot to rebuild Customer.ordersCount and Customer.totalSpent
// from the Order table. Useful after deploying changes that stop trusting
// Shopify's customer totals, or after any orders sync that you suspect has
// drifted from the aggregate columns.
export async function POST() {
  try {
    const auth = await requireAuth(["ADMIN"]);
    if (!auth.authorized) return auth.response!;

    const updated = await recomputeCustomerAggregates();

    logActivity({
      userId: (auth.session?.user as any)?.id,
      userName: auth.session?.user?.name,
      userEmail: auth.session?.user?.email,
      action: "CUSTOMER_RECOMPUTE_TOTALS",
      entity: "CUSTOMER",
      details: `Recomputed ordersCount and totalSpent for ${updated} customer(s)`,
    });

    return NextResponse.json({
      success: true,
      message: `Recomputed totals for ${updated} customer(s)`,
      data: { updated },
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
