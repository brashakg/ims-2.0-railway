// ============================================================================
// A typed reorder level - ONE parser (owner ruling D12, 2026-09-29: per shop).
// ============================================================================
// The ledger editor, the Reorder dashboard modal and the add/edit product form
// all take a level a person types. Blank (or null/undefined) = NOT SET = no
// low-stock alert, saved as null; 0 is a real level; anything that is not a
// whole number >= 0 is invalid and is never saved as a number.

/** The level a field holds: a whole number >= 0, else null (= not set). */
export const typedLevel = (value: unknown): number | null => {
  const n =
    value === null || value === undefined || String(value).trim() === ''
      ? NaN
      : Number(value);
  return Number.isInteger(n) && n >= 0 ? n : null;
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
  'Reorder level must be a whole number, 0 or more. Leave it blank for not set.';
