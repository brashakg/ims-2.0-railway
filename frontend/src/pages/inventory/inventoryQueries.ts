// ============================================================================
// IMS 2.0 - Inventory shared data via React Query
// ============================================================================
// Wave 2 split of the old InventoryPage tab container (19 tabs in useState).
// Same template as pages/reports/reportsQueries.ts: ONE cache shared by the
// layout and every section page (5-min staleTime from the app QueryClient),
// so the layout's stat strip and the Stock ledger page hit the same entry
// instead of each refetching /inventory/stock on mount.
//
// Nothing here changes a request's URL or params - these are the same calls
// the old page made, moved behind useQuery. The Movements ledger is NOT here:
// it is load-more paged and used by exactly one page, so its fetch lives in
// InventoryMovementsPage (shared cache buys nothing for a single consumer).

import { useQuery } from '@tanstack/react-query';
import type { LucideIcon } from 'lucide-react';
import {
  BookOpen,
  Clock,
  Ear,
  Eye,
  Glasses,
  Headphones,
  Search as Lens,
  Smartphone,
  Sparkles,
  Sun,
  Watch,
} from 'lucide-react';
import type { ProductCategory } from '../../types';
import { inventoryApi, catalogApi, storeApi, type OnlineStatus } from '../../services/api';
// DIRECT module imports (not the api barrel) to dodge the TS2614 re-export
// resolution issue documented in CLAUDE.md.
import { productApi, type Cataloguer } from '../../services/api/products';
import { displayPlacementsApi, type DisplayPlacement } from '../../services/api/displayPlacements';
import { displayFixturesApi, type DisplayFixture } from '../../services/api/displayFixtures';
import { onlineStoreApi, type OnlineStoreSummary } from '../../services/api/onlineStore';
import { canonicalCategory } from '../../utils/categoryNormalize';

// Category configuration - one copy, used by the layout (counts, hint) and
// the Stock ledger page (filter chips + label lookups).
export const CATEGORIES: { code: ProductCategory; label: string; icon: LucideIcon }[] = [
  { code: 'FR', label: 'Frames', icon: Glasses },
  { code: 'SG', label: 'Sunglasses', icon: Sun },
  { code: 'RG', label: 'Reading Glasses', icon: BookOpen },
  { code: 'LS', label: 'Optical Lenses', icon: Lens },
  { code: 'CL', label: 'Contact Lenses', icon: Eye },
  { code: 'CCL', label: 'Colour Contacts', icon: Eye },
  { code: 'WT', label: 'Watches', icon: Watch },
  { code: 'SMTWT', label: 'Smartwatches', icon: Smartphone },
  { code: 'SMTSG', label: 'Smart Sunglasses', icon: Sparkles },
  { code: 'SMTFR', label: 'Smart Frames', icon: Sparkles },
  { code: 'CK', label: 'Wall Clocks', icon: Clock },
  { code: 'ACC', label: 'Accessories', icon: Headphones },
  { code: 'HA', label: 'Hearing Aids', icon: Ear },
];

// Stock item type (verbatim from the old InventoryPage).
export interface StockItem {
  id: string;
  sku: string;
  name: string;
  productName?: string;
  category: ProductCategory;
  brand: string;
  mrp: number;
  offerPrice: number;
  /** Units on the shelf for sale (AVAILABLE) at this store -- reserved units
   *  are NOT inside it (the server counts them apart, inventory/stock.py). */
  stock: number;
  quantity?: number;
  /** Units reserved for an order: still in the shop, not for sale. */
  reserved: number;
  location?: string;
  lowStockThreshold?: number;
  minStock?: number;
  barcode?: string;
  storeBarcode?: string;
  /** Procurement Phase 1 (additive from /inventory/stock): the latest ACCEPTED
   *  GRN that stocked this product at this store, or null/absent. */
  last_grn?: { grn_number?: string; qty?: number; date?: string } | null;
  /** Owner 2026-07-05: product images on the inventory ledger. image_url =
   *  first image (row thumbnail); images = full array for the lightbox. */
  image_url?: string | null;
  images?: string[];
  /** Cataloguer attribution: who created the product master row. */
  created_by?: string | null;
  created_by_name?: string | null;
  /** Audit F47: what the units in the shop (shelf + reserved, the same units
   *  as unitsHeld) COST, and per unit. Sent only to the cost readers
   *  (managers + accounts); absent for the counter. unit_cost is per COSTED
   *  unit and null when no unit has a cost -- never a made-up Rs 0. */
  cost_value?: number;
  unit_cost?: number | null;
  /** Units with no known cost (they add nothing to cost_value), so the screen
   *  can say "N units have no cost". Cost readers only, like cost_value. */
  uncosted_units?: number;
}

/** Units the shop holds of a row: on the shelf + reserved for an order. The
 *  stock-value tiles (cost and selling) count exactly these, as the server's
 *  cost_value does (services/stock_value.py), and the ledger shows them as
 *  `stock` available plus `reserved` -- never stock minus reserved, since
 *  stock already excludes the reserved ones (review r1 #39). */
export const unitsHeld = (i: Pick<StockItem, 'stock' | 'reserved'>) =>
  (i.stock || 0) + (i.reserved || 0);

/** Verbatim the old loadInventory() normalisation: one vocabulary at ingest. */
function normalizeStockItems(data: unknown): StockItem[] {
  const items = (data as { items?: StockItem[] })?.items || data || [];
  if (!Array.isArray(items)) return [];
  return items.map((item: StockItem) => ({
    ...item,
    name: item.name || item.productName || 'Unknown Product',
    stock: item.stock || item.quantity || 0,
    lowStockThreshold: item.lowStockThreshold || item.minStock || 5,
    reserved: item.reserved || 0,
    category: canonicalCategory(item.category) as ProductCategory,
  }));
}

