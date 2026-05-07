"use client";

import { useSession } from "next-auth/react";
import { useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Image as ImageIcon,
  RotateCw,
  Edit3,
  ArrowRight,
  TrendingUp,
  Bolt,
  ChevronRight,
} from "lucide-react";
import Topbar from "@/components/Topbar";

interface Stats {
  total: number;
  published: number;
  draft: number;
  lowStock: number;
}

interface RecentProduct {
  id: string;
  title: string;
  brand: string | null;
  status: string;
  stock: number;
  createdAt: string;
}

interface ActionCardData {
  key: string;
  label: string;
  value: number;
  sub: string;
  Icon: React.ElementType;
  tone: "critical" | "magic" | "warning" | "success";
  href: string;
}

function formatINRCompact(amount: number): string {
  if (!amount) return "₹0";
  if (amount >= 1e7) return `₹${(amount / 1e7).toFixed(2)} Cr`;
  if (amount >= 1e5) return `₹${(amount / 1e5).toFixed(2)} L`;
  if (amount >= 1000) return `₹${(amount / 1000).toFixed(1)}k`;
  return `₹${amount.toLocaleString("en-IN")}`;
}

function ActionCard({ data }: { data: ActionCardData }) {
  const toneToBg = {
    critical: "var(--critical-bg)",
    magic: "var(--magic-bg)",
    warning: "var(--warning-bg)",
    success: "var(--brand-bg)",
  } as const;
  const toneToFg = {
    critical: "var(--critical)",
    magic: "var(--highlight)",
    warning: "#a06d00",
    success: "var(--brand)",
  } as const;
  const Icon = data.Icon;
  return (
    <Link
      href={data.href}
      className="polaris-card flex flex-col gap-2 hover:shadow-md transition-all"
      style={{
        padding: 14,
        textAlign: "left",
        cursor: "pointer",
      }}
    >
      <div className="flex items-center justify-between">
        <div
          className="flex items-center justify-center"
          style={{
            width: 28,
            height: 28,
            borderRadius: 7,
            background: toneToBg[data.tone],
          }}
        >
          <Icon size={14} color={toneToFg[data.tone]} />
        </div>
        <ArrowRight size={14} color="var(--text-tertiary)" />
      </div>
      <div
        className="tabular-nums"
        style={{
          fontSize: 28,
          fontWeight: 600,
          lineHeight: 1,
          letterSpacing: -0.5,
        }}
      >
        {data.value}
      </div>
      <div>
        <div style={{ fontSize: 13, fontWeight: 500 }}>{data.label}</div>
        <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
          {data.sub}
        </div>
      </div>
    </Link>
  );
}

