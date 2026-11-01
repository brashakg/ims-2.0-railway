// The till's bill key (Idempotency-Key of the sale) is kept in localStorage
// beside the cart draft: after a lost answer the cashier may reload, and a
// fresh key would ring the same bill again as a second sale. Keys are kept
// per bill -- a map {fingerprint: {key, at}} (THE fingerprint: orderPayload's
// billFingerprint, the body the sale sends) -- so two tabs, or a held bill and
// the live one, never overwrite or wipe each other's key. Each press of the
// same bill refreshes its `at`; an entry untouched for 15 minutes is dropped.
// A success, a reset, a recall or a logout drops ONLY that bill's entry; a
// hold carries the entry inside the held snapshot (posStore).
import { useEffect } from 'react';

const STORAGE_KEY = 'ims_pos_bill_keys';

/** The till's own words for a sale whose answer was lost: the bill key is
    kept with the cart, so pressing again replays it -- promised ONLY while
    the server guarantees the replay (its unique index), and only while this
    bill's key is still kept (the surfaces swap to the CHECK words once the
    bill changes or its key is old or gone). */
export const TILL_SALE_UNCONFIRMED =
  'The server did not confirm this sale. Press Complete Sale again - it will not be charged twice.';
export const TILL_SALE_UNCONFIRMED_CHECK =
  'The server did not confirm this sale. Check the Orders screen for it before pressing Complete Sale again.';
/** A same-key press while the first press is still being saved (it holds the
    units): the bill must NOT change, or the next press is another sale. */
export const TILL_SALE_STILL_SAVING =
  'The first press of this sale is still being saved. Do not change the bill - wait a moment and press Complete Sale again.';
export const BILL_KEY_TTL_MS = 15 * 60_000;

export interface BillKeyEntry { key: string; at: number }

function readAll(now: number): Record<string, BillKeyEntry> {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    const out: Record<string, BillKeyEntry> = {};
    for (const [fp, e] of Object.entries(raw || {})) {
      const entry = e as BillKeyEntry;
      if (entry && entry.key && typeof entry.at === 'number' && now - entry.at <= BILL_KEY_TTL_MS) out[fp] = entry;
    }
    return out;
  } catch {
    return {};
  }
}

function writeAll(map: Record<string, BillKeyEntry>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    /* private mode: the key still serves this page */
  }
}

/** This bill's kept key, or null when none is kept (or it is too old). */
export function billKeyEntry(fingerprint: string, now: number = Date.now()): BillKeyEntry | null {
  return readAll(now)[fingerprint] || null;
}

/** Keep `entry` as this bill's key (a recalled held bill), stamped now. */
export function putBillKey(fingerprint: string, key: string, now: number = Date.now()): void {
  writeAll({ ...readAll(now), [fingerprint]: { key, at: now } });
}

/** The key a press of THIS bill uses: `preferred` (the surface's in-memory
 *  key for this bill) first, else the kept one, else a fresh one -- and its
 *  `at` is refreshed, so a cashier re-pressing for 14 minutes keeps it. */
export function billKeyFor(fingerprint: string, now: number = Date.now(), preferred?: string | null): string {
  const map = readAll(now);
  const key = preferred || map[fingerprint]?.key
    || (typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID() : `idem-${now}-${Math.random().toString(36).slice(2)}`);
  map[fingerprint] = { key, at: now };
  writeAll(map);
  return key;
}

/** Drop THIS bill's key (never another tab's or a held bill's). */
export function clearBillKey(fingerprint: string): void {
  const map = readAll(Date.now());
  if (!(fingerprint in map)) {
    writeAll(map); // still prune the expired ones
    return;
  }
  delete map[fingerprint];
  writeAll(map);
}

/** The lost-sale promise ("press again - not charged twice") holds only while
 *  THIS bill's key is kept: once the bill changes, or its key is dropped or
 *  older than the TTL, the surface's message swaps to the CHECK words. */
export function useUnconfirmedWording(
  billFp: string,
  errorMsg: string | null,
  setErrorMsg: (f: (m: string | null) => string | null) => void,
): void {
  useEffect(() => {
    if (errorMsg !== TILL_SALE_UNCONFIRMED) return undefined;
    const toCheck = () => setErrorMsg((m) => (m === TILL_SALE_UNCONFIRMED ? TILL_SALE_UNCONFIRMED_CHECK : m));
    const entry = billKeyEntry(billFp);
    if (!entry) {
      toCheck();
      return undefined;
    }
    const t = setTimeout(toCheck, Math.max(0, entry.at + BILL_KEY_TTL_MS - Date.now()));
    return () => clearTimeout(t);
  }, [billFp, errorMsg, setErrorMsg]);
}
