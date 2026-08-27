// ============================================================================
// The smartglasses cataloguing form — the two things that silently empty it
// ----------------------------------------------------------------------------
// The 2026-08-25 rework collapsed the two smartglasses picker tiles into one
// ("Smartglasses", SMTFR) and kept SMTSG alive only as a FIELD-LIST ALIAS for
// codes already stored on products. Two failure modes hide behind that:
//
//  1. Delete the alias line and getCategoryFields('SMTSG') returns NO FIELDS AT
//     ALL whenever the category registry has not resolved yet (the fetch is a
//     module-level promise; on a cold load it has not). The operator opens a
//     stored smart glass and sees an empty form.
//  2. The synonym tables still RESOLVE free text and legacy spellings to
//     'SMTSG' — a code with no tile in the picker, so nothing highlights and
//     the review row shows a dash.
//
// The backend half of the same contract (form list == server registry, field
// for field) is pinned by
// backend/tests/test_smartglass_listing.py::test_frontend_list_and_backend_registry_agree_field_for_field.
// ============================================================================

import { describe, it, expect } from 'vitest';
import {
  CATEGORIES,
  categoryName,
  getCategoryFields,
  inferCategoryCode,
  inferCategoryFromText,
} from '../productAddShared';

const names = (code: string) => getCategoryFields(code).map((f) => f.name);

describe('SMTSG is a field-list alias, not an empty category', () => {
  it('offers the SAME fields as the one remaining tile', () => {
    // No registry fetch has resolved in this test process, which is exactly the
    // cold-load case: getCategoryFields falls back to the local metadata.
    expect(names('SMTFR').length).toBeGreaterThan(30);
    expect(names('SMTSG')).toEqual(names('SMTFR'));
  });

  it('carries the electronics fields, not the old short list', () => {
    const set = new Set(names('SMTSG'));
    for (const f of [
      'camera_mp',
      'camera_type',
      'video_resolution',
      'audio_type',
      'microphone_count',
      'voice_assistant',
      'controls',
      'battery_life_hours',
      'charging_case',
      'connectivity',
      'storage_gb',
      'prescription_ready',
      'generation',
    ]) {
      expect(set.has(f)).toBe(true);
    }
  });
});

describe('every inferred smart-eyewear code has a tile to highlight', () => {
  const tiles = new Set(CATEGORIES.map((c) => c.code));

  it('there is exactly one smartglasses tile', () => {
    expect(CATEGORIES.filter((c) => c.name.startsWith('Smartglasses')).map((c) => c.code))
      .toEqual(['SMTFR']);
  });

  it.each([
    'SMARTSUNGLASS',
    'SMARTSUNGLASSES',
    'SMTSG',
    'SMARTGLASSES',
    'SMARTGLASS',
    'SMTFR',
  ])('inferCategoryCode(%s) lands on a real tile', (raw) => {
    const code = inferCategoryCode(raw);
    expect(tiles.has(code)).toBe(true);
    expect(categoryName(code)).not.toBe('');
  });

  it.each([
    'Ray-Ban Meta smart sunglasses',
    'Ray-Ban Meta smart glasses',
    'Rayban META RW4006 SmartGlasses',
  ])('inferCategoryFromText(%s) lands on a real tile', (text) => {
    const code = inferCategoryFromText(text);
    expect(tiles.has(code)).toBe(true);
    expect(categoryName(code)).not.toBe('');
  });
});
