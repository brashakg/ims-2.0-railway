import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/prisma";
import { hash } from "bcryptjs";
import {
  serializeFeatures,
  type FeatureKey,
} from "@/lib/features";

export async function GET() {
  try {
    const session = await getServerSession(authOptions);

    if (!session?.user) {
      return NextResponse.json(
        {
          success: false,
          message: "Unauthorized",
        },
        { status: 401 }
      );
    }

    const users = await prisma.user.findMany({
      select: {
        id: true,
        email: true,
        name: true,
        role: true,
        enabledFeatures: true,
        locationId: true,
        location: {
          select: {
            id: true,
            name: true,
            code: true,
          },
        },
        createdAt: true,
        updatedAt: true,
      },
      orderBy: {
        createdAt: "desc",
      },
    });

    return NextResponse.json(users);
  } catch (error) {
    console.error("Error fetching users:", error);
    return NextResponse.json(
      {
        success: false,
        message: "Error fetching users",
      },
      { status: 500 }
    );
  }
}

export async function POST(request: NextRequest) {
  try {
    const session = await getServerSession(authOptions);

    if (!session?.user || session.user.role !== "ADMIN") {
      return NextResponse.json(
        {
          success: false,
          message: "Unauthorized",
        },
        { status: 403 }
      );
    }

    const {
      email,
      password,
      name,
      role,
      locationId,
      enabledFeatures,
    } = await request.json();

    if (!email || !password) {
      return NextResponse.json(
        {
          success: false,
          message: "Email and password are required",
        },
        { status: 400 }
      );
    }

    // Check if user already exists
    const existingUser = await prisma.user.findUnique({
      where: { email },
    });

    if (existingUser) {
      return NextResponse.json(
        {
          success: false,
          message: "User with this email already exists",
        },
        { status: 409 }
      );
    }

    // Hash password
    const hashedPassword = await hash(password, 12);

    // Normalize enabledFeatures: NULL → use role defaults; array → CSV;
    // string → trim + filter to known keys.
    let serializedFeatures: string | null = null;
    if (enabledFeatures !== undefined && enabledFeatures !== null) {
      const arr: FeatureKey[] = Array.isArray(enabledFeatures)
        ? enabledFeatures
        : typeof enabledFeatures === "string"
          ? (enabledFeatures
              .split(",")
              .map((s: string) => s.trim())
              .filter(Boolean) as FeatureKey[])
          : [];
      serializedFeatures = serializeFeatures(arr);
    }

    const user = await prisma.user.create({
      data: {
        email,
        password: hashedPassword,
        name,
        role: role || "CATALOG_MANAGER",
        locationId,
        enabledFeatures: serializedFeatures,
      },
      select: {
        id: true,
        email: true,
        name: true,
        role: true,
        enabledFeatures: true,
        locationId: true,
        location: {
          select: {
            id: true,
            name: true,
            code: true,
          },
        },
        createdAt: true,
        updatedAt: true,
      },
    });

    return NextResponse.json(user, { status: 201 });
  } catch (error) {
    console.error("Error creating user:", error);
    return NextResponse.json(
      {
        success: false,
        message: "Error creating user",
      },
      { status: 500 }
    );
  }
}
