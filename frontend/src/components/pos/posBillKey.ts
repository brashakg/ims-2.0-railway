// The till's bill key (Idempotency-Key of the sale) is kept in localStorage
// beside the cart draft: after a lost answer the cashier may reload, and a
// fresh key would ring the same bill again as a second sale. Keys are kept
// per bill -- a map {bill id: {key, at}} (THE bill id: posStore's bill_id,
// made once for the bill, never derived from its content or the date --
// money review 23; parameters below keep the old name "fingerprint") -- so two tabs, or a held bill and
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

// The promise a lost DEBIT leg makes ("press again - it finishes this
// payment once") holds only while its bill key is kept: once the bill
// changes or the key expires, the next press is a new sale (money review 20).
const DEBIT_PROMISE = 'press Complete Sale again: it finishes this payment once, on this same bill.';

/** The till's words when a debit leg's answer was lost. */
export function tillDebitLegUnconfirmed(labels: string[]): string {
  return `The ${labels.join(' and ')} payment could not be confirmed. Do NOT take another tender for this `
    + `amount - ${DEBIT_PROMISE} (The server refuses any new store-credit or voucher payment for this `
    + 'customer until this one is resolved.)';
}

/** The words once that promise has lapsed. */
export const TILL_DEBIT_CHECK =
  'A store-credit or voucher payment on this bill could not be confirmed. Check the order payments on the '
  + 'Orders screen before taking another tender - pressing Complete Sale now would be a new sale.';

export interface BillKeyEntry { key: string; at: number; pending?: boolean }

function readAll(now: number): Record<string, BillKeyEntry> {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    const out: Record<string, BillKeyEntry> = {};
    for (const [fp, e] of Object.entries(raw || {})) {
      const entry = e as BillKeyEntry;
      // A PENDING key never expires (money review 24): its press may have
      // landed, so only a settled key ages out.
      if (entry && entry.key && typeof entry.at === 'number' && (entry.pending || now - entry.at <= BILL_KEY_TTL_MS)) {
        out[fp] = entry;
      }
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

/** Keep `entry` as this bill's key (a recalled held bill), stamped now --
    still pending if its press was (money review 23). */
export function putBillKey(fingerprint: string, key: string, now: number = Date.now(), pending = false): void {
  writeAll({ ...readAll(now), [fingerprint]: { key, at: now, ...(pending ? { pending: true } : {}) } });
}

/** The key a press of THIS bill uses: `preferred` (the surface's in-memory
 *  key for this bill) first, else the kept one, else a fresh one -- and its
 *  `at` is refreshed, so a cashier re-pressing for 14 minutes keeps it. */
export function billKeyFor(fingerprint: string, now: number = Date.now(), preferred?: string | null): string {
  const map = readAll(now);
  const key = preferred || map[fingerprint]?.key
    || (typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID() : `idem-${now}-${Math.random().toString(36).slice(2)}`);
  // `pending`: a press of this bill is in flight or its answer was lost --
  // the only time the saved draft may keep the customer, member and Rx.
  map[fingerprint] = { key, at: now, pending: true };
  writeAll(map);
  return key;
}

/** A definite answer (a refusal) for this bill: its key stays for the next
 *  press (e.g. after a manager's approval), but nothing is pending any more. */
export function settleBillKey(fingerprint: string): void {
  const map = readAll(Date.now());
  if (!map[fingerprint]) return;
  map[fingerprint] = { ...map[fingerprint], pending: false };
  writeAll(map);
}

/** THE one settle rule after a refused press (money review 28), for every
 *  surface: a bill that may have been saved stays held until it is saved or
 *  the cashier discards it. A key that WAS pending before this press never
 *  settles on a refusal of any kind (only a success clears it, or a Discard
 *  / reset). A key that was not pending settles on a refusal from the order
 *  door, or when this press never reached it (nothing is outstanding). */
export function settleAfterPress(
  fingerprint: string,
  wasPending: boolean,
  res: { pending?: boolean; sent?: boolean; definite?: boolean },
): void {
  if (res.pending || wasPending) return;
  if (res.definite || !res.sent) settleBillKey(fingerprint);
}

/** The login page's "Clear cache": everything goes EXCEPT the till's bill
 *  keys and held bills -- a pending key there may stand for a sale that was
 *  saved (money review 29). */
export function clearCacheKeepingBills(): void {
  const keep: Record<string, string> = {};
  for (const k of [STORAGE_KEY, 'ims-held-bills']) {
    const v = localStorage.getItem(k);
    if (v !== null) keep[k] = v;
  }
  localStorage.clear();
  for (const [k, v] of Object.entries(keep)) localStorage.setItem(k, v);
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

/** The lost-sale promise ("press again - not charged twice"), and a lost
 *  debit leg's ("it finishes this payment once"), hold only while
 *  THIS bill's key is kept: once the bill changes, or its key is dropped or
 *  older than the TTL, the surface's message swaps to the CHECK words. */
export function useUnconfirmedWording(
  billFp: string,
  errorMsg: string | null,
  setErrorMsg: (f: (m: string | null) => string | null) => void,
): void {
  useEffect(() => {
    const debit = !!errorMsg && errorMsg.includes(DEBIT_PROMISE);
    if (errorMsg !== TILL_SALE_UNCONFIRMED && !debit) return undefined;
    const toCheck = () => setErrorMsg((m) => {
      if (m === TILL_SALE_UNCONFIRMED) return TILL_SALE_UNCONFIRMED_CHECK;
      return m && m.includes(DEBIT_PROMISE) ? TILL_DEBIT_CHECK : m;
    });
    const entry = billKeyEntry(billFp);
    if (!entry) {
      toCheck();
      return undefined;
    }
    if (entry.pending) return undefined; // a pending key is kept: the promise holds
    const t = setTimeout(toCheck, Math.max(0, entry.at + BILL_KEY_TTL_MS - Date.now()));
    return () => clearTimeout(t);
  }, [billFp, errorMsg, setErrorMsg]);
}
