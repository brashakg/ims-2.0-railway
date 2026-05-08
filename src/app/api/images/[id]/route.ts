import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";
import { unlink } from "fs/promises";
import { join } from "path";

interface RouteParams {
  params: Promise<{ id: string }>;
}

export async function DELETE(request: NextRequest, { params }: RouteParams) {
  try {
    const auth = await requireAuth(["ADMIN", "CATALOG_MANAGER"]);
    if (!auth.authorized) return auth.response!;
    const { id } = await params;

    const image = await prisma.productImage.findUnique({
      where: { id },
    });

    if (!image) {
      return NextResponse.json(
        { success: false, error: "Image not found" },
        { status: 404 }
      );
    }

    // Try to delete the file from disk
    if (image.url) {
      try {
        const filePath = join(process.cwd(), "public", image.url);
        await unlink(filePath);
      } catch {
        // File may not exist on disk — continue with DB deletion
      }
      // Also try deleting the original if different
      if (image.originalUrl && image.originalUrl !== image.url) {
        try {
          const originalPath = join(process.cwd(), "public", image.originalUrl);
          await unlink(originalPath);
        } catch {
          // Ignore
        }
      }
    }

    // Delete from database
    await prisma.productImage.delete({
      where: { id },
    });

    return NextResponse.json(
      { success: true, message: "Image deleted successfully" },
      { status: 200 }
    );
  } catch (error) {
    const errorMessage =
      error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json(
      { success: false, error: errorMessage },
      { status: 500 }
    );
  }
}
