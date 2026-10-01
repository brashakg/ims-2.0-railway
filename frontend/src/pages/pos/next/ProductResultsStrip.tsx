// ============================================================================
// IMS 2.0 - POS product results strip (Wave 4, owner spec 6)
// ============================================================================
// "Product entry compact - one row of controls, one row of results." This is
// the RESULTS row: the controls row above it is the existing BarcodeScanner.
//
// MOUNT (BillingSurface left column, directly under the scanner):
//
//   const [productQuery, setProductQuery] = useState('');
//   <BarcodeScanner onScan={handleScan} onManualSearch={setProductQuery} ... />
//   <ProductResultsStrip
//     storeId={activeStoreId}
//     query={productQuery}
//     onBlocked={setErrorMsg}
//     onPicked={() => setProductQuery('')}
//   />
//
// It owns NO money math. The price gate (offer>MRP, zero/NaN) and the
// cart-line mapping are productIntake.ts - the same two brains the scan path
// and the classic POS call, so all three doors price a line identically.
// Search is the existing useProducts query hook with the same key the classic
// surface uses, so browsing here costs no extra network trip.
//
// THE CARD IS SHARED. The general counter's browse grid re-typed this card's
// internals and they drifted five ways (id fallback missing `id`, a second
// offer-price read, a re-typed stock chain, no low-stock badge, no result
// cap). <ProductCard> below is now the ONE implementation - the strip renders
// it layout="strip", the counter layout="grid". Each surface keeps its own
// SURROUNDING layout (one scrolling row here, a category grid there); the
// card's data reads, badges and disabled states exist once.
//
// STOCK (F46, owner-approved 2026-09-28): the number on a card is this shop's
// sellable count from useSellableStock -- the oversell guard's own number,
// re-read on every sale here, every remount and refocus, and every 30 s. The
// guard at Complete sale stays the authority (another till can sell the last
// unit between two reads). The surface fetches it ONCE for the rows it shows
// and hands every card that one answer; the card looks its own figure up by
// productIdOf, the id the hook asked with, so no surface re-types the lookup.

import { Package } from 'lucide-react';
import { usePOSStore } from '../../../stores/posStore';
import { useProducts, useSellableStock } from '../../../hooks/usePOSQueries';
import { posPriceGuard, cartItemFromProduct, productIdOf } from '../../../components/pos/productIntake';
import type { SellableStock } from '../../../services/api/inventory';

interface ProductResultsStripProps {
  /** Terminal's active store - results are scoped to its stock. */
  storeId: string;
  /** Manual-search text from the scanner row. Empty = browse the store list. */
  query: string;
  /** Money-guard block message, or null to clear. Wire to the surface's ONE
      error banner - this strip renders no banner of its own. */
  onBlocked?: (message: string | null) => void;
  /** Fired after a line is added (e.g. to clear the search box). */
  onPicked?: () => void;
}

const money = (v: number) => `₹${Math.round(v || 0).toLocaleString('en-IN')}`;

/** Result cap, shared by every surface that renders product cards. A capped
    list must SAY it is capped (the grid shows a narrow-the-search hint) so
    stock is never silently hidden. */
export const MAX_PRODUCT_RESULTS = 24;

/** A minted product name ends " - {Colour}" (product_naming.build_product_name
    on the server). Split it off so a card truncates the MODEL, never the
    colour: two colours of one frame otherwise read identically. A name with
    no separator has no colour line. ponytail: reads the name, not the colour
    attributes - an explicit name without " - " can still be cut. */
function nameParts(name: string): [string, string] {
  const i = name.lastIndexOf(' - ');
  return i > 0 ? [name.slice(0, i), name.slice(i + 3)] : [name, ''];
}

export type ProductCardLayout = 'strip' | 'grid';

/**
 * The one POS product card. Every read here mirrors the intake brain:
 * the offer chain is the SAME spelling chain posPriceGuard uses
 * (offer_price || offerPrice || mrp - productIntake.ts), so the price on the
 * card is the price the cart line will carry.
 */
