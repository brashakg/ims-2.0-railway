// Unit tests for the Catalog Autopilot -> Add Product prefill mapper.
//
// NOTE: vitest is not yet wired into this project's CI (the `test:unit` npm
// script is still a stub and vitest is not a declared dependency), and these
// pure-function tests are excluded from `tsc` by tsconfig (`**/*.test.*`). They
// follow the same convention as the existing src/utils/__tests__ specs so they
// run as soon as vitest is added. The functions under test are intentionally
// side-effect-free so this file needs no DOM / network mocking.

import { describe, it, expect } from 'vitest';
import {
  inferCategoryCode,
  inferCategoryFromText,
  autopilotCandidateToFormValues,
  mapAutopilotCandidate,
  candidateReferences,
  buildProductPayload,
  AUTOPILOT_REFERENCE_ATTR,
} from '../productAddShared';
import type { AutopilotCandidate } from '../../../services/api/catalogAutopilot';

// Minimal candidate factory — only the fields the mapper reads.
function candidate(partial: Partial<AutopilotCandidate>): AutopilotCandidate {
  return {
    candidate_id: 'c1',
    job_id: 'j1',
    source: 'brand_site',
    source_class: 'AUTHORIZED',
    score: 0.9,
    ...partial,
  };
}

describe('inferCategoryCode', () => {
  it('maps human labels and enums to CATEGORIES codes', () => {
    expect(inferCategoryCode('Sunglasses')).toBe('SG');
    expect(inferCategoryCode('SUNGLASS')).toBe('SG');
    expect(inferCategoryCode('Frame')).toBe('FR');
    expect(inferCategoryCode('Contact Lens')).toBe('CL');
    expect(inferCategoryCode('optical lens')).toBe('LS');
    expect(inferCategoryCode('Reading Glasses')).toBe('RG');
    expect(inferCategoryCode('Smart Watch')).toBe('SMTWT');
  });

  it('passes through an exact CATEGORIES code', () => {
    expect(inferCategoryCode('SG')).toBe('SG');
    expect(inferCategoryCode('HA')).toBe('HA');
  });

  it('returns empty string for unknown / blank so the user picks', () => {
    expect(inferCategoryCode('')).toBe('');
    expect(inferCategoryCode(null)).toBe('');
    expect(inferCategoryCode(undefined)).toBe('');
    expect(inferCategoryCode('Telescope')).toBe('');
  });
});

describe('inferCategoryFromText', () => {
  it('infers a category from a free-text title / description', () => {
    expect(inferCategoryFromText('Ray-Ban Aviator Sunglasses')).toBe('SG');
    expect(inferCategoryFromText('Titan Rectangle Eyeglass Frame')).toBe('FR');
    expect(inferCategoryFromText('Acuvue Oasys Contact Lenses')).toBe('CL');
    expect(inferCategoryFromText('Zeiss 1.6 Optical Lens')).toBe('LS');
    expect(inferCategoryFromText('Reading Glasses +2.00')).toBe('RG');
    expect(inferCategoryFromText('Casio Analog Wrist Watch')).toBe('WT');
    expect(inferCategoryFromText('Phonak Hearing Aid BTE')).toBe('HA');
  });

  it('prefers the more specific keyword (sunglass over glass, smart watch over watch)', () => {
    expect(inferCategoryFromText('designer sunglasses')).toBe('SG');
    expect(inferCategoryFromText('Apple Smart Watch series 9')).toBe('SMTWT');
    expect(inferCategoryFromText('reading glasses')).toBe('RG');
  });

  it('combines multiple text fragments and is blank when nothing matches', () => {
    expect(inferCategoryFromText('Ray-Ban', 'RB4105', 'Classic aviator sunglasses')).toBe('SG');
    expect(inferCategoryFromText('Acme', 'X1', 'a generic gadget')).toBe('');
    expect(inferCategoryFromText('', null, undefined)).toBe('');
  });
});

