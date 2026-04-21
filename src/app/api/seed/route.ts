import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { hash } from "bcryptjs";

// Only thing that gets seeded anymore is the initial admin user.
// Everything else (attributes, locations, discount rules) is populated
// either by Shopify sync or by the admin via the app UI — NO mock data.
//
// Why this file used to be bigger: previous versions seeded curated
// starter lists for brands, shapes, materials, colors, locations and
// discount rules. Those lists have since gone stale vs the real 4,391-
// product catalog (72 real brands, 51 real shape spellings including
// typos, actual store locations from Shopify, category-specific
// discount rules the admin configures in the UI). The starter lists
// were actively misleading the cataloging UI.
//
// If you need to repopulate AttributeType.options from the real data
// already in the DB, call POST /api/admin/resync-attributes.
const SEED_ADMIN = {
  email: "admin@bettervision.in",
  password: process.env.SEED_ADMIN_PASSWORD || "admin123",
  name: "Admin User",
};

export async function GET(_request: NextRequest) {
  try {
    const existingAdmin = await prisma.user.findUnique({
      where: { email: SEED_ADMIN.email },
    });

    if (existingAdmin) {
      return NextResponse.json({
        success: true,
        message: "Admin user already exists — nothing to seed.",
      });
    }

    const hashedPassword = await hash(SEED_ADMIN.password, 12);
    await prisma.user.create({
      data: {
        email: SEED_ADMIN.email,
        password: hashedPassword,
        name: SEED_ADMIN.name,
        role: "ADMIN",
      },
    });

    return NextResponse.json({
      success: true,
      message:
        "Admin user created. Locations will populate via POST /api/locations { action: 'sync_from_shopify' }. Attribute options populate via POST /api/admin/resync-attributes once you have products synced. Discount rules are added via /dashboard/admin/discount-rules.",
    });
  } catch (error) {
    return NextResponse.json(
      {
        success: false,
        message: "Error seeding database",
        error: error instanceof Error ? error.message : String(error),
      },
      { status: 500 }
    );
  }
}
