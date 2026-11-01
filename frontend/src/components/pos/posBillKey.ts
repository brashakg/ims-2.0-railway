// The till's bill key (Idempotency-Key of the sale) lives WITH THE CART in
// sessionStorage, not in a component ref: after a lost answer the cashier may
// reload the page, and a fresh key there would ring the same bill again as a
// second sale. The key is bound to the cart's content -- a different cart is a
// different sale and gets a new key -- and is cleared when the sale succeeds.
const STORAGE_KEY = 'ims_pos_bill_key';

/** What makes two presses "the same bill": its lines and its customer. */
export function cartFingerprint(store: any): string {
  const lines = (store?.cart || []).map((i: any) => [
    i.product_id || i.id, Number(i.quantity) || 0, Number(i.unit_price) || 0,
    Number(i.discount_percent) || 0, Number(i.discount_amount) || 0,
  ]);
  return JSON.stringify({ c: store?.customer?.id || store?.customer?.customer_id || null, l: lines });
}

export function billKeyFor(fingerprint: string): string {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    const saved = raw ? JSON.parse(raw) : null;
    if (saved && saved.fp === fingerprint && saved.key) return String(saved.key);
  } catch {
    /* unreadable storage: a fresh key below */
  }
  const key = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID() : `idem-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ key, fp: fingerprint }));
  } catch {
    /* private mode: the key still serves this page */
  }
  return key;
}

export function clearBillKey(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* nothing to clear */
  }
}