describe('autopilotCandidateToFormValues', () => {
  it('maps brand/model into the attribute keys the form reads', () => {
    const v = autopilotCandidateToFormValues(
      candidate({ brand: 'Ray-Ban', model: 'RB4105', category: 'Sunglasses' })
    );
    expect(v.category).toBe('SG');
    expect(v.attributes.brand_name).toBe('Ray-Ban');
    // Both model keys set so whichever the chosen category renders is filled.
    expect(v.attributes.model_no).toBe('RB4105');
    expect(v.attributes.model_name).toBe('RB4105');
  });

  it('prefers explicit suggested HSN/GST over the category default', () => {
    const v = autopilotCandidateToFormValues(
      candidate({
        brand: 'Ray-Ban', model: 'RB4105', category: 'Sunglasses',
        suggested_hsn: '900410', suggested_gst_rate: 18,
      })
    );
    expect(v.hsnCode).toBe('900410');
    expect(v.gstRate).toBe('18');
  });

  it('falls back to the category HSN/GST when no suggestion is present', () => {
    // Frame -> 4-digit HSN 9003 @ 5% per the canonical GST 2.0 map.
    const v = autopilotCandidateToFormValues(
      candidate({ brand: 'Ray-Ban', model: 'RX5154', category: 'Frame' })
    );
    expect(v.hsnCode).toBe('9003');
    expect(v.gstRate).toBe('5');
  });

  it('uses description, falling back to usp', () => {
    const withDesc = autopilotCandidateToFormValues(
      candidate({ description: 'Full description', usp: 'Short USP' })
    );
    expect(withDesc.description).toBe('Full description');
    const uspOnly = autopilotCandidateToFormValues(
      candidate({ description: null, usp: 'Short USP' })
    );
    expect(uspOnly.description).toBe('Short USP');
  });

  it('carries specs as attributes but drops the category label', () => {
    const v = autopilotCandidateToFormValues(
      candidate({
        brand: 'Oakley', model: 'OO9208', category: 'Sunglasses',
        specs: { shape: 'Rectangle', gender: 'Men', category: 'Sunglasses' },
      })
    );
    expect(v.attributes.shape).toBe('Rectangle');
    expect(v.attributes.gender).toBe('Men');
    // The spec-level "category" must NOT leak in as a form field.
    expect(v.attributes.category).toBeUndefined();
  });

  it('leaves pricing blank and online sync off (Autopilot is not a price source)', () => {
    const v = autopilotCandidateToFormValues(candidate({ brand: 'Titan', model: 'T123' }));
    expect(v.mrp).toBe('');
    expect(v.offerPrice).toBe('');
    expect(v.costPrice).toBe('');
    expect(v.syncToShopify).toBe(false);
    // Tier left blank so the operator must consciously pick it (no silent MASS).
    expect(v.discountCategory).toBe('');
  });

  it('defaults gstRate to 18 when category is unknown and no suggestion', () => {
    const v = autopilotCandidateToFormValues(
      candidate({ brand: 'Acme', model: 'X1', category: 'Telescope' })
    );
    expect(v.category).toBe('');
    expect(v.hsnCode).toBe('');
    expect(v.gstRate).toBe('18');
  });

  it('infers the category from the title when the candidate has NO category', () => {
    // The common scraped case: no `category` field at all. Previously this
    // produced category='' -> a field-less form -> the staged brand/model looked
    // "lost". Now the title/brand text drives the inference.
    const v = autopilotCandidateToFormValues(
      candidate({ brand: 'Ray-Ban', model: 'RB3025', title: 'Ray-Ban Aviator Sunglasses', category: null })
    );
    expect(v.category).toBe('SG');
    expect(v.attributes.brand_name).toBe('Ray-Ban');
    expect(v.attributes.model_no).toBe('RB3025');
  });

  it('never DROPS brand/model/description even when no category can be inferred', () => {
    const v = autopilotCandidateToFormValues(
      candidate({
        brand: 'Acme', model: 'X1', category: null,
        title: 'Acme X1 gadget', description: 'A generic gadget with no category signal',
      })
    );
    // No category (nothing matched) but the identity + description survive so the
    // operator only has to pick a category to reveal + populate the fields.
    expect(v.category).toBe('');
    expect(v.attributes.brand_name).toBe('Acme');
    expect(v.attributes.model_no).toBe('X1');
    expect(v.attributes.model_name).toBe('X1');
    expect(v.description).toBe('A generic gadget with no category signal');
  });

  it('carries candidate image_urls into the images array', () => {
    const v = autopilotCandidateToFormValues(
      candidate({
        brand: 'Ray-Ban', model: 'RB4105', category: 'Sunglasses',
        image_urls: ['https://cdn.example/a.jpg', 'https://cdn.example/b.jpg'],
      })
    );
    expect(v.images).toEqual(['https://cdn.example/a.jpg', 'https://cdn.example/b.jpg']);
  });

  it('has an empty images array when the candidate has no image_urls', () => {
    const v = autopilotCandidateToFormValues(candidate({ brand: 'Titan', model: 'T1' }));
    expect(v.images).toEqual([]);
  });

  it('v2: maps scraped specs onto the declared category fields', () => {
    const v = autopilotCandidateToFormValues(
      candidate({
        brand: 'Ray-Ban', model: 'RB4105', category: 'SUNGLASS',
        specs: { 'Lens Width': '52 mm', Bridge: '18', 'Temple Length': '140 mm', 'Frame Color': 'Black' },
      })
    );
    expect(v.category).toBe('SG');
    expect(v.attributes.lens_size).toBe('52');
    expect(v.attributes.bridge_width).toBe('18');
    expect(v.attributes.temple_length).toBe('140');
    expect(v.attributes.colour_code).toBe('Black');
    // Raw scraped keys are still kept (harmless passthrough).
    expect(v.attributes['Lens Width']).toBe('52 mm');
  });

  it('v2: parses the size string from the candidate size / title', () => {
    const v = autopilotCandidateToFormValues(
      candidate({ brand: 'Ray-Ban', model: 'RB3025', category: 'SUNGLASS', size: '58-14-135' })
    );
    expect(v.attributes.lens_size).toBe('58');
    expect(v.attributes.bridge_width).toBe('14');
    expect(v.attributes.temple_length).toBe('135');
  });

  it('v2: colour falls back to the search colour when the scrape lacks one', () => {
    const v = autopilotCandidateToFormValues(
      candidate({ brand: 'Ray-Ban', model: 'RB4105', category: 'SUNGLASS', color: '601/58' })
    );
    expect(v.attributes.colour_code).toBe('601/58');
  });
});