/** The per-store stock ledger (drives the table AND the layout stat strip). */
export function useStock(storeId: string | undefined, createdBy?: string) {
  return useQuery({
    queryKey: ['inventory', 'stock', storeId ?? 'none', createdBy || 'all'] as const,
    enabled: !!storeId,
    queryFn: async () => {
      const data = await inventoryApi.getStock(
        storeId as string,
        undefined,
        createdBy ? { created_by: createdBy } : undefined,
      );
      return normalizeStockItems(data);
    },
  });
}

export function useLowStock(storeId: string | undefined) {
  return useQuery({
    queryKey: ['inventory', 'low-stock', storeId ?? 'none'] as const,
    enabled: !!storeId,
    queryFn: async () => {
      const data = await inventoryApi.getLowStock(storeId as string);
      return normalizeStockItems(data);
    },
  });
}

/** The identity keys the online-status lookup accepts for a row. */
export function onlineStatusIds(items: StockItem[]): string[] {
  return Array.from(new Set(
    items.flatMap((i) => [i.sku, i.barcode, i.storeBarcode])
      .map((v) => String(v || '').trim())
      .filter(Boolean),
  ));
}

/** Which SKUs are live in Shopify + how much online stock exists. */
export function useOnlineStatus(ids: string[]) {
  return useQuery({
    queryKey: ['inventory', 'online-status', [...ids].sort().join('|')] as const,
    enabled: ids.length > 0,
    queryFn: async (): Promise<Record<string, OnlineStatus>> => {
      try {
        return await catalogApi.getOnlineStatus(ids);
      } catch {
        return {}; // fail-soft: bridge off -> no badges (as before)
      }
    },
  });
}

/** Match a row to its online status by sku -> barcode -> storeBarcode. */
export function getOnlineFor(
  item: StockItem,
  onlineStatus: Record<string, OnlineStatus> | undefined,
): OnlineStatus | undefined {
  if (!onlineStatus) return undefined;
  const keys = [item.sku, item.barcode, item.storeBarcode]
    .map((v) => String(v || '').trim())
    .filter(Boolean);
  for (const k of keys) {
    if (onlineStatus[k]) return onlineStatus[k];
  }
  return undefined;
}

/** Stores the user may view (multi-store roles see all; others their own). */
export function useInventoryStores(allStoreAccess: boolean, storeIds: string[] | undefined) {
  return useQuery({
    queryKey: ['inventory', 'stores', allStoreAccess, (storeIds || []).join('|')] as const,
    queryFn: async () => {
      try {
        const res = (await storeApi.getStores()) as
          | { stores?: unknown[] }
          | unknown[];
        const list = (res as { stores?: unknown[] })?.stores || res || [];
        return (Array.isArray(list) ? list : [])
          .map((raw) => {
            const s = raw as Record<string, unknown>;
            return {
              id: String(s.store_id || s.id || s._id || ''),
              name: String(s.store_name || s.storeName || s.name || s.store_id || s.id || ''),
              store_type: String(s.store_type || ''),
            };
          })
          .filter((s) => s.id && (allStoreAccess || (storeIds || []).includes(s.id)));
      } catch {
        return [] as { id: string; name: string; store_type: string }[];
      }
    },
  });
}

/** F21: QUARANTINED units still lacking a printed red label (nav badge). */
export function useQuarantineUnlabeled(storeId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ['inventory', 'quarantine-unlabeled', storeId ?? 'none'] as const,
    enabled,
    queryFn: async () => {
      try {
        const res = await inventoryApi.getQuarantinedStock(storeId ? { store_id: storeId } : undefined);
        return res.unlabeled_count || 0;
      } catch {
        return 0; // fail-soft: non-manager / no data -> badge stays 0
      }
    },
  });
}

/** Cataloguer roster for the "Catalogued by" filter (manager ladder). */
export function useCataloguers(enabled: boolean) {
  return useQuery({
    queryKey: ['inventory', 'cataloguers'] as const,
    enabled,
    queryFn: async (): Promise<Cataloguer[]> => {
      try {
        const res = await productApi.getCataloguers();
        return res.cataloguers || [];
      } catch {
        return []; // fail-soft: 403 just hides the filter (as before)
      }
    },
  });
}

/** v2-2b: placements + fixtures for the Zone column / display-layout badge.
 *  Batched: one list call each per store - NEVER N+1 per row. */
export function usePlacements(storeId: string | undefined) {
  return useQuery({
    queryKey: ['inventory', 'placements', storeId ?? 'none'] as const,
    enabled: !!storeId,
    queryFn: async (): Promise<DisplayPlacement[]> => {
      try {
        const res = await displayPlacementsApi.list({ store_id: storeId as string });
        return res.placements || [];
      } catch {
        return [];
      }
    },
  });
}

export function useFixturesMap(storeId: string | undefined) {
  return useQuery({
    queryKey: ['inventory', 'fixtures', storeId ?? 'none'] as const,
    enabled: !!storeId,
    queryFn: async (): Promise<Record<string, DisplayFixture>> => {
      try {
        const res = await displayFixturesApi.list({ store_id: storeId as string, active: true });
        const m: Record<string, DisplayFixture> = {};
        for (const f of res.fixtures || []) m[f.fixture_id] = f;
        return m;
      } catch {
        return {};
      }
    },
  });
}

/** Website counts for the ONLINE-store view. getSummary() never throws
 *  (403/404 -> { available:false }), so KPI cards degrade to links, not 0s. */
export function useOnlineSummary(enabled: boolean) {
  return useQuery({
    queryKey: ['inventory', 'online-summary'] as const,
    enabled,
    queryFn: (): Promise<OnlineStoreSummary> => onlineStoreApi.getSummary(),
  });
}
