// The till's bill key (Idempotency-Key of the sale) is kept beside the cart,
// in the SAME storage as the cart draft (localStorage, 'ims-pos-draft'): after
// a lost answer the cashier may reload, and a fresh key would ring the same
// bill again as a second sale. The key is bound to the cart's content (a
// different cart is a different sale), expires after 15 minutes, and is
// dropped on success, on a reset, a hold, a recall and a logout (posStore).
const STORAGE_KEY = 'ims_pos_bill_key';

/** The till's own words for a sale whose answer was lost: the bill key is
    kept with the cart, so pressing again replays it -- promised ONLY while
    the server guarantees the replay (its unique index), and only for the
    same cart (the surfaces swap to the CHECK words once the cart changes). */
export const TILL_SALE_UNCONFIRMED =
  'The server did not confirm this sale. Press Complete Sale again - it will not be charged twice.';
export const TILL_SALE_UNCONFIRMED_CHECK =
  'The server did not confirm this sale. Check the Orders screen for it before pressing Complete Sale again.';
export const BILL_KEY_TTL_MS = 15 * 60_000;

/** What makes two presses "the same bill": its lines and its customer. */
export function cartFingerprint(store: any): string {
  const lines = (store?.cart || []).map((i: any) => [
    i.product_id || i.id, Number(i.quantity) || 0, Number(i.unit_price) || 0,
    Number(i.discount_percent) || 0, Number(i.discount_amount) || 0,
  ]);
  return JSON.stringify({ c: store?.customer?.id || store?.customer?.customer_id || null, l: lines });
}

function readSaved(): { key?: string; fp?: string; at?: number } | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/** The key a press of THIS cart reuses (made fresh when none, another cart's
 *  or an expired one is stored). */
export function billKeyFor(fingerprint: string, now: number = Date.now()): string {
  const saved = readSaved();
  if (saved && saved.fp === fingerprint && saved.key && typeof saved.at === 'number'
      && now - saved.at <= BILL_KEY_TTL_MS) {
    return String(saved.key);
  }
  const key = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID() : `idem-${now}-${Math.random().toString(36).slice(2)}`;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ key, fp: fingerprint, at: now }));
  } catch {
    /* private mode: the key still serves this page */
  }
  return key;
}

export function clearBillKey(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* nothing to clear */
  }
}
