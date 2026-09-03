// ============================================================================
// IMS 2.0 - Inventory module layout
// ============================================================================
// Wave 2 split: the old InventoryPage held NINETEEN sections in useState -
// only the default ledger was addressable, and the menu linked three screens
// out of the whole module. Each section is now a real page with its own URL
// (/inventory/stock, /inventory/quarantine, ...), and two former tabs land on
// the standalone pages that already implemented them properly:
// /inventory/audit (stock count) and /inventory/power-grid (lens grid).
//
// This layout keeps what was ALWAYS on screen - the editorial header, the
// store picker, the stat strip and the two-level section nav - and each
// section page owns its own widgets and data. The store being viewed lives in
// layout state (like the Reports period picker) and sections read it off the
// outlet context, so switching sections never resets it.

import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Outlet, useLocation, useNavigate, useOutletContext } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowRightLeft,
  BarChart3,
  Boxes,
  Clock,
  Eye,
  Globe,
  Hash,
  LayoutGrid,
  Loader2,
  Package,
  Plus,
  RefreshCw,
  ShoppingCart,
  TrendingDown,
  Barcode,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { StockTransferModal } from '../../components/inventory/StockTransferModal';
// ONLINE (stockless / pooled-fulfilment) store detection - single testable
// source of truth (see storeMode.ts + its unit test).
import { isOnlineStore, isOnlineStoreId } from './storeMode';
import { canManageInventory } from './inventoryRoles';
import {
  CATEGORIES,
  getOnlineFor,
  onlineStatusIds,
  useFixturesMap,
  useInventoryStores,
  useLowStock,
  useOnlineStatus,
  useOnlineSummary,
  useQuarantineUnlabeled,
  useStock,
} from './inventoryQueries';

export interface InventoryContext {
  /** The store being viewed (layout store picker; follows the topbar store). */
  storeId: string;
  /** True when the viewed store is an ONLINE (stockless, pooled) store. */
  isOnlineStoreView: boolean;
  stores: { id: string; name: string; store_type?: string }[];
}

/** Section pages read the viewed store + mode the layout resolved. */
export function useInventoryContext() {
  return useOutletContext<InventoryContext>();
}

interface NavEntry {
  path: string;
  label: string;
  icon: LucideIcon;
  /** Manager-ladder only (audit + power grid live behind tighter routes). */
  manageOnly?: boolean;
  count?: number;
}

