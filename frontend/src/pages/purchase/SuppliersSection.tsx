// ============================================================================
// IMS 2.0 - Suppliers section (/purchase/suppliers)
// ============================================================================
// Wave 1 split: the supplier ledger + add/edit modal flow moved verbatim out
// of the old PurchaseManagementPage tab container. Renders inside
// PurchaseLayout. The edit wire (pencil -> supplier={editingSupplier}) is
// pinned by SuppliersSection.edit.test.tsx.

import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Search, Loader2, AlertTriangle } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { vendorsApi } from '../../services/api';
import { SupplierPanel } from './SupplierPanel';
import { SupplierFormModal } from './SupplierFormModal';
import { mapVendorToSupplier } from './purchaseMappers';
import type { Supplier } from './purchaseTypes';

export function SuppliersSection() {
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();

  const [searchQuery, setSearchQuery] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [showSupplierModal, setShowSupplierModal] = useState(false);
  const [editingSupplier, setEditingSupplier] = useState<Supplier | null>(null);

  // Header "New supplier" button navigates to ?new=1 (see PurchaseLayout).
  useEffect(() => {
    if (searchParams.get('new')) {
      setShowSupplierModal(true);
      const next = new URLSearchParams(searchParams);
      next.delete('new');
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  useEffect(() => {
    loadData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadData = async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const vendorsResp = await vendorsApi.getVendors({ is_active: true });
      const rawVendors: unknown[] = vendorsResp?.vendors ?? [];
      setSuppliers(rawVendors.map(mapVendorToSupplier));
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : 'Failed to load purchase data';
      setLoadError(msg);
      toast.error('Failed to load purchase data');
    } finally {
      setIsLoading(false);
    }
  };

  const filteredSuppliers = suppliers.filter(supplier =>
    supplier.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    supplier.code.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <>
      {/* Load Error Banner */}
      {loadError && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-sm font-medium text-red-900">Failed to load data</p>
            <p className="text-xs text-red-700 mt-1">{loadError}</p>
          </div>
          <button
            onClick={loadData}
            className="text-xs font-medium text-red-700 hover:text-red-900 underline"
          >
            Retry
          </button>
        </div>
      )}

      {/* Search */}
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
          <input
            type="text"
            placeholder="Search suppliers..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="input-field pl-10"
          />
        </div>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="flex items-center justify-center h-96">
          <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
        </div>
      ) : (
        <SupplierPanel suppliers={filteredSuppliers} onEdit={setEditingSupplier} />
      )}

      {/* Add Supplier Modal */}
      {showSupplierModal && (
        <SupplierFormModal
          onClose={() => setShowSupplierModal(false)}
          onCreated={(newSupplier) => {
            setSuppliers(prev => [...prev, newSupplier]);
            setShowSupplierModal(false);
          }}
        />
      )}

      {/* Edit Supplier Modal */}
      {editingSupplier && (
        <SupplierFormModal
          supplier={editingSupplier}
          onClose={() => setEditingSupplier(null)}
          onSaved={(saved) => {
            setSuppliers(prev => prev.map(s => (s.id === saved.id ? saved : s)));
            setEditingSupplier(null);
          }}
        />
      )}
    </>
  );
}
