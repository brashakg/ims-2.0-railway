// ============================================================================
// Inventory store-mode helper — COMPATIBILITY RE-EXPORT
// ----------------------------------------------------------------------------
// W1.4 / RC-B: the ONLINE-store detector was lifted to utils/storeMode.ts so
// every surface (shell banner, POS, purchase, till, transfers) shares the one
// source of truth. This module re-exports it so existing inventory imports
// (InventoryPage, tests) keep working unchanged. Import NEW code from
// '../../utils/storeMode' directly.
// ============================================================================

export { ONLINE_STORE_IDS, isOnlineStore, isOnlineStoreId } from '../../utils/storeMode';
export type { StoreModeInput } from '../../utils/storeMode';