export default function DashboardPage() {
  const { data: session } = useSession();
  const [stats, setStats] = useState<Stats>({
    total: 0,
    published: 0,
    draft: 0,
    lowStock: 0,
  });
  const [products, setProducts] = useState<RecentProduct[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [statsRes, productsRes] = await Promise.all([
          fetch("/api/products/stats"),
          fetch("/api/products?limit=10"),
        ]);
        const statsJson = await statsRes.json();
        const productsJson = await productsRes.json();

        if (statsJson.success) {
          setStats({
            total: statsJson.data.total || 0,
            published: statsJson.data.published || 0,
            draft: statsJson.data.draft || 0,
            lowStock: statsJson.data.lowStock || 0,
          });
        }

        const productsList = productsJson.data || [];
        setProducts(
          productsList.slice(0, 8).map((p: any) => ({
            id: p.id,
            title: p.title || p.productName || "Untitled",
            brand: p.brand || null,
            status: p.status,
            stock: p.locations
              ? p.locations.reduce(
                  (sum: number, loc: any) => sum + (loc.quantity || 0),
                  0
                )
              : 0,
            createdAt: p.createdAt,
          }))
        );
      } catch (error) {
        console.error("Failed to fetch dashboard data:", error);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  const userFirstName =
    (session?.user?.name || session?.user?.email || "")
      .split(/[\s@]/)[0] || "there";

  const today = new Date().toLocaleDateString("en-IN", {
    weekday: "long",
    month: "long",
    day: "numeric",
  });

  // Action cards: pull from real stats. We don't yet have separate counts
  // for "awaiting design" or "sync failed" — those default to 0 until we
  // wire the workers. Visible structure matches the design handoff.
  const cards: ActionCardData[] = [
    {
      key: "lowStock",
      label: "Low stock",
      value: stats.lowStock,
      sub: "products below threshold",
      Icon: AlertTriangle,
      tone: "critical",
      href: "/dashboard/products?filter=low_stock",
    },
    {
      key: "awaitingDesign",
      label: "Awaiting design",
      value: 0,
      sub: "raw images, edits pending",
      Icon: ImageIcon,
      tone: "magic",
      href: "/dashboard/design-queue",
    },
    {
      key: "syncFailed",
      label: "Sync failed",
      value: 0,
      sub: "products didn't reach Shopify",
      Icon: RotateCw,
      tone: "critical",
      href: "/dashboard/shopify",
    },
    {
      key: "draft",
      label: "Drafts",
      value: stats.draft,
      sub: "ready to review & publish",
      Icon: Edit3,
      tone: "warning",
      href: "/dashboard/products?status=DRAFT",
    },
  ];

  // Mini sparkline for the greeting card. Placeholder data — replace with
  // a /api/reports/revenue?days=30 series once that endpoint exists.
  const revenuePoints = [12, 18, 22, 28, 24, 35, 42, 38, 45, 52, 48, 56, 62, 58, 65, 72, 68, 78];
  const sparklinePath = (() => {
    const max = Math.max(...revenuePoints);
    const w = 240;
    const h = 40;
    return revenuePoints
      .map((v, i) => {
        const x = (i / (revenuePoints.length - 1)) * w;
        const y = h - (v / max) * h;
        return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
      })
      .join(" ");
  })();

  return (
    <>
      <Topbar
        title="Dashboard"
        subtitle="Action-first overview"
        breadcrumb={[{ label: "Home" }]}
      />
      <div style={{ padding: 24, maxWidth: 1400, margin: "0 auto" }}>
        {/* ─── Hero — 4 action cards ───────────────────────── */}
        <div
          className="grid gap-3 mb-4"
          style={{ gridTemplateColumns: "repeat(4, minmax(0, 1fr))" }}
        >
          {cards.map((c) => (
            <ActionCard key={c.key} data={c} />
          ))}
        </div>

        {/* ─── Greeting card (gradient) ───────────────────────── */}
        <div
          className="grid gap-3 mb-4"
          style={{ gridTemplateColumns: "1.4fr 1fr" }}
        >
          <div
            className="polaris-card"
            style={{
              padding: 18,
              background: "linear-gradient(135deg, #1a1a1a 0%, #2c2c2c 100%)",
              color: "white",
              border: "none",
            }}
          >
            <div className="flex items-start justify-between mb-4">
              <div>
                <div style={{ fontSize: 12, opacity: 0.7, marginBottom: 4 }}>
                  Good day, {userFirstName}
                </div>
                <div style={{ fontSize: 18, fontWeight: 600 }}>
                  {stats.lowStock + stats.draft > 0 ? (
                    <>
                      You've got{" "}
                      <span style={{ color: "#7fdfb5" }}>
                        {stats.lowStock + stats.draft}
                      </span>{" "}
                      things to handle
                    </>
                  ) : (
                    <>Everything looks calm today</>
                  )}
                </div>
                <div style={{ fontSize: 12, opacity: 0.7, marginTop: 4 }}>
                  Better Vision · {today}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-3 gap-3">
              <div>
                <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 2 }}>
                  Total products
                </div>
                <div
                  className="tabular-nums"
                  style={{ fontSize: 22, fontWeight: 600 }}
                >
                  {stats.total}
                </div>
                <div
                  style={{
                    fontSize: 11,
                    color: "#7fdfb5",
                    display: "flex",
                    alignItems: "center",
                    gap: 3,
                    marginTop: 2,
                  }}
                >
                  <TrendingUp size={11} color="#7fdfb5" />
                  catalog
                </div>
              </div>
              <div>
                <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 2 }}>
                  Published
                </div>
                <div
                  className="tabular-nums"
                  style={{ fontSize: 22, fontWeight: 600 }}
                >
                  {stats.published}
                </div>
                <div
                  style={{ fontSize: 11, opacity: 0.7, marginTop: 2 }}
                >
                  live on Shopify
                </div>
              </div>
              <div>
                <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 2 }}>
                  Drafts
                </div>
                <div
                  className="tabular-nums"
                  style={{ fontSize: 22, fontWeight: 600 }}
                >
                  {stats.draft}
                </div>
                <div style={{ fontSize: 11, opacity: 0.7, marginTop: 2 }}>
                  awaiting publish
                </div>
              </div>
            </div>

            {/* Sparkline */}
            <div
              style={{
                marginTop: 14,
                paddingTop: 12,
                borderTop: "1px solid rgba(255,255,255,0.1)",
              }}
            >
              <svg
                width="100%"
                height="40"
                viewBox="0 0 240 40"
                preserveAspectRatio="none"
              >
                <path
                  d={sparklinePath}
                  fill="none"
                  stroke="#7fdfb5"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
          </div>

          {/* Quick actions */}
          <div className="polaris-card" style={{ padding: 18 }}>
            <div
              className="flex items-center justify-between"
              style={{ marginBottom: 12 }}
            >
              <div className="polaris-card-title">Quick actions</div>
              <Bolt size={14} color="var(--text-tertiary)" />
            </div>
            <div className="flex flex-col gap-2">
              <Link
                href="/dashboard/products/new"
                className="polaris-btn polaris-btn-success"
                style={{ justifyContent: "flex-start", padding: "8px 12px" }}
              >
                Add product
              </Link>
              <Link
                href="/dashboard/shopify"
                className="polaris-btn"
                style={{ justifyContent: "flex-start", padding: "8px 12px" }}
              >
                Sync to Shopify
              </Link>
              <Link
                href="/dashboard/stock-tally"
                className="polaris-btn"
                style={{ justifyContent: "flex-start", padding: "8px 12px" }}
              >
                Run stock tally
              </Link>
              <Link
                href="/dashboard/stock-import"
                className="polaris-btn"
                style={{ justifyContent: "flex-start", padding: "8px 12px" }}
              >
                Import Excel
              </Link>
            </div>
          </div>
        </div>

        {/* ─── Recent products ───────────────────────── */}
        <div className="polaris-card">
          <div className="polaris-card-header">
            <div className="polaris-card-title">Recent products</div>
            <Link
              href="/dashboard/products"
              className="polaris-btn polaris-btn-plain polaris-btn-sm"
            >
              View all <ChevronRight size={11} />
            </Link>
          </div>
          {loading ? (
            <div
              style={{
                padding: 32,
                textAlign: "center",
                color: "var(--text-tertiary)",
                fontSize: 13,
              }}
            >
              Loading…
            </div>
          ) : products.length === 0 ? (
            <div
              style={{
                padding: 32,
                textAlign: "center",
                color: "var(--text-tertiary)",
                fontSize: 13,
              }}
            >
              No products yet.{" "}
              <Link
                href="/dashboard/products/new"
                style={{ color: "var(--brand-text)" }}
              >
                Create one
              </Link>{" "}
              to get started.
            </div>
          ) : (
            <table className="polaris-table">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Brand</th>
                  <th>Status</th>
                  <th className="tabular-nums" style={{ textAlign: "right" }}>
                    Stock
                  </th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {products.map((p) => (
                  <tr key={p.id}>
                    <td style={{ fontWeight: 500 }}>{p.title}</td>
                    <td style={{ color: "var(--text-secondary)" }}>
                      {p.brand || "—"}
                    </td>
                    <td>
                      <span
                        className={`polaris-badge ${
                          p.status === "PUBLISHED"
                            ? "polaris-badge-success"
                            : p.status === "ARCHIVED"
                              ? ""
                              : "polaris-badge-warning"
                        }`}
                      >
                        {p.status}
                      </span>
                    </td>
                    <td
                      className="tabular-nums"
                      style={{
                        textAlign: "right",
                        color:
                          p.stock < 5
                            ? "var(--critical)"
                            : "var(--text)",
                        fontWeight: p.stock < 5 ? 600 : 500,
                      }}
                    >
                      {p.stock}
                    </td>
                    <td
                      style={{
                        color: "var(--text-tertiary)",
                        fontSize: 12,
                      }}
                    >
                      {new Date(p.createdAt).toLocaleDateString("en-IN", {
                        month: "short",
                        day: "numeric",
                      })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </>
  );
}
