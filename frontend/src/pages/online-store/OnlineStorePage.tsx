// ============================================================================
// IMS 2.0 - Online Store — module hub (post-cutover)
// ============================================================================
// The landing hub for the e-commerce admin. Every section is LIVE in-app; IMS
// is the single Shopify writer (BVI retired 2026-07-20). Shows live module
// status, per-storefront push posture and per-section counts via
// GET /api/v1/online-store/summary, with a Refresh that re-reads the summary
// AND the sync banner together. Failure states are labeled HONESTLY (RC-E):
// 403 -> "no permission", 404/501 -> "not on this deploy", anything else ->
// "Couldn't load — Retry"; a DB-down / degraded backend renders an amber
// warning instead of fake zero counts. Cards are filtered to the viewer's role
// so no card dead-links into the route guard.

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Store,
  Package,
  Layers,
  Menu as MenuIcon,
  Image as ImageIcon,
  Users,
  ShoppingBag,
  Boxes,
  Activity,
  RefreshCw,
  Loader2,
  CheckCircle2,
  AlertTriangle,
  EyeOff,
  ArrowRight,
  Tag,
  ReceiptText,
} from 'lucide-react';
import { onlineStoreApi, type OnlineStoreSummary } from '../../services/api/onlineStore';
import OnlineStoreSyncBanner from '../../components/online-store/OnlineStoreSyncBanner';
import { useAuth } from '../../context/AuthContext';

type CountKey =
  | 'products'
  | 'variants'
  | 'collections'
  | 'menus'
  | 'images_pending_design'
  | 'customers'
  | 'orders';

interface Section {
  key: string;
  title: string;
  blurb: string;
  icon: typeof Package;
  phase: string;
  /** Which summary count (if any) to surface as a live stat on this card. */
  countKey?: CountKey;
  countLabel?: string;
  /** When set, the section is LIVE: the card becomes a link to this in-app
   *  route and shows an "Open" CTA instead of the "Coming soon" pill. */
  href?: string;
  /** When set, the card is shown ONLY to these roles — mirror of the route's
   *  ProtectedRoute allowedRoles in App.tsx (OS-035: no card may dead-link a
   *  role into the /unauthorized bounce). Omitted = every hub role sees it. */
  allowedRoles?: string[];
}

