// ============================================================================
// IMS 2.0 - Global Command Palette (cmdk)
// ----------------------------------------------------------------------------
// Replaces the topbar "Search or jump to" stub (Topbar.tsx:45-53 in the
// previous revision) which just navigated to /customers. Single overlay
// modal, opens on Cmd/Ctrl+K or click of the topbar button. Four result
// sections (Recent / Customers / Orders / Products / Jump-to-page), all
// keyboard-driven via cmdk. Frontend-only - reuses existing search
// endpoints, no backend changes.
//
// Owner instructions are tracked in CLAUDE.md; the original Phase 6.13
// developer flagged the replacement as "Future work: replace with a
// proper command palette (cmdk lib)". This component is that work.
// ============================================================================

import { useEffect, useMemo, useRef, useState } from 'react';
import { Command } from 'cmdk';
import { useNavigate } from 'react-router-dom';
import { customerApi } from '../../services/api/customers';
import { orderApi } from '../../services/api/sales';
import { productApi } from '../../services/api/products';
import { useAuth } from '../../context/AuthContext';
import type { UserRole } from '../../types';
import { Icon } from './Icon';

// ----------------------------------------------------------------------------
// Recent items (localStorage)
// ----------------------------------------------------------------------------

const RECENT_KEY = 'cmdk_recent_v1';
const RECENT_LIMIT = 5;

export interface RecentItem {
  type: 'customer' | 'order' | 'product' | 'page';
  id: string;
  label: string;
  sub?: string;
  route: string;
}

function loadRecent(): RecentItem[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (x: any) =>
          x &&
          typeof x === 'object' &&
          typeof x.type === 'string' &&
          typeof x.id === 'string' &&
          typeof x.label === 'string' &&
          typeof x.route === 'string',
      )
      .slice(0, RECENT_LIMIT) as RecentItem[];
  } catch {
    return [];
  }
}

function pushRecent(next: RecentItem): RecentItem[] {
  const list = loadRecent();
  // LRU: dedupe by (type, id), put the new one in front
  const deduped = list.filter((r) => !(r.type === next.type && r.id === next.id));
  const updated = [next, ...deduped].slice(0, RECENT_LIMIT);
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(updated));
  } catch {
    /* storage disabled or full - in-memory only is fine */
  }
  return updated;
}

// ----------------------------------------------------------------------------
// Static page index (role-filtered). Mirrors the visible items in
// frontend/src/components/shell/Rail.tsx so we never surface a page the
// signed-in user is not allowed to load.
// ----------------------------------------------------------------------------

interface JumpPage {
  label: string;
  route: string;
  requireRoles?: UserRole[];
  hint?: string;
}

