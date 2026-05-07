import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";

export async function GET() {
  try {
    // Audit fix: was unauthenticated. Now requires a valid session — the
    // values aren't sensitive but a logged-out hit should not 200.
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;
    const attributeTypes = await prisma.attributeType.findMany({
      include: {
        options: true,
      },
      orderBy: {
        name: "asc",
      },
    });

    return NextResponse.json(attributeTypes);
  } catch (error) {
    console.error("Error fetching attributes:", error);
    return NextResponse.json(
      {
        success: false,
        message: "Error fetching attributes",
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

    const { name, label, value, attributeTypeId, options } = await request.json();

    // If attributeTypeId is provided, add a single option to existing type
    if (attributeTypeId && value) {
      const option = await prisma.attributeOption.create({
        data: { attributeTypeId, value },
      });
      return NextResponse.json(option, { status: 201 });
    }

    if (!name) {
      return NextResponse.json(
        { success: false, message: "Name is required" },
        { status: 400 }
      );
    }

    const attributeType = await prisma.attributeType.create({
      data: {
        name,
        label: label || name,
        options: options ? {
          create: (options as string[]).map((v: string) => ({ value: v })),
        } : undefined,
      },
      include: {
        options: true,
      },
    });

    return NextResponse.json(attributeType, { status: 201 });
  } catch (error) {
    console.error("Error creating attribute:", error);
    return NextResponse.json(
      {
        success: false,
        message: "Error creating attribute",
      },
      { status: 500 }
    );
  }
}
