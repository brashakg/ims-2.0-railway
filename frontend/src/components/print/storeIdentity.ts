// ============================================================================
// IMS 2.0 - Print issuing-identity resolver (frontend)
// ============================================================================
// Every printout must carry the identity of the store that ISSUED the document
// (legal/trade name, address, GSTIN, phone, per-brand logo), sourced from the
// document's OWN store_id -> the store record -> its entity_id -> the entity.
//
// GET /stores/{id} returns a RAW snake_case Mongo doc (store_name, store_code,
// state_code, ...) but the camelCase `Store` type + GSTInvoice read storeName /
// storeCode / stateCode. That type lie is the root cause of blank invoice
// headers. This module is the ONE adapter at the API boundary that:
//   - normalises a raw store doc into BOTH the camelCase `Store` (for GSTInvoice)
//     and the snake_case `StoreLike` (for legalPrimitives), and
//   - resolves the store's parent entity (for the statutory identity block),
//     folding the store's own GSTIN into the entity gstins when the entity is
//     missing one for this store's state.
//
// Fail-soft: a failed fetch returns nulls/empty; callers decide whether to
// block a statutory print (a tax invoice with no GSTIN must not be issued).

import { storeApi } from '../../services/api';
import { entitiesApi, type Entity } from '../../services/api/entities';
import type { Store } from '../../types';
import type { EntityLike, StoreLike } from './legalPrimitives';

/** A raw store doc as returned by GET /stores/{id} (snake_case Mongo shape). */
export interface RawStoreDoc {
  store_id?: string;
  store_code?: string;
  store_name?: string;
  brand?: string;
  gstin?: string;
  entity_id?: string;
  address?: string;
  city?: string;
  state?: string;
  state_code?: string;
  pincode?: string;
  phone?: string;
  email?: string;
  [k: string]: unknown;
}

/** Resolved identity for printing a store-specific document. */
export interface StoreIdentity {
  /** camelCase Store shape GSTInvoice / types/index.ts expect. */
  store: Store;
  /** snake_case StoreLike shape legalPrimitives.buildLegalHeader expects. */
  storeLike: StoreLike;
  /** Parent legal entity (legal_name, pan, cin, gstins[], invoice.logo_url). */
  entity: EntityLike | null;
  /** True when the store name resolved (i.e. a real, configured store). */
  hasIdentity: boolean;
  /** True when a GSTIN is present (required to issue a GST tax invoice). */
  hasGstin: boolean;
}

function s(v: unknown): string {
  return v === null || v === undefined ? '' : String(v).trim();
}

/** Normalise a raw snake_case store doc into the camelCase `Store` shape so the
 *  GST invoice header (storeName/storeCode/stateCode/...) renders. Accepts a
 *  doc that is already camelCase (idempotent). */
export function toStoreView(raw: RawStoreDoc | Store | null | undefined): Store {
  const r = (raw || {}) as Record<string, unknown>;
  const get = (snake: string, camel: string): string =>
    s(r[snake] !== undefined && r[snake] !== '' ? r[snake] : r[camel]);
  return {
    id: get('store_id', 'id'),
    storeCode: get('store_code', 'storeCode'),
    storeName: get('store_name', 'storeName'),
    brand: (r.brand as Store['brand']) || ('BETTER_VISION' as Store['brand']),
    gstin: get('gstin', 'gstin'),
    address: get('address', 'address'),
    city: get('city', 'city'),
    state: get('state', 'state'),
    stateCode: get('state_code', 'stateCode'),
    pincode: get('pincode', 'pincode'),
    latitude: Number(r.latitude ?? 0),
    longitude: Number(r.longitude ?? 0),
    geoFenceRadius: Number(r.geofence_radius_m ?? r.geoFenceRadius ?? 0),
    isActive: r.is_active !== undefined ? !!r.is_active : (r.isActive as boolean) ?? true,
    isHQ: r.is_hq !== undefined ? !!r.is_hq : (r.isHQ as boolean) ?? false,
    enabledCategories: (r.enabled_categories || r.enabledCategories || []) as Store['enabledCategories'],
    openingTime: get('opening_time', 'openingTime') || '10:00',
    closingTime: get('closing_time', 'closingTime') || '21:00',
  };
}

