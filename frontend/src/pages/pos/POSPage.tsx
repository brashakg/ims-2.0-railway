// ============================================================================
// IMS 2.0 - Point of Sale Page (Comprehensive Implementation)
// ============================================================================
// Full-featured POS with:
// - Category-specific workflows (Spectacles, Sunglasses, Contact Lens, Watch, etc.)
// - Add Lens to Frame/Sunglass functionality
// - Prescription linking for optical products
// - Role-based discount caps and permissions
// - MRP/Offer price validation per SYSTEM_INTENT.md

import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { CustomerSearch } from '../../components/pos/CustomerSearch';
import { PrescriptionPanel } from '../../components/pos/PrescriptionPanel';
import { PrescriptionSelectModal } from '../../components/pos/PrescriptionSelectModal';
import { ProductSearchModal } from '../../components/pos/ProductSearchModal';
import type { SearchResultProduct } from '../../components/pos/ProductSearchModal';
import { LensDetailsModal } from '../../components/pos/LensDetailsModal';
import type { LensDetails } from '../../components/pos/LensDetailsModal';
import { OrderDetailsPanel } from '../../components/pos/OrderDetailsPanel';
import { PaymentCollectionPanel } from '../../components/pos/PaymentCollectionPanel';
import { BarcodeScanner } from '../../components/pos/BarcodeScanner';
import { POS_CATEGORIES, CATEGORY_CONFIG, getCategoryConfigByPOSId } from '../../types/productAttributes';
import type { Customer, Patient, Prescription, Payment, CartItem, ProductCategory, UserRole } from '../../types';
import { inventoryApi, orderApi } from '../../services/api';
import {
  User, ShoppingCart, X, AlertCircle, Check, Plus, Printer, Save, RotateCcw,
  Glasses, Sun, Eye, Watch, Ear, Package, Wrench, Barcode, Smartphone,
  Trash2, ChevronDown, ChevronUp, Link2, Unlink, AlertTriangle,
  BookOpen, Cpu, Sparkles, Clock,
} from 'lucide-react';
import clsx from 'clsx';

// ============================================================================
// Types
// ============================================================================

interface OrderDetailsData {
  deliveryDate: string;
  deliveryTime: string;
  salesPerson: string;
  notes: string;
  isExpress: boolean;
  isUrgent: boolean;
}

// Extended CartItem with lens linking support
interface ExtendedCartItem extends CartItem {
  linkedLensId?: string;        // ID of lens item linked to this frame
  linkedFrameId?: string;       // ID of frame this lens is linked to
  lensDetails?: LensDetails;    // Lens configuration details
  isLensItem?: boolean;         // Is this a lens-only item
  productAttributes?: Record<string, string>; // Category-specific attributes
}

// Icon mapping for ALL categories
const ICON_MAP: Record<string, React.FC<{ className?: string }>> = {
  Glasses,      // FR - Spectacles
  Sun,          // SG - Sunglasses
  Eye,          // CL - Contact Lens
  BookOpen,     // RG - Reading Glasses
  Cpu,          // SMTFR - Smart Glasses
  Sparkles,     // SMTSG - Smart Sunglasses
  Watch,        // WT - Wrist Watch
  Smartphone,   // SMTWT - Smart Watch
  Clock,        // CK - Clocks
  Ear,          // HA - Hearing Aid
  Package,      // ACC - Accessories
  Wrench,       // SVC - Services/Repair
};

// Role-based discount caps
const ROLE_DISCOUNT_CAPS: Record<UserRole, number> = {
  SALES_STAFF: 10,
  SALES_CASHIER: 10,
  OPTOMETRIST: 10,
  WORKSHOP_STAFF: 5,
  STORE_MANAGER: 20,
  ACCOUNTANT: 15,
  CATALOG_MANAGER: 15,
  AREA_MANAGER: 25,
  ADMIN: 100,
  SUPERADMIN: 100,
};

// ============================================================================
// POS Page Component
// ============================================================================