const ALL_PAGES: JumpPage[] = [
  { label: 'Hub', route: '/dashboard' },
  { label: 'Notifications', route: '/notifications' },
  { label: 'POS', route: '/pos', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'CASHIER', 'SALES_CASHIER', 'SALES_STAFF'] },
  { label: 'Customers', route: '/customers', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'CASHIER', 'SALES_CASHIER', 'SALES_STAFF'] },
  { label: 'Walkouts', route: '/walkouts', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'SALES_STAFF', 'SALES_CASHIER', 'CASHIER'] },
  { label: 'Orders', route: '/orders', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CASHIER', 'SALES_CASHIER', 'SALES_STAFF', 'OPTOMETRIST', 'WORKSHOP_STAFF'] },
  { label: 'Estimates / Quotations', route: '/estimates', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'SALES_CASHIER', 'SALES_STAFF'] },
  { label: 'Returns', route: '/returns', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'CASHIER', 'SALES_CASHIER'] },
  { label: 'Clinical', route: '/clinical', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST'] },
  { label: 'Inventory', route: '/inventory', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER', 'WORKSHOP_STAFF'] },
  { label: 'Power Grid', route: '/inventory/power-grid', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER', 'OPTOMETRIST'] },
  { label: 'Online Stock', route: '/inventory/online-sync', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER'] },
  { label: 'Purchase', route: '/purchase', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
  { label: 'Vendor Returns', route: '/purchase/vendor-returns', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'WORKSHOP_STAFF'] },
  { label: 'Workshop', route: '/workshop', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'WORKSHOP_STAFF'] },
  { label: 'Catalog', route: '/catalog/add', requireRoles: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
  { label: 'Catalog Autopilot', route: '/catalog/autopilot', requireRoles: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
  { label: 'Pricing & Offers', route: '/catalog/pricing', requireRoles: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
  { label: 'Tasks & SOPs', route: '/tasks', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
  { label: 'Expenses', route: '/finance/expenses' },
  { label: 'HR', route: '/hr', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
  { label: 'Salary Setup', route: '/hr/salary-setup', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
  { label: 'Payroll Run', route: '/hr/payroll-run', requireRoles: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
  { label: 'Incentive', route: '/incentive', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'SALES_STAFF', 'SALES_CASHIER', 'CASHIER'] },
  { label: 'Reports', route: '/reports', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
  { label: 'Finance', route: '/finance/dashboard', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
  { label: 'Cash Register', route: '/finance/cash-register', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
  { label: 'Cash Flow', route: '/finance/cash-flow', requireRoles: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
  { label: 'GST Credit (ITC)', route: '/finance/itc', requireRoles: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
  { label: 'Marketing', route: '/customers/campaigns', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'] },
  { label: 'Jarvis', route: '/jarvis', requireRoles: ['SUPERADMIN'] },
  { label: 'Activity Log', route: '/admin/activity-log', requireRoles: ['SUPERADMIN'] },
  { label: 'Print', route: '/print', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'CASHIER', 'SALES_CASHIER', 'SALES_STAFF'] },
  { label: 'Settings', route: '/settings', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'AREA_MANAGER', 'CATALOG_MANAGER', 'ACCOUNTANT'] },
  { label: 'Store Onboarding', route: '/setup', requireRoles: ['SUPERADMIN', 'ADMIN'] },
  { label: 'Organization', route: '/organization', requireRoles: ['SUPERADMIN', 'ADMIN'] },
];

// ----------------------------------------------------------------------------
// Debounce hook (200ms per the spec)
// ----------------------------------------------------------------------------

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

// ----------------------------------------------------------------------------
// Result row shapes
// ----------------------------------------------------------------------------

interface CustomerHit {
  customer_id: string;
  name: string;
  phone?: string;
}

interface OrderHit {
  order_id: string;
  order_number?: string;
  customer_name?: string;
  customer_phone?: string;
  status?: string;
  total_amount?: number;
  created_at?: string;
}

interface ProductHit {
  product_id: string;
  sku?: string;
  brand?: string;
  model?: string;
  name?: string;
}

// ----------------------------------------------------------------------------
// Component
// ----------------------------------------------------------------------------

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const navigate = useNavigate();
  const { user, hasRole } = useAuth();
  const [query, setQuery] = useState('');
  const debouncedQuery = useDebounced(query, 200);
  const inputRef = useRef<HTMLInputElement>(null);

  // Recent items - re-read on every open so an action in another tab is reflected.
  const [recent, setRecent] = useState<RecentItem[]>([]);

  // Result state - each section keeps its own loading + results so a slow
  // endpoint never blocks the others.
  const [customers, setCustomers] = useState<CustomerHit[]>([]);
  const [customersLoading, setCustomersLoading] = useState(false);
  const [orders, setOrders] = useState<OrderHit[]>([]);
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [products, setProducts] = useState<ProductHit[]>([]);
  const [productsLoading, setProductsLoading] = useState(false);

  // Reset query + reload recent on each open. cmdk autofocuses its <Command.Input>
  // when mounted, so the explicit focus() below is a belt-and-braces fallback
  // for the case where the input is mounted before the dialog body is ready.
  useEffect(() => {
    if (open) {
      setQuery('');
      setRecent(loadRecent());
      window.setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  // ----- Per-section search effects --------------------------------------
  // Each section ignores stale responses via a local cancellation flag so a
  // racing fast/slow request can't paint the wrong list.

  useEffect(() => {
    if (!open) return;
    const q = debouncedQuery.trim();
    if (q.length < 2) {
      setCustomers([]);
      setCustomersLoading(false);
      return;
    }
    let cancelled = false;
    setCustomersLoading(true);

    // The dedicated phone-search endpoint is digits-only; the generic /customers
    // list endpoint accepts a free-text `search` param that matches phone / name /
    // email server-side. Use the digit-only path when the query looks like a phone
    // number (fastest, exact phone match), otherwise fall back to the list search.
    const isDigits = /^\d{3,}$/.test(q);
    const promise = isDigits
      ? customerApi.searchByPhone(q)
      : customerApi.getCustomers({ search: q, pageSize: 8 });

    promise
      .then((res: any) => {
        if (cancelled) return;
        const list = res?.customers || res?.results || (Array.isArray(res) ? res : []);
        const normalized: CustomerHit[] = (Array.isArray(list) ? list : [])
          .slice(0, 8)
          .map((c: any) => ({
            customer_id: c.customer_id || c.id || c._id,
            name: c.name || c.full_name || c.customer_name || 'Unnamed',
            phone: c.phone || c.mobile || c.phone_number,
          }))
          .filter((c: CustomerHit) => !!c.customer_id);
        setCustomers(normalized);
      })
      .catch(() => {
        if (!cancelled) setCustomers([]);
      })
      .finally(() => {
        if (!cancelled) setCustomersLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, debouncedQuery]);

  useEffect(() => {
    if (!open) return;
    const q = debouncedQuery.trim();
    if (q.length < 2) {
      setOrders([]);
      setOrdersLoading(false);
      return;
    }
    let cancelled = false;
    setOrdersLoading(true);

    // The /orders endpoint doesn't expose a server-side `search` filter on this
    // build, so we ask for a recent slice and filter client-side. The list is
    // already store-scoped by the user's JWT, so this stays within the operator's
    // visibility. Limit=50 keeps the network round-trip small.
    orderApi
      .getOrders({ limit: 50 })
      .then((res: any) => {
        if (cancelled) return;
        const list = res?.orders || (Array.isArray(res) ? res : []);
        const ql = q.toLowerCase();
        const matched: OrderHit[] = (Array.isArray(list) ? list : [])
          .map((o: any) => ({
            order_id: o.order_id || o.id || o._id,
            order_number: o.order_number || o.invoice_number,
            customer_name: o.customer_name || o.customer?.name,
            customer_phone: o.customer_phone || o.customer?.phone,
            status: o.status,
            total_amount: o.total_amount ?? o.grand_total,
            created_at: o.created_at,
          }))
          .filter((o: OrderHit) => !!o.order_id)
          .filter((o: OrderHit) => {
            const hay = [
              o.order_id,
              o.order_number,
              o.customer_name,
              o.customer_phone,
            ]
              .filter(Boolean)
              .map((s) => String(s).toLowerCase())
              .join(' ');
            return hay.includes(ql);
          })
          .slice(0, 8);
        setOrders(matched);
      })
      .catch(() => {
        if (!cancelled) setOrders([]);
      })
      .finally(() => {
        if (!cancelled) setOrdersLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, debouncedQuery]);

  useEffect(() => {
    if (!open) return;
    const q = debouncedQuery.trim();
    if (q.length < 2) {
      setProducts([]);
      setProductsLoading(false);
      return;
    }
    let cancelled = false;
    setProductsLoading(true);
    productApi
      .searchProducts(q)
      .then((res: any) => {
        if (cancelled) return;
        const list = res?.products || (Array.isArray(res) ? res : []);
        const normalized: ProductHit[] = (Array.isArray(list) ? list : [])
          .slice(0, 8)
          .map((p: any) => ({
            product_id: p.product_id || p.id || p._id,
            sku: p.sku,
            brand: p.brand,
            model: p.model || p.model_no,
            name: p.name,
          }))
          .filter((p: ProductHit) => !!p.product_id);
        setProducts(normalized);
      })
      .catch(() => {
        if (!cancelled) setProducts([]);
      })
      .finally(() => {
        if (!cancelled) setProductsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, debouncedQuery]);

  // Role-filtered page list - mirrors Rail.tsx hasAnyRole + activeRole logic.
  const visiblePages = useMemo(() => {
    const userRoles = user?.roles;
    const activeRole = user?.activeRole;
    return ALL_PAGES.filter((p) => {
      if (!p.requireRoles) return true;
      if (userRoles && p.requireRoles.some((r) => userRoles.includes(r))) return true;
      if (activeRole && p.requireRoles.includes(activeRole)) return true;
      // Also let hasRole() catch SUPERADMIN/ADMIN broad access.
      return hasRole(p.requireRoles);
    });
  }, [user, hasRole]);

  // ----- Selection handlers ---------------------------------------------

  const closeAndGo = (route: string) => {
    onOpenChange(false);
    navigate(route);
  };

  const selectCustomer = (c: CustomerHit) => {
    const route = `/customers/${c.customer_id}/360`;
    pushRecent({
      type: 'customer',
      id: c.customer_id,
      label: c.name,
      sub: c.phone,
      route,
    });
    closeAndGo(route);
  };

  const selectOrder = (o: OrderHit) => {
    // No standalone order detail route in this build; deep-link the
    // Orders page with the order_id in the query so the user lands
    // pre-filtered. OrdersPage reads searchParams.
    const route = `/orders?order_id=${encodeURIComponent(o.order_id)}`;
    pushRecent({
      type: 'order',
      id: o.order_id,
      label: o.order_number || o.order_id,
      sub: o.customer_name || o.customer_phone,
      route,
    });
    closeAndGo(route);
  };

  const selectProduct = (p: ProductHit) => {
    // No product-detail page either - send to inventory pre-filtered by SKU.
    const filter = p.sku || p.product_id;
    const route = `/inventory?search=${encodeURIComponent(filter)}`;
    const label =
      [p.brand, p.model].filter(Boolean).join(' ') || p.sku || p.name || 'Product';
    pushRecent({
      type: 'product',
      id: p.product_id,
      label,
      sub: p.sku,
      route,
    });
    closeAndGo(route);
  };

  const selectPage = (page: JumpPage) => {
    pushRecent({
      type: 'page',
      id: page.route,
      label: page.label,
      route: page.route,
    });
    closeAndGo(page.route);
  };

  const selectRecent = (r: RecentItem) => {
    pushRecent(r); // bump it to front of LRU
    closeAndGo(r.route);
  };

  const trimmedQuery = query.trim();
  const showRecent = trimmedQuery.length === 0 && recent.length > 0;

  return (
    <Command.Dialog
      open={open}
      onOpenChange={onOpenChange}
      label="Search or jump to"
      shouldFilter={false}
      // We disable cmdk's built-in filtering because each section is fed by
      // its own debounced query - rows are already filtered when they arrive,
      // so the library would double-filter and drop matches the server kept.
      overlayClassName="cmdk-overlay"
      contentClassName="cmdk-content"
    >
      <Command.Input
        ref={inputRef}
        value={query}
        onValueChange={setQuery}
        placeholder="Search customers, orders, products, or jump to a page..."
        className="cmdk-input"
        autoFocus
      />
      <Command.List className="cmdk-list">
        {trimmedQuery.length > 0 &&
          !customersLoading &&
          !ordersLoading &&
          !productsLoading &&
          customers.length === 0 &&
          orders.length === 0 &&
          products.length === 0 && (
            <Command.Empty className="cmdk-empty">
              No results for &ldquo;{trimmedQuery}&rdquo;
            </Command.Empty>
          )}

        {showRecent && (
          <Command.Group heading="Recent" className="cmdk-group">
            {recent.map((r) => (
              <Command.Item
                key={`recent-${r.type}-${r.id}`}
                value={`recent ${r.type} ${r.id} ${r.label}`}
                onSelect={() => selectRecent(r)}
                className="cmdk-item"
              >
                <RowIcon kind={r.type} />
                <div className="cmdk-row-text">
                  <div className="cmdk-row-label">{r.label}</div>
                  {r.sub && <div className="cmdk-row-sub">{r.sub}</div>}
                </div>
                <span className="cmdk-row-tag">{labelForType(r.type)}</span>
              </Command.Item>
            ))}
          </Command.Group>
        )}

        {trimmedQuery.length >= 2 && (
          <Command.Group heading="Customers" className="cmdk-group">
            {customersLoading && customers.length === 0 ? (
              <div className="cmdk-loading">Searching customers...</div>
            ) : customers.length === 0 ? (
              <div className="cmdk-empty-inline">No customer matches</div>
            ) : (
              customers.map((c) => (
                <Command.Item
                  key={`customer-${c.customer_id}`}
                  value={`customer ${c.customer_id} ${c.name} ${c.phone ?? ''}`}
                  onSelect={() => selectCustomer(c)}
                  className="cmdk-item"
                >
                  <RowIcon kind="customer" />
                  <div className="cmdk-row-text">
                    <div className="cmdk-row-label">{c.name}</div>
                    {c.phone && <div className="cmdk-row-sub">{c.phone}</div>}
                  </div>
                  <span className="cmdk-row-tag">Customer</span>
                </Command.Item>
              ))
            )}
          </Command.Group>
        )}

        {trimmedQuery.length >= 2 && (
          <Command.Group heading="Orders" className="cmdk-group">
            {ordersLoading && orders.length === 0 ? (
              <div className="cmdk-loading">Searching orders...</div>
            ) : orders.length === 0 ? (
              <div className="cmdk-empty-inline">No order matches</div>
            ) : (
              orders.map((o) => (
                <Command.Item
                  key={`order-${o.order_id}`}
                  value={`order ${o.order_id} ${o.order_number ?? ''} ${o.customer_name ?? ''} ${o.customer_phone ?? ''}`}
                  onSelect={() => selectOrder(o)}
                  className="cmdk-item"
                >
                  <RowIcon kind="order" />
                  <div className="cmdk-row-text">
                    <div className="cmdk-row-label">
                      {o.order_number || o.order_id}
                    </div>
                    <div className="cmdk-row-sub">
                      {[o.customer_name, o.customer_phone, o.status]
                        .filter(Boolean)
                        .join(' - ')}
                    </div>
                  </div>
                  <span className="cmdk-row-tag">Order</span>
                </Command.Item>
              ))
            )}
          </Command.Group>
        )}

        {trimmedQuery.length >= 2 && (
          <Command.Group heading="Products" className="cmdk-group">
            {productsLoading && products.length === 0 ? (
              <div className="cmdk-loading">Searching products...</div>
            ) : products.length === 0 ? (
              <div className="cmdk-empty-inline">No product matches</div>
            ) : (
              products.map((p) => (
                <Command.Item
                  key={`product-${p.product_id}`}
                  value={`product ${p.product_id} ${p.sku ?? ''} ${p.brand ?? ''} ${p.model ?? ''} ${p.name ?? ''}`}
                  onSelect={() => selectProduct(p)}
                  className="cmdk-item"
                >
                  <RowIcon kind="product" />
                  <div className="cmdk-row-text">
                    <div className="cmdk-row-label">
                      {[p.brand, p.model].filter(Boolean).join(' ') ||
                        p.name ||
                        p.sku ||
                        'Product'}
                    </div>
                    {p.sku && <div className="cmdk-row-sub">SKU {p.sku}</div>}
                  </div>
                  <span className="cmdk-row-tag">Product</span>
                </Command.Item>
              ))
            )}
          </Command.Group>
        )}

        <Command.Group heading="Jump to page" className="cmdk-group">
          {(() => {
            // Local fuzzy filter for page titles - case-insensitive substring
            // match plus a tiny acronym match (e.g. "gst" matches "GST Credit").
            const q = trimmedQuery.toLowerCase();
            const matched = q
              ? visiblePages.filter((p) => p.label.toLowerCase().includes(q))
              : visiblePages;
            const sliced = matched.slice(0, q ? 8 : 12);
            if (sliced.length === 0) {
              return <div className="cmdk-empty-inline">No page matches</div>;
            }
            return sliced.map((p) => (
              <Command.Item
                key={`page-${p.route}`}
                value={`page ${p.label} ${p.route}`}
                onSelect={() => selectPage(p)}
                className="cmdk-item"
              >
                <RowIcon kind="page" />
                <div className="cmdk-row-text">
                  <div className="cmdk-row-label">{p.label}</div>
                  <div className="cmdk-row-sub cmdk-row-sub-mono">{p.route}</div>
                </div>
                <span className="cmdk-row-tag">Page</span>
              </Command.Item>
            ));
          })()}
        </Command.Group>
      </Command.List>
      <div className="cmdk-footer">
        <span className="cmdk-hint">
          <kbd className="cmdk-kbd">Up</kbd>
          <kbd className="cmdk-kbd">Down</kbd>
          <span>navigate</span>
        </span>
        <span className="cmdk-hint">
          <kbd className="cmdk-kbd">Enter</kbd>
          <span>open</span>
        </span>
        <span className="cmdk-hint">
          <kbd className="cmdk-kbd">Esc</kbd>
          <span>close</span>
        </span>
      </div>
    </Command.Dialog>
  );
}

// ----------------------------------------------------------------------------
// Subcomponents
// ----------------------------------------------------------------------------

function labelForType(t: RecentItem['type']): string {
  switch (t) {
    case 'customer':
      return 'Customer';
    case 'order':
      return 'Order';
    case 'product':
      return 'Product';
    case 'page':
      return 'Page';
  }
}

function RowIcon({ kind }: { kind: RecentItem['type'] }) {
  const Cmp =
    kind === 'customer'
      ? Icon.users
      : kind === 'order'
      ? Icon.receipt
      : kind === 'product'
      ? Icon.tag
      : Icon.home;
  return (
    <span className="cmdk-row-icon">
      <Cmp width={16} height={16} />
    </span>
  );
}
