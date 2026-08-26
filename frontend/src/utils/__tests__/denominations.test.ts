/**
 * The denomination ladder and the one rule that matters on this screen:
 * BLANK IS NOT ZERO.
 *
 * A cashier who counts nothing and a cashier who counts an empty drawer are
 * telling you two different things, and only one of them is a reason to go
 * looking for money. `toCountInput` is where that distinction is either kept
 * or thrown away on the way to the server.
 */
import { describe, it, expect } from 'vitest';
import {
  NOTE_FACES,
  COIN_FACES,
  blankDenoms,
  denomTotal,
  denomTotalPaisa,
  setPieces,
  hasCount,
  toCountInput,
} from '../denominations';

describe('the ladder', () => {
  it('carries every face in circulation and no withdrawn Rs 2000 note', () => {
    expect(NOTE_FACES).toEqual([500, 200, 100, 50, 20, 10]);
    expect(COIN_FACES).toEqual([20, 10, 5, 2, 1]);
    expect(NOTE_FACES).not.toContain(2000);
  });

  it('gives the Rs 20 and Rs 10 a note row AND a coin row', () => {
    // Face alone is not an identity: both exist as a note and as a coin, and a
    // drawer holding ten Rs 20 coins is not a drawer holding ten Rs 20 notes.
    const rows = blankDenoms();
    expect(rows).toHaveLength(NOTE_FACES.length + COIN_FACES.length);
    const keys = rows.map((r) => `${r.kind}-${r.face}`);
    expect(new Set(keys).size).toBe(rows.length);
    expect(keys).toContain('note-20');
    expect(keys).toContain('coin-20');
  });
});

describe('the arithmetic', () => {
  it('totals in rupees and in exact integer paisa', () => {
    const rows = [
      { face: 500, kind: 'note' as const, pieces: 3 },
      { face: 20, kind: 'coin' as const, pieces: 7 },
    ];
    expect(denomTotal(rows)).toBe(1640);
    expect(denomTotalPaisa(rows)).toBe(164000);
    expect(Number.isInteger(denomTotalPaisa(rows))).toBe(true);
  });

  it('clamps a negative or junk piece count to zero without touching its neighbours', () => {
    const rows = blankDenoms();
    const set = setPieces(setPieces(rows, 0, 4), 1, -9);
    expect(set[0].pieces).toBe(4);
    expect(set[1].pieces).toBe(0);
    expect(setPieces(rows, 0, NaN)[0].pieces).toBe(0);
    // Immutable: the original grid is untouched.
    expect(rows[0].pieces).toBe(0);
  });
});

describe('blank is not zero', () => {
  it('sends NOTHING when the grid was never touched', () => {
    // undefined -> the field is omitted -> the server records NOT_CAPTURED.
    expect(toCountInput(blankDenoms(), false)).toBeUndefined();
    expect(hasCount(blankDenoms())).toBe(false);
  });

  it('never sends a grid of zeroes, which would read as an emptied drawer', () => {
    const sent = toCountInput(blankDenoms(), false);
    expect(sent).toBeUndefined();
    // The failure this guards against: a payload whose rows are all-zero.
    expect(sent?.rows ?? []).toEqual([]);
  });

  it('sends a real count of nothing when the cashier did open the grid', () => {
    const sent = toCountInput(blankDenoms(), true);
    expect(sent).toEqual({ rows: [], state: 'COUNTED' });
  });

  it('sends only the faces that were actually there', () => {
    const rows = setPieces(setPieces(blankDenoms(), 0, 2), 2, 5);
    const sent = toCountInput(rows, true);
    // ASSERT THE SET AND THE COUNT, not just the total.
    expect(sent?.rows).toHaveLength(2);
    expect(new Set(sent?.rows.map((r) => `${r.kind}-${r.face}-${r.pieces}`))).toEqual(
      new Set(['note-500-2', 'note-100-5']),
    );
    expect(denomTotal(sent?.rows ?? [])).toBe(1500);
  });
});
