// S4: a collection's stock value is always at cost (pieces x average cost);
// the note beside it says how many pieces have no cost yet (too low by them),
// never "at selling price".
import { describe, expect, it } from 'vitest';
import { basisLabel } from '../collectionsShared';

describe('the note beside a collection stock value', () => {
  it('names the pieces with no cost', () => {
    expect(basisLabel(3)).toBe('3 pcs no cost');
    expect(basisLabel(1)).toBe('1 pc no cost');
  });
  it('is absent when every piece has a cost', () => {
    expect(basisLabel(0)).toBeNull();
    expect(basisLabel(undefined)).toBeNull();
  });
});
