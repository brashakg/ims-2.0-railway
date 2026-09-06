// ============================================================================
// IMS 2.0 - Workshop: create-job-from-order modal (incl. the F9 DC hardlock)
// ============================================================================
// Moved verbatim out of WorkshopPage.tsx (Wave 3 file diet).

import { Search } from 'lucide-react';
import clsx from 'clsx';
import type { WorkshopPageState } from './useWorkshopPage';

export function CreateJobModal({ page }: { page: WorkshopPageState }) {
  const { showCreateJob, setShowCreateJob, setCreateSelectedOrder, setCreateOrders, createSelectedOrder, createOrderSearch, setCreateOrderSearch, searchOrdersForJob, createOrders, createPriority, setCreatePriority, createExpectedDate, setCreateExpectedDate, createFitting, setCreateFitting, createNotes, setCreateNotes, dcHardlock, canOverrideHardlock, overrideReason, setOverrideReason, handleCreateJob, createLoading } = page;
  return (
    <>
      {/* CREATE JOB MODAL */}
      {showCreateJob && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[85dvh] overflow-y-auto">
            <div className="p-5 border-b border-gray-200 flex items-center justify-between">
              <h3 className="font-semibold text-gray-900">Create Workshop Job from Order</h3>
              <button onClick={() => { setShowCreateJob(false); setCreateSelectedOrder(null); setCreateOrders([]); }} className="p-1 hover:bg-gray-100 rounded text-gray-500 hover:text-gray-700">
                ×
              </button>
            </div>
            <div className="p-5 space-y-4">
              {!createSelectedOrder ? (
                <>
                  <div className="flex gap-2">
                    <div className="relative flex-1">
                      <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                      <input value={createOrderSearch} onChange={e => setCreateOrderSearch(e.target.value)}
                        onKeyDown={e => e.key === 'Enter' && searchOrdersForJob()}
                        placeholder="Search order number or customer..."
                        className="w-full pl-9 pr-4 py-2.5 border border-gray-300 bg-white text-gray-900 rounded-lg text-sm placeholder-gray-500" />
                    </div>
                    <button onClick={searchOrdersForJob} className="px-4 py-2 bg-bv-red-600 text-white rounded-lg text-sm font-semibold hover:bg-bv-red-700">Search</button>
                  </div>
                  {createOrders.length > 0 && (
                    <div className="space-y-1.5 max-h-60 overflow-y-auto">
                      {createOrders.map((o: any) => (
                        <button key={o.id} onClick={() => setCreateSelectedOrder(o)}
                          className="w-full flex items-center justify-between p-3 rounded-lg border border-gray-300 hover:border-bv-red-400 hover:bg-gray-100 text-left text-gray-900 transition-colors">
                          <div>
                            <p className="text-sm font-medium">{o.orderNumber}</p>
                            <p className="text-xs text-gray-500">{o.customerName} · {(o.items || []).length} items</p>
                          </div>
                          <span className="text-sm font-bold text-bv-red-700">₹{Math.round(o.grandTotal || 0).toLocaleString('en-IN')}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <>
                  <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="font-medium text-sm">{createSelectedOrder.orderNumber}</p>
                        <p className="text-xs text-gray-500">{createSelectedOrder.customerName}</p>
                      </div>
                      <button onClick={() => setCreateSelectedOrder(null)} className="text-xs text-bv-red-600 hover:underline">Change</button>
                    </div>
                    <div className="mt-2 space-y-1">
                      {(createSelectedOrder.items || []).map((item: any, i: number) => (
                        <div key={i} className="flex items-center justify-between text-xs">
                          <span className="text-gray-900">{item.productName || item.product_name || item.name}</span>
                          <span className="text-gray-500 text-xs">{item.category}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div>
                    <label className="text-xs text-gray-500 block mb-1">Priority</label>
                    <div className="flex gap-2">
                      {(['NORMAL', 'EXPRESS', 'URGENT'] as const).map(p => (
                        <button key={p} onClick={() => setCreatePriority(p)}
                          className={clsx('flex-1 py-2 rounded-lg text-xs font-medium border-2 transition-all',
                            createPriority === p
                              ? p === 'URGENT' ? 'border-red-500 bg-red-50 text-red-700'
                                : p === 'EXPRESS' ? 'border-amber-500 bg-amber-50 text-amber-700'
                                  : 'border-bv-red-600 bg-bv-red-50 text-bv-red-700'
                              : 'border-gray-300 text-gray-600 bg-white')}>
                          {p}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div>
                    <label className="text-xs text-gray-500 block mb-1">Expected Delivery Date</label>
                    <input type="date" value={createExpectedDate} onChange={e => setCreateExpectedDate(e.target.value)}
                      min={new Date().toISOString().split('T')[0]}
                      className="w-full px-3 py-2 border border-gray-300 bg-white text-gray-900 rounded-lg text-sm"
                      title="Expected Delivery Date" />
                  </div>

                  <div>
                    <label className="text-xs text-gray-500 block mb-1">Fitting Instructions</label>
                    <textarea value={createFitting} onChange={e => setCreateFitting(e.target.value)}
                      placeholder="PD, segment height, tilt, wrap angle, frame adjustments..."
                      className="w-full px-3 py-2 border border-gray-300 bg-white text-gray-900 rounded-lg text-sm h-16 resize-none placeholder-gray-500" />
                  </div>

                  <div>
                    <label className="text-xs text-gray-500 block mb-1">Special Notes for Workshop</label>
                    <textarea value={createNotes} onChange={e => setCreateNotes(e.target.value)}
                      placeholder="Tint, drill mount, special coating, customer preferences..."
                      className="w-full px-3 py-2 border border-gray-300 bg-white text-gray-900 rounded-lg text-sm h-16 resize-none placeholder-gray-500" />
                  </div>

                  {/* F9 — DC hardlock banner. Semantic amber = action required;
                      not a decorative colour. */}
                  {dcHardlock && (
                    <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
                      <p className="font-medium">Delivery Challan required</p>
                      <p className="mt-1">{dcHardlock}</p>
                      <a href="/purchase/grn" className="mt-2 inline-block underline">
                        Go to GRN / DC entry
                      </a>
                      {canOverrideHardlock && (
                        <div className="mt-3">
                          <label className="block text-xs font-medium mb-1">
                            Override reason (Admin)
                          </label>
                          <input
                            type="text"
                            value={overrideReason}
                            onChange={e => setOverrideReason(e.target.value)}
                            placeholder="e.g. Emergency — DC in transit"
                            className="w-full px-3 py-2 border border-amber-300 bg-white text-gray-900 rounded-lg text-sm"
                          />
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
            {createSelectedOrder && (
              <div className="p-5 border-t border-gray-200 flex gap-2">
                <button onClick={() => { setShowCreateJob(false); setCreateSelectedOrder(null); }}
                  className="flex-1 px-4 py-2.5 border border-gray-300 text-gray-600 rounded-lg text-sm hover:bg-gray-100">Cancel</button>
                <button onClick={handleCreateJob} disabled={createLoading}
                  className="flex-1 px-4 py-2.5 bg-bv-red-600 text-white rounded-lg text-sm font-semibold hover:bg-bv-red-700 disabled:opacity-50">
                  {createLoading ? 'Creating...' : 'Create Job'}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
