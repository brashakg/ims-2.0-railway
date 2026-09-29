// ============================================================================
// IMS 2.0 - The one Purchase shop scope (audit F63, owner ruling 2026-09-28)
// ============================================================================
// Admins open Purchase on ALL STORES and may narrow it to one shop; every tab
// (orders, receiving, invoices, returns, variance, recon, this month) reads the
// same choice. Everyone else keeps their own shop. The rule itself lives on
// the server (api.dependencies.resolve_store_scope: no store_id = all stores
// for ADMIN/SUPERADMIN, the caller's own shop for anyone else, 403 for another
// shop); this only carries the admin's pick between tabs.

import { create } from 'zustand';
import { useAuth } from '../../context/AuthContext';
import { useStores } from '../../hooks/usePOSQueries';

const useChosenShop = create<{ shop: string; setShop: (shop: string) => void }>((set) => ({
  shop: '',
  setShop: (shop) => set({ shop }),
}));

/** `storeId` is what a Purchase list read sends as ?store_id: the admin's
 *  pick (undefined = all stores), else the caller's own shop. */
export function usePurchaseShop() {
  const { user } = useAuth();
  const { shop, setShop } = useChosenShop();
  const canPick = !!user?.roles?.some((r) => r === 'ADMIN' || r === 'SUPERADMIN');
  const storeId = canPick ? shop || undefined : user?.activeStoreId || undefined;
  return { storeId, canPick, shop, setShop };
}

type StoreRow = { store_id?: string; store_name?: string; store_code?: string };

/** The admin's shop filter; renders nothing for anyone else. */
export function PurchaseShopPicker() {
  const { canPick, shop, setShop } = usePurchaseShop();
  return canPick ? <ShopSelect shop={shop} setShop={setShop} /> : null;
}

function ShopSelect({ shop, setShop }: { shop: string; setShop: (shop: string) => void }) {
  const { data } = useStores();
  const stores = (Array.isArray(data) ? data : []) as StoreRow[];
  return (
    <label className="flex items-center gap-2 text-sm text-gray-600">
      Shop
      <select
        value={shop}
        onChange={(e) => setShop(e.target.value)}
        className="text-sm border border-gray-300 rounded px-2 py-1.5 bg-white"
        aria-label="Purchase shop"
      >
        <option value="">All stores</option>
        {stores.map((s) => (
          <option key={s.store_id} value={s.store_id}>
            {s.store_name || s.store_code || s.store_id}
          </option>
        ))}
      </select>
    </label>
  );
}
