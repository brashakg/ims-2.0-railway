"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Search,
  Package,
  ShoppingCart,
  Users,
  FolderOpen,
  ClipboardCheck,
  Settings,
  BarChart3,
  ArrowRight,
} from "lucide-react";

interface ProductHit {
  id: string;
  title: string | null;
  brand: string | null;
  modelNo: string | null;
  status: string;
  shopifyProductId: string | null;
  images: Array<{ url: string }>;
}

interface NavAction {
  label: string;
  href: string;
  icon: React.ElementType;
  hint: string;
}

const QUICK_NAV: NavAction[] = [
  { label: "Products", href: "/dashboard/products", icon: Package, hint: "Browse / edit catalog" },
  { label: "Orders", href: "/dashboard/orders", icon: ShoppingCart, hint: "Synced from Shopify" },
  { label: "Customers", href: "/dashboard/customers", icon: Users, hint: "Customer list" },
  { label: "Collections", href: "/dashboard/collections", icon: FolderOpen, hint: "Shopify collections" },
  { label: "Stock Tally", href: "/dashboard/stock-tally", icon: ClipboardCheck, hint: "Barcode count" },
  { label: "Reports", href: "/dashboard/reports", icon: BarChart3, hint: "Insights + analytics" },
  { label: "Shopify Sync", href: "/dashboard/shopify", icon: Settings, hint: "Pull / push" },
];

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

/**
 * Cmd+K-style command palette. Opens via the sidebar Search button or
 * the keyboard shortcut. Two layers:
 *   1. Quick navigation actions (route shortcuts) shown when query empty
 *   2. Live product search via /api/products?search=q&limit=8 when query
 *      has 2+ chars
 *
 * Click or Enter activates the highlighted item; Esc closes.
 */
export default function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ProductHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const debounceRef = useRef<NodeJS.Timeout | null>(null);

  // Focus input when opened, reset state when closed
  useEffect(() => {
    if (open) {
      setQuery("");
      setResults([]);
      setActiveIdx(0);
      // microtask defer so the input is in the DOM before we focus
      setTimeout(() => inputRef.current?.focus(), 30);
    }
  }, [open]);

  // Debounced product search
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const q = query.trim();
    if (q.length < 2) {
      setResults([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await fetch(
          `/api/products?search=${encodeURIComponent(q)}&limit=8`
        );
        const data = await res.json();
        setResults((data.data as ProductHit[]) || []);
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query]);

  // Reset cursor when results change
  useEffect(() => {
    setActiveIdx(0);
  }, [results.length, query]);

  if (!open) return null;

  // Build the flat list the keyboard cursor walks. Empty query → quick
  // nav. Active query → product hits + a "Search all products" action.
  const showResults = query.trim().length >= 2;
  const navItems: Array<{
    type: "nav" | "product" | "viewAll";
    href: string;
    label: string;
    sublabel?: string;
    image?: string | null;
    icon?: React.ElementType;
  }> = showResults
    ? [
        ...results.map((p) => ({
          type: "product" as const,
          href: `/dashboard/products/edit/${p.id}`,
          label: p.title || "(untitled)",
          sublabel: `${p.brand || "—"} · ${p.modelNo || "—"}`,
          image: p.images[0]?.url || null,
        })),
        {
          type: "viewAll" as const,
          href: `/dashboard/products?search=${encodeURIComponent(query.trim())}`,
          label: `Search all products for "${query.trim()}"`,
          icon: Search,
        },
      ]
    : QUICK_NAV.map((n) => ({
        type: "nav" as const,
        href: n.href,
        label: n.label,
        sublabel: n.hint,
        icon: n.icon,
      }));

  const activate = (idx: number) => {
    const item = navItems[idx];
    if (!item) return;
    onClose();
    router.push(item.href);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(navItems.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      activate(activeIdx);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 80,
        background: "rgba(26,26,26,0.4)",
        backdropFilter: "blur(2px)",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        paddingTop: "10vh",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="fade-in"
        style={{
          width: 640,
          maxWidth: "92vw",
          background: "var(--bg-surface)",
          borderRadius: 12,
          boxShadow: "var(--shadow-lg)",
          border: "1px solid var(--border)",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          maxHeight: "70vh",
        }}
      >
        {/* Search input */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "12px 16px",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <Search size={16} color="var(--text-tertiary)" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKey}
            placeholder="Search products, jump to a page…"
            style={{
              flex: 1,
              border: "none",
              outline: "none",
              fontSize: 14,
              padding: 4,
              background: "transparent",
              color: "var(--text)",
            }}
          />
          <span className="polaris-kbd">esc</span>
        </div>

        {/* Results */}
        <div style={{ flex: 1, overflowY: "auto", padding: "8px 0" }}>
          {/* Section header */}
          <div
            style={{
              padding: "6px 16px 4px",
              fontSize: 10,
              fontWeight: 600,
              textTransform: "uppercase",
              letterSpacing: 0.6,
              color: "var(--text-tertiary)",
            }}
          >
            {showResults
              ? loading
                ? "Searching…"
                : `Products (${results.length})`
              : "Quick navigation"}
          </div>

          {showResults && !loading && results.length === 0 && (
            <div
              style={{
                padding: "16px 16px 8px",
                fontSize: 13,
                color: "var(--text-tertiary)",
              }}
            >
              No products match &ldquo;{query}&rdquo;.
            </div>
          )}

          {navItems.map((item, idx) => {
            const Icon = item.icon;
            const isActive = idx === activeIdx;
            return (
              <button
                key={`${item.type}-${idx}-${item.href}`}
                type="button"
                onMouseEnter={() => setActiveIdx(idx)}
                onClick={() => activate(idx)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  width: "100%",
                  padding: "8px 16px",
                  border: "none",
                  background: isActive
                    ? "var(--bg-surface-hover)"
                    : "transparent",
                  cursor: "pointer",
                  textAlign: "left",
                }}
              >
                {item.image ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={item.image}
                    alt=""
                    style={{
                      width: 26,
                      height: 26,
                      borderRadius: 4,
                      objectFit: "cover",
                      border: "1px solid var(--border-subdued)",
                    }}
                  />
                ) : (
                  <div
                    style={{
                      width: 26,
                      height: 26,
                      borderRadius: 6,
                      background: "var(--bg-surface-tertiary)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    {Icon && <Icon size={14} color="var(--text-secondary)" />}
                  </div>
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: 13,
                      fontWeight: 500,
                      color: "var(--text)",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {item.label}
                  </div>
                  {item.sublabel && (
                    <div
                      style={{
                        fontSize: 11,
                        color: "var(--text-tertiary)",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {item.sublabel}
                    </div>
                  )}
                </div>
                {isActive && (
                  <ArrowRight size={14} color="var(--text-secondary)" />
                )}
              </button>
            );
          })}
        </div>

        {/* Footer */}
        <div
          style={{
            padding: "8px 16px",
            borderTop: "1px solid var(--border)",
            background: "var(--bg-surface-tertiary)",
            display: "flex",
            alignItems: "center",
            gap: 12,
            fontSize: 11,
            color: "var(--text-tertiary)",
          }}
        >
          <span>
            <span className="polaris-kbd">↑</span>{" "}
            <span className="polaris-kbd">↓</span> navigate
          </span>
          <span>
            <span className="polaris-kbd">↵</span> open
          </span>
          <span>
            <span className="polaris-kbd">esc</span> close
          </span>
        </div>
      </div>
    </div>
  );
}
