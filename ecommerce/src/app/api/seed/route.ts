import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { hash } from "bcryptjs";
import crypto from "crypto";

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
// SECURITY: no hardcoded password default. The seed admin password MUST be
// supplied via SEED_ADMIN_PASSWORD — if it is unset we refuse to create the
// account rather than minting a well-known "admin123" login on a public route.
const SEED_ADMIN = {
  email: "admin@bettervision.in",
  password: process.env.SEED_ADMIN_PASSWORD || "",
  name: "Admin User",
};

// Constant-time secret comparison so the gate can't be probed by timing.
function secretMatches(provided: string, expected: string): boolean {
  const a = Buffer.from(provided);
  const b = Buffer.from(expected);
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

export async function GET(request: NextRequest) {
  try {
    // SECURITY GATE: this route provisions the initial ADMIN account and is
    // reachable unauthenticated, so it must be locked behind a deploy-only
    // secret. Require SEED_SECRET to be configured AND presented (via the
    // x-seed-secret header or ?secret= query param). No secret -> 401, never
    // a silent open door.
    const expectedSecret = process.env.SEED_SECRET || "";
    if (!expectedSecret) {
      return NextResponse.json(
        {
          success: false,
          message:
            "Seeding is disabled: SEED_SECRET is not configured on the server.",
        },
        { status: 401 }
      );
    }
    const providedSecret =
      request.headers.get("x-seed-secret") ||
      request.nextUrl.searchParams.get("secret") ||
      "";
    if (!secretMatches(providedSecret, expectedSecret)) {
      return NextResponse.json(
        { success: false, message: "Invalid or missing seed secret." },
        { status: 401 }
      );
    }

    if (!SEED_ADMIN.password) {
      return NextResponse.json(
        {
          success: false,
          message:
            "Cannot seed admin: SEED_ADMIN_PASSWORD is not set. Refusing to create a default-password account.",
        },
        { status: 400 }
      );
    }

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
