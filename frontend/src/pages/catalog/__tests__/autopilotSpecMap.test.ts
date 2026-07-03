// Unit tests for the Catalog Autopilot v2 scraped-spec -> category-field mapper.
// Pure functions — no DOM / network mocking needed. The category registry is
// NOT loaded here, so getCategoryFields deterministically uses the local
// CATEGORY_FIELDS metadata (the offline fallback), making these tests stable.

import { describe, it, expect } from 'vitest';
import {
  mapSpecsToCategoryFields,
  parseEyewearSize,
  coerceModality,
  normSpecKey,
} from '../autopilotSpecMap';

describe('normSpecKey', () => {
  it('lower-cases and collapses punctuation to single spaces', () => {
    expect(normSpecKey('Lens Width (mm)')).toBe('lens width mm');
    expect(normSpecKey('BRIDGE-SIZE')).toBe('bridge size');
    expect(normSpecKey('  Replacement__Schedule ')).toBe('replacement schedule');
  });
});

describe('parseEyewearSize', () => {
  it('parses the classic 52-18-140 triple', () => {
    expect(parseEyewearSize('52-18-140')).toEqual({ lens: '52', bridge: '18', temple: '140' });
  });

  it('parses the square-glyph and slash separators', () => {
    expect(parseEyewearSize('52□18-140')).toEqual({ lens: '52', bridge: '18', temple: '140' });
    expect(parseEyewearSize('52/18/140')).toEqual({ lens: '52', bridge: '18', temple: '140' });
    expect(parseEyewearSize('52 18 140')).toEqual({ lens: '52', bridge: '18', temple: '140' });
  });

  it('parses a lens-bridge pair without a temple', () => {
    expect(parseEyewearSize('54-16')).toEqual({ lens: '54', bridge: '16' });
  });

  it('parses a bare two-digit lens size', () => {
    expect(parseEyewearSize('52')).toEqual({ lens: '52' });
    expect(parseEyewearSize('58 mm')).toEqual({ lens: '58' });
  });

  it('parses out of surrounding text (a title)', () => {
    expect(parseEyewearSize('Ray-Ban RB3025 Aviator 58-14-135 Gold')).toEqual({
      lens: '58', bridge: '14', temple: '135',
    });
  });

  it('rejects implausible numbers', () => {
    expect(parseEyewearSize('99-99-999')).toEqual({});
    expect(parseEyewearSize('12-99')).toEqual({});
    expect(parseEyewearSize('')).toEqual({});
    expect(parseEyewearSize(null)).toEqual({});
  });
});

describe('coerceModality', () => {
  it('maps common phrasings onto the modality enum', () => {
    expect(coerceModality('Daily disposable')).toBe('DAILY');
    expect(coerceModality('Monthly')).toBe('MONTHLY');
    expect(coerceModality('2-week replacement')).toBe('FORTNIGHTLY');
    expect(coerceModality('Quarterly (3 months)')).toBe('QUARTERLY');
    expect(coerceModality('Yearly / annual')).toBe('YEARLY');
    expect(coerceModality('Colour cosmetic lens')).toBe('COLOR');
  });

  it('prefers quarterly over the generic month check', () => {
    expect(coerceModality('3 month replacement')).toBe('QUARTERLY');
  });

  it('returns empty for unknowns', () => {
    expect(coerceModality('sometimes')).toBe('');
    expect(coerceModality('')).toBe('');
  });
});

