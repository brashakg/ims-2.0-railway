// Unit tests for the canonical category-registry merge in productAddShared.
//
// Catalog field-parity (#17): the three product-entry doors (Quick Add, Guided,
// Rapid Grid) derive their REQUIRED/optional flags from the backend canonical
// registry (GET /products/categories) -- there is no second copy of the
// required-ness rule on the FE. getCategoryFields() merges the registry's
// required flags onto the local UI metadata and appends any server-required
// field the local metadata lacks (so a server-required field can never be
// invisible / unfillable). These tests lock that merge behaviour.
//
// The registry fetch is mocked so the merge logic is tested deterministically
// (no network). vitest runs each file in isolation, so the module-level
// registry cache in productAddShared starts empty here.

import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the products API module the shared layer imports for the registry fetch.
vi.mock('../../../services/api/products', () => ({
  productApi: {
    getCategoryRegistry: vi.fn(),
  },
}));

import { productApi } from '../../../services/api/products';
import {
  loadCategoryRegistry,
  isCategoryRegistryLoaded,
  registryRequiredFields,
  getCategoryFields,
  CATEGORY_FIELDS,
} from '../productAddShared';

const REGISTRY = {
  categories: [
    {
      code: 'FRAME',
      sku_prefix: 'FR',
      name: 'Frame',
      required_fields: ['brand_name', 'model_no', 'colour_code'],
      optional_fields: ['subbrand', 'size', 'frame_material', 'frame_type'],
      fields: [
        { name: 'brand_name', label: 'Brand Name', required: true },
        { name: 'model_no', label: 'Model No', required: true },
        { name: 'colour_code', label: 'Colour Code', required: true },
        { name: 'subbrand', label: 'Sub Brand', required: false },
        { name: 'size', label: 'Size', required: false },
        { name: 'frame_material', label: 'Frame Material', required: false },
        { name: 'frame_type', label: 'Frame Type', required: false },
      ],
      forced_discount_category: null,
    },
    {
      code: 'HEARING_AID',
      sku_prefix: 'HA',
      name: 'Hearing Aid',
      // `vent_size` is a SYNTHETIC server-known field with NO local UI metadata
      // under HA (CATEGORY_FIELDS.HA does not declare it) -> the merge must still
      // surface it so a server-known field can never be invisible / unfillable.
      // It is marked required here to lock the strongest case: a server-REQUIRED
      // field absent locally is appended AND marked required.
      required_fields: ['brand_name', 'model_no', 'vent_size'],
      optional_fields: ['subbrand', 'serial_no', 'machine_capacity', 'machine_type'],
      fields: [
        { name: 'brand_name', label: 'Brand Name', required: true },
        { name: 'model_no', label: 'Model No', required: true },
        { name: 'vent_size', label: 'Vent Size', required: true },
        { name: 'machine_capacity', label: 'Machine Capacity', required: false },
        { name: 'machine_type', label: 'Machine Type', required: false },
      ],
      forced_discount_category: 'NON_DISCOUNTABLE',
    },
    {
      code: 'ACCESSORIES',
      sku_prefix: 'ACC',
      name: 'Accessories',
      // Registry requires a field (model_name) the LOCAL ACC metadata marks
      // optional -- the merge must flip it to required + (if missing) append it.
      required_fields: ['brand_name', 'model_name'],
      optional_fields: ['subbrand', 'size', 'pack'],
      fields: [
        { name: 'brand_name', label: 'Brand Name', required: true },
        { name: 'model_name', label: 'Model Name', required: true },
        { name: 'subbrand', label: 'Sub Brand', required: false },
        { name: 'size', label: 'Size', required: false },
        { name: 'pack', label: 'Pack Size', required: false },
      ],
      forced_discount_category: null,
    },
  ],
};

describe('canonical category registry merge', () => {
  beforeEach(async () => {
    vi.mocked(productApi.getCategoryRegistry).mockResolvedValue(REGISTRY as never);
    await loadCategoryRegistry();
  });

  it('loads the registry into the module cache', () => {
    expect(isCategoryRegistryLoaded()).toBe(true);
  });

  it('exposes the registry required set per FE picker code (alias-resolved)', () => {
    // FE picker code FR -> canonical FRAME.
    const fr = registryRequiredFields('FR');
    expect(fr).not.toBeNull();
    expect(fr).toEqual(new Set(['brand_name', 'model_no', 'colour_code']));
    // HA -> HEARING_AID (incl. the synthetic registry-only required vent_size).
    expect(registryRequiredFields('HA')).toEqual(
      new Set(['brand_name', 'model_no', 'vent_size']),
    );
  });

  it('overrides each local field required flag from the registry', () => {
    const fields = getCategoryFields('FR');
    const byName = Object.fromEntries(fields.map((f) => [f.name, f]));
    // Registry-required -> required true.
    expect(byName.brand_name.required).toBe(true);
    expect(byName.model_no.required).toBe(true);
    expect(byName.colour_code.required).toBe(true);
    // Registry-optional -> required false (even if a local flag differed).
    expect(byName.subbrand.required).toBe(false);
  });

  it('flips a locally-optional field to required when the registry requires it', () => {
    // ACC: local metadata may mark model_name optional; the registry requires it.
    const fields = getCategoryFields('ACC');
    const modelName = fields.find((f) => f.name === 'model_name');
    expect(modelName).toBeDefined();
    expect(modelName?.required).toBe(true);
    // And registryRequiredFields agrees.
    expect(registryRequiredFields('ACC')).toEqual(
      new Set(['brand_name', 'model_name']),
    );
  });

  it('appends a registry field the local metadata lacks (never hidden)', () => {
    // HA local metadata has no `vent_size` input; the merge must append it so a
    // server-known field is always visible + collectible. As a registry-REQUIRED
    // field, the appended field must also carry required=true.
    const localNames = new Set((CATEGORY_FIELDS.HA || []).map((f) => f.name));
    const fields = getCategoryFields('HA');
    const byName = Object.fromEntries(fields.map((f) => [f.name, f]));
    expect(byName.vent_size).toBeDefined();
    expect(byName.vent_size.required).toBe(true);
    // It was genuinely absent locally (so this proves the append path ran).
    expect(localNames.has('vent_size')).toBe(false);
  });

  it('every registry-required field is present + marked required after merge', () => {
    for (const code of ['FR', 'HA', 'ACC']) {
      const req = registryRequiredFields(code)!;
      const byName = Object.fromEntries(
        getCategoryFields(code).map((f) => [f.name, f]),
      );
      req.forEach((name) => {
        expect(byName[name], `${code}.${name} must be rendered`).toBeDefined();
        expect(byName[name].required, `${code}.${name} must be required`).toBe(true);
      });
    }
  });
});
