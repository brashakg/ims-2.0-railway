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
 *  pick (undefined = all stores), else the caller's own shop. A new order or
 *  return is raised at `ownStoreId` (the server's rule); `showShopOf` then
 *  points an admin's list at that shop if it is showing another, so what was
 *  just raised never vanishes from view. */
export function usePurchaseShop() {
  const { user } = useAuth();
  const { shop, setShop } = useChosenShop();
  const canPick = !!user?.roles?.some((r) => r === 'ADMIN' || r === 'SUPERADMIN');
  const ownStoreId = user?.activeStoreId || undefined;
  const storeId = canPick ? shop || undefined : ownStoreId;
  const showShopOf = (raisedAt: string | undefined) => {
    if (canPick && shop && raisedAt && shop !== raisedAt) setShop(raisedAt);
  };
  return { storeId, ownStoreId, canPick, shop, setShop, showShopOf };
}

type StoreRow = { store_id?: string; store_name?: string; store_code?: string };

const storeName = (s: StoreRow) => s.store_name || s.store_code || s.store_id;

/** For an admin, whatever shop the filter shows: a new PO delivers to his own
 *  shop (W1.4), so say which before he creates it. Nothing for anyone else. */
export function NewOrdersDeliverTo() {
  const { canPick, ownStoreId } = usePurchaseShop();
  return canPick && ownStoreId ? <OwnShop storeId={ownStoreId} /> : null;
}

function OwnShop({ storeId }: { storeId: string }) {
  const { data } = useStores();
  const row = ((Array.isArray(data) ? data : []) as StoreRow[]).find((s) => s.store_id === storeId);
  return <span className="text-xs text-gray-500">New orders deliver to {row ? storeName(row) : storeId}</span>;
}

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
            {storeName(s)}
          </option>
        ))}
      </select>
    </label>
  );
}
