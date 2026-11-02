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
  canPack,
  canMarkFound,
  canRequestWriteOff,
  canWriteOff,
  canPrintChallan,
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

  it('S3: either end may call off a box that has not shipped', () => {
    expect(canCancel(storeMgrA, t({ status: 'packed' }))).toBe(true);
    expect(canCancel(storeMgrB, t({ status: 'pending_approval' }))).toBe(true);
  });

  it('requires a cancel role and one end of the move', () => {
    const workshopA: TransferActor = { roles: ['WORKSHOP_STAFF'], storeIds: ['STORE-A'], activeStoreId: 'STORE-A' };
    expect(canCancel(workshopA, t({ status: 'approved' }))).toBe(false);
    const areaMgrC: TransferActor = { roles: ['AREA_MANAGER'], storeIds: ['STORE-C'], activeStoreId: 'STORE-C' };
    expect(canCancel(areaMgrC, t({ status: 'approved' }))).toBe(false);
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
  it('pack: source-side ship role from approval until the box ships', () => {
    expect(canPack(storeMgrA, t({ status: 'approved' }))).toBe(true);
    expect(canPack(storeMgrA, t({ status: 'packed' }))).toBe(true);
    expect(canPack(storeMgrA, t({ status: 'pending_approval' }))).toBe(false);
    expect(canPack(storeMgrA, t({ status: 'in_transit' }))).toBe(false);
    expect(canPack(storeMgrB, t({ status: 'approved' }))).toBe(false);
  });

  it('ship: source-side ship role on a packed box only (S3: pieces scanned first)', () => {
    expect(canShip(storeMgrA, t({ status: 'approved' }))).toBe(false);
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

  it('complete: dest-side complete role once the box is on the way', () => {
    expect(canComplete(storeMgrB, t({ status: 'received' }))).toBe(true);
    expect(canComplete(storeMgrB, t({ status: 'partially_received' }))).toBe(true);
    // S3: accepting a box with nothing scanned reports every piece missing.
    expect(canComplete(storeMgrB, t({ status: 'in_transit' }))).toBe(true);
    expect(canComplete(storeMgrB, t({ status: 'packed' }))).toBe(false);
    expect(canComplete(storeMgrA, t({ status: 'received' }))).toBe(false);
    // WORKSHOP_STAFF is not a complete role.
    const workshopB: TransferActor = {
      roles: ['WORKSHOP_STAFF'],
      storeIds: ['STORE-B'],
      activeStoreId: 'STORE-B',
    };
    expect(canComplete(workshopB, t({ status: 'received' }))).toBe(false);
  });
});

describe("canPrintChallan — the server's own answer, never a role list", () => {
  it('offers the Delivery Challan only when the server says this user prints it', () => {
    expect(canPrintChallan({ can_print_challan: true })).toBe(true);
    expect(canPrintChallan({ can_print_challan: false })).toBe(false);
  });

  it('offers nothing when the reply does not say (an older server)', () => {
    expect(canPrintChallan({})).toBe(false);
  });
});

describe('missing on the way', () => {
  it('found is marked by a manager of the shop where the piece turned up', () => {
    expect(canMarkFound(storeMgrA, t({}), 'source')).toBe(true);
    expect(canMarkFound(storeMgrA, t({}), 'destination')).toBe(false);
    expect(canMarkFound(storeMgrB, t({}), 'destination')).toBe(true);
  });

  it('either shop may send it to the admin; only an admin writes it off', () => {
    expect(canRequestWriteOff(storeMgrB, t({}))).toBe(true);
    expect(canWriteOff(storeMgrA)).toBe(false);
    expect(canWriteOff(areaMgrJharkhand)).toBe(false);
    expect(canWriteOff(admin)).toBe(true);
  });
});
