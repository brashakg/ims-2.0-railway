// ============================================================================
// A typed reorder level - ONE parser (owner ruling D12, 2026-09-29: per shop).
// ============================================================================
// The ledger editor, the Reorder dashboard modal and the add/edit product form
// all take a level a person types. Blank (or null/undefined) = NOT SET = no
// low-stock alert, saved as null; 0 is a real level; anything that is not a
// whole number >= 0 is invalid and is never saved as a number.

/** The server's cap on a level (the write model's le=100000). */
export const MAX_LEVEL = 100000;

/** The level a field holds: digits only (no '0x10', '1e2', '2.5', signs) and at
 *  most MAX_LEVEL, else null (= not set). */
export const typedLevel = (value: unknown): number | null => {
  if (value === null || value === undefined) return null;
  const text = typeof value === 'number' ? String(value) : String(value).trim();
  if (!/^\d+$/.test(text)) return null;
  const n = Number(text);
  return n <= MAX_LEVEL ? n : null;
};

/** What a field shows for a level: '' for not set, never -1 or NaN. */
export const levelText = (level: unknown): string => String(typedLevel(level) ?? '');

/** Blank is fine (clears the level); a non-blank value must parse. */
export const isLevelInputValid = (value: unknown): boolean =>
  value === null ||
  value === undefined ||
  String(value).trim() === '' ||
  typedLevel(value) !== null;

export const LEVEL_INPUT_ERROR =
  'Reorder level must be a whole number from 0 to 100000. Leave it blank for not set.';

/** A browser reports value "" for malformed number text ("1e", "--") and sets
 *  validity.badInput: that is invalid, never "blank clears the level". */
export const isBadInput = (el: { validity?: { badInput?: boolean } } | null | undefined): boolean =>
  !!el?.validity?.badInput;
