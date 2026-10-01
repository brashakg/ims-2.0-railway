/**
 * THE match for a stock-row search box (Inventory > Stock, New transfer).
 *
 * The text fields (name, SKU, brand) match on any part. A unit's own IMS code
 * (the stock row's `unit_barcodes`: every unit on hand at this shop, new
 * 'BV0000000042' or old 'BV--91FA3858') matches whole, in any letter case --
 * a scanned or typed label names ONE unit, so 'BV' alone must not list every
 * product.
 */
export function stockRowMatches(
  query: string,
  texts: Array<string | undefined>,
  unitCodes: string[] = [],
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    texts.some((t) => t?.toLowerCase().includes(q)) ||
    unitCodes.some((c) => c.toLowerCase() === q)
  );
}