describe('mapAutopilotCandidate (rich v2 result)', () => {
  it('reports which fields were auto-filled + the unmapped extras', () => {
    const res = mapAutopilotCandidate(
      candidate({
        brand: 'Ray-Ban', model: 'RB4105', category: 'SUNGLASS',
        specs: { 'Lens Width': '52 mm', 'Hinge Type': 'Spring' },
      })
    );
    expect(res.autoFilled).toEqual(
      expect.arrayContaining(['brand_name', 'model_no', 'lens_size'])
    );
    expect(res.extras['Hinge Type']).toBe('Spring');
    expect(res.extras['Lens Width']).toBeUndefined();
  });

  it('a category override (the form pick) wins and drives the mapping', () => {
    const res = mapAutopilotCandidate(
      candidate({
        brand: 'Titan', model: 'T123', category: 'SUNGLASS',
        specs: { 'Case Diameter': '42 mm' },
      }),
      'WT'
    );
    expect(res.values.category).toBe('WT');
    expect(res.values.attributes.dial_size).toBe('42');
  });

  it('AI attributes fill gaps but never beat the deterministic mapping', () => {
    const res = mapAutopilotCandidate(
      candidate({
        brand: 'Ray-Ban', model: 'RB4105', category: 'SUNGLASS',
        specs: { 'Lens Width': '52 mm' },
        ai_attributes: { lens_size: '99', colour_code: '601/58', bogus: 'zap' },
      })
    );
    // Deterministic 52 wins over the AI's 99; the AI fills the colour gap.
    expect(res.values.attributes.lens_size).toBe('52');
    expect(res.values.attributes.colour_code).toBe('601/58');
    // Unknown attribute names from the AI are ignored.
    expect(res.values.attributes.bogus).toBeUndefined();
    expect(res.autoFilled).toEqual(expect.arrayContaining(['lens_size', 'colour_code']));
  });

  it('carries reference URLs + persists them into the payload attributes', () => {
    const res = mapAutopilotCandidate(
      candidate({
        brand: 'Ray-Ban', model: 'RB4105', category: 'SUNGLASS',
        source_url: 'https://www.ray-ban.com/india/p/rb4105-black',
        references: [{ source: 'brand_site', url: 'https://www.ray-ban.com/india/p/rb4105-black' }],
      })
    );
    expect(res.referenceUrls).toEqual(['https://www.ray-ban.com/india/p/rb4105-black']);
    expect(res.values.attributes[AUTOPILOT_REFERENCE_ATTR]).toBe(
      'https://www.ray-ban.com/india/p/rb4105-black'
    );
    // ...and buildProductPayload forwards it onto the created product doc.
    const payload = buildProductPayload({
      ...res.values,
      mrp: '9990',
      discountCategory: 'PREMIUM',
    });
    expect(payload.attributes[AUTOPILOT_REFERENCE_ATTR]).toBe(
      'https://www.ray-ban.com/india/p/rb4105-black'
    );
  });

  it('no references -> no reference attribute (no empty junk persisted)', () => {
    const res = mapAutopilotCandidate(candidate({ brand: 'Titan', model: 'T1' }));
    expect(res.referenceUrls).toEqual([]);
    expect(res.values.attributes[AUTOPILOT_REFERENCE_ATTR]).toBeUndefined();
  });
});

describe('candidateReferences (reference chips)', () => {
  it('derives {domain, url} chips from references, deduped', () => {
    const chips = candidateReferences(
      candidate({
        references: [
          { source: 'brand_site', url: 'https://www.ray-ban.com/india/p/rb4105' },
          { source: 'brand_site', url: 'https://www.ray-ban.com/india/p/rb4105' },
          { source: 'marketplace', url: 'https://amazon.in/rb4105' },
        ],
      })
    );
    expect(chips).toEqual([
      { domain: 'ray-ban.com', url: 'https://www.ray-ban.com/india/p/rb4105' },
      { domain: 'amazon.in', url: 'https://amazon.in/rb4105' },
    ]);
  });

  it('falls back to source_url, then url', () => {
    expect(candidateReferences(candidate({ source_url: 'https://x.example/p/1' }))).toEqual([
      { domain: 'x.example', url: 'https://x.example/p/1' },
    ]);
    expect(candidateReferences(candidate({ url: 'https://y.example/p/2' }))).toEqual([
      { domain: 'y.example', url: 'https://y.example/p/2' },
    ]);
  });

  it('drops invalid URLs and returns [] when there is nothing', () => {
    expect(candidateReferences(candidate({ source_url: 'not-a-url' }))).toEqual([]);
    expect(candidateReferences(candidate({}))).toEqual([]);
  });
});
