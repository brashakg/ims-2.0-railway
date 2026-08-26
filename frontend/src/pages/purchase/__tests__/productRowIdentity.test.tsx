// ============================================================================
// IMS 2.0 - Purchase Order product row identity + place of supply
// (owner items 1 and 3, 2026-08-26)
// ============================================================================
// The picker row used to be "brand + name" over "sku + cost", which on a
// 40-line order left a buyer unable to tell two colours of one model apart.
// These pin what the row now shows, and how the CGST/SGST-vs-IGST question is
// answered from the two GST numbers.

import { describe, it, expect, vi } from 'vitest';

vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({}) }));
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => ({ user: null }) }));
vi.mock('../../../services/api', () => ({ vendorsApi: {}, productApi: {} }));
vi.mock('../../../services/api/stores', () => ({ storeApi: { getStore: vi.fn() } }));

import { productHeadline, productDetail, blockingGaps } from '../PurchaseOrderForm';
import { isInterStateSupply, gstStateCode } from '../../../constants/gst';

describe('purchase order product row — telling two frames apart', () => {
  const RAY_BAN = {
    product_id: 'p1',
    sku: 'FR-RB-0012',
    name: 'Ray-Ban Aviator Classic',
    brand: 'Ray-Ban',
    model: 'RB3025',
    color: 'GOLD',
    size: '58',
    mrp: 6990,
  };

  it('leads with brand and model', () => {
    expect(productHeadline(RAY_BAN)).toBe('Ray-Ban RB3025');
  });

  it('carries colour, size and MRP — what separates two of the same model', () => {
    expect(productDetail(RAY_BAN)).toBe('GOLD · 58 · MRP ₹6,990');
    const otherColour = { ...RAY_BAN, product_id: 'p2', color: 'GUNMETAL' };
    expect(productHeadline(otherColour)).toBe(productHeadline(RAY_BAN));
    expect(productDetail(otherColour)).not.toBe(productDetail(RAY_BAN));
  });

  it('reads identity out of `attributes` as well as the flat columns', () => {
    const canonical = {
      product_id: 'p3',
      sku: 'FR-OK-0001',
      attributes: {
        brand_name: 'Oakley',
        model_no: 'OO9208',
        colour_code: 'MATTE BLACK',
        lens_size: '38',
      },
      mrp: 15900,
    };
    expect(productHeadline(canonical)).toBe('Oakley OO9208');
    expect(productDetail(canonical)).toBe('MATTE BLACK · 38 · MRP ₹15,900');
  });

  it('falls back to the product name when a legacy row has no model', () => {
    expect(productHeadline({ product_id: 'p4', sku: 'X1', name: 'Assorted Cloth' })).toBe(
      'Assorted Cloth',
    );
    expect(productDetail({ product_id: 'p4', sku: 'X1' })).toBe('');
  });
});

describe('place of supply from the two GST numbers', () => {
  // Jharkhand = 20, Maharashtra = 27.
  const JH = { gstin: '20AABCU9603R1ZM', state: 'Jharkhand' };
  const MH = { gstin: '27AACCA1234B1Z2', state: 'Maharashtra' };

  it('reads the state out of a GSTIN', () => {
    expect(gstStateCode(JH.gstin)).toBe('20');
    expect(gstStateCode(MH.gstin)).toBe('27');
    expect(gstStateCode('', null, undefined)).toBe('');
  });

  it('same state -> CGST + SGST; different states -> IGST', () => {
    expect(isInterStateSupply(JH, JH)).toBe(false);
    expect(isInterStateSupply(MH, JH)).toBe(true);
  });

  it('trusts the GST numbers over a mistyped state name', () => {
    expect(isInterStateSupply({ ...MH, state: 'Jharkhand' }, JH)).toBe(true);
  });

  it('falls back to the declared states when a GSTIN is missing', () => {
    expect(isInterStateSupply({ state: 'Maharashtra' }, JH)).toBe(true);
    expect(isInterStateSupply({ state: 'jharkhand' }, JH)).toBe(false);
  });

  it('answers "cannot tell" rather than guessing', () => {
    expect(isInterStateSupply({}, JH)).toBeNull();
    expect(isInterStateSupply(JH, {})).toBeNull();
  });
});

describe('what actually blocks a purchase order being sent', () => {
  it('a missing cost is NOT a blocker — the rate on this order becomes the cost', () => {
    expect(blockingGaps({ done_gaps: ['cost_price'] })).toEqual([]);
  });

  it('names every other gap in words the buyer can act on', () => {
    expect(blockingGaps({ done_gaps: ['cost_price', 'mrp', 'hsn_code'] })).toEqual([
      'MRP',
      'HSN number',
    ]);
  });

  it('a fully catalogued product blocks nothing', () => {
    expect(blockingGaps({ done_gaps: [] })).toEqual([]);
    expect(blockingGaps({})).toEqual([]);
  });
});
