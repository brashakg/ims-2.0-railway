// ============================================================================
// IMS 2.0 - Suppliers section (/purchase/suppliers)
// ============================================================================
// Wave 1 split: the supplier ledger + add/edit modal flow moved verbatim out
// of the old PurchaseManagementPage tab container. Renders inside
// PurchaseLayout. The edit wire (pencil -> supplier={editingSupplier}) is
// pinned by SuppliersSection.edit.test.tsx.

import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Search, Loader2, AlertTriangle } from 'lucide-react';
import { SupplierPanel } from './SupplierPanel';
import { SupplierFormModal } from './SupplierFormModal';
import { useSuppliers, vendorsQueryKey } from './purchaseQueries';
import type { Supplier } from './purchaseTypes';

export function SuppliersSection() {
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();

  const [searchQuery, setSearchQuery] = useState('');
  const [showSupplierModal, setShowSupplierModal] = useState(false);
  const [editingSupplier, setEditingSupplier] = useState<Supplier | null>(null);

  // Cached across section switches; refreshes in the background.
  const suppliersQ = useSuppliers();
  const suppliers = suppliersQ.data ?? [];
  const isLoading = suppliersQ.isPending;
  const loadError = suppliersQ.isError ? 'Failed to load purchase data' : null;

  // Cache writer for add/edit: the ledger updates in place, no refetch flash.
  const patchSuppliers = (fn: (old: Supplier[]) => Supplier[]) =>
    queryClient.setQueryData<Supplier[]>(vendorsQueryKey, (old) => fn(old ?? []));

  // Header "New supplier" button navigates to ?new=1 (see PurchaseLayout).
  useEffect(() => {
    if (searchParams.get('new')) {
      setShowSupplierModal(true);
      const next = new URLSearchParams(searchParams);
      next.delete('new');
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  const loadData = () => {
    void suppliersQ.refetch();
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
            patchSuppliers(prev => [...prev, newSupplier]);
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
            patchSuppliers(prev => prev.map(s => (s.id === saved.id ? saved : s)));
            setEditingSupplier(null);
          }}
        />
      )}
    </>
  );
}
