// ============================================================================
// IMS 2.0 - The one Purchase shop scope (audit F63, owner ruling 2026-09-28)
// ============================================================================
// Admins open Purchase on ALL STORES and may narrow it to one shop; every tab
// (orders, receiving, invoices, returns, variance, recon, this month) reads the
// same choice. Everyone else keeps their own shop. The rule itself lives on
// the server (api.dependencies.resolve_store_scope: no store_id = all stores
// for ADMIN/SUPERADMIN, the caller's own shop for anyone else, 403 for another
// shop); this only carries the admin's pick between tabs.
//
// Owner ruling 2026-10-07 (R3): anyone else with NO shop gets no shop's data.
// The server refuses them (resolve_store_scope, the same words as NO_SHOP);
// PurchaseShopGate says so instead of loading a screen of 403s.

import type { ReactNode } from 'react';
import { create } from 'zustand';
import { useAuth } from '../../context/AuthContext';
import { useStores } from '../../hooks/usePOSQueries';
import { hasNoActiveStore, userSeesAllStores } from '../../utils/storeAccess';

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
  // The app's one store-access rule (utils/storeAccess): who sees every shop,
  // and what counts as no shop at all (blank or whitespace).
  const canPick = userSeesAllStores(user?.roles);
  const ownStoreId = user?.activeStoreId || undefined;
  const storeId = canPick ? shop || undefined : ownStoreId;
  const showShopOf = (raisedAt: string | undefined) => {
    if (canPick && shop && raisedAt && shop !== raisedAt) setShop(raisedAt);
  };
  const noShop = !canPick && !ownStoreId;
  return { storeId, ownStoreId, canPick, noShop, shop, setShop, showShopOf };
}

/** The server's refusal for a login with no shop (api.dependencies NO_SHOP_DETAIL). */
export const NO_SHOP = 'Your login has no shop assigned - ask an admin to assign one.';

/** Wraps every Purchase screen, and the Finance dashboard's supplier figures
 *  (same server rule): a non-admin login with no shop reads the plain message
 *  and nothing else -- no Purchase list is requested, and no refused read
 *  shows as an empty 'we owe nobody' total. */
export function PurchaseShopGate({ children }: { children: ReactNode }) {
  const { noShop } = usePurchaseShop();
  if (!noShop) return <>{children}</>;
  return (
    <p role="alert" className="m-4 p-4 rounded border border-amber-200 bg-amber-50 text-sm text-amber-900">
      {NO_SHOP}
    </p>
  );
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
  return (
    <span className="text-xs text-gray-500">
      New orders deliver to <PurchaseShopName storeId={storeId} />
    </span>
  );
}

/** A shop's name from the store list; its id until the list arrives. */
export function PurchaseShopName({ storeId }: { storeId: string }) {
  const { data } = useStores();
  const row = ((Array.isArray(data) ? data : []) as StoreRow[]).find((s) => s.store_id === storeId);
  return <>{row ? storeName(row) : storeId}</>;
}

/** The admin's shop filter; renders nothing for anyone else. */
export function PurchaseShopPicker() {
  const { canPick, shop, setShop } = usePurchaseShop();
  return canPick ? <ShopSelect shop={shop} setShop={setShop} /> : null;
}

/** Where an admin has the picker, everyone else is told -- read-only -- which
 *  shop every Purchase figure covers: their own, the server's rule. Nothing
 *  for an admin (the picker says it) or a login with no shop to name. */
export function PurchaseShopLabel() {
  const { canPick, ownStoreId } = usePurchaseShop();
  if (canPick || !ownStoreId) return null;
  return (
    <span className="text-sm text-gray-600">
      Shop: <span className="font-medium text-gray-900"><PurchaseShopName storeId={ownStoreId} /></span>
    </span>
  );
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
