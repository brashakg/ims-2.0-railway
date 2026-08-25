/**
 * The Indian denomination ladder and its arithmetic -- defined ONCE.
 *
 * This mirrors backend/api/services/cash_denominations.py. Two count sheets in
 * this app each carried their own copy of the faces, the row shape and the
 * tally; a third would have been a defect, not a convenience. The backend is
 * the authority on the stored shape -- this file only says what the screen
 * sends and what it shows while the user is typing.
 */

export type DenomKind = 'note' | 'coin';

export interface DenomRow {
  face: number;
  kind: DenomKind;
  pieces: number;
}

/** The three states, so BLANK IS NEVER ZERO.
 *  NOT_CAPTURED  nobody entered anything -- absence, not an empty drawer.
 *  SUGGESTED     the machine proposed it and a human accepted it untouched.
 *  COUNTED       a human entered or edited the pieces. */
export type CountState = 'COUNTED' | 'SUGGESTED' | 'NOT_CAPTURED';

export interface CashCountInput {
  rows: DenomRow[];
  state: CountState;
}

// Currency in circulation. No Rs 2000 note (RBI withdrew it); the Rs 20 exists
// as BOTH a note and a coin, which is why `kind` is part of a row's identity.
export const NOTE_FACES = [500, 200, 100, 50, 20, 10];
export const COIN_FACES = [20, 10, 5, 2, 1];

export function blankDenoms(): DenomRow[] {
  return [
    ...NOTE_FACES.map((face) => ({ face, kind: 'note' as DenomKind, pieces: 0 })),
    ...COIN_FACES.map((face) => ({ face, kind: 'coin' as DenomKind, pieces: 0 })),
  ];
}

/** Sum of the grid in RUPEES. */
export function denomTotal(rows: DenomRow[]): number {
  return rows.reduce((sum, r) => sum + r.face * (r.pieces || 0), 0);
}

/** Sum of the grid in PAISA (face is rupees; x100 once, at the boundary). */
export function denomTotalPaisa(rows: DenomRow[]): number {
  return rows.reduce((sum, r) => sum + r.face * 100 * (r.pieces || 0), 0);
}

/** Immutably set one row's piece count. Negatives and junk clamp to 0. */
export function setPieces(rows: DenomRow[], index: number, pieces: number): DenomRow[] {
  const n = Math.max(0, Math.floor(pieces) || 0);
  return rows.map((r, i) => (i === index ? { ...r, pieces: n } : r));
}

/** True once ANY face has a piece count -- i.e. somebody actually counted. */
export function hasCount(rows: DenomRow[]): boolean {
  return rows.some((r) => (r.pieces || 0) > 0);
}

/**
 * What the screen sends. An untouched grid returns `undefined` -- the field is
 * simply omitted, which the backend records as NOT_CAPTURED. It must NEVER be
 * sent as a grid of zeroes: a count of nothing and nobody counting are
 * different facts, and only one of them is a reason to go looking for money.
 */
export function toCountInput(rows: DenomRow[], touched: boolean): CashCountInput | undefined {
  if (!touched && !hasCount(rows)) return undefined;
  return { rows: rows.filter((r) => (r.pieces || 0) > 0), state: 'COUNTED' };
}
