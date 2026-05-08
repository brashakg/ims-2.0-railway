import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/prisma";
import { hash } from "bcryptjs";
import { serializeFeatures, type FeatureKey } from "@/lib/features";

const USER_SELECT = {
  id: true,
  email: true,
  name: true,
  role: true,
  enabledFeatures: true,
  locationId: true,
  location: { select: { id: true, name: true, code: true } },
  createdAt: true,
  updatedAt: true,
} as const;

/** PATCH /api/users/[id] — update name, role, location, enabledFeatures, or
 *  password. Admin-only. Self-edit is allowed for name/password only — to
 *  prevent a non-admin from escalating their own role/features. */
export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const session = await getServerSession(authOptions);
    if (!session?.user) {
      return NextResponse.json(
        { success: false, message: "Unauthorized" },
        { status: 401 }
      );
    }

    const { id } = await params;
    const isAdmin = (session.user as any).role === "ADMIN";
    const isSelf = (session.user as any).id === id;

    if (!isAdmin && !isSelf) {
      return NextResponse.json(
        { success: false, message: "Insufficient permissions" },
        { status: 403 }
      );
    }

    const body = await request.json().catch(() => ({}));
    const data: Record<string, unknown> = {};

    if (typeof body.name === "string") data.name = body.name;
    if (typeof body.password === "string" && body.password.length > 0) {
      data.password = await hash(body.password, 12);
    }

    // Role / location / features can ONLY be changed by an admin. A user
    // editing their own profile cannot grant themselves more privileges.
    if (isAdmin) {
      if (typeof body.role === "string") data.role = body.role;
      if (body.locationId !== undefined) {
        data.locationId = body.locationId || null;
      }
      if (body.enabledFeatures !== undefined) {
        if (body.enabledFeatures === null) {
          // Explicit null → revert to role defaults.
          data.enabledFeatures = null;
        } else {
          const arr: FeatureKey[] = Array.isArray(body.enabledFeatures)
            ? body.enabledFeatures
            : typeof body.enabledFeatures === "string"
              ? (body.enabledFeatures
                  .split(",")
                  .map((s: string) => s.trim())
                  .filter(Boolean) as FeatureKey[])
              : [];
          data.enabledFeatures = serializeFeatures(arr);
        }
      }
    }

    if (Object.keys(data).length === 0) {
      return NextResponse.json(
        { success: false, message: "No updatable fields provided" },
        { status: 400 }
      );
    }

    const updated = await prisma.user.update({
      where: { id: id },
      data,
      select: USER_SELECT,
    });

    return NextResponse.json(updated);
  } catch (error) {
    if ((error as any)?.code === "P2025") {
      return NextResponse.json(
        { success: false, message: "User not found" },
        { status: 404 }
      );
    }
    console.error("Error updating user:", error);
    return NextResponse.json(
      { success: false, message: "Error updating user" },
      { status: 500 }
    );
  }
}

/** DELETE /api/users/[id] — admin only. Cannot delete self (would lock
 *  out the only admin if they fat-finger the wrong row). */
export async function DELETE(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const session = await getServerSession(authOptions);
    if (!session?.user || (session.user as any).role !== "ADMIN") {
      return NextResponse.json(
        { success: false, message: "Unauthorized" },
        { status: 403 }
      );
    }
    const { id } = await params;
    if ((session.user as any).id === id) {
      return NextResponse.json(
        { success: false, message: "Cannot delete your own account" },
        { status: 400 }
      );
    }

    await prisma.user.delete({ where: { id: id } });
    return NextResponse.json({ success: true });
  } catch (error) {
    if ((error as any)?.code === "P2025") {
      return NextResponse.json(
        { success: false, message: "User not found" },
        { status: 404 }
      );
    }
    console.error("Error deleting user:", error);
    return NextResponse.json(
      { success: false, message: "Error deleting user" },
      { status: 500 }
    );
  }
}
