// ============================================================================
// IMS 2.0 - Create Purchase Order Modal
// ============================================================================

import { useState } from 'react';
import {
  FileText,
  Plus,
  X as XIcon,
  Trash2,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import type { Supplier, PurchaseOrder, POItem } from './purchaseTypes';

interface POFormItem {
  productName: string;
  sku: string;
  quantity: number;
  unitCost: number;
  taxRate: number;
}

interface PurchaseOrderFormProps {
  suppliers: Supplier[];
  existingPOCount: number;
  onClose: () => void;
  onCreated: (po: PurchaseOrder) => void;
}

export function PurchaseOrderForm({ suppliers, existingPOCount, onClose, onCreated }: PurchaseOrderFormProps) {
  const toast = useToast();

  const [supplierId, setSupplierId] = useState('');
  const [expectedDelivery, setExpectedDelivery] = useState('');
  const [notes, setNotes] = useState('');
  const [items, setItems] = useState<POFormItem[]>([
    { productName: '', sku: '', quantity: 1, unitCost: 0, taxRate: 18 },
  ]);

  const addItem = () => {
    setItems(prev => [...prev, { productName: '', sku: '', quantity: 1, unitCost: 0, taxRate: 18 }]);
  };

  const removeItem = (index: number) => {
    setItems(prev => prev.filter((_, i) => i !== index));
  };

  const updateItem = (index: number, field: string, value: string | number) => {
    setItems(prev => prev.map((item, i) => i === index ? { ...item, [field]: value } : item));
  };

  const calcLineTotal = (item: POFormItem) => {
    return item.quantity * item.unitCost * (1 + item.taxRate / 100);
  };

  const calcSubtotal = () => items.reduce((sum, item) => sum + item.quantity * item.unitCost, 0);
  const calcTax = () => items.reduce((sum, item) => sum + item.quantity * item.unitCost * item.taxRate / 100, 0);
  const calcGrandTotal = () => calcSubtotal() + calcTax();

  const handleCreate = () => {
    if (!supplierId) {
      toast.error('Please select a supplier');
      return;
    }
    if (!expectedDelivery) {
      toast.error('Please set an expected delivery date');
      return;
    }
    const validItems = items.filter(item => item.productName.trim() && item.quantity > 0 && item.unitCost > 0);
    if (validItems.length === 0) {
      toast.error('Please add at least one valid line item');
      return;
    }

    const supplier = suppliers.find(s => s.id === supplierId);
    const poItems: POItem[] = validItems.map((item, idx) => ({
      productId: `new-${Date.now()}-${idx}`,
      productName: item.productName,
      sku: item.sku || 'N/A',
      quantity: item.quantity,
      unitCost: item.unitCost,
      taxRate: item.taxRate,
      total: calcLineTotal(item),
    }));

    const subtotal = validItems.reduce((sum, item) => sum + item.quantity * item.unitCost, 0);
    const taxAmount = validItems.reduce((sum, item) => sum + item.quantity * item.unitCost * item.taxRate / 100, 0);

    const newPO: PurchaseOrder = {
      id: `po-${Date.now()}`,
      poNumber: `PO-2024-${String(existingPOCount + 1).padStart(3, '0')}`,
      supplierId,
      supplierName: supplier?.name ?? 'Unknown',
      date: new Date().toISOString().split('T')[0],
      expectedDelivery,
      status: 'DRAFT',
      items: poItems,
      subtotal,
      taxAmount,
      total: subtotal + taxAmount,
      notes: notes || undefined,
    };

    onCreated(newPO);
    toast.success(`Purchase Order ${newPO.poNumber} created as Draft`);
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-start justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-3xl my-8">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-200">
          <h2 className="text-xl font-bold text-gray-900 flex items-center gap-2">
            <FileText className="w-5 h-5 text-blue-600" />
            Create Purchase Order
          </h2>
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <XIcon className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* Body */}
        <div className="p-6 space-y-6">
          {/* Supplier & Delivery Date */}
          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Supplier *</label>
              <select
                value={supplierId}
                onChange={(e) => setSupplierId(e.target.value)}
                className="input-field"
              >
                <option value="">Select a supplier...</option>
                {suppliers.map(s => (
                  <option key={s.id} value={s.id}>{s.name} ({s.code})</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Expected Delivery Date *</label>
              <input
                type="date"
                value={expectedDelivery}
                onChange={(e) => setExpectedDelivery(e.target.value)}
                className="input-field"
              />
            </div>
          </div>

          {/* Line Items */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <label className="block text-sm font-medium text-gray-700">Line Items *</label>
              <button
                onClick={addItem}
                className="text-sm text-blue-600 hover:text-blue-700 font-medium flex items-center gap-1"
              >
                <Plus className="w-4 h-4" />
                Add Item
              </button>
            </div>

            <div className="space-y-3">
              {items.map((item, index) => (
                <div key={index} className="p-3 bg-gray-50 rounded-lg border border-gray-200">
                  <div className="grid grid-cols-12 gap-2 items-end">
                    <div className="col-span-12 tablet:col-span-4">
                      <label className="block text-xs text-gray-600 mb-1">Product Name</label>
                      <input
                        type="text"
                        placeholder="Product name"
                        value={item.productName}
                        onChange={(e) => updateItem(index, 'productName', e.target.value)}
                        className="input-field text-sm"
                      />
                    </div>
                    <div className="col-span-6 tablet:col-span-2">
                      <label className="block text-xs text-gray-600 mb-1">SKU</label>
                      <input
                        type="text"
                        placeholder="SKU"
                        value={item.sku}
                        onChange={(e) => updateItem(index, 'sku', e.target.value)}
                        className="input-field text-sm"
                      />
                    </div>
                    <div className="col-span-6 tablet:col-span-1">
                      <label className="block text-xs text-gray-600 mb-1">Qty</label>
                      <input
                        type="number"
                        min="1"
                        value={item.quantity}
                        onChange={(e) => updateItem(index, 'quantity', parseInt(e.target.value) || 0)}
                        className="input-field text-sm"
                      />
                    </div>
                    <div className="col-span-6 tablet:col-span-2">
                      <label className="block text-xs text-gray-600 mb-1">Unit Cost ({'\u20B9'})</label>
                      <input
                        type="number"
                        min="0"
                        step="0.01"
                        value={item.unitCost}
                        onChange={(e) => updateItem(index, 'unitCost', parseFloat(e.target.value) || 0)}
                        className="input-field text-sm"
                      />
                    </div>
                    <div className="col-span-4 tablet:col-span-1">
                      <label className="block text-xs text-gray-600 mb-1">Tax %</label>
                      <input
                        type="number"
                        min="0"
                        max="28"
                        value={item.taxRate}
                        onChange={(e) => updateItem(index, 'taxRate', parseFloat(e.target.value) || 0)}
                        className="input-field text-sm"
                      />
                    </div>
                    <div className="col-span-6 tablet:col-span-1 text-right">
                      <label className="block text-xs text-gray-600 mb-1">Total</label>
                      <p className="text-sm font-semibold text-gray-900 py-2">{'\u20B9'}{calcLineTotal(item).toLocaleString()}</p>
                    </div>
                    <div className="col-span-2 tablet:col-span-1 flex justify-end">
                      <button
                        onClick={() => removeItem(index)}
                        disabled={items.length === 1}
                        className="p-2 text-red-500 hover:bg-red-50 rounded-lg transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Totals */}
          <div className="flex justify-end">
            <div className="w-64 space-y-2 p-4 bg-gray-50 rounded-lg">
              <div className="flex justify-between text-sm">
                <span className="text-gray-600">Subtotal</span>
                <span className="font-medium text-gray-900">{'\u20B9'}{calcSubtotal().toLocaleString()}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-gray-600">Tax</span>
                <span className="font-medium text-gray-900">{'\u20B9'}{calcTax().toLocaleString()}</span>
              </div>
              <div className="flex justify-between text-sm font-bold border-t border-gray-300 pt-2">
                <span className="text-gray-900">Grand Total</span>
                <span className="text-gray-900">{'\u20B9'}{calcGrandTotal().toLocaleString()}</span>
              </div>
            </div>
          </div>

          {/* Notes */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Notes</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Any additional notes for this purchase order..."
              rows={3}
              className="input-field"
            />
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-6 border-t border-gray-200">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleCreate}
            className="btn-primary flex items-center gap-2"
          >
            <FileText className="w-4 h-4" />
            Create as Draft
          </button>
        </div>
      </div>
    </div>
  );
}
