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

import { Package } from 'lucide-react';
import { usePOSStore } from '../../../stores/posStore';
import { useProducts } from '../../../hooks/usePOSQueries';
import { posPriceGuard, cartItemFromProduct } from '../../../components/pos/productIntake';

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

/** The product list spells on-hand quantity three ways depending on which
    join served the row. null means "this row carries no stock figure at all"
    - never read that as zero, or every unjoined product looks out of stock. */
function stockOf(p: any): number | null {
  return p.stock ?? p.quantity ?? p.stock_available ?? null;
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

  const rows = (products as any[]).slice(0, 24);

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
      {rows.map((product) => {
        const productId = product.product_id || product._id || product.id;
        const mrp = product.mrp || 0;
        const offer = product.offer_price || mrp;
        const stock = stockOf(product);
        // Owner ruling 2026-08-25: oversell = BLOCK. A row that reports zero
        // on hand cannot be billed here; a row with no figure is not blocked.
        const outOfStock = stock !== null && stock <= 0;
        const lowStock = stock !== null && stock > 0 && stock <= 3;
        const inCart = (store.cart || []).some((i) => i.product_id === productId);

        return (
          <button
            key={productId}
            type="button"
            onClick={() => handlePick(product)}
            disabled={inCart || outOfStock}
            title={`${product.name || ''} ${product.sku ? `· ${product.sku}` : ''}`}
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
              {stock !== null && (
                <span
                  className={`ml-auto text-[9px] px-1 py-0.5 rounded font-medium shrink-0 ${
                    outOfStock
                      ? 'bg-red-100 text-red-600'
                      : lowStock
                        ? 'bg-amber-100 text-amber-700'
                        : 'bg-gray-100 text-gray-600'
                  }`}
                >
                  {outOfStock ? 'Out' : lowStock ? `${stock} left` : stock}
                </span>
              )}
            </div>
            <span className="text-xs font-semibold text-gray-900 truncate">{product.name}</span>
            <span className="text-[10px] text-gray-500 truncate">
              {[product.brand, product.sku].filter(Boolean).join(' · ')}
            </span>
            <span className="mt-auto flex items-baseline gap-1.5">
              <span className="text-sm font-bold text-gray-900">{money(offer)}</span>
              {offer < mrp && (
                <span className="text-[10px] text-gray-500 line-through">{money(mrp)}</span>
              )}
              {inCart && <span className="ml-auto text-[9px] text-green-700">In cart</span>}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export default ProductResultsStrip;
