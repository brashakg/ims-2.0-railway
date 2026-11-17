// The Inventory module's section menu: ONE list read by the layout's two-level
// tabs and by global search (CommandPalette), so a section staff can open from
// the tabs is also findable by name (audit R39).
//
// `manageOnly` entries are shown to the manage ladder only (canManageInventory).
// `standalone` entries are their own routes OUTSIDE InventoryLayout (their own
// gates; the accountant opens Reorder too), so they render without this menu:
// a group tab never lands on one, or the tab would hide its own siblings
// (audit R37).

import {
  AlertTriangle,
  ArrowRightLeft,
  BarChart3,
  Barcode,
  Boxes,
  Clock,
  Eye,
  Hash,
  LayoutGrid,
  Package,
  ShoppingCart,
  TrendingDown,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

export interface InventoryNavEntry {
  path: string;
  label: string;
  icon: LucideIcon;
  manageOnly?: boolean;
  standalone?: boolean;
  /** Extra words staff search with (global search only). */
  keywords?: string;
}

export interface InventoryNavGroup {
  id: string;
  label: string;
  icon: LucideIcon;
  entries: InventoryNavEntry[];
}

export const INVENTORY_NAV_GROUPS: InventoryNavGroup[] = [
  {
    id: 'catalog', label: 'Catalog', icon: Package,
    entries: [
      { path: '/inventory/stock', label: 'Stock ledger', icon: Package, keywords: 'stock list on hand' },
      { path: '/inventory/display-layout', label: 'Display layout', icon: LayoutGrid, keywords: 'fixtures floor zones' },
    ],
  },
  {
    id: 'health', label: 'Stock health', icon: AlertTriangle,
    entries: [
      { path: '/inventory/non-moving', label: 'Non-moving', icon: TrendingDown, keywords: 'dead stock slow' },
      { path: '/inventory/aging', label: 'Stock aging', icon: Clock, keywords: 'ageing old stock' },
      { path: '/inventory/alerts', label: 'Alerts', icon: AlertTriangle, keywords: 'stock alerts' },
    ],
  },
  {
    id: 'ops', label: 'Operations', icon: ShoppingCart,
    entries: [
      // S5: THE Reorder list (Low stock, Replenishment and Buy Desk merged).
      { path: '/inventory/reorders', label: 'Reorder', icon: ShoppingCart, manageOnly: true, standalone: true, keywords: 'low stock buy replenish' },
      { path: '/inventory/transfers', label: 'Transfers', icon: ArrowRightLeft, keywords: 'stock transfer move between shops' },
      { path: '/inventory/movements', label: 'Movements', icon: Eye, keywords: 'stock movement history' },
      { path: '/inventory/rebalance', label: 'Rebalance', icon: ArrowRightLeft, keywords: 'stock rebalance between shops' },
      { path: '/inventory/audit', label: 'Stock count', icon: Barcode, manageOnly: true, standalone: true, keywords: 'stock audit cycle count' },
      { path: '/inventory/quarantine', label: 'Quarantine', icon: AlertTriangle, keywords: 'damaged faulty unlabelled stock' },
      // Moved off the Stock & supply menu (owner, 2026-11-01).
      { path: '/inventory/opening-stock', label: 'Opening stock', icon: Package, manageOnly: true, standalone: true, keywords: 'opening stock import' },
      { path: '/inventory/online-sync', label: 'Online stock', icon: Boxes, manageOnly: true, standalone: true, keywords: 'online stock website shopify' },
    ],
  },
  {
    id: 'optical', label: 'Optical', icon: Eye,
    entries: [
      { path: '/inventory/serial-numbers', label: 'Serial numbers', icon: Hash, keywords: 'serial' },
      { path: '/inventory/contact-lens', label: 'Contact lens', icon: Eye, keywords: 'contact lenses cl stock' },
      { path: '/inventory/power-grid', label: 'Lens power grid', icon: BarChart3, manageOnly: true, standalone: true, keywords: 'lens stock power grid' },
    ],
  },
  {
    id: 'insights', label: 'Insights', icon: BarChart3,
    entries: [
      { path: '/inventory/sell-through', label: 'Sell-through', icon: TrendingDown, keywords: 'stock sell through' },
      { path: '/inventory/overstock', label: 'Overstock', icon: Boxes, keywords: 'excess stock' },
      { path: '/inventory/brand-insights', label: 'Brands', icon: BarChart3, keywords: 'brand stock insights' },
      { path: '/inventory/collection-insights', label: 'Collections', icon: Boxes, keywords: 'collection stock insights' },
    ],
  },
];

/** The entries this person may see (manage-ladder entries need canManage). */
export function visibleInventoryGroups(canManage: boolean): InventoryNavGroup[] {
  return INVENTORY_NAV_GROUPS.map((g) => ({
    ...g,
    entries: g.entries.filter((e) => !e.manageOnly || canManage),
  }));
}

/** Where a group tab lands: its first entry that keeps this menu on screen. */
export function groupLanding(group: InventoryNavGroup): string {
  return (group.entries.find((e) => !e.standalone) ?? group.entries[0]).path;
}
