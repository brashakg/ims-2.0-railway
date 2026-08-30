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

import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  Plus,
  FileText,
  Receipt,
  Truck,
  TrendingUp,
  AlertTriangle,
  PackageX,
} from 'lucide-react';
import { useIsOnlineStore } from '../../hooks/useIsOnlineStore';

const SECTIONS = [
  { path: '/purchase/orders', label: 'Purchase Orders', icon: FileText },
  { path: '/purchase/invoices', label: 'Purchase Invoices', icon: Receipt },
  { path: '/purchase/variance', label: 'Variance', icon: PackageX },
  { path: '/purchase/suppliers', label: 'Suppliers', icon: Truck },
  { path: '/purchase/vendor-returns', label: 'Vendor Returns', icon: AlertTriangle },
  { path: '/purchase/analytics', label: 'Analytics', icon: TrendingUp },
];

export function PurchaseLayout() {
  // W1.4 / OS-006: POs created here deliver to the ACTIVE store. An ONLINE
  // store holds no stock, so warn up front (backend rejects with 400 too).
  const onlineStore = useIsOnlineStore();
  const { pathname } = useLocation();
  const navigate = useNavigate();

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
          shrink-0 keep all six reachable on iPad portrait / phone widths
          (the row is wider than 768px; it scrolls instead of clipping). */}
      <div className="border-b border-gray-200 overflow-x-auto">
        <nav className="flex gap-4 tablet:gap-8 w-max min-w-full">
          {SECTIONS.map(({ path, label, icon: Icon }) => (
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