export function ProductCard({
  product,
  layout,
  onPick,
  stock,
}: {
  product: any;
  layout: ProductCardLayout;
  onPick: () => void;
  /** The screen's ONE useSellableStock answer. The card reads its own
      figure by productIdOf -- the id the hook asked with. A null figure = the
      sale guard does not gate this row; no figure = not known yet. Neither
      shows a badge or blocks. */
  stock?: SellableStock;
}) {
  const store = usePOSStore();
  const id = productIdOf(product);
  const figure = id ? stock?.sellable[id] : undefined;
  const mrp = product.mrp || 0;
  const offer = product.offer_price || product.offerPrice || mrp;
  const counted = typeof figure === 'number';
  // Owner ruling 2026-08-25: oversell = BLOCK. A row this shop cannot sell
  // cannot be billed here; a row with no figure is not blocked.
  const outOfStock = counted && figure <= 0;
  const lowStock = counted && figure > 0 && figure <= 3;
  const inCart = (store.cart || []).some((i) => i.product_id === id);

  const stockBadge =
    counted ? (
      <span
        className={`text-[9px] px-1 py-0.5 rounded font-medium shrink-0 ${
          outOfStock
            ? 'bg-red-100 text-red-600'
            : lowStock
              ? 'bg-amber-100 text-amber-700'
              : 'bg-gray-100 text-gray-600'
        }`}
      >
        {outOfStock ? 'Out of stock' : `${figure} in stock`}
      </span>
    ) : null;

  const title = `${product.name || ''} ${product.sku ? `· ${product.sku}` : ''}`;
  const [model, colour] = nameParts(product.name || product.model || 'Item');
  const colourLine = colour ? (
    <span className="text-[11px] font-medium text-gray-700 break-words">{colour}</span>
  ) : null;
  const secondLine = [product.brand, product.sku].filter(Boolean).join(' · ');

  if (layout === 'grid') {
    return (
      <button
        type="button"
        onClick={onPick}
        disabled={inCart || outOfStock}
        title={title}
        className={
          'min-h-[44px] text-left rounded-xl border p-2 flex flex-col gap-1 disabled:cursor-not-allowed ' +
          (outOfStock
            ? 'border-gray-200 bg-gray-50 opacity-50'
            : inCart
              ? 'border-green-300 bg-green-50'
              : 'border-gray-200 bg-white hover:bg-gray-50 active:bg-gray-100')
        }
      >
        <div className="relative h-20 rounded-lg bg-gray-50 flex items-center justify-center overflow-hidden">
          {product.image_url ? (
            <img src={product.image_url} alt="" className="h-full w-full object-contain" />
          ) : (
            <Package className="w-6 h-6 text-gray-400" />
          )}
          {stockBadge && <span className="absolute top-1 right-1">{stockBadge}</span>}
        </div>
        <span className="text-xs font-medium text-gray-900 line-clamp-2">{model}</span>
        {colourLine}
        <span className="text-[11px] text-gray-500 truncate">{secondLine}</span>
        <span className="text-sm font-semibold text-gray-900">
          {money(offer)}
          {offer < mrp && (
            <span className="ml-1 text-[11px] font-normal text-gray-500 line-through">
              {money(mrp)}
            </span>
          )}
        </span>
        {inCart && !outOfStock && <span className="text-[11px] text-green-700">In cart</span>}
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={onPick}
      disabled={inCart || outOfStock}
      title={title}
      className={`w-[152px] min-h-[92px] shrink-0 rounded-xl border px-2.5 py-2 text-left flex flex-col gap-1 ${
        outOfStock
          ? 'border-red-200 bg-red-50 opacity-60 cursor-not-allowed'
          : inCart
            ? 'border-green-300 bg-green-50 opacity-70'
            : 'border-gray-200 bg-white hover:bg-gray-50 active:bg-gray-100'
      }`}
    >
      <div className="flex items-start gap-2">
        <div className="w-8 h-8 shrink-0 flex items-center justify-center">
          {product.image_url ? (
            <img src={product.image_url} alt="" className="h-8 w-auto object-contain" />
          ) : (
            <Package className="w-4 h-4 text-gray-500" />
          )}
        </div>
        {stockBadge && <span className="ml-auto">{stockBadge}</span>}
      </div>
      <span className="text-xs font-semibold text-gray-900 truncate">{model}</span>
      {colourLine}
      <span className="text-[10px] text-gray-500 truncate">{secondLine}</span>
      <span className="mt-auto flex items-baseline gap-1.5">
        <span className="text-sm font-bold text-gray-900">{money(offer)}</span>
        {offer < mrp && (
          <span className="text-[10px] text-gray-500 line-through">{money(mrp)}</span>
        )}
        {inCart && <span className="ml-auto text-[9px] text-green-700">In cart</span>}
      </span>
    </button>
  );
}

export function ProductResultsStrip({
  storeId,
  query,
  onBlocked,
  onPicked,
}: ProductResultsStripProps) {
  const store = usePOSStore();
  const { data: products = [], isLoading } = useProducts({
    search: query.trim() || undefined,
    store_id: storeId || undefined,
  });
  const rows = (products as any[]).slice(0, MAX_PRODUCT_RESULTS);
  const { data: stock } = useSellableStock(storeId, rows);

  const handlePick = (product: any) => {
    const guard = posPriceGuard(product);
    if (!guard.ok) {
      onBlocked?.(guard.message || 'Blocked');
      return;
    }
    onBlocked?.(null);
    store.addToCart(cartItemFromProduct(product, guard));
    onPicked?.();
  };

  // One row, horizontally scrolled: the POS page must never grow vertically
  // (spec 11b), so results scroll sideways inside this strip instead.
  const rowClass = 'flex gap-2 overflow-x-auto pb-1';

  if (isLoading) {
    return (
      <div className={rowClass} aria-busy="true">
        {[0, 1, 2, 3, 4].map((i) => (
          <div
            key={i}
            className="w-[152px] h-[92px] shrink-0 rounded-xl border border-gray-200 bg-white animate-pulse"
          />
        ))}
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="min-h-[44px] flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 text-sm text-gray-500">
        <Package className="w-4 h-4 shrink-0" />
        {query.trim()
          ? `No product matches "${query.trim()}" in this store.`
          : 'Scan a barcode or type a name, brand or SKU to find products.'}
      </div>
    );
  }

  return (
    <div className={rowClass}>
      {rows.map((product) => (
        <ProductCard
          key={productIdOf(product) || product.sku}
          product={product}
          layout="strip"
          stock={stock}
          onPick={() => handlePick(product)}
        />
      ))}
    </div>
  );
}

export default ProductResultsStrip;