describe('mapSpecsToCategoryFields', () => {
  it('parses the frame size string into three fields (the big win)', () => {
    const { mapped } = mapSpecsToCategoryFields('FR', { Size: '52-18-140' }, {});
    expect(mapped.lens_size).toBe('52');
    expect(mapped.bridge_width).toBe('18');
    expect(mapped.temple_length).toBe('140');
  });

  it("uses the user's Size search input when the scrape lacks one", () => {
    const { mapped } = mapSpecsToCategoryFields('SG', {}, { size: '54-16-145' });
    expect(mapped.lens_size).toBe('54');
    expect(mapped.bridge_width).toBe('16');
    expect(mapped.temple_length).toBe('145');
  });

  it('falls back to the title for the size string', () => {
    const { mapped } = mapSpecsToCategoryFields(
      'SG', {}, {}, 'Ray-Ban RB3025 Aviator 58-14-135', undefined
    );
    expect(mapped.lens_size).toBe('58');
    expect(mapped.bridge_width).toBe('14');
    expect(mapped.temple_length).toBe('135');
  });

  it('matches synonym keys case/punctuation-insensitively and strips units', () => {
    const { mapped } = mapSpecsToCategoryFields(
      'SG',
      {
        'Lens Width (mm)': '52 mm',
        'BRIDGE': '18mm',
        'Temple Length': '140 mm',
        'Frame Color': 'Matte Black',
      },
      {}
    );
    expect(mapped.lens_size).toBe('52');
    expect(mapped.bridge_width).toBe('18');
    expect(mapped.temple_length).toBe('140');
    expect(mapped.colour_code).toBe('Matte Black');
  });

  it("falls back to the user's Colour input when the scrape lacks a colour", () => {
    const { mapped } = mapSpecsToCategoryFields('SG', {}, { color: '601/58' });
    expect(mapped.colour_code).toBe('601/58');
  });

  it('does NOT let a scraped colour beat nothing into the wrong field (dial vs frame)', () => {
    // Watch: "Dial Colour" must land on dial_colour, not colour_code.
    const { mapped } = mapSpecsToCategoryFields('WT', { 'Dial Colour': 'Blue' }, {});
    expect(mapped.dial_colour).toBe('Blue');
    expect(mapped.colour_code).toBeUndefined();
  });

  it('maps contact-lens specs incl. the modality enum, BC/DIA and pack', () => {
    const { mapped } = mapSpecsToCategoryFields(
      'CL',
      {
        'Replacement schedule': 'Monthly disposable',
        'Base Curve (BC)': '8.6',
        'Diameter (DIA)': '14.2 mm',
        'Lenses per box': '6 lenses',
      },
      {}
    );
    expect(mapped.modality).toBe('MONTHLY');
    expect(mapped.base_curve).toBe('8.6');
    expect(mapped.diameter).toBe('14.2');
    expect(mapped.pack).toBe('6');
  });

  it('maps watch dial size from case diameter', () => {
    const { mapped } = mapSpecsToCategoryFields(
      'WT',
      { 'Case Diameter': '42 mm', 'Strap Color': 'Brown' },
      {}
    );
    expect(mapped.dial_size).toBe('42');
    expect(mapped.belt_colour).toBe('Brown');
  });

  it('maps optical-lens index and coating onto the select options', () => {
    const { mapped } = mapSpecsToCategoryFields(
      'LS',
      { 'Refractive Index': '1.6', Coating: 'Anti-reflective coating' },
      {}
    );
    expect(mapped.index).toBe('1.60');
    expect(mapped.coating).toBe('ARC');
  });

  it('keeps unmapped specs as extras (nothing scraped is lost)', () => {
    const { mapped, extras } = mapSpecsToCategoryFields(
      'SG',
      { 'Lens Width': '52 mm', 'Hinge Type': 'Spring', 'Made In': 'Italy' },
      {}
    );
    expect(mapped.lens_size).toBe('52');
    // "Made In" now maps onto the declared country_of_origin field (rich eyewear
    // field set); "Hinge Type" has no declared home so it stays an extra.
    expect(mapped.country_of_origin).toBe('Italy');
    expect(extras['Hinge Type']).toBe('Spring');
    expect(extras['Made In']).toBeUndefined(); // consumed -> country_of_origin
    expect(extras['Lens Width']).toBeUndefined(); // consumed
  });

  it('drops the spec-level category label entirely', () => {
    const { mapped, extras } = mapSpecsToCategoryFields('SG', { category: 'Sunglasses' }, {});
    expect(mapped.category).toBeUndefined();
    expect(extras.category).toBeUndefined();
  });

  it('fills model/brand from the query for declared identity fields', () => {
    const { mapped } = mapSpecsToCategoryFields('SG', {}, { brand: 'Ray-Ban', model: 'RB4105' });
    expect(mapped.brand_name).toBe('Ray-Ban');
    expect(mapped.model_no).toBe('RB4105');
  });

  it('returns empty when there is no category (mapper needs declared fields)', () => {
    const { mapped, extras } = mapSpecsToCategoryFields('', { 'Lens Width': '52' }, {});
    expect(mapped).toEqual({});
    expect(extras['Lens Width']).toBe('52');
  });

  it('deterministic scrape value wins over the query fallback', () => {
    const { mapped } = mapSpecsToCategoryFields(
      'SG',
      { 'Colour Code': '601/58' },
      { color: 'Black' }
    );
    expect(mapped.colour_code).toBe('601/58');
  });
});
