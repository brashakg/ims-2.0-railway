// ============================================================================
// IMS 2.0 - transfer lifecycle permission-gate tests
// ============================================================================
// These lock the FE button-visibility gates to the backend status+role+store
// guards in routers/transfers.py. The headline case is the P0 fix: Cancel must
// NEVER be offered once a transfer has shipped (in_transit / partially_received
// / received / completed), because cancel does no stock reversal and skips the
// GST deemed-supply mirror invoice.

import { describe, it, expect } from 'vitest';
import {
  canApprove,
  canShip,
  canReceive,
  canComplete,
  canCancel,
  isSourceSide,
  isDestSide,
  type TransferActor,
  type TransferLike,
} from '../transferPermissions';

const t = (over: Partial<TransferLike>): TransferLike => ({
  status: 'draft',
  from_location_id: 'STORE-A',
  to_location_id: 'STORE-B',
  ...over,
});

const admin: TransferActor = { roles: ['ADMIN'], storeIds: [], activeStoreId: '' };
const areaMgrJharkhand: TransferActor = {
  roles: ['AREA_MANAGER'],
  storeIds: ['STORE-A'],
  activeStoreId: 'STORE-A',
};
const storeMgrA: TransferActor = {
  roles: ['STORE_MANAGER'],
  storeIds: ['STORE-A'],
  activeStoreId: 'STORE-A',
};
const storeMgrB: TransferActor = {
  roles: ['STORE_MANAGER'],
  storeIds: ['STORE-B'],
  activeStoreId: 'STORE-B',
};

describe('canCancel — P0: pre-ship statuses ONLY', () => {
  it('is offered on pre-ship statuses for a source-side canceller', () => {
    for (const status of ['draft', 'pending_approval', 'approved', 'picking', 'packed']) {
      expect(canCancel(admin, t({ status }))).toBe(true);
    }
  });

  it('is NEVER offered once the transfer has shipped or beyond', () => {
    for (const status of [
      'in_transit',
      'partially_received',
      'received',
      'completed',
      'cancelled',
      'rejected',
    ]) {
      expect(canCancel(admin, t({ status }))).toBe(false);
    }
  });

  it('requires a cancel role and the source side', () => {
    // Right status, wrong role.
    expect(canCancel(storeMgrA, t({ status: 'approved' }))).toBe(false);
    // Right role + status, but the actor only reaches the DESTINATION store.
    const areaMgrB: TransferActor = {
      roles: ['AREA_MANAGER'],
      storeIds: ['STORE-B'],
      activeStoreId: 'STORE-B',
    };
    expect(canCancel(areaMgrB, t({ status: 'approved' }))).toBe(false);
  });
});

describe('AREA_MANAGER is NOT treated as fully cross-store', () => {
  it('can act only on transfers touching its own stores (source side)', () => {
    // In-region transfer (STORE-A is the source): approve is offered.
    expect(canApprove(areaMgrJharkhand, t({ status: 'pending_approval' }))).toBe(true);
    // Out-of-region transfer (Maharashtra->Maharashtra): NOT offered, so no 403.
    const foreign = t({
      status: 'pending_approval',
      from_location_id: 'STORE-M1',
      to_location_id: 'STORE-M2',
    });
    expect(canApprove(areaMgrJharkhand, foreign)).toBe(false);
    expect(isSourceSide(areaMgrJharkhand, foreign)).toBe(false);
  });
});

describe('SUPERADMIN/ADMIN keep the cross-store bypass', () => {
  it('reaches both sides of any transfer', () => {
    const foreign = t({ from_location_id: 'STORE-X', to_location_id: 'STORE-Y' });
    expect(isSourceSide(admin, foreign)).toBe(true);
    expect(isDestSide(admin, foreign)).toBe(true);
  });
});

describe('multi-store side-check uses the full store_ids set', () => {
  it('a STORE_MANAGER covering two stores sees actions on either store', () => {
    const multi: TransferActor = {
      roles: ['STORE_MANAGER'],
      storeIds: ['STORE-A', 'STORE-B'],
      activeStoreId: 'STORE-A', // active is A, but B is also assigned
    };
    // Receive happens at the destination STORE-B, which the manager also covers.
    expect(canReceive(multi, t({ status: 'in_transit' }))).toBe(true);
  });
});

describe('ship / receive / complete gates match backend status windows', () => {
  it('ship: source-side ship role on approved|packed only', () => {
    expect(canShip(storeMgrA, t({ status: 'approved' }))).toBe(true);
    expect(canShip(storeMgrA, t({ status: 'packed' }))).toBe(true);
    expect(canShip(storeMgrA, t({ status: 'pending_approval' }))).toBe(false);
    expect(canShip(storeMgrA, t({ status: 'in_transit' }))).toBe(false);
  });

  it('receive: dest-side receive role on in_transit|partially_received only', () => {
    expect(canReceive(storeMgrB, t({ status: 'in_transit' }))).toBe(true);
    expect(canReceive(storeMgrB, t({ status: 'partially_received' }))).toBe(true);
    expect(canReceive(storeMgrB, t({ status: 'approved' }))).toBe(false);
    // Source-side manager cannot receive.
    expect(canReceive(storeMgrA, t({ status: 'in_transit' }))).toBe(false);
  });

  it('complete: dest-side complete role on received|partially_received only', () => {
    expect(canComplete(storeMgrB, t({ status: 'received' }))).toBe(true);
    expect(canComplete(storeMgrB, t({ status: 'partially_received' }))).toBe(true);
    expect(canComplete(storeMgrB, t({ status: 'in_transit' }))).toBe(false);
    // WORKSHOP_STAFF is not a complete role.
    const workshopB: TransferActor = {
      roles: ['WORKSHOP_STAFF'],
      storeIds: ['STORE-B'],
      activeStoreId: 'STORE-B',
    };
    expect(canComplete(workshopB, t({ status: 'received' }))).toBe(false);
  });
});
