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
  // The server-fed state list (useGstStateCodes), as the callers hold it.
  const STATES = { '20': 'Jharkhand', '27': 'Maharashtra' };

  it('reads the state out of a GSTIN', () => {
    expect(gstStateCode(JH.gstin)).toBe('20');
    expect(gstStateCode(MH.gstin)).toBe('27');
    expect(gstStateCode('', null, undefined)).toBe('');
  });

  it('same state -> CGST + SGST; different states -> IGST', () => {
    expect(isInterStateSupply(JH, JH, STATES)).toBe(false);
    expect(isInterStateSupply(MH, JH, STATES)).toBe(true);
  });

  it('trusts the GST numbers over a mistyped state name', () => {
    expect(isInterStateSupply({ ...MH, state: 'Jharkhand' }, JH, STATES)).toBe(true);
  });

  it('will not decide the tax off an address when a GSTIN is missing', () => {
    // A declared state is not a registration, and the tax turns on the
    // registration. The SERVER decides from the two GSTINs and nothing else
    // (purchase_invoice_engine.determine_place_of_supply), so reading the
    // address here would preview IGST on an order the server stores as
    // CGST + SGST -- wrong money on screen.
    expect(isInterStateSupply({ state: 'Maharashtra' }, JH, STATES)).toBeNull();
    expect(isInterStateSupply({ state: 'jharkhand' }, JH, STATES)).toBeNull();
  });

  it('answers "cannot tell" rather than guessing', () => {
    // EXACTLY one answer for the unknown side, and it is not `false`.
    // A falsy unknown renders as "Same state - CGST + SGST" on every
    // vendor whose GST number is missing: a wrong TAX LABEL stated with
    // confidence. The caller has to be able to tell the two apart.
    expect(isInterStateSupply({}, JH, STATES)).toBeNull();
    expect(isInterStateSupply(JH, {}, STATES)).toBeNull();
    expect(isInterStateSupply({}, {}, STATES)).toBeNull();
    expect(isInterStateSupply({}, JH, STATES)).not.toBe(false);
  });

  it('a two-digit prefix the server does not list is NOT a state, so no verdict', () => {
    // "88" parses as two digits but names no Indian state. The engine's
    // parser (org_validation, behind determine_place_of_supply) reads NO
    // state off such a GSTIN and books the bill intra-state as its
    // conservative default -- so a screen answering "IGST" here (88 != 20)
    // contradicts the split the server actually stores. Reproduced on the
    // pre-fix tree: the raw-prefix read did exactly that.
    const junk = { gstin: '88AABCU9603R1ZF' };
    expect(isInterStateSupply(junk, JH, STATES)).toBeNull();
    expect(isInterStateSupply(junk, JH, STATES)).not.toBe(true);
    expect(isInterStateSupply(JH, junk, STATES)).toBeNull();
  });

  it('FAIL-CLOSED: no server state list -> no verdict, even on two valid GSTINs', () => {
    // {} is what useGstStateCodes returns before the fetch lands and for the
    // whole session when /entities/meta/options is down. A fail-open grace
    // clause here ("until the list arrives the raw read stands") let a junk
    // "88..." GSTIN print IGST all session on a meta-endpoint failure --
    // and nothing pinned it. This does.
    expect(isInterStateSupply(MH, JH, {})).toBeNull();
    expect(isInterStateSupply(JH, JH, {})).toBeNull();
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
