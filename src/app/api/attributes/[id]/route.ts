import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/prisma";

interface RouteParams {
  params: Promise<{ id: string }>;
}

export async function PUT(request: NextRequest, { params }: RouteParams) {
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
    const { id } = await params;

    const { value } = await request.json();

    if (!value) {
      return NextResponse.json(
        {
          success: false,
          message: "Value is required",
        },
        { status: 400 }
      );
    }

    const attributeOption = await prisma.attributeOption.update({
      where: { id },
      data: { value },
    });

    return NextResponse.json(attributeOption);
  } catch (error) {
    console.error("Error updating attribute option:", error);
    return NextResponse.json(
      {
        success: false,
        message: "Error updating attribute option",
      },
      { status: 500 }
    );
  }
}

export async function DELETE(request: NextRequest, { params }: RouteParams) {
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

    const { id } = await params;

    await prisma.attributeOption.delete({
      where: { id },
    });

    return NextResponse.json(
      {
        success: true,
        message: "Attribute option deleted successfully",
      },
      { status: 200 }
    );
  } catch (error) {
    console.error("Error deleting attribute option:", error);
    return NextResponse.json(
      {
        success: false,
        message: "Error deleting attribute option",
      },
      { status: 500 }
    );
  }
}