export function InventoryLayout() {
  const { user, hasRole } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const queryClient = useQueryClient();

  const [showTransferModal, setShowTransferModal] = useState(false);

  // Role-based permissions (each list used exactly once, so it lives here).
  const canTransfer = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER']);
  const canQuarantine = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']);
  const canAddProduct = hasRole(['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER']);
  const allStoreAccess = hasRole(['SUPERADMIN', 'ADMIN', 'AREA_MANAGER']);
  const canManage = canManageInventory(user?.roles);

  // Viewed store: defaults to the global active store and follows it when it
  // is switched elsewhere (topbar), exactly as the old page did.
  const [storeFilter, setStoreFilter] = useState<string>(user?.activeStoreId || '');
  useEffect(() => {
    if (user?.activeStoreId) setStoreFilter(user.activeStoreId);
  }, [user?.activeStoreId]);
  const storeId = storeFilter || user?.activeStoreId || '';

  const storesQ = useInventoryStores(allStoreAccess, user?.storeIds);
  const stores = storesQ.data ?? [];
  const activeStore = stores.find((s) => s.id === storeId);
  const isOnlineStoreView = isOnlineStore(activeStore) || isOnlineStoreId(storeId);

  // Stat-strip data. The strip was on every tab of the old page, so the
  // layout owns these queries; the Stock ledger page hits the same cache.
  const stockQ = useStock(storeId || undefined);
  const inventory = stockQ.data ?? [];
  const lowStockQ = useLowStock(storeId || undefined);
  const lowStockCount = (lowStockQ.data ?? []).length;
  const onlineStatusQ = useOnlineStatus(onlineStatusIds(inventory));
  const fixturesQ = useFixturesMap(storeId || undefined);
  const fixtureCount = Object.keys(fixturesQ.data ?? {}).length;
  const quarantineQ = useQuarantineUnlabeled(storeId || undefined, canQuarantine);
  const quarantineUnlabeled = quarantineQ.data ?? 0;
  const onlineSummaryQ = useOnlineSummary(isOnlineStoreView);
  const onlineSummary = isOnlineStoreView ? onlineSummaryQ.data ?? null : null;

  const totalSKUs = inventory.length;
  const totalValue = inventory.reduce(
    (sum, item) => sum + ((item.offerPrice || item.mrp || 0) * (item.stock || 0)), 0);
  const onlineCount = inventory.reduce(
    (n, i) => (getOnlineFor(i, onlineStatusQ.data)?.online ? n + 1 : n), 0);

  const isLoading = stockQ.isFetching;
  const error = stockQ.isError ? 'Failed to load inventory. Please try again.' : null;

  const [refreshing, setRefreshing] = useState(false);
  const refresh = async () => {
    setRefreshing(true);
    try {
      // Refetches every ACTIVE 'inventory' query - the strip plus whatever
      // the open section has mounted, and nothing it has not.
      await queryClient.invalidateQueries({ queryKey: ['inventory'] });
    } finally {
      setRefreshing(false);
    }
  };

  // Warm the sibling section chunks once the browser is idle, so the FIRST
  // click on any section renders without the lazy-chunk spinner (Wave 1
  // template). Vite dedupes these against the route-level lazy() imports.
  useEffect(() => {
    const idle: (cb: () => void) => void =
      'requestIdleCallback' in window
        ? (cb) => (window as Window & { requestIdleCallback: (cb: () => void) => void }).requestIdleCallback(cb)
        : (cb) => { setTimeout(cb, 1500); };
    idle(() => {
      void import('./InventoryStockPage');
      void import('./InventoryMovementsPage');
      void import('./InventorySections');
    });
  }, []);

  // Two-level nav, verbatim the old 5 groups. The two manage-gated entries
  // navigate to the standalone routes that already implement those screens.
  const baseGroups: Array<{ id: string; label: string; icon: LucideIcon; entries: NavEntry[] }> = [
    {
      id: 'catalog', label: 'Catalog', icon: Package,
      entries: [
        { path: '/inventory/stock', label: 'Stock ledger', icon: Package, count: totalSKUs },
        { path: '/inventory/display-layout', label: 'Display layout', icon: LayoutGrid, count: fixtureCount || undefined },
      ],
    },
    {
      id: 'health', label: 'Stock health', icon: AlertTriangle,
      entries: [
        { path: '/inventory/low-stock', label: 'Low stock', icon: AlertTriangle, count: lowStockCount },
        { path: '/inventory/non-moving', label: 'Non-moving', icon: TrendingDown },
        { path: '/inventory/aging', label: 'Stock aging', icon: Clock },
        { path: '/inventory/alerts', label: 'Alerts', icon: AlertTriangle },
      ],
    },
    {
      id: 'ops', label: 'Operations', icon: ShoppingCart,
      entries: [
        { path: '/inventory/reorders', label: 'Reorders', icon: ShoppingCart },
        { path: '/inventory/transfers', label: 'Transfers', icon: ArrowRightLeft },
        { path: '/inventory/movements', label: 'Movements', icon: Eye },
        { path: '/inventory/rebalance', label: 'Rebalance', icon: ArrowRightLeft },
        { path: '/inventory/audit', label: 'Stock count', icon: Barcode, manageOnly: true },
        { path: '/inventory/quarantine', label: 'Quarantine', icon: AlertTriangle, count: quarantineUnlabeled || undefined },
      ],
    },
    {
      id: 'optical', label: 'Optical', icon: Eye,
      entries: [
        { path: '/inventory/serial-numbers', label: 'Serial numbers', icon: Hash },
        { path: '/inventory/contact-lens', label: 'Contact lens', icon: Eye },
        { path: '/inventory/power-grid', label: 'Lens power grid', icon: BarChart3, manageOnly: true },
      ],
    },
    {
      id: 'insights', label: 'Insights', icon: BarChart3,
      entries: [
        { path: '/inventory/sell-through', label: 'Sell-through', icon: TrendingDown },
        { path: '/inventory/overstock', label: 'Overstock', icon: Boxes },
        { path: '/inventory/brand-insights', label: 'Brands', icon: BarChart3 },
        { path: '/inventory/collection-insights', label: 'Collections', icon: Boxes },
      ],
    },
  ];
  const groups = baseGroups.map((g) => ({
    ...g,
    entries: g.entries.filter((e) => !e.manageOnly || canManage),
  }));

  const activeGroup =
    groups.find((g) => g.entries.some((e) => e.path === pathname)) ?? groups[0];
  const activeEntry = activeGroup.entries.find((e) => e.path === pathname);

  const ctx: InventoryContext = { storeId, isOnlineStoreView, stores };

  // ONLINE KPI value: while loading -> "..."; role can't read the counts
  // (available:false) -> a "View" link, never a misleading 0.
  const onlineCounts = onlineSummary?.counts || {};
  const renderOnlineValue = (n?: number | null) => {
    if (onlineSummary == null) return <div className="v">…</div>;
    if (!onlineSummary.available) {
      return (
        <button
          type="button"
          onClick={() => navigate('/online-store')}
          className="v"
          style={{ fontSize: 16, color: 'var(--bv)', background: 'none', border: 0, padding: 0, cursor: 'pointer' }}
        >
          View →
        </button>
      );
    }
    return <div className="v">{(n ?? 0).toLocaleString('en-IN')}</div>;
  };

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow mb-1.5">Inventory</div>
          <h1>What's on the floor.</h1>
          <div className="hint">Live stock by SKU across {CATEGORIES.length} categories · cycle count · transfers · non-moving flags.</div>
        </div>
        <div className="row gap-2 flex-wrap">
          {stores.length > 1 && (
            <select
              aria-label="Store"
              value={storeId}
              onChange={(e) => setStoreFilter(e.target.value)}
              className="input-field text-sm py-1.5 w-48"
            >
              {stores.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}{s.id === user?.activeStoreId ? ' (current)' : ''}
                </option>
              ))}
            </select>
          )}
          <button onClick={refresh} disabled={isLoading || refreshing} className="btn sm">
            {isLoading || refreshing ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Refresh
          </button>
          {isOnlineStoreView ? (
            // ONLINE store: the physical actions don't apply - the primary CTA
            // is the Online Store module itself.
            <button onClick={() => navigate('/online-store')} className="btn sm primary">
              <Globe className="w-4 h-4" /> Open online store
            </button>
          ) : (
            <>
              {canTransfer && (
                <button onClick={() => setShowTransferModal(true)} className="btn sm">
                  <ArrowRightLeft className="w-4 h-4" /> New transfer
                </button>
              )}
              {canAddProduct && (
                <button onClick={() => navigate('/catalog/add')} className="btn sm primary">
                  <Plus className="w-4 h-4" /> Add product
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="s-section" style={{ padding: 12, borderColor: 'var(--err-50)', background: 'var(--err-50)', display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
          <AlertTriangle className="w-5 h-5" style={{ color: 'var(--err)' }} />
          <span style={{ color: 'var(--err)' }}>{error}</span>
          <button onClick={refresh} className="btn sm ml-auto">Retry</button>
        </div>
      )}

      {isOnlineStoreView ? (
        <>
          {/* ONLINE store explainer - an online store owns no stock of its own;
              it sells from every shop's pooled stock (reserve-on-order). */}
          <div
            className="s-section"
            style={{ padding: '12px 14px', display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 14, background: 'var(--bv-50)', borderColor: 'var(--line)' }}
          >
            <Globe className="w-5 h-5" style={{ color: 'var(--bv)', flexShrink: 0, marginTop: 2 }} strokeWidth={1.8} />
            <div style={{ fontSize: 13, color: 'var(--ink-2)', lineHeight: 1.5 }}>
              <strong style={{ color: 'var(--ink)' }}>This online store sells from all shops' pooled stock — it holds no stock of its own.</strong>{' '}
              Physical figures like stock value, low-stock and floor zones don't apply here; the numbers below reflect what's live on the website.{' '}
              <button
                type="button"
                onClick={() => navigate('/online-store')}
                style={{ color: 'var(--bv)', background: 'none', border: 0, padding: 0, cursor: 'pointer', fontWeight: 600 }}
              >
                Manage the online store →
              </button>
            </div>
          </div>

          {/* ONLINE stat strip - website-meaningful counts. */}
          <div className="stat-strip">
            <div>
              <div className="l">Synced to website</div>
              {renderOnlineValue(onlineCounts.products)}
              <div className="d">products live on the store</div>
            </div>
            <div>
              <div className="l">Collections</div>
              {renderOnlineValue(onlineCounts.collections)}
              <div className="d">merchandising sets</div>
            </div>
            <div>
              <div className="l">Online orders</div>
              {renderOnlineValue(onlineCounts.orders)}
              <div className="d">channel = online</div>
            </div>
            <div>
              <div className="l">Online customers</div>
              {renderOnlineValue(onlineCounts.customers)}
              <div className="d">joined from Shopify</div>
            </div>
            <div>
              <div className="l">Images pending</div>
              {renderOnlineValue(onlineCounts.images_pending_design)}
              <div className="d">awaiting design work</div>
            </div>
          </div>
        </>
      ) : (
        /* 6-cell stat strip (incl. Online) */
        <div className="stat-strip stat-strip-6">
          <div>
            <div className="l">Total SKUs</div>
            <div className="v">{totalSKUs.toLocaleString('en-IN')}</div>
            <div className="d">across {CATEGORIES.length} categories</div>
          </div>
          <div>
            <div className="l">Stock value</div>
            <div className="v">₹ {(totalValue / 100000).toFixed(1)}L</div>
            <div className="d">total landed inventory</div>
          </div>
          <div>
            <div className="l">Low stock</div>
            <div className="v" style={{ color: lowStockCount > 0 ? 'var(--err)' : 'var(--ink)' }}>{lowStockCount}</div>
            <div className={'d ' + (lowStockCount > 0 ? 'bad' : 'good')}>
              {lowStockCount > 0 ? 'needs reorder' : 'all above reorder pt'}
            </div>
          </div>
          <div>
            <div className="l">Online</div>
            <div className="v" style={{ color: onlineCount > 0 ? 'var(--ok, #059669)' : 'var(--ink)' }}>{onlineCount}</div>
            <div className="d">{onlineCount > 0 ? 'listed in Shopify' : 'none synced online'}</div>
          </div>
          <div>
            <div className="l">Categories</div>
            <div className="v">{CATEGORIES.length}</div>
            <div className="d">incl. lenses, frames, CL</div>
          </div>
          <div>
            <div className="l">View</div>
            <div className="v" style={{ fontSize: 22 }}>{activeEntry?.label ?? '—'}</div>
            <div className="d">active section</div>
          </div>
        </div>
      )}

      {/* Primary nav groups (5) - each click lands on the group's first
          section URL. Count badges live only on the sub-entries. */}
      <div className="inv-tabs">
        {groups.map((g) => {
          const GIcon = g.icon;
          return (
            <button
              key={g.id}
              onClick={() => {
                if (g.id === activeGroup.id) return;
                navigate(g.entries[0].path);
              }}
              className={activeGroup.id === g.id ? 'on' : ''}
            >
              <GIcon className="w-4 h-4" />
              {g.label}
            </button>
          );
        })}
      </div>

      {/* Sub-nav for the active group - each entry is a real URL now, so every
          section is linkable and bookmarkable.
          ponytail: still <button>, not <NavLink>, because index.css styles
          `.inv-tabs button` by element - an <a> would render unstyled and
          index.css is outside this PR's blast radius. */}
      {activeGroup.entries.length > 1 && (
        <div className="inv-tabs -mt-1.5 pl-1 gap-3.5">
          {activeGroup.entries.map((e) => {
            const EIcon = e.icon;
            return (
              <button
                key={e.path}
                onClick={() => navigate(e.path)}
                className={pathname === e.path ? 'on' : ''}
                style={{ fontSize: 13 }}
              >
                <EIcon className="w-3.5 h-3.5" />
                {e.label}
                {typeof e.count === 'number' && <span className="count">· {e.count}</span>}
              </button>
            );
          })}
        </div>
      )}

      <Outlet context={ctx} />

      {/* Stock Transfer Modal (header action - available from every section) */}
      <StockTransferModal
        isOpen={showTransferModal}
        onClose={() => setShowTransferModal(false)}
        onTransferCreated={() => {
          setShowTransferModal(false);
          void queryClient.invalidateQueries({ queryKey: ['inventory'] });
        }}
      />
    </div>
  );
}

export default InventoryLayout;
