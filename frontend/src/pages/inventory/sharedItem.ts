// The ONE label for a SKU that is live on Shopify but shares its Shopify item
// with another IMS product (the writer refuses it, so it is fixed in IMS). The
// Online Stock page and the Inventory page both import it; the backend state
// is stock_allocation.SHARES_SHOPIFY_ITEM / `shares_item` on the online-status row.
export const SHARES_ITEM_LABEL = 'Shares a Shopify item with another product - fix in IMS';
