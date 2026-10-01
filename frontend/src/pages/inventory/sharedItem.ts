// The ONE label for a SKU that is live on Shopify but shares its Shopify item
// with another IMS product (the writer refuses it, so it is fixed in IMS). The
// Online Stock page and the Inventory page both import it; the backend state
// is stock_allocation.SHARES_SHOPIFY_ITEM / `shares_item` on the online-status row.
export const SHARES_ITEM_LABEL = 'Shares a Shopify item with another product - fix in IMS';

// The ONE rule for "is this SKU online / is its online state unknown". A SKU
// that shares a Shopify item is live (online) even when `online` is null, and
// is never "unknown". The stat tile and the stock page both use these.
interface OnlineRow {
  online?: boolean | null;
  shares_item?: boolean | number | null;
}

export function isOnlineRow(o: OnlineRow | null | undefined): boolean {
  return !!o && (!!o.online || !!o.shares_item);
}

export function isOnlineUnknown(o: OnlineRow | null | undefined): boolean {
  return !!o && o.online === null && !o.shares_item;
}
