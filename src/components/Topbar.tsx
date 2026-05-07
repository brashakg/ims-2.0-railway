"use client";

import { ReactNode } from "react";
import Link from "next/link";
import { Bell, Plus, ChevronRight } from "lucide-react";

export interface BreadcrumbItem {
  label: string;
  href?: string;
}

interface TopbarProps {
  /** The page title — 18px / 600 / -0.2 letter-spacing per design. */
  title: ReactNode;
  /** Optional inline subtitle next to the title (12px tertiary). */
  subtitle?: ReactNode;
  /** Optional breadcrumb above the title. Last item renders as text. */
  breadcrumb?: BreadcrumbItem[];
  /** Page-specific action buttons rendered to the right of the title. */
  actions?: ReactNode;
  /** Optional override for the "Add product" CTA. Defaults to a Link to
   *  /dashboard/products/new — set this to override per page or hide. */
  primaryAction?: ReactNode | null;
}

/**
 * Polaris-flavored topbar: 56px tall, sticky to the top of the main
 * column, breadcrumb + title + page actions + global "Add product" CTA.
 *
 * Pages that opt into this should NOT render their own h1 or breadcrumb
 * — pass them through props so the topbar stays the single source of
 * page identity.
 */
export default function Topbar({
  title,
  subtitle,
  breadcrumb,
  actions,
  primaryAction,
}: TopbarProps) {
  const defaultPrimary = (
    <Link
      href="/dashboard/products/new"
      className="polaris-btn polaris-btn-primary"
    >
      <Plus size={14} color="white" />
      Add product
      <span
        className="polaris-kbd"
        style={{
          background: "rgba(255,255,255,0.15)",
          borderColor: "rgba(255,255,255,0.2)",
          color: "rgba(255,255,255,0.9)",
          marginLeft: 4,
        }}
      >
        N
      </span>
    </Link>
  );

  return (
    <div
      className="flex items-center gap-4 px-6 sticky top-0 z-10"
      style={{
        height: "var(--topbar-h)",
        borderBottom: "1px solid var(--border)",
        background: "var(--bg-surface)",
      }}
    >
      <div className="flex-1 min-w-0">
        {breadcrumb && breadcrumb.length > 0 && (
          <div
            className="flex items-center gap-1 mb-0.5 truncate"
            style={{ fontSize: 11, color: "var(--text-tertiary)" }}
          >
            {breadcrumb.map((b, i) => {
              const isLast = i === breadcrumb.length - 1;
              return (
                <span key={i} className="flex items-center gap-1">
                  {i > 0 && (
                    <ChevronRight
                      size={11}
                      color="var(--text-tertiary)"
                    />
                  )}
                  {b.href && !isLast ? (
                    <Link
                      href={b.href}
                      style={{
                        color: "var(--text-tertiary)",
                        textDecoration: "none",
                      }}
                      className="hover:underline"
                    >
                      {b.label}
                    </Link>
                  ) : (
                    <span style={{ color: isLast ? "var(--text-secondary)" : "var(--text-tertiary)" }}>
                      {b.label}
                    </span>
                  )}
                </span>
              );
            })}
          </div>
        )}
        <div className="flex items-baseline gap-2.5 truncate">
          <h1
            className="m-0 truncate"
            style={{
              fontSize: 18,
              fontWeight: 600,
              letterSpacing: -0.2,
            }}
          >
            {title}
          </h1>
          {subtitle && (
            <span
              style={{ fontSize: 12, color: "var(--text-tertiary)" }}
              className="truncate"
            >
              {subtitle}
            </span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2 flex-shrink-0">
        {actions}
        <button
          type="button"
          className="polaris-btn polaris-btn-icon"
          title="Notifications"
          style={{ minHeight: "auto" }}
        >
          <Bell size={15} />
        </button>
        {primaryAction === undefined ? defaultPrimary : primaryAction}
      </div>
    </div>
  );
}
