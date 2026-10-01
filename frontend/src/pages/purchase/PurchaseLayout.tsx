// ============================================================================
// IMS 2.0 - Purchase module layout
// ============================================================================
// Wave 1 split: the old PurchaseManagementPage tab container became real
// pages, one URL per section (/purchase/orders, /purchase/invoices, …).
// This layout keeps the shared editorial header, the online-store warning
// and the section nav; each section page owns its own data + actions.
//
// The header action ("New PO" / "New supplier") navigates to ?new=1 on the
// section — the section page reads the flag, opens its create modal and
// clears the param. Keeps the button in the header without cross-component
// plumbing, and makes "new PO" deep-linkable.

import { useEffect } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  Plus,
  FileText,
  Receipt,
  Truck,
  TrendingUp,
  AlertTriangle,
  PackageX,
  CalendarDays,
} from 'lucide-react';
import { useIsOnlineStore } from '../../hooks/useIsOnlineStore';
import { useAuth } from '../../context/AuthContext';
import { NewOrdersDeliverTo, PurchaseShopLabel, PurchaseShopPicker } from './purchaseShop';
import { APPROVE_ROLES } from './invoices/shared';

const SECTIONS = [
  { path: '/purchase/orders', label: 'Purchase Orders', icon: FileText },
  // Supplier bills -- what we owe and have paid: the accounts roles only
  // (owner ruling 2026-10-01; the route reads the same APPROVE_ROLES).
  { path: '/purchase/invoices', label: 'Purchase Invoices', icon: Receipt, roles: APPROVE_ROLES },
  { path: '/purchase/variance', label: 'Variance', icon: PackageX },
  { path: '/purchase/suppliers', label: 'Suppliers', icon: Truck },
  { path: '/purchase/vendor-returns', label: 'Vendor Returns', icon: AlertTriangle },
  { path: '/purchase/analytics', label: 'Analytics', icon: TrendingUp },
  // Audit F56: what we ordered, received, were billed, paid and owe -- the
  // supplier-balance readers only (APPROVE_ROLES = the API's _AP_ROLES; the
  // route and the Suppliers card read the same list).
  { path: '/purchase/this-month', label: 'This month', icon: CalendarDays, roles: APPROVE_ROLES },
];

export function PurchaseLayout() {
  // W1.4 / OS-006: POs created here deliver to the ACTIVE store. An ONLINE
  // store holds no stock, so warn up front (backend rejects with 400 too).
  const onlineStore = useIsOnlineStore();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { hasRole } = useAuth();

  // Warm the sibling section chunks once the browser is idle, so the FIRST
  // click on any tab renders without the lazy-chunk download spinner (owner
  // feedback: switching sections felt like a page reload). Vite dedupes these
  // against the route-level lazy() imports — no double download.
  useEffect(() => {
    const idle: (cb: () => void) => void =
      'requestIdleCallback' in window
        ? (cb) => (window as Window & { requestIdleCallback: (cb: () => void) => void }).requestIdleCallback(cb)
        : (cb) => { setTimeout(cb, 1500); };
    idle(() => {
      void import('./PurchaseOrdersSection');
      void import('./PurchaseInvoicesSection');
      void import('./PurchaseVarianceTab');
      void import('./SuppliersSection');
      void import('./VendorReturns');
      void import('./PurchaseAnalyticsSection');
      void import('./PurchasesThisMonthSection');
    });
  }, []);

  // Sections whose primary create action lives in the header.
  const headerAction =
    pathname === '/purchase/orders'
      ? 'New PO'
      : pathname === '/purchase/suppliers'
        ? 'New supplier'
        : null;

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Purchase &amp; Supply</div>
          <h1>Stock, from upstream.</h1>
          <div className="hint">Vendor ledger, purchase orders, GRN verification with quantity + price variance, payment aging, credit notes.</div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {/* Audit F63: admins read every tab across all stores or one shop;
              everyone else is told which shop (their own) the tabs cover. */}
          <PurchaseShopPicker />
          <PurchaseShopLabel />
          {/* Invoices page carries its own Create-from-GRN / Manual buttons; the
              variance page is read-mostly (its own Dismiss action lives inline). */}
          {headerAction && (
            <button
              onClick={() => navigate(`${pathname}?new=1`)}
              className="btn sm primary"
            >
              <Plus className="w-4 h-4" />
              {headerAction}
            </button>
          )}
          {/* F63: whatever shop the filter shows, a new PO delivers to the
              admin's own shop (W1.4) -- say which before he creates it. */}
          {pathname === '/purchase/orders' && <NewOrdersDeliverTo />}
        </div>
      </div>

      {/* W1.4 / OS-006: online-store warning — POs deliver to the active store. */}
      {onlineStore && (
        <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
          <div className="text-sm text-blue-900">
            <p className="font-medium">You're on an online store — it holds no stock.</p>
            <p className="text-xs text-blue-800 mt-1">
              Purchase orders and goods receipts must be raised under a physical
              shop. Switch stores from the header dropdown; creating a PO here
              will be rejected.
            </p>
          </div>
        </div>
      )}

      {/* Section nav — real links, one URL per section. overflow-x-auto +
          shrink-0 keep every tab reachable on iPad portrait / phone widths
          (the row is wider than 768px; it scrolls instead of clipping).
          gap-5: all seven tabs fit a 1024x768 landscape tablet. Measured in
          headless Chromium with Inter 500 actually loaded: the row is 941px
          at gap-5 in a 958px box (classic scrollbar); gap-6 was 965px and
          clipped "This month". */}
      <div className="border-b border-gray-200 overflow-x-auto">
        <nav className="flex gap-4 tablet:gap-5 w-max min-w-full">
          {SECTIONS.filter((s) => !s.roles || hasRole(s.roles)).map(({ path, label, icon: Icon }) => (
            <NavLink
              key={path}
              to={path}
              className={({ isActive }) =>
                `pb-3 px-1 border-b-2 font-medium text-sm transition-colors shrink-0 whitespace-nowrap ${
                  isActive
                    ? 'border-blue-600 text-blue-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`
              }
            >
              <div className="flex items-center gap-2">
                <Icon className="w-4 h-4" />
                {label}
              </div>
            </NavLink>
          ))}
        </nav>
      </div>

      <Outlet />
    </div>
  );
}
