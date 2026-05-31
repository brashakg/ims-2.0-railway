// ============================================================================
// IMS 2.0 — Queue Existing Customer Modal (Clinical, Phase 6.13)
// ============================================================================
// Search-first flow for adding an existing customer's patient to the
// optometrist queue. User feedback: "search customer and proceed with
// existing not working in clinic" — previously the only path was the
// create-new modal, so even familiar customers had to be re-created.
//
// Behaviour:
//   1. Phone / name search (debounced 250ms)
//   2. Results list — click a customer to expand patients
//   3. Pick a patient → add-to-queue action
//   4. Explicit "Not found — create new" fallback that swaps to the
//      existing AddCustomerModal without losing the search query
//
// No backend changes; uses customerApi.getCustomers under the hood.

import { useEffect, useRef, useState } from 'react';
import { X, Search, User, UserPlus, Loader2, Phone } from 'lucide-react';
import { customerApi } from '../../services/api';
import { buildCustomerSearchHits } from '../../utils/customerSearchHits';
import type { Customer, Patient } from '../../types';

interface QueueExistingCustomerModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** Called with the picked customer + patient when the user confirms. */
  onQueue: (customer: Customer, patient: Patient | null) => Promise<void> | void;
  /** Called when the user clicks "Create new" — parent should close this
   *  modal and open AddCustomerModal, optionally pre-filling the query. */
  onCreateNew: (initialQuery: string) => void;
  storeId?: string;
}

