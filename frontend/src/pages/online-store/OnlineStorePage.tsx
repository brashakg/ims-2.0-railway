// ============================================================================
// IMS 2.0 - Online Store (e-commerce / BVI merge) — module shell
// ============================================================================
// Phase 1 FOUNDATION only (see docs/reference/BVI_MERGE_PLAN.md §A/§B-Phase 1).
// This is the landing shell for the consolidated e-commerce admin that is being
// rebuilt INTO IMS (retiring the separate BVI Next.js app). It lists the planned
// sections as "coming soon" cards and shows live module status + counts via
// GET /api/v1/online-store/summary (graceful fallback if that endpoint 404s in a
// stale deploy). The actual feature screens (collections editor, mega-menu,
// image design queue, Shopify push, …) land in later phases — NOT here.

import { useEffect, useState } from 'react';
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
  ExternalLink,
  Loader2,
  CheckCircle2,
  ArrowRight,
} from 'lucide-react';
import { onlineStoreApi, type OnlineStoreSummary } from '../../services/api/onlineStore';
import { ecommerceSsoApi } from '../../services/api/ecommerceSso';

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
}

// The planned Online Store sections, ordered by the blueprint's phase roadmap.
// "phase" mirrors BVI_MERGE_PLAN.md §B so the owner can see what ships when.
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
  },
  {
    key: 'stock-tally',
    title: 'Stock tally',
    blurb:
      'Reconciles online-listed quantity against real on-hand so you never sell the same unit twice — a conservative, buffered allocation.',
    icon: Boxes,
    phase: 'Phase 5',
  },
  {
    key: 'store-health',
    title: 'Store health',
    blurb:
      'Orphan SKUs, attribute coverage and barcode-match status — the readiness checks before any product goes live online.',
    icon: Activity,
    phase: 'Phase 5',
  },
  {
    key: 'shopify-sync',
    title: 'Shopify sync',
    blurb:
      'The single-writer push of products, collections, menus and inventory to Shopify — armed only at the final, owner-approved cutover.',
    icon: RefreshCw,
    phase: 'Phase 6',
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
  const [summary, setSummary] = useState<OnlineStoreSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [openingAdmin, setOpeningAdmin] = useState(false);

  // External admin URL (current BVI app) — kept reachable during the
  // strangler-fig transition until the in-app screens fully replace it.
  const ecommerceUrl =
    (import.meta.env.VITE_ECOMMERCE_URL as string | undefined) || 'https://uniparallel.com';

  useEffect(() => {
    let alive = true;
    onlineStoreApi
      .getSummary()
      .then((s) => {
        if (alive) setSummary(s);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  // SSO handoff to the current external admin; falls back to a plain open.
  const openCurrentAdmin = async () => {
    setOpeningAdmin(true);
    try {
      const r = await ecommerceSsoApi.getUrl();
      window.open(r.url, '_blank', 'noopener,noreferrer');
    } catch {
      window.open(ecommerceUrl, '_blank', 'noopener,noreferrer');
    } finally {
      setOpeningAdmin(false);
    }
  };

  const counts = summary?.counts ?? {};
  const available = summary?.available ?? false;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          <Store className="w-5 h-5" /> Online Store
        </h1>
        <button
          type="button"
          onClick={openCurrentAdmin}
          disabled={openingAdmin}
          className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg px-3 py-1.5 disabled:opacity-60"
          title="Open the current storefront admin in a new tab"
        >
          {openingAdmin ? <Loader2 className="w-4 h-4 animate-spin" /> : <ExternalLink className="w-4 h-4" />}
          Open current admin
        </button>
      </div>
      <p className="text-sm text-gray-500 mb-4 max-w-3xl">
        The e-commerce admin is being rebuilt inside IMS so the catalog, collections, navigation and the
        design workflow all live in one place. Sections below light up as each phase ships. The storefront
        (bettervision.in) keeps running on Shopify throughout — nothing changes for shoppers.
      </p>

      {/* Module status banner */}
      <div className="mb-6 rounded-xl border border-gray-200 bg-white p-4">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-gray-500">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading module status…
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <div className="flex items-center gap-2">
              <span
                className={
                  'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium border ' +
                  (available
                    ? 'bg-green-100 text-green-800 border-green-200'
                    : 'bg-blue-50 text-blue-800 border-blue-200')
                }
              >
                {available ? <CheckCircle2 className="w-3.5 h-3.5" /> : <Loader2 className="w-3.5 h-3.5" />}
                {available ? 'Module connected' : 'Foundation — coming online'}
              </span>
              <span className="text-xs text-gray-500">
                Status: {summary?.status || 'COMING_SOON'}
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
            {summary?.message && (
              <span className="text-xs text-gray-500">{summary.message}</span>
            )}
            {!available && (
              <span className="text-xs text-gray-400">
                Live counts appear once the module backend is deployed.
              </span>
            )}
          </div>
        )}
      </div>

      {/* Section cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {SECTIONS.map((section) => {
          const SectionIcon = section.icon;
          const rawCount = section.countKey
            ? (counts as Record<string, number | null | undefined>)[section.countKey]
            : undefined;
          const showCount = available && section.countKey;
          const isLive = !!section.href;

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
        Online Store module · Phase 1 foundation. See the BVI merge blueprint for the full roadmap.
      </p>
    </div>
  );
}
