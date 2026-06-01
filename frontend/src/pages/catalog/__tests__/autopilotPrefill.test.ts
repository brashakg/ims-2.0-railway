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
  autopilotCandidateToFormValues,
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
    expect(v.discountCategory).toBe('MASS');
  });

  it('defaults gstRate to 18 when category is unknown and no suggestion', () => {
    const v = autopilotCandidateToFormValues(
      candidate({ brand: 'Acme', model: 'X1', category: 'Telescope' })
    );
    expect(v.category).toBe('');
    expect(v.hsnCode).toBe('');
    expect(v.gstRate).toBe('18');
  });
});
