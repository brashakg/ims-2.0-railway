import { describe, it, expect } from 'vitest';
import { typedLevel, levelText, isLevelInputValid } from '../reorderLevel';

describe('typed reorder level (one parser: ledger, Reorder dashboard, add/edit form)', () => {
  it.each(['', '   ', null, undefined])('blank (%j) is not set', (v) => {
    expect(typedLevel(v)).toBeNull();
    expect(levelText(v)).toBe('');
    expect(isLevelInputValid(v)).toBe(true);
  });

  it('0 is a real level, not blank', () => {
    expect(typedLevel('0')).toBe(0);
    expect(typedLevel(0)).toBe(0);
    expect(levelText(0)).toBe('0');
  });

  it('a whole number >= 0 parses', () => {
    expect(typedLevel('12')).toBe(12);
    expect(typedLevel(' 7 ')).toBe(7);
  });

  it.each(['-1', '2.5', 'abc', '1e', -3, NaN])('%j is never a level and is invalid input', (v) => {
    expect(typedLevel(v)).toBeNull();
    expect(levelText(v)).toBe('');
    expect(isLevelInputValid(v)).toBe(false);
  });
});
