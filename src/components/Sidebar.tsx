"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSession, signOut } from "next-auth/react";
import { useState } from "react";
import {
  effectiveFeatures,
  type FeatureKey,
} from "@/lib/features";
import {
  Menu,
  X,
  LayoutDashboard,
  Package,
  FolderOpen,
  Image as ImageIcon,
  ShoppingCart,
  Users,
  ArrowLeftRight,
  BarChart3,
  Megaphone,
  Globe,
  ClipboardCheck,
  Settings,
  MapPin,
  Tag,
  UserCog,
  LogOut,
  ScrollText,
  HardDriveDownload,
  Percent,
  AlertTriangle,
  Palette,
  ChevronDown,
  Search,
  Grid3x3,
} from "lucide-react";

interface NavItem {
  href: string;
  label: string;
  icon: React.ElementType;
  exact?: boolean;
  /** Optional badge — count or pill text. Not driven by data yet, but the
   *  shape mirrors the design handoff so we can wire counts later. */
  badge?: string;
  badgeTone?: "default" | "critical" | "magic";
  /** Required feature key. Item is hidden if user doesn't have this
   *  feature enabled. Dashboard / Home items leave this undefined since
   *  every authenticated user should see them. */
  feature?: FeatureKey;
}

interface NavGroup {
  id: string;
  label?: string;
  items: NavItem[];
}

/**
 * Polaris-flavored sidebar (design handoff 2026-05).
 *
 * Differences from the previous dark-slate sidebar:
 *  - Light tertiary surface (#f1f2f3) with right border, not a dark card.
 *  - Section labels are 10px uppercase tracked.
 *  - Active item is white-card-on-tertiary with subtle shadow.
 *  - Brand block at top with a green BV gradient logo + workspace dropdown.
 *  - Search trigger row that opens the Command Palette (⌘K) — palette
 *    itself is wired in a later step.
 *  - User block with avatar at the bottom instead of just an email.
 */
