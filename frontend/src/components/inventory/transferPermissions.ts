// ============================================================================
// IMS 2.0 - Stock transfer lifecycle permission gates (pure)
// ============================================================================
// These predicates mirror the backend status + role + store guards in
// routers/transfers.py 1:1 so the UI never offers a lifecycle action the server
// would 400/403. They are extracted from StockTransferManagement so the
// load-bearing safety logic — above all the pre-ship-only Cancel gate — is
// directly unit-testable, not buried in a rendered component.

export interface TransferActor {
  roles: string[];
  storeIds: string[];
  activeStoreId: string;
}

export interface TransferLike {
  status: string;
  from_location_id: string;
  to_location_id: string;
}

// Role lists — kept identical to the role arrays each endpoint checks in
// routers/transfers.py.
export const APPROVE_ROLES = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER'];
export const SHIP_ROLES = ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'WORKSHOP_STAFF'];
export const RECEIVE_ROLES = ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'WORKSHOP_STAFF'];
export const COMPLETE_ROLES = ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'];
export const CANCEL_ROLES = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'];
// "Missing on the way": found / sent for write-off by either shop's managers;
// the write-off itself is admin-only (owner ruling 2026-08-25).
export const MISSING_ROLES = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'];
export const WRITE_OFF_ROLES = ['SUPERADMIN', 'ADMIN'];

// Only SUPERADMIN/ADMIN get the backend's cross-store bypass (user_store_scope
// in dependencies.py). AREA_MANAGER is bounded to their store_ids by
// can_access_store_scoped, so they are NOT cross-store here — otherwise the UI
// would surface source-side actions on out-of-region transfers that then 403.
export const CROSS_STORE_ROLES = ['SUPERADMIN', 'ADMIN'];

// Cancel is allowed ONLY before anything ships. Once a transfer is in_transit /
// partially_received / received the source units have already left (and, for
// received, landed at the destination); cancelling then does NO stock reversal
// and permanently skips the inter-entity/inter-state GST deemed-supply mirror
// invoice (which books only on /complete). So Cancel must disappear the moment
// the transfer ships. This is an ALLOWLIST, not a denylist, deliberately.
export const CANCELLABLE_STATUSES = [
  'draft',
  'pending_approval',
  'approved',
  'picking',
  'packed',
];

const norm = (s: string): string => (s || '').toLowerCase();

const hasRole = (actor: TransferActor, allowed: string[]): boolean =>
  (actor.roles || []).some((r) => allowed.includes(r));

const isCrossStore = (actor: TransferActor): boolean =>
  hasRole(actor, CROSS_STORE_ROLES);

// A store is reachable if it is any of the user's assigned store_ids OR the
// active store — mirroring the backend can_access_store_scoped set. This makes
// visibility correct for a multi-store STORE_MANAGER/WORKSHOP_STAFF too (they
// no longer have to switch active store to act on their other store's side).
const reachableStores = (actor: TransferActor): Set<string> =>
  new Set<string>([
    ...(actor.storeIds || []),
    ...(actor.activeStoreId ? [actor.activeStoreId] : []),
  ]);

export const isSourceSide = (actor: TransferActor, t: TransferLike): boolean =>
  isCrossStore(actor) || reachableStores(actor).has(t.from_location_id);

export const isDestSide = (actor: TransferActor, t: TransferLike): boolean =>
  isCrossStore(actor) || reachableStores(actor).has(t.to_location_id);

export const canApprove = (actor: TransferActor, t: TransferLike): boolean =>
  hasRole(actor, APPROVE_ROLES) &&
  norm(t.status) === 'pending_approval' &&
  isSourceSide(actor, t);

// S3: pack = scan pieces OUT, any time after approval until the box ships.
export const canPack = (actor: TransferActor, t: TransferLike): boolean =>
  hasRole(actor, SHIP_ROLES) &&
  ['approved', 'picking', 'packed'].includes(norm(t.status)) &&
  isSourceSide(actor, t);

// A box ships only once pieces are scanned into it (status packed).
export const canShip = (actor: TransferActor, t: TransferLike): boolean =>
  hasRole(actor, SHIP_ROLES) && norm(t.status) === 'packed' && isSourceSide(actor, t);

// The e-way bill no. and vehicle no. (PUT /transfers/{id}: the sending side's
// managers). The e-way bill is raised against the tax invoice number minted at
// Ship, so the sending side must reach it while the box is on the way.
export const EWAY_ROLES = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'];
export const canEditEway = (actor: TransferActor, t: TransferLike): boolean =>
  hasRole(actor, EWAY_ROLES) && ['packed', 'in_transit'].includes(norm(t.status)) && isSourceSide(actor, t);

export const canReceive = (actor: TransferActor, t: TransferLike): boolean =>
  hasRole(actor, RECEIVE_ROLES) &&
  ['in_transit', 'partially_received'].includes(norm(t.status)) &&
  isDestSide(actor, t);

export const canComplete = (actor: TransferActor, t: TransferLike): boolean =>
  hasRole(actor, COMPLETE_ROLES) &&
  ['in_transit', 'received', 'partially_received'].includes(norm(t.status)) &&
  isDestSide(actor, t);

// S3: either end may call off a box that has not shipped -- the asking shop
// withdraws its request, the sending shop declines to send.
export const canCancel = (actor: TransferActor, t: TransferLike): boolean =>
  hasRole(actor, CANCEL_ROLES) &&
  CANCELLABLE_STATUSES.includes(norm(t.status)) &&
  (isSourceSide(actor, t) || isDestSide(actor, t));

// A missing piece is marked found by a manager of the shop where it turned up.
export const canMarkFound = (
  actor: TransferActor,
  t: TransferLike,
  at: 'source' | 'destination',
): boolean =>
  hasRole(actor, MISSING_ROLES) && (at === 'source' ? isSourceSide(actor, t) : isDestSide(actor, t));

export const canRequestWriteOff = (actor: TransferActor, t: TransferLike): boolean =>
  hasRole(actor, MISSING_ROLES) && (isSourceSide(actor, t) || isDestSide(actor, t));

export const canWriteOff = (actor: TransferActor): boolean => hasRole(actor, WRITE_OFF_ROLES);

// The Delivery Challan button (owner 2026-10-08: a valued challan -- a move
// between two GST registrations -- is printed by managers and accounts only).
// No role list here: every transfer reply carries the server's own answer
// (can_print_challan, from print_documents.may_print_challan), and an older
// reply without it offers nothing rather than a button that answers 403.
export const canPrintChallan = (t: { can_print_challan?: boolean }): boolean =>
  t.can_print_challan === true;

// Who may ask another shop for stock / send their own (create_transfer's roles).
export const CREATE_ROLES = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'];
export const hasCreateRole = (actor: TransferActor): boolean => hasRole(actor, CREATE_ROLES);
