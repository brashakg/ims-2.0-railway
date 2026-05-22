import { NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/apiAuth";

export async function GET() {
  try {
    const auth = await requireAuth();
    if (!auth.authorized) return auth.response!;

    const totalProducts = await prisma.product.count();
    if (totalProducts === 0) {
      return NextResponse.json({
        success: true,
        data: {
          overallScore: 0,
          healthStatus: "critical",
          dataQuality: [],
          seoHealth: [],
          contentCompletenessScore: 0,
          recommendations: [],
          lastAuditDate: new Date().toISOString(),
        },
      });
    }

    // ── Data Quality ──
    const [
      withImages,
      withTitle,
      withDescription,
      withSeoTitle,
      withTags,
      withBrand,
      withCategory,
    ] = await Promise.all([
      prisma.product.count({ where: { images: { some: {} } } }),
      prisma.product.count({ where: { title: { not: null } } }),
      prisma.product.count({ where: { htmlDescription: { not: null } } }),
      prisma.product.count({ where: { seoTitle: { not: null } } }),
      prisma.product.count({ where: { tags: { not: null } } }),
      prisma.product.count({ where: { brand: { not: "" } } }),
      prisma.product.count({ where: { category: { not: "" } } }),
    ]);

    const pct = (n: number) => Math.round((n / totalProducts) * 100);

    const dataQuality = [
      { field: "Product Images", count: withImages, total: totalProducts, percentage: pct(withImages) },
      { field: "Product Title", count: withTitle, total: totalProducts, percentage: pct(withTitle) },
      { field: "HTML Description", count: withDescription, total: totalProducts, percentage: pct(withDescription) },
      { field: "Brand", count: withBrand, total: totalProducts, percentage: pct(withBrand) },
      { field: "Category", count: withCategory, total: totalProducts, percentage: pct(withCategory) },
      { field: "Tags", count: withTags, total: totalProducts, percentage: pct(withTags) },
    ];

    // ── SEO Health ──
    const [withSeoDesc, withPageUrl] = await Promise.all([
      prisma.product.count({ where: { seoDescription: { not: null } } }),
      prisma.product.count({ where: { pageUrl: { not: null } } }),
    ]);

    const seoHealth = [
      { field: "SEO Title", count: withSeoTitle, total: totalProducts, percentage: pct(withSeoTitle) },
      { field: "SEO Description", count: withSeoDesc, total: totalProducts, percentage: pct(withSeoDesc) },
      { field: "Page URL", count: withPageUrl, total: totalProducts, percentage: pct(withPageUrl) },
    ];

    // ── Overall score ──
    const allPcts = [...dataQuality.map((d) => d.percentage), ...seoHealth.map((s) => s.percentage)];
    const contentCompletenessScore = Math.round(allPcts.reduce((a, b) => a + b, 0) / allPcts.length);

    let healthStatus: string;
    if (contentCompletenessScore >= 90) healthStatus = "excellent";
    else if (contentCompletenessScore >= 70) healthStatus = "good";
    else if (contentCompletenessScore >= 50) healthStatus = "warning";
    else healthStatus = "critical";

    // ── AI-style recommendations ──
    const recommendations: { title: string; description: string; priority: string; affectedCount: number }[] = [];

    const missingImages = totalProducts - withImages;
    if (missingImages > 0) {
      recommendations.push({
        title: "Add Missing Product Images",
        description: `${missingImages} products have no images. Adding quality photos can boost conversions.`,
        priority: missingImages > 100 ? "high" : "medium",
        affectedCount: missingImages,
      });
    }
    const missingDesc = totalProducts - withDescription;
    if (missingDesc > 0) {
      recommendations.push({
        title: "Complete Product Descriptions",
        description: `${missingDesc} products lack an HTML description. Rich descriptions improve SEO and buyer confidence.`,
        priority: missingDesc > 200 ? "high" : "medium",
        affectedCount: missingDesc,
      });
    }
    const missingSeoTitle = totalProducts - withSeoTitle;
    if (missingSeoTitle > 0) {
      recommendations.push({
        title: "Add SEO Titles",
        description: `${missingSeoTitle} products are missing SEO titles. Custom titles help search rankings.`,
        priority: missingSeoTitle > 500 ? "high" : "medium",
        affectedCount: missingSeoTitle,
      });
    }
    const missingSeoDesc = totalProducts - withSeoDesc;
    if (missingSeoDesc > 0) {
      recommendations.push({
        title: "Write Meta Descriptions",
        description: `${missingSeoDesc} products need meta descriptions for better click-through rates.`,
        priority: "medium",
        affectedCount: missingSeoDesc,
      });
    }
    const missingTags = totalProducts - withTags;
    if (missingTags > 0) {
      recommendations.push({
        title: "Tag Untagged Products",
        description: `${missingTags} products have no tags. Tags improve filtering and collection management.`,
        priority: "low",
        affectedCount: missingTags,
      });
    }

    return NextResponse.json({
      success: true,
      data: {
        overallScore: contentCompletenessScore,
        healthStatus,
        dataQuality,
        seoHealth,
        contentCompletenessScore,
        recommendations,
        lastAuditDate: new Date().toISOString(),
      },
    });
  } catch (error) {
    console.error("Store health error:", error);
    return NextResponse.json(
      { success: false, error: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 }
    );
  }
}