// The Online Store sections, ordered by the blueprint's original phase roadmap.
// Every section is LIVE (href set); "phase" is kept as provenance only. The
// "Coming soon" branch below is retained defensively for any future section
// added without an href.
const SECTIONS: Section[] = [
  {
    key: 'products',
    title: 'Products / PIM',
    blurb:
      'The product catalog as it appears online — titles, SEO, theme, and the bridged variant tier (color/size) mapped to physical stock.',
    icon: Package,
    phase: 'Phase 1',
    countKey: 'products',
    countLabel: 'products',
    // Phase 1 shipped: the read-only Products / PIM list is live in-app.
    href: '/online-store/products',
  },
  {
    key: 'collections',
    title: 'Collections',
    blurb:
      'Manual and smart (rule-based) collections with auto-collection lineage by brand, category and attribute — plus SEO and banners.',
    icon: Layers,
    phase: 'Phase 2',
    countKey: 'collections',
    countLabel: 'collections',
    // Phase 2 shipped: the Collections editor is live in-app.
    href: '/online-store/collections',
  },
  {
    key: 'discount-rules',
    title: 'Discount rules',
    blurb:
      'Automatic online pricing by category, brand and sub-brand — e.g. "Ray-Ban frames → 20% off". Sets the website price only; in-store POS pricing is untouched.',
    icon: Tag,
    phase: 'Pricing',
    // Live: the online discount-rule engine + editor is in-app (rebuild of BVI).
    href: '/online-store/discount-rules',
    // Pricing surface — DESIGN_MANAGER is deliberately excluded (App.tsx route).
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'],
  },
  {
    key: 'menus',
    title: 'Mega-menu editor',
    blurb:
      'Visual editor for the storefront navigation tree — nested items, thumbnails, badges and pin-to-top, matching the live mega-menu.',
    icon: MenuIcon,
    phase: 'Phase 3',
    countKey: 'menus',
    countLabel: 'menus',
    // Phase 3 shipped: the Mega-menu editor is live in-app.
    href: '/online-store/menus',
  },
  {
    key: 'images',
    title: 'Image design queue',
    blurb:
      'The design team workflow: raw photo to edited hero image, role-gated, with per-image design status — entirely inside IMS.',
    icon: ImageIcon,
    phase: 'Phase 4',
    countKey: 'images_pending_design',
    countLabel: 'awaiting design',
    // Phase 4 shipped: the image design queue is live in-app.
    href: '/online-store/images',
  },
  {
    key: 'customers',
    title: 'Customers',
    blurb:
      'Online shoppers joined to the unified IMS customer record by phone/email, carrying their Shopify customer id.',
    icon: Users,
    phase: 'Phase 3',
    countKey: 'customers',
    countLabel: 'customers',
    // Phase 3 shipped: the read-only online Customers list is live in-app.
    href: '/online-store/customers',
  },
  {
    key: 'orders',
    title: 'Orders',
    blurb:
      'Online orders flowing into the IMS books as they happen — customer upsert and stock decrement, tagged channel "online".',
    icon: ShoppingBag,
    phase: 'Phase 3',
    countKey: 'orders',
    countLabel: 'online orders',
    // Phase 3b shipped: the Online orders view is live in-app.
    href: '/online-store/orders',
  },
  {
    key: 'refund-reviews',
    title: 'Refund reviews',
    blurb:
      'Online refunds land here as proposed GST credit notes — confirm to post the credit note and restock, or reject. Nothing hits the books until an accountant confirms.',
    icon: ReceiptText,
    phase: 'Finance',
    // Live: the accountant consumer for the Shopify refund review queue.
    href: '/online-store/refund-reviews',
    // Accountant surface — CATALOG/DESIGN managers are bounced by the route.
    allowedRoles: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'],
  },
  {
    key: 'stock-tally',
    title: 'Stock tally',
    blurb:
      'Reconciles online-listed quantity against real on-hand so you never sell the same unit twice — a conservative, buffered allocation.',
    icon: Boxes,
    phase: 'Phase 5',
    // Phase 5 shipped: the read-only stock-tally reconciliation is live in-app.
    href: '/online-store/stock-tally',
  },
  {
    key: 'store-health',
    title: 'Store health',
    blurb:
      'Orphan SKUs, attribute coverage and barcode-match status — the readiness checks before any product goes live online.',
    icon: Activity,
    phase: 'Phase 5',
    // Phase 5 shipped: the read-only Store health readiness dashboard is live in-app.
    href: '/online-store/store-health',
  },
  {
    key: 'shopify-sync',
    title: 'Shopify sync',
    blurb:
      'The single-writer push of products, collections, menus and inventory to Shopify — armed only at the final, owner-approved cutover.',
    icon: RefreshCw,
    phase: 'Phase 6',
    // Phase 6 shipped: the Shopify sync control panel (status + dry-run) is live
    // in-app. The live push itself stays owner-armed behind the backend gates.
    href: '/online-store/shopify',
  },
];

function fmtCount(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  try {
    return n.toLocaleString('en-IN');
  } catch {
    return String(n);
  }
}

