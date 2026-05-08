"use client";

// Primitive UI components for the Edit Product redesign.
// Polaris-aligned tokens are scoped to the .ep-shell wrapper (see
// EditProductV2). Components themselves use Tailwind plus the CSS vars
// so they read like Polaris but compose with the existing app styles.
//
// Ported from CLAUDE_CODE_HANDOFF.md §3, §4.

import { ReactNode, ButtonHTMLAttributes, useEffect, useRef, useState } from "react";
import { ChevronLeft, AlertCircle, Check, Loader2, Search, Eye } from "lucide-react";

/* ============================================================
 * Section + Field + Row
 * ============================================================ */

export function Section({
  id,
  title,
  subtitle,
  badge,
  children,
}: {
  id: string;
  title: string;
  subtitle?: string;
  badge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section
      id={`sec-${id}`}
      className="ep-section"
      style={{
        background: "var(--ep-surface)",
        border: "1px solid var(--ep-border)",
        borderRadius: 10,
        scrollMarginTop: 12,
        marginBottom: 16,
        overflow: "hidden",
      }}
    >
      <header
        className="flex items-start justify-between gap-3 px-5 py-4 border-b"
        style={{ borderColor: "var(--ep-border-subdued)" }}
      >
        <div>
          <h2 style={{ fontSize: 13.5, fontWeight: 600, color: "var(--ep-text)", margin: 0 }}>
            {title}
          </h2>
          {subtitle && (
            <p style={{ fontSize: 11.5, color: "var(--ep-text-3)", margin: "2px 0 0" }}>{subtitle}</p>
          )}
        </div>
        {badge && <div className="shrink-0">{badge}</div>}
      </header>
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}

export function Field({
  label,
  hint,
  required,
  children,
  htmlFor,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: ReactNode;
  htmlFor?: string;
}) {
  return (
    <div>
      <label
        htmlFor={htmlFor}
        className="block mb-1"
        style={{ fontSize: 11.5, fontWeight: 600, color: "var(--ep-text-2)" }}
      >
        {label}
        {required && <span style={{ color: "var(--ep-critical)", marginLeft: 3 }}>*</span>}
      </label>
      {children}
      {hint && (
        <p style={{ fontSize: 11, color: "var(--ep-text-3)", marginTop: 4 }}>{hint}</p>
      )}
    </div>
  );
}

export function Row({ children, cols }: { children: ReactNode; cols?: number }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${cols || 2}, minmax(0, 1fr))`,
        gap: 14,
      }}
    >
      {children}
    </div>
  );
}

/* ============================================================
 * Inputs (Polaris-aligned)
 * ============================================================ */

const inputBase: React.CSSProperties = {
  width: "100%",
  height: 32,
  padding: "0 10px",
  border: "1px solid var(--ep-border)",
  borderRadius: 7,
  background: "var(--ep-surface)",
  color: "var(--ep-text)",
  fontSize: 13,
  outline: "none",
};

export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} style={{ ...inputBase, ...(props.style || {}) }} />;
}

export function TextArea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      style={{
        ...inputBase,
        height: "auto",
        minHeight: 64,
        padding: "8px 10px",
        ...(props.style || {}),
      }}
    />
  );
}

export function CurrencyInput({
  value,
  onChange,
  placeholder,
  ...rest
}: {
  value: string | number;
  onChange: (v: string) => void;
  placeholder?: string;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {
  return (
    <div style={{ position: "relative" }}>
      <span
        style={{
          position: "absolute",
          left: 10,
          top: "50%",
          transform: "translateY(-50%)",
          color: "var(--ep-text-3)",
          fontSize: 13,
          fontVariantNumeric: "tabular-nums",
          pointerEvents: "none",
        }}
      >
        ₹
      </span>
      <input
        type="number"
        inputMode="decimal"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        {...rest}
        style={{
          ...inputBase,
          paddingLeft: 22,
          fontVariantNumeric: "tabular-nums",
          ...(rest.style || {}),
        }}
      />
    </div>
  );
}

/* ============================================================
 * SegmentedControl — replaces <select> for ≤4 short options
 * ============================================================ */

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
}: {
  options: Array<{ label: string; value: T }>;
  value: T | null | undefined;
  onChange: (v: T) => void;
}) {
  return (
    <div
      role="tablist"
      style={{
        display: "inline-flex",
        background: "var(--ep-surface-3)",
        border: "1px solid var(--ep-border)",
        borderRadius: 8,
        padding: 2,
        gap: 2,
      }}
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(opt.value)}
            style={{
              padding: "5px 11px",
              minHeight: 26,
              fontSize: 12.5,
              fontWeight: active ? 600 : 500,
              border: 0,
              borderRadius: 6,
              cursor: "pointer",
              background: active ? "var(--ep-surface)" : "transparent",
              color: active ? "var(--ep-text)" : "var(--ep-text-2)",
              boxShadow: active ? "0 1px 2px rgba(0,0,0,0.06)" : "none",
            }}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

/* ============================================================
 * ChipGroup — used for Product Type chips at top of Identity
 * ============================================================ */

export function ChipGroup<T extends string>({
  options,
  value,
  onChange,
  showIcons = true,
}: {
  options: Array<{ label: string; value: T; icon?: string }>;
  value: T | null | undefined;
  onChange: (v: T) => void;
  showIcons?: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              height: 28,
              padding: "0 12px",
              fontSize: 12.5,
              fontWeight: active ? 600 : 500,
              border: `1px solid ${active ? "var(--ep-action)" : "var(--ep-border)"}`,
              borderRadius: 999,
              cursor: "pointer",
              background: active ? "var(--ep-action)" : "var(--ep-surface)",
              color: active ? "var(--ep-action-text)" : "var(--ep-text)",
              transition: "background 80ms",
            }}
          >
            {showIcons && opt.icon && <span style={{ fontSize: 13 }}>{opt.icon}</span>}
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

/* ============================================================
 * StatusPill — Active / Draft / Archived
 * ============================================================ */

export function StatusPill({ status }: { status: string | null | undefined }) {
  const s = (status || "DRAFT").toUpperCase();
  const map: Record<string, { bg: string; fg: string; label: string }> = {
    ACTIVE:    { bg: "var(--ep-success-bg)",  fg: "var(--ep-success-text)",  label: "Active" },
    PUBLISHED: { bg: "var(--ep-success-bg)",  fg: "var(--ep-success-text)",  label: "Published" },
    DRAFT:     { bg: "var(--ep-surface-3)",   fg: "var(--ep-text-2)",        label: "Draft" },
    ARCHIVED:  { bg: "var(--ep-warning-bg)",  fg: "var(--ep-warning-text)",  label: "Archived" },
  };
  const cfg = map[s] || map.DRAFT;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        height: 22,
        padding: "0 9px",
        fontSize: 11,
        fontWeight: 600,
        borderRadius: 999,
        background: cfg.bg,
        color: cfg.fg,
      }}
    >
      {cfg.label}
    </span>
  );
}

/* ============================================================
 * SaveIndicator
 * ============================================================ */

export type SaveState = "idle" | "dirty" | "saving" | "saved" | "error";

export function SaveIndicator({ state, savedAt, error }: { state: SaveState; savedAt?: number; error?: string }) {
  const text = (() => {
    switch (state) {
      case "dirty":
        return "Unsaved changes";
      case "saving":
        return "Saving…";
      case "saved":
        if (!savedAt) return "Saved";
        return `Saved · ${relTime(savedAt)}`;
      case "error":
        return error || "Save failed";
      default:
        return "";
    }
  })();
  const color =
    state === "error"
      ? "var(--ep-critical-text)"
      : state === "saving"
        ? "var(--ep-text-2)"
        : state === "dirty"
          ? "var(--ep-warning-text)"
          : "var(--ep-text-3)";
  const Icon =
    state === "saving" ? Loader2 : state === "saved" ? Check : state === "error" ? AlertCircle : null;
  return (
    <span
      title={state === "error" ? error : undefined}
      style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color }}
    >
      {Icon && <Icon className={state === "saving" ? "w-3.5 h-3.5 animate-spin" : "w-3.5 h-3.5"} />}
      {text}
    </span>
  );
}

function relTime(ts: number): string {
  const s = Math.max(1, Math.floor((Date.now() - ts) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  return `${h}h ago`;
}

/* ============================================================
 * TopBar
 * ============================================================ */

export interface TopBarProps {
  productTitle: string;
  productSku?: string;
  status: string | null | undefined;
  saveState: SaveState;
  savedAt?: number;
  saveError?: string;
  issuesCount: number;
  onIssuesClick: () => void;
  onPreview: () => void;
  onCommandPalette: () => void;
  onSaveDraft: () => void;
  onPublish: () => void;
  publishDisabled: boolean;
  onBack: () => void;
}

export function TopBar({
  productTitle,
  productSku,
  status,
  saveState,
  savedAt,
  saveError,
  issuesCount,
  onIssuesClick,
  onPreview,
  onCommandPalette,
  onSaveDraft,
  onPublish,
  publishDisabled,
  onBack,
}: TopBarProps) {
  return (
    <header
      className="ep-topbar"
      style={{
        position: "sticky",
        top: 0,
        zIndex: 50,
        height: 53,
        background: "var(--ep-surface)",
        borderBottom: "1px solid var(--ep-border)",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "0 20px",
        gap: 12,
      }}
    >
      <div className="flex items-center gap-3 min-w-0">
        <button
          onClick={onBack}
          aria-label="Back to products"
          style={{
            width: 28,
            height: 28,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            border: "1px solid var(--ep-border)",
            borderRadius: 6,
            background: "var(--ep-surface)",
            color: "var(--ep-text-2)",
            cursor: "pointer",
          }}
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
        <div className="min-w-0">
          <div style={{ fontSize: 11, color: "var(--ep-text-3)", lineHeight: 1 }}>
            Products / Edit
          </div>
          <div className="flex items-center gap-2 mt-0.5">
            <h1
              style={{
                fontSize: 16,
                fontWeight: 600,
                color: "var(--ep-text)",
                margin: 0,
                maxWidth: 320,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
              title={productTitle}
            >
              {productTitle || "Untitled product"}
            </h1>
            <StatusPill status={status} />
            {productSku && (
              <span
                style={{
                  fontSize: 11,
                  fontFamily: "ui-monospace, SFMono-Regular, monospace",
                  color: "var(--ep-text-3)",
                  padding: "2px 6px",
                  background: "var(--ep-surface-3)",
                  borderRadius: 4,
                }}
              >
                {productSku}
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2 shrink-0">
        <SaveIndicator state={saveState} savedAt={savedAt} error={saveError} />
        <span style={{ width: 1, height: 20, background: "var(--ep-border)" }} />
        {issuesCount > 0 && (
          <button
            onClick={onIssuesClick}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              height: 30,
              padding: "0 10px",
              border: "1px solid var(--ep-critical)",
              background: "var(--ep-critical-bg)",
              color: "var(--ep-critical-text)",
              borderRadius: 6,
              fontSize: 12,
              fontWeight: 600,
              cursor: "pointer",
            }}
            title="Click to jump to first unresolved issue"
          >
            <AlertCircle className="w-3.5 h-3.5" />
            {issuesCount} issue{issuesCount === 1 ? "" : "s"}
          </button>
        )}
        <button
          onClick={onPreview}
          title="Preview (P)"
          style={btnGhost}
        >
          <Eye className="w-3.5 h-3.5" />
          Preview
        </button>
        <button
          onClick={onCommandPalette}
          title="Command palette (⌘K)"
          style={btnGhost}
        >
          <Search className="w-3.5 h-3.5" />
          ⌘K
        </button>
        <button onClick={onSaveDraft} style={btnSecondary}>
          Save draft
        </button>
        <button
          onClick={onPublish}
          disabled={publishDisabled}
          title={publishDisabled ? "Resolve issues first" : "Publish (⌘↵)"}
          style={{
            ...btnPrimary,
            opacity: publishDisabled ? 0.5 : 1,
            cursor: publishDisabled ? "not-allowed" : "pointer",
          }}
        >
          Publish
          <span style={{ fontSize: 10, marginLeft: 5, opacity: 0.7 }}>⌘↵</span>
        </button>
      </div>
    </header>
  );
}

const btnGhost: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
  height: 30,
  padding: "0 10px",
  border: "1px solid var(--ep-border)",
  background: "var(--ep-surface)",
  color: "var(--ep-text-2)",
  borderRadius: 6,
  fontSize: 12,
  fontWeight: 500,
  cursor: "pointer",
};

const btnSecondary: React.CSSProperties = {
  ...btnGhost,
  color: "var(--ep-text)",
};

const btnPrimary: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 5,
  height: 30,
  padding: "0 12px",
  border: "1px solid var(--ep-action)",
  background: "var(--ep-action)",
  color: "var(--ep-action-text)",
  borderRadius: 6,
  fontSize: 12,
  fontWeight: 600,
};

/* ============================================================
 * SectionNav — left rail with scroll-spy
 * ============================================================ */

export interface NavItem {
  id: string;
  label: string;
  hint?: string;
  /** Number of issues in this section. Renders a red dot when > 0. */
  issues?: number;
}

export function SectionNav({
  items,
  activeId,
  onJump,
}: {
  items: NavItem[];
  activeId: string;
  onJump: (id: string) => void;
}) {
  return (
    <nav
      style={{
        position: "sticky",
        top: 53 + 12,
        alignSelf: "flex-start",
        padding: "12px 0",
      }}
    >
      <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {items.map((it) => {
          const active = it.id === activeId;
          return (
            <li key={it.id}>
              <button
                onClick={() => onJump(it.id)}
                aria-current={active ? "true" : undefined}
                style={{
                  width: "100%",
                  textAlign: "left",
                  padding: "8px 12px",
                  border: 0,
                  background: active ? "var(--ep-surface-3)" : "transparent",
                  borderLeft: `2px solid ${active ? "var(--ep-action)" : "transparent"}`,
                  color: active ? "var(--ep-text)" : "var(--ep-text-2)",
                  fontSize: 12.5,
                  fontWeight: active ? 600 : 500,
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  borderRadius: 0,
                }}
              >
                <span style={{ flex: 1, display: "block" }}>
                  {it.label}
                  {it.hint && (
                    <span style={{ display: "block", fontSize: 11, color: "var(--ep-text-3)", fontWeight: 400 }}>
                      {it.hint}
                    </span>
                  )}
                </span>
                {it.issues && it.issues > 0 ? (
                  <span
                    style={{
                      width: 7,
                      height: 7,
                      borderRadius: 999,
                      background: "var(--ep-critical)",
                    }}
                  />
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** Hook: scroll-spy. Sets active section based on the topmost visible section. */
export function useScrollSpy(ids: string[], offset = 120) {
  const activeRef = useRef<string>(ids[0] || "");
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onScroll = () => {
      const y = window.scrollY + offset;
      let current = ids[0];
      for (const id of ids) {
        const el = document.getElementById(`sec-${id}`);
        if (el && el.offsetTop <= y) current = id;
      }
      if (current !== activeRef.current) {
        activeRef.current = current;
        window.dispatchEvent(new CustomEvent("ep-scroll-spy", { detail: current }));
      }
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => window.removeEventListener("scroll", onScroll);
  }, [ids.join(",")]);
}

/* ============================================================
 * RailGroup + RailRow
 * ============================================================ */

export function RailGroup({
  title,
  badge,
  children,
}: {
  title: string;
  badge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div
      style={{
        background: "var(--ep-surface)",
        border: "1px solid var(--ep-border)",
        borderRadius: 10,
        marginBottom: 12,
        overflow: "hidden",
      }}
    >
      <header
        style={{
          padding: "10px 14px",
          borderBottom: "1px solid var(--ep-border-subdued)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--ep-text-2)", textTransform: "uppercase", letterSpacing: 0.5 }}>
          {title}
        </span>
        {badge}
      </header>
      <div style={{ padding: "8px 0" }}>{children}</div>
    </div>
  );
}

export function RailRow({
  label,
  value,
  variant = "default",
  onClick,
  copyable,
}: {
  label: string;
  value: ReactNode;
  variant?: "default" | "good" | "warn";
  onClick?: () => void;
  copyable?: string;
}) {
  const [copied, setCopied] = useState(false);
  const colorMap = {
    default: "var(--ep-text)",
    good: "var(--ep-success-text)",
    warn: "var(--ep-warning-text)",
  };
  const handleClick = () => {
    if (copyable) {
      navigator.clipboard?.writeText(copyable);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
    onClick?.();
  };
  const interactive = !!(onClick || copyable);
  return (
    <div
      onClick={interactive ? handleClick : undefined}
      style={{
        padding: "6px 14px",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "space-between",
        gap: 12,
        cursor: interactive ? "pointer" : "default",
        fontSize: 12,
      }}
      title={copyable ? "Click to copy" : undefined}
    >
      <span style={{ color: "var(--ep-text-3)", flexShrink: 0 }}>{label}</span>
      <span
        style={{
          color: colorMap[variant],
          fontWeight: variant !== "default" ? 600 : 500,
          textAlign: "right",
          fontVariantNumeric: "tabular-nums",
          maxWidth: "70%",
          wordBreak: "break-word",
        }}
      >
        {copied ? "Copied!" : value}
      </span>
    </div>
  );
}

/* ============================================================
 * Polaris token CSS — injected once per shell
 * ============================================================ */

export const EP_TOKENS_CSS = `
.ep-shell {
  --ep-bg: #F1F1F1;
  --ep-surface: #FFFFFF;
  --ep-surface-2: #FAFBFB;
  --ep-surface-3: #F1F1F1;
  --ep-text: #303030;
  --ep-text-2: #616161;
  --ep-text-3: #8A8A8A;
  --ep-border: #E3E3E3;
  --ep-border-strong: #B5B5B5;
  --ep-border-subdued: #EBEBEB;
  --ep-action: #303030;
  --ep-action-hover: #1A1A1A;
  --ep-action-text: #FFFFFF;
  --ep-success: #29845A;
  --ep-success-bg: #CDFEE1;
  --ep-success-text: #0C5132;
  --ep-critical: #E51C00;
  --ep-critical-bg: #FEE9E8;
  --ep-critical-text: #8E1F0B;
  --ep-warning: #B98900;
  --ep-warning-bg: #FFEAA0;
  --ep-warning-text: #6E5300;
  --ep-info: #2C6ECB;
  --ep-info-bg: #EBF4FE;
  --ep-info-text: #00527C;
  background: var(--ep-bg);
  color: var(--ep-text);
  font-size: 13px;
  line-height: 1.5;
}
.ep-shell input:focus,
.ep-shell textarea:focus,
.ep-shell select:focus {
  border-color: var(--ep-action) !important;
  box-shadow: 0 0 0 3px rgba(48,48,48,0.10) !important;
}
.ep-shell button:hover { filter: brightness(0.96); }
.ep-shell .ep-section h2 { letter-spacing: -0.1px; }
`;
