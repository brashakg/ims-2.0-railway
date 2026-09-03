// ============================================================================
// IMS 2.0 - Held (parked) bills, shared by both tills
// ============================================================================
// Putting a half-built bill aside to serve the next customer is not a
// convenience: the store ALSO parks a cart automatically when the screen goes
// idle, so without a recall list that work is simply gone. The new POS had no
// way to hold or recall at all, which meant an auto-parked cart could never be
// brought back.
//
// The heavy lifting already lives in the posStore (parkCurrentSale builds and
// tags the snapshot, restoreHeldSale puts it back atomically). What lived in
// POSLayout - and is now here, ONCE - is the part that is easy to get subtly
// wrong: scoping every read and every delete to the CURRENT user.
//
// WHY THAT SCOPING MATTERS: held bills survive logout, so an auto-parked cart
// can be resumed after re-login. On a shared shop terminal that means one
// cashier must never see, recall or delete another's parked cart. Legacy bills
// with no held_by are treated as NOT yours (hidden) rather than as shared - a
// missing owner is not a shared owner.

import { useCallback, useEffect, useState } from 'react';

const STORAGE_KEY = 'ims-held-bills';

export interface HeldBill {
  id: string;
  customer: string;
  items: number;
  total: number;
  heldAt: string;
  held_by?: string | null;
  store_id?: string | null;
  auto?: boolean;
  reason?: string;
  state: unknown;
}

function readAll(): HeldBill[] {
  try {
    const all = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
    return Array.isArray(all) ? all : [];
  } catch {
    return [];
  }
}

/** Remove one bill from the PERSISTED list, leaving every other user's alone. */
function removeFromStorage(billId: string) {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(readAll().filter((b) => b?.id !== billId)),
    );
  } catch {
    /* a full or blocked localStorage must not break the till */
  }
}

/**
 * Held bills for the CURRENT user, with hold / recall / discard.
 *
 * `store` is the posStore instance. `currentUserId` scopes everything: pass the
 * signed-in user's id, never a store id, or two cashiers on one terminal share
 * a parked cart.
 */
export function useHeldBills(store: any, currentUserId: string) {
  const [heldBills, setHeldBills] = useState<HeldBill[]>([]);

  const refresh = useCallback(() => {
    // Only bills whose held_by IS this user. An absent held_by is excluded on
    // purpose - see the header note.
    setHeldBills(readAll().filter((b) => b && b.held_by && b.held_by === currentUserId));
  }, [currentUserId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  /** Park the current cart and clear the till for the next customer. */
  const holdCurrentBill = useCallback(() => {
    // ONE code path with the idle auto-park: the store builds and tags the
    // snapshot (held_by, store_id, auto=false) and pushes it to storage.
    store.parkCurrentSale({ heldBy: currentUserId });
    refresh();
    store.resetTransaction();
  }, [store, currentUserId, refresh]);

  /** Restore a parked bill. Only ever one of this user's own. */
  const recallBill = useCallback(
    (billId: string) => {
      const bill = readAll().find(
        (b) => b?.id === billId && b?.held_by && b.held_by === currentUserId,
      );
      if (!bill) return false;
      // Atomic REPLACE, not a merge into the current cart: restores the cart
      // verbatim plus the bill-level discount and delivery fields.
      store.restoreHeldSale(bill.state);
      removeFromStorage(billId);
      refresh();
      return true;
    },
    [store, currentUserId, refresh],
  );

  /** Throw a parked bill away. Ownership is re-checked against storage, not
   *  against the rendered list, so a stale screen cannot delete someone else's. */
  const discardBill = useCallback(
    (billId: string) => {
      const owned = readAll().some(
        (b) => b?.id === billId && b?.held_by && b.held_by === currentUserId,
      );
      if (!owned) return false;
      removeFromStorage(billId);
      refresh();
      return true;
    },
    [currentUserId, refresh],
  );

  return { heldBills, refresh, holdCurrentBill, recallBill, discardBill };
}

export default useHeldBills;
