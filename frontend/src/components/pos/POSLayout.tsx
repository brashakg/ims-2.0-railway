import { useState, useCallback, useEffect } from 'react';
import { ShoppingCart, BarChart3, Barcode, Search, Zap } from 'lucide-react';
import ProductCatalog from './ProductCatalog';
import CartPanel from './CartPanel';
import BillingEngine from './BillingEngine';
import PaymentProcessor from './PaymentProcessor';

export interface CartItem {
  product_id: string;
  name: string;
  sku: string;
  brand?: string;
  unit_price: number;
  quantity: number;
  image_url?: string;
  category: string;
  stock?: number;
  is_optical?: boolean;
  prescription?: {
    sph_od?: number;
    cyl_od?: number;
    axis_od?: number;
    add_od?: number;
    pd_od?: number;
    sph_os?: number;
    cyl_os?: number;
    axis_os?: number;
    add_os?: number;
    pd_os?: number;
  };
  discount_percent?: number;
  is_combo?: boolean;
  combo_items?: string[];
}

export interface Customer {
  id: string;
  name: string;
  phone: string;
  email?: string;
  address?: string;
  loyalty_points?: number;
}

type POSStage = 'catalog' | 'billing' | 'payment' | 'receipt';

export function POSLayout() {
  const [cartItems, setCartItems] = useState<CartItem[]>([]);
  const [selectedCustomer, setSelectedCustomer] = useState<Customer | null>(null);
  const [currentStage, setCurrentStage] = useState<POSStage>('catalog');
  const [barcodeInput, setBarcodeInput] = useState('');
  const [billData, setBillData] = useState<any>(null);
  const [showKeyboardHelp, setShowKeyboardHelp] = useState(false);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyboardShortcuts = (e: KeyboardEvent) => {
      // F2: Quick product search
      if (e.key === 'F2') {
        e.preventDefault();
        setCurrentStage('catalog');
        setTimeout(() => {
          const barcodeInput = document.querySelector('input[placeholder*="Scan barcode"]') as HTMLInputElement;
          barcodeInput?.focus();
        }, 0);
      }
      // F9: Proceed to payment
      if (e.key === 'F9') {
        e.preventDefault();
        if (cartItems.length > 0) {
          setCurrentStage('billing');
        }
      }
      // ESC: Cancel/Go back
      if (e.key === 'Escape') {
        e.preventDefault();
        if (currentStage !== 'catalog') {
          setCurrentStage('catalog');
        }
      }
      // CTRL+L: Show loyalty points
      if (e.ctrlKey && e.key === 'l') {
        e.preventDefault();
        if (selectedCustomer?.loyalty_points) {
          alert(`Loyalty Points: ${selectedCustomer.loyalty_points}`);
        }
      }
    };

    window.addEventListener('keydown', handleKeyboardShortcuts);
    return () => window.removeEventListener('keydown', handleKeyboardShortcuts);
  }, [cartItems.length, currentStage, selectedCustomer]);

  // Add item to cart from barcode or product selection
  const handleAddToCart = useCallback((product: any) => {
    setCartItems(prev => {
      const existing = prev.find(item => item.product_id === product.id);
      if (existing) {
        return prev.map(item =>
          item.product_id === product.id
            ? { ...item, quantity: item.quantity + 1 }
            : item
        );
      }
      return [
        ...prev,
        {
          product_id: product.id,
          name: product.name,
          sku: product.sku,
          brand: product.brand,
          unit_price: product.price,
          quantity: 1,
          image_url: product.image_url,
          category: product.category,
          stock: product.stock,
          is_optical: product.is_optical || false,
        },
      ];
    });
    setBarcodeInput('');
  }, []);

  const handleUpdateQuantity = useCallback((productId: string, quantity: number) => {
    if (quantity <= 0) {
      setCartItems(prev => prev.filter(item => item.product_id !== productId));
    } else {
      setCartItems(prev =>
        prev.map(item =>
          item.product_id === productId ? { ...item, quantity } : item
        )
      );
    }
  }, []);

  const handleRemoveItem = useCallback((productId: string) => {
    setCartItems(prev => prev.filter(item => item.product_id !== productId));
  }, []);

  const handleApplyDiscount = useCallback((productId: string, discountPercent: number) => {
    setCartItems(prev =>
      prev.map(item =>
        item.product_id === productId ? { ...item, discount_percent: discountPercent } : item
      )
    );
  }, []);

  const handleProceedToBilling = useCallback(() => {
    if (cartItems.length === 0) {
      alert('Cart is empty. Add items to proceed.');
      return;
    }
    setCurrentStage('billing');
  }, [cartItems.length]);

  const handleBillingComplete = useCallback((calculatedBill: any) => {
    setBillData(calculatedBill);
    setCurrentStage('payment');
  }, []);

  const handlePaymentComplete = useCallback(() => {
    // Reset for next transaction
    setCartItems([]);
    setSelectedCustomer(null);
    setBillData(null);
    setCurrentStage('receipt');
  }, []);

  const handleNewTransaction = useCallback(() => {
    setCartItems([]);
    setSelectedCustomer(null);
    setBillData(null);
    setCurrentStage('catalog');
  }, []);

  return (
    <div className="h-screen bg-gray-900 text-white overflow-hidden">
      {/* Keyboard Shortcuts Help */}
      {showKeyboardHelp && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50">
          <div className="bg-gray-800 rounded-lg p-6 max-w-md">
            <h3 className="text-lg font-bold mb-4">Keyboard Shortcuts</h3>
            <div className="space-y-2 text-sm mb-6">
              <div className="flex justify-between"><span>F2</span><span className="text-gray-400">Quick Product Search</span></div>
              <div className="flex justify-between"><span>F9</span><span className="text-gray-400">Proceed to Billing</span></div>
              <div className="flex justify-between"><span>ESC</span><span className="text-gray-400">Back to Catalog</span></div>
              <div className="flex justify-between"><span>CTRL+L</span><span className="text-gray-400">Show Loyalty Points</span></div>
            </div>
            <button
              onClick={() => setShowKeyboardHelp(false)}
              className="w-full bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded-lg"
            >
              Close
            </button>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="bg-gradient-to-r from-blue-900 to-purple-900 px-6 py-4 border-b border-gray-700">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <BarChart3 className="w-8 h-8 text-blue-400" />
            <div>
              <h1 className="text-2xl font-bold">Enterprise POS</h1>
              <p className="text-sm text-gray-300">
                {currentStage === 'catalog' && '📦 Product Selection [F2: Search, F9: Billing, ESC: Back]'}
                {currentStage === 'billing' && '📊 Billing & Discounts [F9: Payment, ESC: Back]'}
                {currentStage === 'payment' && '💳 Payment Processing'}
                {currentStage === 'receipt' && '✅ Transaction Complete'}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-4">
            {selectedCustomer && (
              <div className="bg-blue-900 px-4 py-2 rounded-lg">
                <p className="text-sm font-semibold">{selectedCustomer.name}</p>
                <p className="text-xs text-gray-300">{selectedCustomer.phone}</p>
                {selectedCustomer.loyalty_points !== undefined && (
                  <p className="text-xs text-yellow-300 mt-1">
                    ⭐ {selectedCustomer.loyalty_points} points
                  </p>
                )}
              </div>
            )}
            <div className="bg-gray-800 px-4 py-2 rounded-lg flex items-center gap-2">
              <ShoppingCart className="w-5 h-5 text-blue-400" />
              <span className="font-semibold">{cartItems.length} items</span>
            </div>
            <button
              onClick={() => setShowKeyboardHelp(!showKeyboardHelp)}
              className="bg-gray-800 hover:bg-gray-700 px-3 py-2 rounded-lg text-xs font-semibold"
              title="Keyboard shortcuts"
            >
              <Zap className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex h-full overflow-hidden bg-gray-900" style={{ height: 'calc(100vh - 120px)' }}>
        {/* Left Side - Product Catalog / Billing */}
        <div className="flex-1 overflow-y-auto border-r border-gray-700">
          {currentStage === 'catalog' && (
            <div className="p-6">
              {/* Barcode Scanner */}
              <div className="mb-6">
                <label className="block text-sm font-medium mb-2">
                  <Barcode className="w-4 h-4 inline mr-2" />
                  Barcode Scanner
                </label>
                <input
                  type="text"
                  value={barcodeInput}
                  onChange={(e) => setBarcodeInput(e.target.value)}
                  placeholder="Scan barcode or product code..."
                  className="w-full bg-gray-800 text-white border border-gray-700 rounded-lg px-4 py-3 text-lg focus:outline-none focus:border-blue-500"
                  autoFocus
                />
              </div>

              {/* Product Catalog */}
              <ProductCatalog
                onAddToCart={handleAddToCart}
                barcodeFilter={barcodeInput}
              />
            </div>
          )}

          {currentStage === 'billing' && (
            <BillingEngine
              cartItems={cartItems}
              selectedCustomer={selectedCustomer}
              onUpdateQuantity={handleUpdateQuantity}
              onRemoveItem={handleRemoveItem}
              onApplyDiscount={handleApplyDiscount}
              onComplete={handleBillingComplete}
              onBack={() => setCurrentStage('catalog')}
            />
          )}

          {currentStage === 'payment' && (
            <PaymentProcessor
              billData={billData}
              selectedCustomer={selectedCustomer}
              onComplete={handlePaymentComplete}
              onBack={() => setCurrentStage('billing')}
            />
          )}
        </div>

        {/* Right Side - Cart Panel */}
        {(currentStage === 'catalog' || currentStage === 'billing') && (
          <div className="w-96 border-l border-gray-700 overflow-y-auto">
            <CartPanel
              items={cartItems}
              selectedCustomer={selectedCustomer}
              onSetCustomer={setSelectedCustomer}
              onUpdateQuantity={handleUpdateQuantity}
              onRemoveItem={handleRemoveItem}
              onApplyDiscount={handleApplyDiscount}
              onProceedToBilling={handleProceedToBilling}
              totalItems={cartItems.reduce((sum, item) => sum + item.quantity, 0)}
              totalAmount={cartItems.reduce((sum, item) => sum + (item.unit_price * item.quantity * (1 - (item.discount_percent || 0) / 100)), 0)}
            />
          </div>
        )}

        {/* Receipt View */}
        {currentStage === 'receipt' && billData && (
          <div className="w-full p-6 flex items-center justify-center">
            <div className="max-w-md">
              <div className="text-center mb-6">
                <div className="w-16 h-16 bg-green-500 rounded-full flex items-center justify-center mx-auto mb-4">
                  <svg className="w-8 h-8 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <h2 className="text-2xl font-bold mb-2">Transaction Complete!</h2>
                <p className="text-gray-400">Bill #: {billData.bill_number}</p>
              </div>

              <div className="bg-gray-800 rounded-lg p-6 mb-6">
                <div className="space-y-2 mb-4 pb-4 border-b border-gray-700">
                  <div className="flex justify-between">
                    <span className="text-gray-400">Subtotal</span>
                    <span>₹{billData.subtotal?.toFixed(2) || '0.00'}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-400">CGST/SGST</span>
                    <span>₹{billData.gst_amount?.toFixed(2) || '0.00'}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-gray-400">Discount</span>
                    <span>-₹{billData.discount_amount?.toFixed(2) || '0.00'}</span>
                  </div>
                </div>
                <div className="flex justify-between text-xl font-bold mb-6">
                  <span>Total Amount</span>
                  <span className="text-green-500">₹{billData.total_amount?.toFixed(2) || '0.00'}</span>
                </div>

                <div className="space-y-2 text-sm mb-6">
                  <p className="text-gray-400">Payment Method: {billData.payment_method}</p>
                  {billData.selected_customer && (
                    <p className="text-gray-400">Customer: {billData.selected_customer.name}</p>
                  )}
                </div>
              </div>

              <button
                onClick={handleNewTransaction}
                className="w-full bg-blue-600 hover:bg-blue-700 px-6 py-3 rounded-lg font-semibold transition-colors"
              >
                Start New Transaction
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default POSLayout;