export function QueueExistingCustomerModal({
  isOpen,
  onClose,
  onQueue,
  onCreateNew,
  storeId,
}: QueueExistingCustomerModalProps) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Customer[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [selectedCustomer, setSelectedCustomer] = useState<Customer | null>(null);
  const [isQueuing, setIsQueuing] = useState(false);
  const [searchErr, setSearchErr] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Debounced search. 3+ chars triggers a call; shorter clears results.
  useEffect(() => {
    if (!isOpen) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const q = query.trim();
    if (q.length < 3) {
      setResults([]);
      setIsSearching(false);
      return;
    }
    setIsSearching(true);
    setSearchErr(null);
    debounceRef.current = setTimeout(async () => {
      try {
        const resp = await customerApi.getCustomers({
          search: q,
          storeId,
          limit: 20,
        });
        const list = (resp as any)?.customers || (resp as any) || [];
        // The /customers endpoint returns raw Mongo docs with snake_case
        // fields (customer_id, mobile, patient_id). Normalize to the
        // camelCase shape the rest of the modal + parent expect, so
        // (a) `c.id` actually resolves (fixes a bug where every row's
        // isSelected check resolved to true and the patient list could
        // never expand) and (b) `c.phone` surfaces in the result list
        // instead of always rendering "—".
        const normalized = (Array.isArray(list) ? list : []).map((c: any) => ({
          ...c,
          id: c.id || c.customer_id || c._id,
          phone: c.phone || c.mobile || '',
          patients: Array.isArray(c.patients)
            ? c.patients.map((p: any) => ({
                ...p,
                id: p.id || p.patient_id,
                phone: p.phone || p.mobile || '',
              }))
            : [],
        }));
        setResults(normalized);
      } catch (e) {
        // eslint-disable-next-line no-console
        console.error('[Clinical] customer search failed:', e);
        setSearchErr('Search failed. Try again.');
        setResults([]);
      } finally {
        setIsSearching(false);
      }
    }, 250);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, isOpen, storeId]);

  // Reset on open/close
  useEffect(() => {
    if (!isOpen) {
      setQuery('');
      setResults([]);
      setSelectedCustomer(null);
      setIsQueuing(false);
      setSearchErr(null);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  // Queue any (customer, patient) pair — shared by the direct patient-hit rows
  // and the expand-an-account flow.
  const queue = async (customer: Customer, patient: Patient | null) => {
    if (isQueuing) return;
    setIsQueuing(true);
    try {
      await onQueue(customer, patient);
      // Parent closes us on success
    } finally {
      setIsQueuing(false);
    }
  };
  const handleQueuePatient = (patient: Patient | null) => {
    if (!selectedCustomer) return;
    return queue(selectedCustomer, patient);
  };

  // Flatten results into labeled account/patient hits so a patient matched by
  // name/phone is a directly-pickable row (not hidden inside a collapsed
  // account). Account hits keep the expand-to-browse-all-patients affordance.
  const hits = buildCustomerSearchHits(results, query);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl w-full max-w-2xl max-h-[85vh] overflow-hidden flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Queue Existing Customer</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Search by phone number or name. Pick a patient to add them to today's queue.
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-gray-500 hover:bg-gray-100 rounded"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Search */}
        <div className="p-5 border-b border-gray-200">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setSelectedCustomer(null);
              }}
              autoFocus
              placeholder="Phone number or name (min 3 characters)"
              className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg text-sm text-gray-900 focus:border-bv-red-400 focus:outline-none"
            />
          </div>
          {searchErr && (
            <p className="text-sm text-red-600 mt-2">{searchErr}</p>
          )}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto">
          {isSearching ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-5 h-5 animate-spin text-bv-red-600" />
            </div>
          ) : query.trim().length < 3 ? (
            <div className="text-center py-12 text-sm text-gray-500">
              Start typing to search customers.
            </div>
          ) : hits.length === 0 ? (
            <div className="text-center py-10 px-5">
              <p className="text-sm text-gray-700 font-medium">No matching customers found</p>
              <p className="text-xs text-gray-500 mt-1 mb-4">
                Create a new customer record with "{query.trim()}"
              </p>
              <button
                onClick={() => onCreateNew(query.trim())}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-bv-red-600 text-white text-sm font-semibold hover:bg-bv-red-700"
              >
                <UserPlus className="w-4 h-4" />
                Create new customer
              </button>
            </div>
          ) : (
            <div className="divide-y divide-gray-200">
              {hits.map((hit) => {
                // A patient matched directly by name/phone — pick them in one click,
                // instead of hiding them inside a collapsed account row.
                if (hit.kind === 'patient') {
                  const p = hit.patient as Patient;
                  return (
                    <button
                      key={hit.key}
                      onClick={() => queue(hit.customer as Customer, p)}
                      disabled={isQueuing}
                      className="w-full px-5 py-3 text-left hover:bg-gray-50 flex items-center justify-between gap-3 disabled:opacity-60"
                    >
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="w-9 h-9 rounded-full bg-blue-100 flex items-center justify-center flex-shrink-0">
                          <User className="w-4 h-4 text-blue-700" />
                        </div>
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-gray-900 truncate">
                            {hit.displayName}
                            <span className="ml-2 align-middle text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded bg-blue-100 text-blue-700">Patient</span>
                          </p>
                          <p className="text-xs text-gray-500 flex items-center gap-1 truncate">
                            <Phone className="w-3 h-3" />
                            {hit.phone || '—'} · under {hit.accountName}
                          </p>
                        </div>
                      </div>
                      <span className="text-xs text-bv-red-600 font-semibold flex-shrink-0">
                        {isQueuing ? 'Adding…' : 'Add to queue →'}
                      </span>
                    </button>
                  );
                }
                // Account holder — expand to browse / queue the account or any dependent.
                const c = hit.customer as Customer;
                const isSelected = selectedCustomer?.id === c.id;
                return (
                  <div key={hit.key} className={isSelected ? 'bg-bv-red-50' : ''}>
                    <button
                      onClick={() => setSelectedCustomer(isSelected ? null : c)}
                      className="w-full px-5 py-3 text-left hover:bg-gray-50 flex items-center justify-between gap-3"
                    >
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="w-9 h-9 rounded-full bg-gray-200 flex items-center justify-center flex-shrink-0">
                          <User className="w-4 h-4 text-gray-600" />
                        </div>
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-gray-900 truncate">
                            {c.name || '—'}
                            <span className="ml-2 align-middle text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">Account</span>
                          </p>
                          <p className="text-xs text-gray-500 flex items-center gap-1">
                            <Phone className="w-3 h-3" />
                            {c.phone || '—'}
                            {c.patients && c.patients.length > 0 && (
                              <span className="ml-2">· {c.patients.length} patient{c.patients.length === 1 ? '' : 's'}</span>
                            )}
                          </p>
                        </div>
                      </div>
                    </button>

                    {/* Expanded patient list */}
                    {isSelected && (
                      <div className="px-5 pb-3 bg-bv-red-50">
                        {c.patients && c.patients.length > 0 ? (
                          <>
                            <p className="text-xs font-medium text-gray-700 mb-2">
                              Pick a patient to queue:
                            </p>
                            <div className="space-y-1.5">
                              {c.patients.map((p) => (
                                <button
                                  key={p.id}
                                  onClick={() => handleQueuePatient(p)}
                                  disabled={isQueuing}
                                  className="w-full text-left px-3 py-2 bg-white border border-gray-200 rounded-lg hover:border-bv-red-400 hover:bg-bv-red-50 transition-colors text-sm flex items-center justify-between disabled:opacity-60"
                                >
                                  <span className="font-medium text-gray-900">{p.name}</span>
                                  <span className="text-xs text-bv-red-600 font-semibold">
                                    {isQueuing ? 'Adding…' : 'Add to queue →'}
                                  </span>
                                </button>
                              ))}
                            </div>
                          </>
                        ) : (
                          <>
                            <p className="text-xs text-gray-600 mb-2">
                              No patients yet for this customer. Queue the customer themselves:
                            </p>
                            <button
                              onClick={() => handleQueuePatient(null)}
                              disabled={isQueuing}
                              className="w-full px-3 py-2 bg-bv-red-600 text-white rounded-lg text-sm font-semibold hover:bg-bv-red-700 disabled:opacity-60"
                            >
                              {isQueuing ? 'Adding…' : `Queue ${c.name}`}
                            </button>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer — always-visible fallback */}
        <div className="px-5 py-3 border-t border-gray-200 bg-gray-50 flex items-center justify-between text-xs">
          <span className="text-gray-500">Customer not in the list?</span>
          <button
            onClick={() => onCreateNew(query.trim())}
            className="text-bv-red-600 hover:text-bv-red-700 font-semibold inline-flex items-center gap-1"
          >
            <UserPlus className="w-3.5 h-3.5" />
            Create new customer
          </button>
        </div>
      </div>
    </div>
  );
}

export default QueueExistingCustomerModal;