/** Build the snake_case StoreLike the legal-header builder consumes. Carries the
 *  brand + gstin so logo/brand selection + GSTIN fallback work. */
export function toStoreLike(raw: RawStoreDoc | Store | null | undefined): StoreLike {
  const r = (raw || {}) as Record<string, unknown>;
  const get = (snake: string, camel: string): string =>
    s(r[snake] !== undefined && r[snake] !== '' ? r[snake] : r[camel]);
  return {
    name: get('store_name', 'storeName'),
    store_name: get('store_name', 'storeName'),
    store_code: get('store_code', 'storeCode'),
    brand: get('brand', 'brand'),
    address: get('address', 'address'),
    city: get('city', 'city'),
    state: get('state', 'state'),
    state_code: get('state_code', 'stateCode'),
    pincode: get('pincode', 'pincode'),
    phone: get('phone', 'phone'),
    email: get('email', 'email'),
    gstin: get('gstin', 'gstin'),
  };
}

/** Map a full backend Entity onto the EntityLike the legal header consumes,
 *  folding the store's own GSTIN in when the entity lacks one for the store's
 *  state, so the header always shows the issuing store's GSTIN (Rule 46). */
export function toEntityLike(
  entity: Entity | null | undefined,
  store?: { gstin?: string; stateCode?: string; state?: string; storeName?: string },
): EntityLike | null {
  if (!entity && !store?.storeName) return null;
  const base: EntityLike = entity
    ? {
        legal_name: entity.legal_name || entity.name,
        name: entity.name,
        pan: entity.pan,
        cin: entity.cin || entity.llpin,
        registered_address: entity.registered_address,
        registered_phone: entity.registered_phone,
        registered_email: entity.registered_email,
        website: entity.website,
        invoice: entity.invoice,
        gstins: entity.gstins ? [...entity.gstins] : [],
      }
    : {
        // No entity: synthesize a thin one from the store (never a fixed brand).
        legal_name: store?.storeName,
        name: store?.storeName,
        gstins: [],
      };
  if (store?.gstin) {
    const list = base.gstins || [];
    const haveForState = list.some(
      (g) => s(g.state_code) && s(g.state_code) === s(store.stateCode),
    );
    const haveThisGstin = list.some((g) => s(g.gstin) === s(store.gstin));
    if (!haveForState && !haveThisGstin) {
      base.gstins = [
        ...list,
        {
          gstin: store.gstin,
          state_code: store.stateCode || '',
          state_name: store.state || '',
          is_primary: list.length === 0,
        },
      ];
    }
  }
  return base;
}

/**
 * Resolve the full issuing identity for a document's own store. Fetches the
 * store by id (snake-case doc), normalises it, then fetches its parent entity.
 * Returns nulls/empty on failure (fail-soft) -- callers gate statutory prints.
 */
export async function resolveStoreIdentity(storeId: string): Promise<StoreIdentity> {
  let raw: RawStoreDoc | null;
  try {
    raw = (await storeApi.getStore(storeId)) as RawStoreDoc;
  } catch {
    raw = null;
  }

  const store = toStoreView(raw);

  let entity: Entity | null = null;
  const entityId = raw?.entity_id as string | undefined;
  if (entityId) {
    try {
      const res = await entitiesApi.get(entityId);
      entity = res?.entity ?? null;
    } catch {
      entity = null;
    }
  }

  const entityLike = toEntityLike(entity, {
    gstin: store.gstin,
    stateCode: store.stateCode,
    state: store.state,
    storeName: store.storeName,
  });
  const storeLike = toStoreLike(raw);
  return {
    store,
    storeLike,
    entity: entityLike,
    hasIdentity: !!store.storeName,
    hasGstin: !!store.gstin,
  };
}
