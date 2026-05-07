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

interface DashboardStats {
  total: number;
  published: number;
  draft: number;
  archived: number;
  syncedWithShopify: number;
  lowStock: number;
  awaitingDesign: number;
  syncFailed: number;
  todaysRevenue: number;
  todaysOrders: number;
  revenueSeries: number[];
  seriesDays: string[];
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

const EMPTY_STATS: DashboardStats = {
  total: 0,
  published: 0,
  draft: 0,
  archived: 0,
  syncedWithShopify: 0,
  lowStock: 0,
  awaitingDesign: 0,
  syncFailed: 0,
  todaysRevenue: 0,
  todaysOrders: 0,
  revenueSeries: new Array(30).fill(0),
  seriesDays: [],
};

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
  const [stats, setStats] = useState<DashboardStats>(EMPTY_STATS);
  const [products, setProducts] = useState<RecentProduct[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [statsRes, productsRes] = await Promise.all([
          fetch("/api/dashboard/stats"),
          fetch("/api/products?limit=10"),
        ]);
        const statsJson = await statsRes.json();
        const productsJson = await productsRes.json();

        if (statsJson.success) {
          // Defensive merge — if a new field is added server-side and the
          // client is stale, this preserves zero defaults instead of
          // crashing on .toFixed() of undefined.
          setStats({ ...EMPTY_STATS, ...statsJson.data });
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

  // Action cards now pull from the real /api/dashboard/stats endpoint —
  // awaitingDesign uses Product.imageDesignStatus = PENDING_DESIGN,
  // syncFailed uses the latest SyncLog action per product.
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
      value: stats.awaitingDesign,
      sub: "raw images, edits pending",
      Icon: ImageIcon,
      tone: "magic",
      href: "/dashboard/design-queue",
    },
    {
      key: "syncFailed",
      label: "Sync failed",
      value: stats.syncFailed,
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

  // Sparkline path from the real 30-day revenue series.
  // Returns a flat-line path when there's no data so the SVG doesn't
  // collapse or render nothing.
  const sparklinePath = (() => {
    const series = stats.revenueSeries.length > 0 ? stats.revenueSeries : [0];
    const max = Math.max(...series, 1); // avoid divide-by-zero
    const w = 240;
    const h = 40;
    return series
      .map((v, i) => {
        const x = series.length === 1 ? 0 : (i / (series.length - 1)) * w;
        const y = h - (v / max) * h;
        return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
      })
      .join(" ");
  })();
  const has30DayRevenue = stats.revenueSeries.some((v) => v > 0);

  // Yesterday vs today comparison for the trend pill.
  const yesterdayRevenue =
    stats.revenueSeries.length >= 2
      ? stats.revenueSeries[stats.revenueSeries.length - 2]
      : 0;
  const revenueDeltaPct =
    yesterdayRevenue > 0
      ? ((stats.todaysRevenue - yesterdayRevenue) / yesterdayRevenue) * 100
      : null;

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
                  {stats.lowStock + stats.awaitingDesign + stats.syncFailed >
                  0 ? (
                    <>
                      You&apos;ve got{" "}
                      <span style={{ color: "#7fdfb5" }}>
                        {stats.lowStock +
                          stats.awaitingDesign +
                          stats.syncFailed}
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
                  Today&apos;s revenue
                </div>
                <div
                  className="tabular-nums"
                  style={{ fontSize: 22, fontWeight: 600 }}
                >
                  {formatINRCompact(stats.todaysRevenue)}
                </div>
                <div
                  style={{
                    fontSize: 11,
                    color:
                      revenueDeltaPct === null
                        ? "rgba(255,255,255,0.5)"
                        : revenueDeltaPct >= 0
                          ? "#7fdfb5"
                          : "#ff8a8a",
                    display: "flex",
                    alignItems: "center",
                    gap: 3,
                    marginTop: 2,
                  }}
                >
                  {revenueDeltaPct === null ? (
                    "no comparison"
                  ) : (
                    <>
                      <TrendingUp size={11} />
                      {revenueDeltaPct >= 0 ? "+" : ""}
                      {revenueDeltaPct.toFixed(1)}% vs yesterday
                    </>
                  )}
                </div>
              </div>
              <div>
                <div style={{ fontSize: 11, opacity: 0.6, marginBottom: 2 }}>
                  Today&apos;s orders
                </div>
                <div
                  className="tabular-nums"
                  style={{ fontSize: 22, fontWeight: 600 }}
                >
                  {stats.todaysOrders}
                </div>
                <div style={{ fontSize: 11, opacity: 0.7, marginTop: 2 }}>
                  paid + partial
                </div>
              </div>
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
                <div style={{ fontSize: 11, opacity: 0.7, marginTop: 2 }}>
                  {stats.published} published · {stats.draft} draft
                </div>
              </div>
            </div>

            {/* Sparkline — 30 day revenue. Hidden if there's truly no
                revenue data so we don't show a flat zero line. */}
            <div
              style={{
                marginTop: 14,
                paddingTop: 12,
                borderTop: "1px solid rgba(255,255,255,0.1)",
              }}
            >
              {has30DayRevenue ? (
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
              ) : (
                <div
                  style={{
                    fontSize: 11,
                    color: "rgba(255,255,255,0.5)",
                    textAlign: "center",
                    padding: "10px 0",
                  }}
                >
                  No revenue in the last 30 days. Sync orders from Shopify to
                  populate this chart.
                </div>
              )}
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
