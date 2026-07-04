import { describe, it, expect } from 'vitest';
import { canonicalCategory, sameCategory } from '../categoryNormalize';

// Owner bug 2026-07-04: products store canonical categories (SUNGLASS/FRAME)
// but filters compared short codes (SG/FR) or legacy plurals (SUNGLASSES) --
// selecting "Sunglasses" showed nothing. Every comparison must round-trip
// through this normalizer.

describe('canonicalCategory', () => {
  it('passes canonical values through unchanged', () => {
    for (const c of [
      'FRAME', 'SUNGLASS', 'OPTICAL_LENS', 'READING_GLASSES', 'CONTACT_LENS',
      'COLORED_CONTACT_LENS', 'WATCH', 'SMARTWATCH', 'SMARTGLASSES',
      'WALL_CLOCK', 'ACCESSORIES', 'SERVICES', 'HEARING_AID',
    ]) {
      expect(canonicalCategory(c)).toBe(c);
    }
  });

  it('maps the short picker codes (Inventory chips)', () => {
    expect(canonicalCategory('SG')).toBe('SUNGLASS');
    expect(canonicalCategory('FR')).toBe('FRAME');
    expect(canonicalCategory('LS')).toBe('OPTICAL_LENS');
    expect(canonicalCategory('CL')).toBe('CONTACT_LENS');
    expect(canonicalCategory('RG')).toBe('READING_GLASSES');
    expect(canonicalCategory('WT')).toBe('WATCH');
    expect(canonicalCategory('CK')).toBe('WALL_CLOCK');
    expect(canonicalCategory('ACC')).toBe('ACCESSORIES');
    expect(canonicalCategory('HA')).toBe('HEARING_AID');
    expect(canonicalCategory('SMTWT')).toBe('SMARTWATCH');
    expect(canonicalCategory('SMTFR')).toBe('SMARTGLASSES');
    expect(canonicalCategory('SMTSG')).toBe('SMARTGLASSES');
  });

  it('maps the legacy plurals (StockAudit / POS / Workshop literals)', () => {
    expect(canonicalCategory('SUNGLASSES')).toBe('SUNGLASS');
    expect(canonicalCategory('FRAMES')).toBe('FRAME');
    expect(canonicalCategory('RX_LENSES')).toBe('OPTICAL_LENS');
    expect(canonicalCategory('CONTACT_LENSES')).toBe('CONTACT_LENS');
    expect(canonicalCategory('COLOUR_CONTACTS')).toBe('COLORED_CONTACT_LENS');
    expect(canonicalCategory('WRIST_WATCHES')).toBe('WATCH');
    expect(canonicalCategory('SMARTWATCHES')).toBe('SMARTWATCH');
  });

  it('is case / separator insensitive', () => {
    expect(canonicalCategory('sunglass')).toBe('SUNGLASS');
    expect(canonicalCategory('Optical Lens')).toBe('OPTICAL_LENS');
    expect(canonicalCategory('contact-lens')).toBe('CONTACT_LENS');
  });

  it('fails open: unknown values pass through upper-snaked (legacy free-form still self-compares)', () => {
    expect(canonicalCategory('CUSTOM THING')).toBe('CUSTOM_THING');
    expect(canonicalCategory('')).toBe('');
    expect(canonicalCategory(null)).toBe('');
    expect(canonicalCategory(undefined)).toBe('');
  });
});

describe('sameCategory', () => {
  it('matches every spelling of the same category (the owner repro)', () => {
    expect(sameCategory('SUNGLASS', 'SG')).toBe(true); // stored vs Inventory chip
    expect(sameCategory('SUNGLASS', 'SUNGLASSES')).toBe(true); // stored vs plural
    expect(sameCategory('FRAME', 'FR')).toBe(true);
    expect(sameCategory('OPTICAL_LENS', 'RX_LENSES')).toBe(true);
  });

  it('does not match different categories or blanks', () => {
    expect(sameCategory('SUNGLASS', 'FR')).toBe(false);
    expect(sameCategory('', '')).toBe(false);
    expect(sameCategory(null, 'SG')).toBe(false);
  });
});