export default function OnlineStorePage() {
  const { user } = useAuth();
  const [summary, setSummary] = useState<OnlineStoreSummary | null>(null);
  const [loading, setLoading] = useState(true);
  // Bumped by the Refresh button: re-runs the summary fetch AND (via the
  // refreshKey prop) the sync banner's posture read together (OS-051), so the
  // two posture indicators on this screen can never drift apart.
  const [refreshKey, setRefreshKey] = useState(0);

  const loadSummary = useCallback(async () => {
    setLoading(true);
    try {
      const s = await onlineStoreApi.getSummary();
      setSummary(s);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSummary();
  }, [loadSummary, refreshKey]);

  const counts = summary?.counts ?? {};
  const available = summary?.available ?? false;
  // Why the summary read failed (only meaningful when !available) — RC-E.
  const failure = !loading && summary && !available ? (summary.reason ?? 'unavailable') : null;
  // Backend answered but its DB is unreachable -> every count is a fail-soft
  // zero, NOT business truth (OS-021).
  const dbDown = available && summary?.db_connected === false;
  const degraded = available && !dbDown && !!summary?.degraded;
  const showCounts = available && !dbDown;
  const pe = summary?.products_ecom ?? null;
  const storefronts = summary?.storefronts ?? null;

  // OS-035: only render cards whose target route admits this viewer's roles.
  const roles: string[] = (user?.roles as string[] | undefined) ?? [];
  const visibleSections = SECTIONS.filter(
    (s) => !s.allowedRoles || s.allowedRoles.some((r) => roles.includes(r)),
  );

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          <Store className="w-5 h-5" /> Online Store
        </h1>
        <button
          type="button"
          onClick={() => setRefreshKey((k) => k + 1)}
          className="btn-outline inline-flex items-center gap-1.5 text-sm"
          title="Reload the module status, counts and push posture"
        >
          <RefreshCw className={'w-4 h-4 ' + (loading ? 'animate-spin' : '')} /> Refresh
        </button>
      </div>
      <p className="text-sm text-gray-500 mb-4 max-w-3xl">
        One place to run the online storefronts — catalog, collections, navigation, the design
        workflow, orders and refunds. IMS is the single Shopify writer; the storefronts themselves
        keep running on Shopify.
      </p>

      {/* Shopify publish (DARK / LIVE) banner — states the push posture up front.
          Driven by the same Refresh as the summary so the two never drift. */}
      <OnlineStoreSyncBanner className="mb-4" refreshKey={refreshKey} />

      {/* Module status banner */}
      <div className="mb-6 rounded-xl border border-gray-200 bg-white p-4">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-gray-500">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading module status…
          </div>
        ) : failure === 'forbidden' ? (
          // RC-E: a 403 is a PERMISSION state, never "coming soon".
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-gray-100 text-gray-700 border border-gray-200 px-2.5 py-1 text-xs font-medium">
              <EyeOff className="w-3.5 h-3.5" /> No permission for this view
            </span>
            <span className="text-xs text-gray-500">
              Your role can't read the Online Store module status. The module itself is running.
            </span>
          </div>
        ) : failure === 'error' ? (
          // RC-E: a real failure gets an honest error + Retry, not a fake state.
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-red-100 text-red-800 border border-red-200 px-2.5 py-1 text-xs font-medium">
              <AlertTriangle className="w-3.5 h-3.5" /> Couldn't load module status
            </span>
            <span className="text-xs text-gray-500">
              The status read failed — counts and posture are unknown right now.
            </span>
            <button
              type="button"
              onClick={() => setRefreshKey((k) => k + 1)}
              className="inline-flex items-center gap-1 text-xs font-medium text-red-700 hover:text-red-900"
            >
              <RefreshCw className="w-3.5 h-3.5" /> Retry
            </button>
          </div>
        ) : failure === 'unavailable' ? (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 text-blue-800 border border-blue-200 px-2.5 py-1 text-xs font-medium">
              <Loader2 className="w-3.5 h-3.5" /> Module summary not served by this deploy
            </span>
            <span className="text-xs text-gray-400">
              Live counts appear once the module backend is deployed.
            </span>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium border bg-green-100 text-green-800 border-green-200">
                <CheckCircle2 className="w-3.5 h-3.5" /> Module connected
              </span>
              <span className="text-xs text-gray-500">
                Status: {summary?.status || 'unknown'}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span
                className={
                  'inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium border ' +
                  (summary?.shopify_writes_enabled
                    ? 'bg-amber-100 text-amber-800 border-amber-200'
                    : 'bg-gray-100 text-gray-600 border-gray-200')
                }
                title="Whether IMS is the live Shopify writer yet (the cutover kill-switch)"
              >
                Shopify push: {summary?.shopify_writes_enabled ? 'LIVE' : 'OFF'}
              </span>
            </div>
            {/* OS-034: per-storefront posture (names from the storefront registry). */}
            {storefronts && storefronts.length > 0 && (
              <div className="flex items-center gap-1.5 flex-wrap">
                {storefronts.map((sf) => (
                  <span
                    key={sf.storefront_id || sf.name}
                    className={
                      'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium border whitespace-nowrap ' +
                      (sf.is_live
                        ? 'bg-green-100 text-green-800 border-green-200'
                        : 'bg-gray-100 text-gray-600 border-gray-200')
                    }
                    title={
                      sf.is_live
                        ? `${sf.name}: pushes write to this live storefront`
                        : `${sf.name}: DARK — pushes to this storefront are dry-run only`
                    }
                  >
                    {sf.name}: {sf.is_live ? 'LIVE' : 'DARK'}
                  </span>
                ))}
              </div>
            )}
            {summary?.message && (
              <span className="text-xs text-gray-500">{summary.message}</span>
            )}
          </div>
        )}
        {/* OS-021: DB-down / degraded — never present fail-soft zeros as truth. */}
        {dbDown && (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2">
            <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
            <p className="text-xs text-amber-800">
              <span className="font-semibold">Database unreachable — figures unavailable.</span>{' '}
              The module answered but couldn't reach its database, so no counts are shown (zeros
              here would be fake, not real business numbers). Refresh to try again.
            </p>
          </div>
        )}
        {degraded && (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2">
            <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
            <p className="text-xs text-amber-800">
              <span className="font-semibold">Some figures couldn't be loaded.</span> One or more
              counts failed mid-request — the numbers below may be incomplete. Refresh to try again.
            </p>
          </div>
        )}
      </div>

      {/* Section cards (role-filtered — OS-035) */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {visibleSections.map((section) => {
          const SectionIcon = section.icon;
          const rawCount = section.countKey
            ? (counts as Record<string, number | null | undefined>)[section.countKey]
            : undefined;
          const showCount = showCounts && section.countKey;
          const isLive = !!section.href;
          // OS-022: the owner's draft/published decision belongs on the hub —
          // surface the staged-product breakdown on the Products card itself.
          const productChips = section.key === 'products' && showCounts && pe ? (
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <span
                className="inline-flex items-center rounded-full bg-green-100 text-green-800 border border-green-200 px-2 py-0.5 text-[11px] font-medium whitespace-nowrap"
                title="Staged products that are PUBLISHED (visible on the website)"
              >
                {fmtCount(pe.published)} published
              </span>
              <span
                className="inline-flex items-center rounded-full bg-amber-100 text-amber-800 border border-amber-200 px-2 py-0.5 text-[11px] font-medium whitespace-nowrap"
                title="Staged products still in DRAFT — not visible to shoppers until the owner publishes them"
              >
                {fmtCount(pe.draft)} drafts
              </span>
              <span
                className="inline-flex items-center rounded-full bg-gray-100 text-gray-600 border border-gray-200 px-2 py-0.5 text-[11px] font-medium whitespace-nowrap"
                title="Staged products with no images yet (text-only)"
              >
                {fmtCount(pe.text_only)} text-only
              </span>
            </div>
          ) : null;

          // Shared inner content (icon, title, blurb, footer). For LIVE
          // sections the footer shows an "Open" CTA; otherwise the count or a
          // "Coming soon" pill.
          const inner = (
            <>
              <div className="flex items-start justify-between gap-2 mb-2">
                <div className="flex items-center gap-2">
                  <span
                    className={
                      'inline-flex items-center justify-center w-8 h-8 rounded-lg ' +
                      (isLive ? 'bg-bv-red-50 text-bv-red-600' : 'bg-gray-100 text-gray-700')
                    }
                  >
                    <SectionIcon className="w-4 h-4" />
                  </span>
                  <h2 className="text-sm font-semibold text-gray-900">{section.title}</h2>
                </div>
                {isLive ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-green-100 text-green-800 border border-green-200 px-2 py-0.5 text-[11px] font-medium whitespace-nowrap">
                    <CheckCircle2 className="w-3 h-3" /> Live
                  </span>
                ) : (
                  <span className="inline-flex items-center rounded-full bg-gray-100 text-gray-500 border border-gray-200 px-2 py-0.5 text-[11px] font-medium whitespace-nowrap">
                    {section.phase}
                  </span>
                )}
              </div>
              <p className="text-xs leading-relaxed text-gray-500 flex-1">{section.blurb}</p>
              {productChips}
              <div className="mt-3 flex items-center justify-between">
                {showCount ? (
                  <span className="text-xs text-gray-700">
                    <span className="text-sm font-semibold text-gray-900">{fmtCount(rawCount)}</span>{' '}
                    {section.countLabel}
                  </span>
                ) : (
                  <span />
                )}
                {isLive ? (
                  <span className="inline-flex items-center gap-1 text-xs font-medium text-bv-red-600">
                    Open <ArrowRight className="w-3.5 h-3.5" />
                  </span>
                ) : !showCount ? (
                  <span className="inline-flex items-center rounded-full bg-blue-50 text-blue-700 border border-blue-200 px-2 py-0.5 text-[11px] font-medium">
                    Coming soon
                  </span>
                ) : null}
              </div>
            </>
          );

          if (isLive && section.href) {
            return (
              <Link
                key={section.key}
                to={section.href}
                className="rounded-xl border border-gray-200 bg-white p-4 flex flex-col hover:border-bv-red-300 hover:shadow-sm transition-colors focus:outline-none focus:ring-2 focus:ring-bv-red-200"
              >
                {inner}
              </Link>
            );
          }

          return (
            <div
              key={section.key}
              className="rounded-xl border border-gray-200 bg-white p-4 flex flex-col"
            >
              {inner}
            </div>
          );
        })}
      </div>

      <p className="mt-6 text-xs text-gray-400">
        Online Store module · all sections are live. IMS is the single Shopify writer; the current
        publishing posture is shown in the banner above.
      </p>
    </div>
  );
}