export function POSPage() {
  const { user } = useAuth();
  const toast = useToast();

  // Customer state
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [selectedPatient, setSelectedPatient] = useState<Patient | null>(null);

  // Category state
  const [activeCategory, setActiveCategory] = useState('spectacles');

  // Prescription state
  const [prescription, setPrescription] = useState<Prescription | null>(null);

  // Modal states
  const [showPrescriptionSelectModal, setShowPrescriptionSelectModal] = useState(false);
  const [showProductSearchModal, setShowProductSearchModal] = useState(false);
  const [showLensModal, setShowLensModal] = useState(false);
  const [selectedItemForLens, setSelectedItemForLens] = useState<string | null>(null);

  // Order items state
  const [orderItems, setOrderItems] = useState<ExtendedCartItem[]>([]);

  // Order details state
  const tomorrow = new Date(Date.now() + 86400000).toISOString().split('T')[0];
  const [orderDetails, setOrderDetails] = useState<OrderDetailsData>({
    deliveryDate: tomorrow,
    deliveryTime: '',
    salesPerson: user?.name || '',
    notes: '',
    isExpress: false,
    isUrgent: false,
  });

  // Payment state
  const [payments, setPayments] = useState<Payment[]>([]);
  const [orderDiscount, setOrderDiscount] = useState({ percent: 0, amount: 0 });

  // Barcode input
  const [barcodeInput, setBarcodeInput] = useState('');
  const barcodeInputRef = useRef<HTMLInputElement>(null);

  // Error state
  const [error, setError] = useState<string | null>(null);

  // Order complete state
  const [orderComplete, setOrderComplete] = useState(false);
  const [completedOrderNumber, setCompletedOrderNumber] = useState<string | null>(null);

  // Draft order for online payment
  const [draftOrderId, setDraftOrderId] = useState<string | null>(null);
  const [draftOrderNumber, setDraftOrderNumber] = useState<string | null>(null);

  // Expanded items for showing details
  const [expandedItems, setExpandedItems] = useState<Set<string>>(new Set());

  // ============================================================================
  // Derived State
  // ============================================================================

  const activeCategoryConfig = useMemo(() => {
    return getCategoryConfigByPOSId(activeCategory);
  }, [activeCategory]);

  const activePOSCategory = useMemo(() => {
    return POS_CATEGORIES.find(c => c.id === activeCategory);
  }, [activeCategory]);

  // Check if current category requires prescription
  const categoryRequiresPrescription = activeCategoryConfig?.requiresPrescription || false;

  // Check if current category can have lens added
  const categoryCanAddLens = activeCategoryConfig?.canAddLens || false;

  // User's maximum discount cap
  const userDiscountCap = useMemo(() => {
    if (!user) return 0;
    const roleCap = ROLE_DISCOUNT_CAPS[user.activeRole] || 10;
    return user.discountCap || roleCap;
  }, [user]);

  // ============================================================================
  // Calculations
  // ============================================================================

  const subtotal = orderItems.reduce((sum, item) => sum + item.finalPrice, 0);
  const gstAmount = Math.round(subtotal * 0.18);
  const grandTotal = subtotal + gstAmount - orderDiscount.amount;
  const totalPaid = payments.reduce((sum, p) => sum + p.amount, 0);
  const balanceDue = grandTotal - totalPaid;

  // ============================================================================
  // Handlers
  // ============================================================================

  const handleCustomerSelect = useCallback((selectedCustomer: Customer) => {
    setCustomer(selectedCustomer);
    setSelectedPatient(selectedCustomer.patients?.[0] || null);
    setError(null);
    toast.success(`Customer selected: ${selectedCustomer.name}`);
  }, [toast]);

  const handleClearCustomer = useCallback(() => {
    setCustomer(null);
    setSelectedPatient(null);
    setPrescription(null);
    setOrderItems([]);
    setOrderDetails({
      deliveryDate: tomorrow,
      deliveryTime: '',
      salesPerson: user?.name || '',
      notes: '',
      isExpress: false,
      isUrgent: false,
    });
    setPayments([]);
    setOrderDiscount({ percent: 0, amount: 0 });
    setError(null);
    setExpandedItems(new Set());
  }, [tomorrow, user?.name]);

  // Handle prescription selection from modal
  const handlePrescriptionSelect = useCallback((selectedPrescription: Prescription) => {
    setPrescription(selectedPrescription);
    setShowPrescriptionSelectModal(false);
    toast.success('Prescription linked successfully');

    // Auto-link prescription to items that need it
    setOrderItems(prev => prev.map(item => {
      if (item.requiresPrescription && !item.prescriptionLinked) {
        return {
          ...item,
          prescriptionLinked: true,
          prescriptionId: selectedPrescription.id,
        };
      }
      return item;
    }));
  }, [toast]);

  // Handle manual prescription entry
  const handlePrescriptionChange = useCallback((newPrescription: Prescription) => {
    setPrescription(newPrescription);
  }, []);

  // Handle barcode scan
  const handleBarcodeSubmit = useCallback(async (barcode: string) => {
    if (!barcode.trim()) return;

    try {
      setError(null);

      // Fetch product from API by barcode
      const stockUnit = await inventoryApi.getStockByBarcode(barcode);

      if (!stockUnit) {
        throw new Error(`Product not found for barcode: ${barcode}`);
      }

      // Check if product is available
      if (stockUnit.status !== 'AVAILABLE' || stockUnit.quantity < 1) {
        throw new Error(`Product is not available (Status: ${stockUnit.status})`);
      }

      // Determine category and requirements
      const requiresPrescription = categoryRequiresPrescription;

      const newItem: ExtendedCartItem = {
        id: `item-${Date.now()}`,
        itemType: activeCategoryConfig?.code as ProductCategory || 'FRAME',
        productId: stockUnit.productId,
        productName: stockUnit.productName || `Product ${stockUnit.productId}`,
        sku: stockUnit.sku || barcode,
        category: activeCategoryConfig?.code as ProductCategory || 'FRAME',
        brand: stockUnit.brand || '',
        quantity: 1,
        unitPrice: stockUnit.price || 0,
        mrp: stockUnit.mrp || stockUnit.price || 0,
        offerPrice: stockUnit.offerPrice || stockUnit.price || 0,
        discountPercent: 0,
        discountAmount: 0,
        finalPrice: stockUnit.offerPrice || stockUnit.price || 0,
        barcode,
        requiresPrescription,
        prescriptionLinked: requiresPrescription && !!prescription,
        prescriptionId: prescription?.id,
        productAttributes: stockUnit.attributes,
      };

      setOrderItems(prev => [...prev, newItem]);
      setBarcodeInput('');
      toast.success(`Added: ${newItem.productName}`);

      // Auto-expand the new item
      setExpandedItems(prev => new Set(prev).add(newItem.id));

      // If this category can have lens and no prescription, prompt for prescription first
      if (categoryCanAddLens && !prescription) {
        toast.info('Add a prescription to enable lens options');
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to lookup product';
      setError(errorMessage);
      toast.error(errorMessage);
    }
  }, [activeCategory, activeCategoryConfig, categoryRequiresPrescription, categoryCanAddLens, prescription, toast]);

  // Handle barcode scan - search for product and add to cart
  const handleBarcodeScan = useCallback(async (barcode: string) => {
    if (!activeCategoryConfig) {
      toast.error('Please select a product category first');
      return;
    }

    try {
      // Search for product by barcode
      const response = await inventoryApi.searchByBarcode(barcode, user?.activeStoreId || '');

      if (!response || !response.product) {
        toast.error(`Product not found with barcode: ${barcode}`);
        return;
      }

      const product = response.product;

      // Convert to SearchResultProduct format and add to cart
      const productForCart: SearchResultProduct = {
        productId: product.id,
        productName: product.name,
        sku: product.sku,
        brand: product.brand,
        mrp: product.mrp,
        offerPrice: product.offerPrice,
        attributes: product.attributes || {},
      };

      handleProductSelect(productForCart);
      toast.success(`Added: ${product.name}`);
    } catch (error: any) {
      toast.error(error?.message || `Failed to find product with barcode: ${barcode}`);
    }
  }, [activeCategoryConfig, user?.activeStoreId, toast]);

  // Handle product selection from search modal
  const handleProductSelect = useCallback((product: SearchResultProduct) => {
    const requiresPrescription = categoryRequiresPrescription;

    const newItem: ExtendedCartItem = {
      id: `item-${Date.now()}`,
      itemType: activeCategoryConfig?.code as ProductCategory || 'FRAME',
      productId: product.productId,
      productName: product.productName,
      sku: product.sku,
      category: activeCategoryConfig?.code as ProductCategory || 'FRAME',
      brand: product.brand,
      quantity: 1,
      unitPrice: product.offerPrice,
      mrp: product.mrp,
      offerPrice: product.offerPrice,
      discountPercent: product.mrp > product.offerPrice ? Math.round((1 - product.offerPrice / product.mrp) * 100) : 0,
      discountAmount: product.mrp - product.offerPrice,
      finalPrice: product.offerPrice,
      barcode: product.barcode,
      requiresPrescription,
      prescriptionLinked: requiresPrescription && !!prescription,
      prescriptionId: prescription?.id,
      stockId: product.id,
      productAttributes: {
        model: product.model,
        color: product.color || '',
        size: product.size || '',
      },
    };

    setOrderItems(prev => [...prev, newItem]);
    setShowProductSearchModal(false);
    toast.success(`Added: ${newItem.productName}`);

    // Auto-expand the new item
    setExpandedItems(prev => new Set(prev).add(newItem.id));
  }, [activeCategoryConfig, categoryRequiresPrescription, prescription, toast]);

  // Open lens details for a frame/sunglass item
  const handleAddLensToItem = useCallback((itemId: string) => {
    if (!prescription) {
      toast.warning('Please add or link a prescription first to add lens');
      setShowPrescriptionSelectModal(true);
      return;
    }
    setSelectedItemForLens(itemId);
    setShowLensModal(true);
  }, [prescription, toast]);

  // Save lens details and create linked lens item
  const handleSaveLensDetails = useCallback((lensDetails: LensDetails) => {
    if (!selectedItemForLens) return;

    // Find the frame item
    const frameItem = orderItems.find(item => item.id === selectedItemForLens);
    if (!frameItem) return;

    // Create a new lens item linked to the frame
    const lensItemId = `lens-${Date.now()}`;
    const lensItem: ExtendedCartItem = {
      id: lensItemId,
      itemType: 'OPTICAL_LENS',
      productId: `lens-${lensDetails.brandId}-${lensDetails.subbrandId}`,
      productName: `${lensDetails.brandLabel} ${lensDetails.subbrandLabel} (${lensDetails.indexLabel})`,
      sku: `LS-${lensDetails.brandId}`,
      category: 'OPTICAL_LENS',
      brand: lensDetails.brandLabel,
      quantity: 2, // Pair of lenses
      unitPrice: lensDetails.finalPrice / 2,
      mrp: lensDetails.totalPrice / 2,
      offerPrice: lensDetails.finalPrice / 2,
      discountPercent: lensDetails.discountAmount > 0 ? Math.round((lensDetails.discountAmount / lensDetails.totalPrice) * 100) : 0,
      discountAmount: lensDetails.discountAmount,
      finalPrice: lensDetails.finalPrice,
      requiresPrescription: true,
      prescriptionLinked: true,
      prescriptionId: prescription?.id,
      isLensItem: true,
      linkedFrameId: selectedItemForLens,
      lensDetails,
    };

    // Update the frame item to link to this lens
    setOrderItems(prev => [
      ...prev.map(item => {
        if (item.id === selectedItemForLens) {
          return {
            ...item,
            linkedLensId: lensItemId,
            prescriptionLinked: true,
            prescriptionId: prescription?.id,
          };
        }
        return item;
      }),
      lensItem,
    ]);

    setShowLensModal(false);
    setSelectedItemForLens(null);
    toast.success(`Lens added: ${lensDetails.brandLabel} ${lensDetails.subbrandLabel}`);
  }, [selectedItemForLens, orderItems, prescription, toast]);

  // Remove lens from frame
  const handleRemoveLensFromFrame = useCallback((frameId: string) => {
    const frameItem = orderItems.find(item => item.id === frameId);
    if (!frameItem?.linkedLensId) return;

    const lensId = frameItem.linkedLensId;

    setOrderItems(prev => prev
      .filter(item => item.id !== lensId)
      .map(item => {
        if (item.id === frameId) {
          const { linkedLensId, ...rest } = item;
          return rest;
        }
        return item;
      })
    );

    toast.success('Lens removed from frame');
  }, [orderItems, toast]);

  // Update item quantity
  const handleUpdateItemQuantity = useCallback((itemId: string, quantity: number) => {
    if (quantity < 1) return;

    setOrderItems(prev => prev.map(item => {
      if (item.id !== itemId) return item;

      // For lens items, quantity should be in pairs (2, 4, 6...)
      const finalQty = item.isLensItem ? Math.max(2, Math.ceil(quantity / 2) * 2) : quantity;

      const basePrice = item.offerPrice;
      const discountAmount = Math.round(basePrice * finalQty * item.discountPercent / 100);
      const finalPrice = basePrice * finalQty - discountAmount;

      return { ...item, quantity: finalQty, finalPrice, discountAmount };
    }));
  }, []);

  // Update item price with discount validation
  const handleUpdateItemPrice = useCallback((itemId: string, newPrice: number) => {
    setOrderItems(prev => prev.map(item => {
      if (item.id !== itemId) return item;

      const basePrice = item.offerPrice * item.quantity;
      const discountPercent = basePrice > 0 ? ((basePrice - newPrice) / basePrice) * 100 : 0;

      // Validate MRP rules per SYSTEM_INTENT.md
      if (item.offerPrice < item.mrp) {
        // Already has HQ discount, no further discount allowed
        if (newPrice < item.offerPrice * item.quantity) {
          toast.error('This item already has an HQ discount. No further discount allowed.');
          return item;
        }
      }

      // Validate against user's discount cap
      if (discountPercent > userDiscountCap) {
        toast.error(`Discount exceeds your cap (${userDiscountCap}%). Request approval for higher discounts.`);
        return item;
      }

      const discountAmount = Math.round(basePrice - newPrice);
      return {
        ...item,
        discountPercent: Math.round(discountPercent * 100) / 100,
        discountAmount,
        finalPrice: newPrice,
      };
    }));
  }, [userDiscountCap, toast]);

  // Remove item
  const handleRemoveItem = useCallback((itemId: string) => {
    const item = orderItems.find(i => i.id === itemId);

    // If removing a frame with linked lens, remove the lens too
    if (item?.linkedLensId) {
      setOrderItems(prev => prev.filter(i => i.id !== itemId && i.id !== item.linkedLensId));
    }
    // If removing a lens, unlink it from the frame
    else if (item?.linkedFrameId) {
      setOrderItems(prev => prev
        .filter(i => i.id !== itemId)
        .map(i => {
          if (i.id === item.linkedFrameId) {
            const { linkedLensId, ...rest } = i;
            return rest;
          }
          return i;
        })
      );
    } else {
      setOrderItems(prev => prev.filter(i => i.id !== itemId));
    }

    setExpandedItems(prev => {
      const newSet = new Set(prev);
      newSet.delete(itemId);
      return newSet;
    });

    toast.success('Item removed');
  }, [orderItems, toast]);

  // Toggle item expansion
  const toggleItemExpansion = useCallback((itemId: string) => {
    setExpandedItems(prev => {
      const newSet = new Set(prev);
      if (newSet.has(itemId)) {
        newSet.delete(itemId);
      } else {
        newSet.add(itemId);
      }
      return newSet;
    });
  }, []);

  // Add payment
  const handleAddPayment = useCallback((payment: Omit<Payment, 'id' | 'paidAt'>) => {
    const newPayment: Payment = {
      ...payment,
      id: `pay-${Date.now()}`,
      paidAt: new Date().toISOString(),
    };
    setPayments(prev => [...prev, newPayment]);
  }, []);

  // Remove payment
  const handleRemovePayment = useCallback((paymentId: string) => {
    setPayments(prev => prev.filter(p => p.id !== paymentId));
  }, []);

  // Initiate online payment
  const handleInitiateOnlinePayment = useCallback(async (): Promise<{ orderId: string; orderNumber: string }> => {
    if (draftOrderId && draftOrderNumber) {
      return { orderId: draftOrderId, orderNumber: draftOrderNumber };
    }

    if (!customer) {
      throw new Error('Please select a customer');
    }

    if (orderItems.length === 0) {
      throw new Error('Please add items to the order');
    }

    // Check prescription requirements
    const itemsNeedingPrescription = orderItems.filter(
      item => item.requiresPrescription && !item.prescriptionLinked
    );
    if (itemsNeedingPrescription.length > 0) {
      throw new Error('Please link prescription for all optical items');
    }

    try {
      const orderData = buildOrderData('DRAFT');
      const createdOrder = await orderApi.createOrder(orderData);

      setDraftOrderId(createdOrder.id);
      setDraftOrderNumber(createdOrder.orderNumber);

      toast.success(`Draft order ${createdOrder.orderNumber} created`);

      return {
        orderId: createdOrder.id,
        orderNumber: createdOrder.orderNumber,
      };
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to create draft order';
      setError(errorMessage);
      toast.error(errorMessage);
      throw err;
    }
  }, [draftOrderId, draftOrderNumber, customer, orderItems, toast]);

  // Build order data for API
  const buildOrderData = useCallback((status: 'DRAFT' | 'CONFIRMED' | 'IN_PROGRESS' | 'READY' | 'DELIVERED' | 'CANCELLED') => {
    return {
      customerId: customer!.id,
      customerName: customer!.name,
      customerPhone: customer!.phone,
      customerEmail: customer!.email,
      patientId: selectedPatient?.id,
      patientName: selectedPatient?.name,
      storeId: user?.activeStoreId || '',
      items: orderItems.map(item => ({
        id: item.id, // Will be replaced by server-generated ID
        itemType: item.itemType || item.category,
        productId: item.productId,
        productName: item.productName,
        sku: item.sku,
        quantity: item.quantity,
        unitPrice: item.unitPrice,
        mrp: item.mrp,
        offerPrice: item.offerPrice,
        discountPercent: item.discountPercent,
        discountAmount: item.discountAmount,
        finalPrice: item.finalPrice,
        prescriptionId: item.prescriptionId,
        lensDetails: item.lensDetails,
        linkedFrameId: item.linkedFrameId,
      })),
      payments: payments.map(p => ({
        id: p.id,
        mode: p.mode,
        amount: p.amount,
        reference: p.reference,
        paidAt: p.paidAt || new Date().toISOString(),
      })),
      subtotal,
      totalDiscount: orderDiscount.amount,
      taxAmount: gstAmount,
      grandTotal,
      amountPaid: totalPaid,
      balanceDue: Math.max(0, balanceDue),
      orderStatus: status,
      paymentStatus: balanceDue <= 0 ? 'PAID' as 'PAID' : totalPaid > 0 ? 'PARTIAL' as 'PARTIAL' : 'PENDING' as 'PENDING',
      notes: orderDetails.notes,
      expectedDelivery: orderDetails.deliveryDate
        ? new Date(`${orderDetails.deliveryDate}T${orderDetails.deliveryTime || '00:00'}`).toISOString()
        : undefined,
      isExpress: orderDetails.isExpress,
      isUrgent: orderDetails.isUrgent,
      prescriptionId: prescription?.id,
    };
  }, [customer, selectedPatient, orderItems, payments, subtotal, orderDiscount, gstAmount, grandTotal, totalPaid, balanceDue, orderDetails, user, prescription]);

  // Hold order
  const handleHoldOrder = useCallback(async () => {
    try {
      if (!customer) {
        toast.error('Please select a customer first');
        return;
      }
      const orderData = buildOrderData('DRAFT');
      const savedOrder = await orderApi.createOrder(orderData);
      toast.success(`Order ${savedOrder.orderNumber} held for later`);
    } catch (err) {
      toast.error('Failed to hold order');
    }
  }, [buildOrderData, customer, toast]);

  // Print bill
  const handlePrintBill = useCallback(() => {
    window.print();
  }, []);

  // Complete order
  const handleCompleteOrder = useCallback(async () => {
    // Validation
    if (!customer) {
      setError('Please select a customer');
      return;
    }

    if (orderItems.length === 0) {
      setError('Please add items to the order');
      return;
    }

    // Check prescription requirements
    const itemsNeedingPrescription = orderItems.filter(
      item => item.requiresPrescription && !item.prescriptionLinked
    );
    if (itemsNeedingPrescription.length > 0) {
      setError('Please link prescription for all optical items');
      return;
    }

    // Check payment
    const creditPayment = payments.find(p => p.mode === 'CREDIT');
    if (balanceDue > 0 && !creditPayment) {
      setError(`Balance due: ₹${balanceDue.toLocaleString('en-IN')}. Add payment or mark as credit.`);
      return;
    }

    try {
      setError(null);

      let finalOrderId: string;
      let finalOrderNumber: string;

      if (draftOrderId && draftOrderNumber) {
        finalOrderId = draftOrderId;
        finalOrderNumber = draftOrderNumber;

        // Add remaining payments
        const manualPayments = payments.filter(p => p.mode !== 'UPI');
        for (const payment of manualPayments) {
          await orderApi.addPayment(finalOrderId, {
            mode: payment.mode,
            amount: payment.amount,
            reference: payment.reference,
          });
        }

        if (balanceDue <= 0) {
          await orderApi.confirmOrder(finalOrderId);
        }
      } else {
        const orderData = buildOrderData('CONFIRMED');
        const createdOrder = await orderApi.createOrder(orderData);
        finalOrderId = createdOrder.id;
        finalOrderNumber = createdOrder.orderNumber;

        if (balanceDue <= 0) {
          await orderApi.confirmOrder(finalOrderId);
        }
      }

      toast.success(`Order ${finalOrderNumber} completed successfully!`);
      setCompletedOrderNumber(finalOrderNumber);
      setOrderComplete(true);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to create order';
      setError(errorMessage);
      toast.error(errorMessage);
    }
  }, [customer, orderItems, payments, balanceDue, draftOrderId, draftOrderNumber, buildOrderData, toast]);

  // New order
  const handleNewOrder = useCallback(() => {
    handleClearCustomer();
    setOrderComplete(false);
    setCompletedOrderNumber(null);
    setDraftOrderId(null);
    setDraftOrderNumber(null);
  }, [handleClearCustomer]);

  // Focus barcode input
  useEffect(() => {
    if (customer && barcodeInputRef.current) {
      barcodeInputRef.current.focus();
    }
  }, [customer, activeCategory]);

  // ============================================================================
  // Order Complete Screen
  // ============================================================================

  if (orderComplete) {
    return (
      <div className="min-h-[80vh] flex items-center justify-center">
        <div className="text-center max-w-md">
          <div className="w-20 h-20 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <Check className="w-10 h-10 text-green-600" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900 mb-2">Order Complete!</h1>
          <p className="text-lg text-gray-700 mb-1">Order #{completedOrderNumber}</p>
          <p className="text-gray-500 mb-6">
            Customer: {customer?.name} | Total: ₹{grandTotal.toLocaleString('en-IN')}
          </p>
          <div className="flex gap-3 justify-center">
            <button className="btn-outline flex items-center gap-2" onClick={handlePrintBill}>
              <Printer className="w-4 h-4" />
              Print Invoice
            </button>
            <button className="btn-primary flex items-center gap-2" onClick={handleNewOrder}>
              <Plus className="w-4 h-4" />
              New Order
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ============================================================================
  // Main POS Layout
  // ============================================================================

  const formatCurrency = (amount: number) => `₹${amount.toLocaleString('en-IN')}`;

  return (
    <div className="h-[calc(100vh-6rem)] flex flex-col gap-4">
      {/* Error Banner */}
      {error && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-lg flex items-center gap-2">
          <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
          <span className="text-sm text-red-700 flex-1">{error}</span>
          <button onClick={() => setError(null)} className="text-red-500 hover:text-red-700">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Top Section: Customer */}
      <div className="flex gap-4">
        <div className="flex-1">
          {!customer ? (
            <CustomerSearch onSelect={handleCustomerSelect} />
          ) : (
            <div className="card py-3 px-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                  <div className="w-10 h-10 bg-bv-red-100 rounded-full flex items-center justify-center">
                    <User className="w-5 h-5 text-bv-red-600" />
                  </div>
                  <div>
                    <p className="font-medium text-gray-900">{customer.name}</p>
                    <p className="text-sm text-gray-500">{customer.phone}</p>
                  </div>
                  {customer.customerType === 'B2B' && <span className="badge-info">B2B</span>}
                  {orderDetails.isExpress && <span className="badge-warning">Express</span>}
                  {orderDetails.isUrgent && <span className="badge-error">Urgent</span>}
                </div>

                {/* Patient selector */}
                {customer.patients && customer.patients.length > 0 && (
                  <select
                    value={selectedPatient?.id || ''}
                    onChange={(e) => {
                      const patient = customer.patients?.find(p => p.id === e.target.value);
                      if (patient) setSelectedPatient(patient);
                    }}
                    className="text-sm border border-gray-200 rounded-lg px-3 py-1.5"
                  >
                    {customer.patients.map(p => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>
                )}

                <button
                  onClick={handleClearCustomer}
                  className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg"
                  title="Clear and start new"
                >
                  <RotateCcw className="w-5 h-5" />
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Show rest of POS only when customer is selected */}
      {customer && (
        <>
          {/* Category Tabs */}
          <div className="flex gap-2 overflow-x-auto pb-1">
            {POS_CATEGORIES.map(cat => {
              const IconComponent = ICON_MAP[cat.icon] || Package;
              const isActive = activeCategory === cat.id;
              return (
                <button
                  key={cat.id}
                  onClick={() => {
                    setActiveCategory(cat.id);
                    // Clear prescription when switching to non-optical category
                    const config = CATEGORY_CONFIG[cat.code];
                    if (!config?.requiresPrescription && !config?.canAddLens) {
                      // Don't clear prescription, might be needed later
                    }
                  }}
                  className={clsx(
                    'flex items-center gap-2 px-4 py-2 rounded-lg whitespace-nowrap transition-all',
                    isActive
                      ? 'bg-bv-red-600 text-white shadow-md'
                      : `${cat.bgColor} ${cat.color} hover:opacity-80`
                  )}
                >
                  <IconComponent className="w-4 h-4" />
                  <span className="text-sm font-medium">{cat.label}</span>
                </button>
              );
            })}
          </div>

          {/* Barcode Scanner - Quick Add Product */}
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <BarcodeScanner
              onScan={handleBarcodeScan}
              placeholder="Scan barcode or type to search product..."
              autoFocus={false}
            />
          </div>

          {/* Main Content Area - 3 Column Layout */}
          <div className="flex-1 grid grid-cols-12 gap-4 min-h-0">
            {/* Left Column: Prescription (if optical) + Barcode + Items */}
            <div className="col-span-7 flex flex-col gap-4 min-h-0 overflow-y-auto">
              {/* Prescription Panel - Only for optical categories */}
              {(categoryRequiresPrescription || categoryCanAddLens) && (
                <div className="bg-white border border-gray-200 rounded-lg p-4">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <Eye className="w-4 h-4 text-bv-red-600" />
                      <h3 className="font-medium text-gray-900">Prescription</h3>
                      {selectedPatient && (
                        <span className="text-xs text-gray-500">({selectedPatient.name})</span>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => setShowPrescriptionSelectModal(true)}
                        className="text-xs px-3 py-1.5 bg-blue-50 text-blue-600 rounded-lg hover:bg-blue-100 transition-colors flex items-center gap-1"
                      >
                        <Link2 className="w-3 h-3" />
                        {prescription ? 'Change' : 'Link Existing'}
                      </button>
                    </div>
                  </div>

                  {prescription ? (
                    <PrescriptionPanel
                      prescription={prescription}
                      onPrescriptionChange={handlePrescriptionChange}
                      onOpenModal={() => setShowPrescriptionSelectModal(true)}
                      patientName={selectedPatient?.name}
                      compact
                    />
                  ) : (
                    <div className="text-center py-6 bg-gray-50 rounded-lg border-2 border-dashed border-gray-200">
                      <Eye className="w-8 h-8 text-gray-300 mx-auto mb-2" />
                      <p className="text-sm text-gray-500 mb-2">No prescription linked</p>
                      <button
                        onClick={() => setShowPrescriptionSelectModal(true)}
                        className="text-sm text-bv-red-600 hover:text-bv-red-700 font-medium"
                      >
                        Link Prescription
                      </button>
                    </div>
                  )}
                </div>
              )}

              {/* Barcode Scanner + Add Product */}
              <div className="bg-white border border-gray-200 rounded-lg p-3">
                <div className="flex items-center gap-3">
                  <div className="relative flex-1">
                    <Barcode className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
                    <input
                      ref={barcodeInputRef}
                      type="text"
                      value={barcodeInput}
                      onChange={(e) => setBarcodeInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          handleBarcodeSubmit(barcodeInput);
                        }
                      }}
                      placeholder="Scan barcode or enter product code..."
                      className="w-full pl-10 pr-4 py-2.5 text-sm border border-gray-200 rounded-lg focus:border-bv-red-500 focus:outline-none"
                    />
                  </div>
                  <button
                    onClick={() => handleBarcodeSubmit(barcodeInput)}
                    className="btn-primary px-4 py-2.5"
                    disabled={!barcodeInput.trim()}
                  >
                    Add
                  </button>
                  <button
                    onClick={() => setShowProductSearchModal(true)}
                    className="btn-outline px-4 py-2.5 flex items-center gap-2"
                    title="Search products"
                  >
                    <Plus className="w-4 h-4" />
                    Search
                  </button>
                </div>
              </div>

              {/* Order Items List */}
              <div className="flex-1 min-h-0 bg-white border border-gray-200 rounded-lg overflow-hidden flex flex-col">
                <div className="p-3 border-b border-gray-200 bg-gray-50 flex items-center justify-between">
                  <h3 className="font-medium text-gray-900 text-sm">
                    Order Items ({orderItems.length})
                  </h3>
                  {categoryCanAddLens && (
                    <span className="text-xs text-gray-500">
                      Click "Add Lens" to add optical lenses to frames
                    </span>
                  )}
                </div>

                <div className="flex-1 overflow-y-auto">
                  {orderItems.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-48 text-gray-400">
                      <ShoppingCart className="w-10 h-10 mb-2 opacity-50" />
                      <p className="text-sm">No items added</p>
                      <p className="text-xs">Scan barcode or search products</p>
                    </div>
                  ) : (
                    <div className="divide-y divide-gray-100">
                      {orderItems.filter(item => !item.isLensItem || !item.linkedFrameId).map((item) => {
                        const isExpanded = expandedItems.has(item.id);
                        const linkedLens = item.linkedLensId
                          ? orderItems.find(i => i.id === item.linkedLensId)
                          : null;
                        const canAddLens = activeCategoryConfig?.canAddLens &&
                          (item.category === 'FRAME' || item.category === 'SUNGLASS' || item.category === 'SMTSG' || item.category === 'SMTFR') &&
                          !item.linkedLensId;
                        const hasDiscount = item.discountAmount > 0;
                        const needsPrescription = item.requiresPrescription && !item.prescriptionLinked;

                        return (
                          <div
                            key={item.id}
                            className={clsx(
                              'p-3',
                              needsPrescription && 'bg-yellow-50'
                            )}
                          >
                            {/* Main Item Row */}
                            <div className="flex items-start gap-3">
                              {/* Expand/Collapse */}
                              <button
                                onClick={() => toggleItemExpansion(item.id)}
                                className="p-1 text-gray-400 hover:text-gray-600 mt-1"
                              >
                                {isExpanded ? (
                                  <ChevronUp className="w-4 h-4" />
                                ) : (
                                  <ChevronDown className="w-4 h-4" />
                                )}
                              </button>

                              {/* Item Details */}
                              <div className="flex-1 min-w-0">
                                <div className="flex items-start justify-between">
                                  <div>
                                    <p className="font-medium text-gray-900 text-sm truncate">
                                      {item.productName}
                                    </p>
                                    <p className="text-xs text-gray-500">
                                      {item.brand} • {item.sku}
                                    </p>
                                  </div>

                                  {/* Price */}
                                  <div className="text-right ml-4">
                                    <p className="font-semibold text-gray-900">
                                      {formatCurrency(item.finalPrice)}
                                    </p>
                                    {hasDiscount && (
                                      <p className="text-xs text-green-600">
                                        -{item.discountPercent.toFixed(0)}% off
                                      </p>
                                    )}
                                  </div>
                                </div>

                                {/* Warning if needs prescription */}
                                {needsPrescription && (
                                  <div className="flex items-center gap-1 mt-1 text-xs text-amber-600">
                                    <AlertTriangle className="w-3 h-3" />
                                    <span>Prescription required</span>
                                    <button
                                      onClick={() => setShowPrescriptionSelectModal(true)}
                                      className="underline ml-1"
                                    >
                                      Link now
                                    </button>
                                  </div>
                                )}

                                {/* Linked Lens Info */}
                                {linkedLens && (
                                  <div className="mt-2 p-2 bg-blue-50 rounded-lg">
                                    <div className="flex items-center justify-between">
                                      <div className="flex items-center gap-2">
                                        <Link2 className="w-3 h-3 text-blue-600" />
                                        <span className="text-xs text-blue-700 font-medium">
                                          + {linkedLens.productName}
                                        </span>
                                      </div>
                                      <div className="flex items-center gap-2">
                                        <span className="text-xs font-semibold text-blue-700">
                                          {formatCurrency(linkedLens.finalPrice)}
                                        </span>
                                        <button
                                          onClick={() => handleRemoveLensFromFrame(item.id)}
                                          className="p-1 text-blue-400 hover:text-red-500"
                                          title="Remove lens"
                                        >
                                          <Unlink className="w-3 h-3" />
                                        </button>
                                      </div>
                                    </div>
                                    {linkedLens.lensDetails && (
                                      <p className="text-xs text-blue-600 mt-1">
                                        {linkedLens.lensDetails.indexLabel} • {linkedLens.lensDetails.coatingLabel}
                                        {linkedLens.lensDetails.addOns?.length > 0 && (
                                          <> • {linkedLens.lensDetails.addOns.join(', ')}</>
                                        )}
                                      </p>
                                    )}
                                  </div>
                                )}

                                {/* Add Lens Button */}
                                {canAddLens && (
                                  <button
                                    onClick={() => handleAddLensToItem(item.id)}
                                    className="mt-2 text-xs px-3 py-1.5 border border-bv-red-300 text-bv-red-600 rounded-lg hover:bg-bv-red-50 flex items-center gap-1"
                                  >
                                    <Plus className="w-3 h-3" />
                                    Add Lens
                                  </button>
                                )}
                              </div>

                              {/* Quantity Controls */}
                              <div className="flex items-center gap-1">
                                <button
                                  onClick={() => handleUpdateItemQuantity(item.id, item.quantity - 1)}
                                  disabled={item.quantity <= 1}
                                  className="w-6 h-6 flex items-center justify-center rounded border border-gray-300 text-gray-600 hover:bg-gray-100 disabled:opacity-50"
                                >
                                  -
                                </button>
                                <input
                                  type="number"
                                  value={item.quantity}
                                  onChange={e => handleUpdateItemQuantity(item.id, parseInt(e.target.value) || 1)}
                                  min="1"
                                  className="w-10 px-1 py-1 text-center text-sm border border-gray-200 rounded"
                                />
                                <button
                                  onClick={() => handleUpdateItemQuantity(item.id, item.quantity + 1)}
                                  className="w-6 h-6 flex items-center justify-center rounded border border-gray-300 text-gray-600 hover:bg-gray-100"
                                >
                                  +
                                </button>
                              </div>

                              {/* Remove */}
                              <button
                                onClick={() => handleRemoveItem(item.id)}
                                className="p-1.5 text-red-400 hover:text-red-600 hover:bg-red-50 rounded"
                                title="Remove item"
                              >
                                <Trash2 className="w-4 h-4" />
                              </button>
                            </div>

                            {/* Expanded Details */}
                            {isExpanded && (
                              <div className="mt-3 pt-3 border-t border-gray-100 ml-8">
                                <div className="grid grid-cols-3 gap-4 text-xs">
                                  <div>
                                    <span className="text-gray-500">MRP</span>
                                    <p className="font-medium">{formatCurrency(item.mrp)}</p>
                                  </div>
                                  <div>
                                    <span className="text-gray-500">Offer Price</span>
                                    <p className="font-medium">{formatCurrency(item.offerPrice)}</p>
                                  </div>
                                  <div>
                                    <span className="text-gray-500">Discount</span>
                                    <p className="font-medium text-green-600">
                                      {item.discountPercent > 0 ? `${item.discountPercent.toFixed(1)}%` : 'None'}
                                    </p>
                                  </div>
                                </div>
                                {/* Edit Price */}
                                <div className="mt-3 flex items-center gap-2">
                                  <span className="text-xs text-gray-500">Edit Price:</span>
                                  <input
                                    type="number"
                                    defaultValue={item.finalPrice}
                                    onBlur={(e) => {
                                      const newPrice = parseFloat(e.target.value) || 0;
                                      if (newPrice !== item.finalPrice) {
                                        handleUpdateItemPrice(item.id, newPrice);
                                      }
                                    }}
                                    className="w-24 px-2 py-1 text-sm border border-gray-200 rounded"
                                  />
                                  <span className="text-xs text-gray-400">
                                    Max discount: {userDiscountCap}%
                                  </span>
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* Right Column: Order Details + Payment */}
            <div className="col-span-5 flex flex-col gap-4 min-h-0 overflow-y-auto">
              {/* Order Details */}
              <OrderDetailsPanel
                orderDetails={orderDetails}
                onChange={setOrderDetails}
              />

              {/* Payment Collection */}
              <PaymentCollectionPanel
                grandTotal={grandTotal}
                payments={payments}
                onAddPayment={handleAddPayment}
                onRemovePayment={handleRemovePayment}
                customerName={customer?.name}
                customerEmail={customer?.email}
                customerContact={customer?.phone}
                orderId={draftOrderId || undefined}
                orderNumber={draftOrderNumber || undefined}
                onInitiateOnlinePayment={handleInitiateOnlinePayment}
                allowCredit={true}
              />

              {/* Order Summary */}
              <div className="bg-white border border-gray-200 rounded-lg p-4">
                <div className="space-y-2 mb-4">
                  <div className="flex justify-between text-sm">
                    <span className="text-gray-600">Subtotal ({orderItems.length} items)</span>
                    <span className="text-gray-900">{formatCurrency(subtotal)}</span>
                  </div>
                  {orderDiscount.amount > 0 && (
                    <div className="flex justify-between text-sm">
                      <span className="text-green-600">Discount ({orderDiscount.percent}%)</span>
                      <span className="text-green-600">-{formatCurrency(orderDiscount.amount)}</span>
                    </div>
                  )}
                  <div className="flex justify-between text-sm">
                    <span className="text-gray-600">GST (18%)</span>
                    <span className="text-gray-900">{formatCurrency(gstAmount)}</span>
                  </div>
                  <div className="flex justify-between text-base font-semibold border-t pt-2">
                    <span>Grand Total</span>
                    <span className="text-bv-red-600">{formatCurrency(grandTotal)}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-gray-600">Paid</span>
                    <span className="text-green-600">{formatCurrency(totalPaid)}</span>
                  </div>
                  {balanceDue > 0 && (
                    <div className="flex justify-between text-sm font-medium">
                      <span className="text-red-600">Balance Due</span>
                      <span className="text-red-600">{formatCurrency(balanceDue)}</span>
                    </div>
                  )}
                </div>

                {/* Action Buttons */}
                <div className="grid grid-cols-2 gap-3">
                  <button
                    onClick={handleHoldOrder}
                    className="btn-outline py-2.5 flex items-center justify-center gap-2"
                  >
                    <Save className="w-4 h-4" />
                    Hold
                  </button>
                  <button
                    onClick={handlePrintBill}
                    className="btn-outline py-2.5 flex items-center justify-center gap-2"
                  >
                    <Printer className="w-4 h-4" />
                    Print
                  </button>
                  <button
                    onClick={handleCompleteOrder}
                    disabled={orderItems.length === 0}
                    className="col-span-2 btn-primary py-3 flex items-center justify-center gap-2 text-base font-medium disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <Check className="w-5 h-5" />
                    Complete Order
                  </button>
                </div>
              </div>
            </div>
          </div>
        </>
      )}

      {/* Modals */}
      {showPrescriptionSelectModal && (
        <PrescriptionSelectModal
          onClose={() => setShowPrescriptionSelectModal(false)}
          onSelect={handlePrescriptionSelect}
          onCreateNew={() => {
            setShowPrescriptionSelectModal(false);
            // Prescription will be entered inline
          }}
          patient={selectedPatient}
          customerId={customer?.id || ''}
          currentPrescriptionId={prescription?.id}
        />
      )}

      {showProductSearchModal && activePOSCategory && (
        <ProductSearchModal
          onClose={() => setShowProductSearchModal(false)}
          onSelect={handleProductSelect}
          category={activeCategoryConfig?.code as ProductCategory || 'FRAME'}
          categoryLabel={activePOSCategory.label}
        />
      )}

      {showLensModal && (
        <LensDetailsModal
          onClose={() => {
            setShowLensModal(false);
            setSelectedItemForLens(null);
          }}
          onSave={handleSaveLensDetails}
        />
      )}
    </div>
  );
}

export default POSPage;