export default function Sidebar() {
  const pathname = usePathname();
  const { data: session } = useSession();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  const userRole = (session?.user as any)?.role || "USER";
  const enabledFeatures: string | null =
    (session?.user as any)?.enabledFeatures ?? null;

  // Resolve the user's feature set once. ADMIN gets all features
  // implicitly; for others we look up role defaults or the explicit
  // override on User.enabledFeatures.
  const features = effectiveFeatures({ role: userRole, enabledFeatures });
  const has = (k: FeatureKey) => features.includes(k);

  // Group order matches the design handoff: main (no header) → Operations
  // → Insights → Admin. Items within main appear without a section label.
  // Each item declares its required feature; the filter below hides any
  // item the user doesn't have access to. Dashboard is always visible.
  const allGroups: NavGroup[] = [
    {
      id: "main",
      items: [
        { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, exact: true },
        { href: "/dashboard/products", label: "Products", icon: Package, feature: "products" },
        { href: "/dashboard/orders", label: "Orders", icon: ShoppingCart, feature: "orders" },
        { href: "/dashboard/customers", label: "Customers", icon: Users, feature: "customers" },
        { href: "/dashboard/collections", label: "Collections", icon: FolderOpen, feature: "collections" },
      ],
    },
    {
      id: "ops",
      label: "Operations",
      items: [
        { href: "/dashboard/stock-tally", label: "Stock Tally", icon: ClipboardCheck, feature: "stock_tally" },
        { href: "/dashboard/stock-transfers", label: "Stock Transfers", icon: ArrowLeftRight, feature: "stock_transfers" },
        { href: "/dashboard/stock-import", label: "Backup & Restore", icon: HardDriveDownload, feature: "stock_import" },
        { href: "/dashboard/design-queue", label: "Design Queue", icon: Palette, badgeTone: "magic", feature: "design_queue" },
        { href: "/dashboard/shopify", label: "Shopify Sync", icon: Settings, feature: "shopify_sync" },
      ],
    },
    {
      id: "insights",
      label: "Insights",
      items: [
        { href: "/dashboard/reports", label: "Reports", icon: BarChart3, feature: "reports" },
        { href: "/dashboard/marketing", label: "Marketing", icon: Megaphone, feature: "marketing" },
        { href: "/dashboard/store-health", label: "Store Health", icon: Globe, feature: "store_health" },
      ],
    },
    {
      id: "admin",
      label: "Admin",
      items: [
        { href: "/dashboard/attributes", label: "Attributes", icon: Tag, feature: "attributes" },
        { href: "/dashboard/admin/discount-rules", label: "Discount Rules", icon: Percent, feature: "discount_rules" },
        { href: "/dashboard/locations", label: "Locations", icon: MapPin, feature: "locations" },
        { href: "/dashboard/users", label: "Users", icon: UserCog, feature: "users" },
        { href: "/dashboard/images", label: "Images", icon: ImageIcon, feature: "images" },
        { href: "/dashboard/activity-logs", label: "Activity Logs", icon: ScrollText, feature: "activity_logs" },
        { href: "/dashboard/admin/orphans", label: "Orphan Audit", icon: AlertTriangle, badgeTone: "critical", feature: "orphan_audit" },
      ],
    },
  ];

  // Apply the per-user feature filter. A group with no surviving items
  // is dropped entirely so we don't render an "Admin" header above an
  // empty list.
  const groups: NavGroup[] = allGroups
    .map((g) => ({
      ...g,
      items: g.items.filter((i) => !i.feature || has(i.feature)),
    }))
    .filter((g) => g.items.length > 0);

  const isActive = (href: string, exact?: boolean) => {
    if (exact) return pathname === href;
    return pathname === href || pathname.startsWith(href + "/");
  };

  const closeMobile = () => setIsMobileMenuOpen(false);

  // User initials: first letters of name, fallback to email prefix.
  const userName = session?.user?.name || session?.user?.email || "User";
  const initials = userName
    .replace(/@.*/, "")
    .split(/[\s._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase() ?? "")
    .join("") || "U";

  const renderItem = (item: NavItem) => {
    const active = isActive(item.href, item.exact);
    const Icon = item.icon;
    return (
      <Link
        key={item.href}
        href={item.href}
        onClick={closeMobile}
        className="flex items-center gap-2.5 w-full px-2.5 py-1.5 rounded-lg text-[13px] mb-px transition-colors"
        style={{
          background: active ? "var(--bg-surface)" : "transparent",
          color: active ? "var(--text)" : "var(--text-secondary)",
          fontWeight: active ? 600 : 500,
          boxShadow: active ? "var(--shadow-xs)" : "none",
        }}
        onMouseEnter={(e) => {
          if (!active) e.currentTarget.style.background = "rgba(0,0,0,0.04)";
        }}
        onMouseLeave={(e) => {
          if (!active) e.currentTarget.style.background = "transparent";
        }}
      >
        <Icon
          size={16}
          color={active ? "var(--text)" : "var(--text-secondary)"}
        />
        <span className="flex-1 text-left truncate">{item.label}</span>
        {item.badge && (
          <span
            className={`polaris-badge ${
              item.badgeTone === "critical"
                ? "polaris-badge-critical"
                : item.badgeTone === "magic"
                  ? "polaris-badge-magic"
                  : ""
            }`}
            style={{ padding: "0 6px", fontSize: 10, height: 16 }}
          >
            {item.badge}
          </span>
        )}
      </Link>
    );
  };

  const sidebarBody = (
    <aside
      className="flex flex-col h-screen sticky top-0 flex-shrink-0"
      style={{
        width: "var(--sidebar-w)",
        background: "var(--bg-surface-tertiary)",
        borderRight: "1px solid var(--border)",
      }}
    >
      {/* Brand */}
      <div
        className="px-3 py-2.5"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        <Link
          href="/dashboard"
          onClick={closeMobile}
          className="flex items-center gap-2.5 w-full px-2 py-1.5 rounded-lg"
          style={{ background: "transparent" }}
        >
          <div
            className="flex items-center justify-center text-white font-bold text-xs flex-shrink-0"
            style={{
              width: 28,
              height: 28,
              borderRadius: 7,
              background: "linear-gradient(135deg, #008060 0%, #00a474 100%)",
              letterSpacing: -0.5,
              boxShadow: "var(--shadow-sm)",
            }}
          >
            BV
          </div>
          <div className="flex-1 min-w-0">
            <div
              style={{ fontWeight: 600, fontSize: 13, color: "var(--text)" }}
              className="truncate"
            >
              Better Vision
            </div>
            <div
              style={{ fontSize: 11, color: "var(--text-tertiary)" }}
              className="truncate"
            >
              bokaro-better-vision
            </div>
          </div>
          <ChevronDown size={14} color="var(--text-tertiary)" />
        </Link>
      </div>

      {/* Search trigger / palette stub. Hooks up to Command Palette in a
          later step — for now it's a static prompt keeping the visual
          layout intact. */}
      <div className="px-2 pt-2 pb-1">
        <button
          type="button"
          className="flex items-center gap-2 w-full"
          style={{
            padding: "6px 10px",
            border: "1px solid var(--border)",
            background: "var(--bg-surface)",
            borderRadius: 8,
            cursor: "pointer",
            color: "var(--text-tertiary)",
            fontSize: 13,
            minHeight: "auto",
          }}
        >
          <Search size={14} />
          <span className="flex-1 text-left">Search anything…</span>
          <span className="polaris-kbd">⌘K</span>
        </button>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-2 pt-1 pb-2">
        {groups.map((g, i) => (
          <div
            key={g.id}
            style={{ marginTop: g.label ? 12 : i === 0 ? 4 : 0 }}
          >
            {g.label && (
              <div
                style={{
                  padding: "6px 10px 4px",
                  fontSize: 10,
                  fontWeight: 600,
                  color: "var(--text-tertiary)",
                  textTransform: "uppercase",
                  letterSpacing: 0.6,
                }}
              >
                {g.label}
              </div>
            )}
            {g.items.map(renderItem)}
          </div>
        ))}
      </nav>

      {/* User block */}
      <div
        className="p-2"
        style={{ borderTop: "1px solid var(--border)" }}
      >
        <div
          className="flex items-center gap-2.5 w-full px-2 py-1.5 rounded-lg"
        >
          <div
            className="flex items-center justify-center flex-shrink-0 text-white font-semibold"
            style={{
              width: 26,
              height: 26,
              borderRadius: 999,
              background: "linear-gradient(135deg, #ff6b9d 0%, #c44569 100%)",
              fontSize: 11,
            }}
          >
            {initials}
          </div>
          <div className="flex-1 min-w-0">
            <div
              style={{ fontSize: 12, fontWeight: 600, color: "var(--text)" }}
              className="truncate"
            >
              {session?.user?.name || session?.user?.email || "User"}
            </div>
            <div
              style={{
                fontSize: 10,
                color: "var(--text-tertiary)",
              }}
              className="flex items-center gap-1 truncate"
            >
              <span
                style={{
                  width: 5,
                  height: 5,
                  borderRadius: 999,
                  background: "var(--brand)",
                  flexShrink: 0,
                }}
              />
              <span className="truncate">{userRole}</span>
            </div>
          </div>
          <button
            type="button"
            onClick={() => signOut({ callbackUrl: "/login" })}
            title="Sign out"
            className="polaris-btn polaris-btn-icon"
            style={{ padding: 5 }}
          >
            <LogOut size={14} color="var(--text-tertiary)" />
          </button>
        </div>
      </div>
    </aside>
  );

  return (
    <>
      {/* Mobile hamburger — fixed top-left */}
      <button
        onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
        className="sm:hidden fixed top-3 left-3 z-[60] p-2 rounded-lg shadow-lg"
        style={{
          background: "var(--text)",
          color: "white",
          minHeight: "auto",
        }}
        aria-label="Toggle menu"
      >
        {isMobileMenuOpen ? (
          <X className="w-5 h-5" />
        ) : (
          <Menu className="w-5 h-5" />
        )}
      </button>

      {/* Mobile overlay */}
      {isMobileMenuOpen && (
        <div
          className="sm:hidden fixed inset-0 bg-black/40 z-[45]"
          onClick={closeMobile}
        />
      )}

      {/* Mobile drawer */}
      <div
        className={`sm:hidden fixed left-0 top-0 z-[50] transition-transform duration-200 ${
          isMobileMenuOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        {sidebarBody}
      </div>

      {/* Desktop sticky sidebar */}
      <div className="hidden sm:block">{sidebarBody}</div>
    </>
  );
}
