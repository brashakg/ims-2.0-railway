// ============================================================================
// IMS 2.0 - Clinic Prescription History + Edit (per customer)
// ============================================================================
// One place in the Clinic module to:
//   * VIEW a customer's past prescriptions, grouped by family member (patient),
//     each annotated with validity / expiry  (bug #3 — old-Rx viewing)
//   * EDIT an existing prescription via PUT /prescriptions/{id}  (bug #1)
//   * Add a clearly-labelled NEW prescription that starts BLANK, so "new" is
//     never confused with "editing the last record"  (bug #2)
//   * PRINT the A5 Rx card
//
// Reuses GET /prescriptions/family/{customer_id} (already grouped by patient)
// and the shared PrescriptionForm for both create and edit.

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  X, Users, User, Eye, Calendar, Plus, Pencil, Printer,
  Loader2, AlertTriangle, CheckCircle, Clock, Search, Phone, ArrowLeft,
} from 'lucide-react';
import clsx from 'clsx';
import { prescriptionApi, clinicalApi, customerApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { PrescriptionForm } from '../pos/PrescriptionForm';

interface FamilyMember {
  patient_id: string | null;
  name: string | null;
  relation: string | null;
  prescription_count: number;
  valid_count: number;
  prescriptions: any[];
}

interface ClinicPrescriptionHistoryProps {
  isOpen: boolean;
  onClose: () => void;
  /** Preset customer to show. Optional in 'panel' + searchable mode, where the
   *  user picks a customer via the built-in search box instead. */
  customerId?: string;
  customerName?: string;
  /** The patient queued for this visit, pre-selected for a "New prescription". */
  defaultPatientId?: string;
  /** 'modal' (default) renders the scrim+dialog overlay opened from a queue row.
   *  'panel' renders inline (no scrim) for use as a full-page Prescriptions tab. */
  mode?: 'modal' | 'panel';
  /** When true (panel/search mode), show a customer search box so a customer can
   *  be looked up by name/mobile, then their family Rx history is shown. */
  searchable?: boolean;
  /** Active store id — scopes the customer search in search mode. */
  storeId?: string;
}

// Backend Rx doc (nested right_eye/left_eye, snake_case) -> the flat field
// shape PrescriptionForm consumes as initialData (sph_od/cyl_od/... + dates).
function rxToFormInitial(rx: any): Record<string, any> {
  const re = rx?.right_eye || rx?.rightEye || {};
  const le = rx?.left_eye || rx?.leftEye || {};
  const num = (v: any) => {
    if (v === undefined || v === null || v === '') return undefined;
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  };
  const str = (v: any) => (v === undefined || v === null || v === '' ? undefined : String(v));
  return {
    sph_od: num(re.sph ?? re.sphere), cyl_od: num(re.cyl ?? re.cylinder), axis_od: num(re.axis),
    add_od: num(re.add ?? re.addition), pd_od: num(re.pd),
    va_od: str(re.acuity ?? re.va), prism_od: str(re.prism), base_od: str(re.base),
    sph_os: num(le.sph ?? le.sphere), cyl_os: num(le.cyl ?? le.cylinder), axis_os: num(le.axis),
    add_os: num(le.add ?? le.addition), pd_os: num(le.pd),
    va_os: str(le.acuity ?? le.va), prism_os: str(le.prism), base_os: str(le.base),
    ipd: str(rx?.ipd),
    lens_type: str(rx?.lens_recommendation),
    next_checkup: str(rx?.next_checkup),
  };
}

function rxValidity(rx: any): { expired: boolean; daysLeft: number | null; label: string } {
  // Server already annotates is_valid + expiry_date on the family payload.
  const expiryRaw = rx?.expiry_date || rx?.expiryDate;
  if (rx?.is_valid === false) return { expired: true, daysLeft: null, label: 'Expired' };
  if (!expiryRaw) return { expired: false, daysLeft: null, label: 'Valid' };
  const expiry = new Date(expiryRaw);
  if (isNaN(expiry.getTime())) return { expired: false, daysLeft: null, label: 'Valid' };
  const days = Math.ceil((expiry.getTime() - Date.now()) / (1000 * 60 * 60 * 24));
  if (days < 0) return { expired: true, daysLeft: days, label: 'Expired' };
  if (days <= 30) return { expired: false, daysLeft: days, label: `Expires in ${days}d` };
  return { expired: false, daysLeft: days, label: 'Valid' };
}

function fmtDate(d: any): string {
  const raw = d?.test_date || d?.testDate || d?.prescription_date || d?.created_at;
  if (!raw) return '—';
  const dt = new Date(raw);
  return isNaN(dt.getTime()) ? '—' : dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function fmtPower(v: any): string {
  if (v === undefined || v === null || v === '') return '-';
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n >= 0 ? `+${n.toFixed(2)}` : n.toFixed(2);
}

// Optometrist display NAME for an Rx card (backlog #2). The backend stores
// optometrist_name on new Rx and backfills it from the users collection on
// read-back. We show a name only — never a raw id (a UUID-looking value with
// no spaces is treated as an unresolved id and hidden).
function optometristName(rx: any): string | null {
  const name = rx?.optometrist_name || rx?.optometristName;
  if (!name || typeof name !== 'string') return null;
  const trimmed = name.trim();
  if (!trimmed) return null;
  const looksLikeId = /^[0-9a-f-]{16,}$/i.test(trimmed) && !trimmed.includes(' ');
  return looksLikeId ? null : trimmed;
}

export function ClinicPrescriptionHistory({
  isOpen,
  onClose,
  customerId,
  customerName,
  defaultPatientId,
  mode = 'modal',
  searchable = false,
  storeId,
}: ClinicPrescriptionHistoryProps) {
  const { user } = useAuth();
  const toast = useToast();

  const isPanel = mode === 'panel';

  // In search mode the customer is chosen via the built-in search box; otherwise
  // the preset prop wins. `selected` holds the picked customer in search mode.
  const [selected, setSelected] = useState<{ id: string; name?: string } | null>(null);
  const effectiveCustomerId = customerId || selected?.id || '';
  const effectiveCustomerName = customerName || selected?.name;

  // Customer search state (search mode only).
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<any[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [searchErr, setSearchErr] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [members, setMembers] = useState<FamilyMember[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Form modal state: either creating (editingRx === null) or editing an Rx.
  const [formOpen, setFormOpen] = useState(false);
  const [formPatientId, setFormPatientId] = useState<string | null>(null);
  const [editingRx, setEditingRx] = useState<any | null>(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!effectiveCustomerId) return;
    setIsLoading(true);
    setError(null);
    try {
      const res = await prescriptionApi.getFamilyRx(effectiveCustomerId);
      setMembers((res.members || []) as FamilyMember[]);
    } catch {
      setError('Failed to load prescriptions. Please try again.');
      setMembers([]);
    } finally {
      setIsLoading(false);
    }
  }, [effectiveCustomerId]);

  useEffect(() => {
    if (isOpen && effectiveCustomerId) load();
  }, [isOpen, effectiveCustomerId, load]);

  // Debounced customer search (search mode). 3+ chars triggers a lookup.
  useEffect(() => {
    if (!isOpen || !searchable || effectiveCustomerId) return;
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
        const resp = await customerApi.getCustomers({ search: q, storeId, limit: 20 });
        const list = (resp as any)?.customers || (resp as any) || [];
        const normalized = (Array.isArray(list) ? list : []).map((c: any) => ({
          ...c,
          id: c.id || c.customer_id || c._id,
          phone: c.phone || c.mobile || '',
        }));
        setResults(normalized);
      } catch {
        setSearchErr('Search failed. Try again.');
        setResults([]);
      } finally {
        setIsSearching(false);
      }
    }, 250);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, isOpen, searchable, effectiveCustomerId, storeId]);

  // Reset the search-mode selection + query when the panel closes so it reopens
  // on a clean search screen.
  useEffect(() => {
    if (!isOpen) {
      setSelected(null);
      setQuery('');
      setResults([]);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const openNew = (patientId: string | null) => {
    setEditingRx(null);
    setFormPatientId(patientId);
    setFormError(null);
    setFormOpen(true);
  };

  const openEdit = (patientId: string | null, rx: any) => {
    setEditingRx(rx);
    setFormPatientId(patientId);
    setFormError(null);
    setFormOpen(true);
  };

  const printA5 = async (rx: any) => {
    const rxId = rx?.prescription_id || rx?.id;
    if (!rxId) {
      toast.error('Cannot print: prescription id missing.');
      return;
    }
    try {
      const html = await clinicalApi.getPrescriptionPrintHtml(rxId);
      const w = window.open('', '_blank');
      if (w) {
        w.document.write(html);
        w.document.close();
        w.focus();
      } else {
        toast.error('Pop-up blocked. Please allow pop-ups to print.');
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to print prescription.');
    }
  };

  // Submit handler for PrescriptionForm — branches on create vs edit.
  const handleFormSubmit = async (rxData: any) => {
    setSaving(true);
    setFormError(null);
    try {
      const isOptometrist = user?.roles?.includes('OPTOMETRIST');
      if (editingRx) {
        // EDIT — PUT only the mutable fields (identity is immutable server-side).
        const rxId = editingRx.prescription_id || editingRx.id;
        await prescriptionApi.updatePrescription(rxId, {
          ...rxData,
          ipd: rxData.ipd || undefined,
          lens_recommendation: rxData.lens_type || undefined,
          next_checkup: rxData.next_checkup || undefined,
        });
        toast.success('Prescription updated');
      } else {
        // NEW — fresh record, never touches an existing one.
        const source = isOptometrist ? 'TESTED_AT_STORE' : 'FROM_DOCTOR';
        await prescriptionApi.createPrescription({
          ...rxData,
          patient_id: formPatientId || effectiveCustomerId,
          customer_id: effectiveCustomerId,
          source,
          optometrist_id: isOptometrist ? user?.id : (user?.id || 'admin-override'),
          // Forward the logged-in user's NAME so the Rx card shows a name, not
          // a raw id (backlog #2). Backend persists + resolves it on read-back.
          optometrist_name: user?.name || undefined,
          validity_months: 12,
          ipd: rxData.ipd || undefined,
          lens_recommendation: rxData.lens_type || undefined,
          next_checkup: rxData.next_checkup || undefined,
          remarks: rxData.doctor_name ? `Dr. ${rxData.doctor_name}` : undefined,
        });
        toast.success('New prescription created');
      }
      setFormOpen(false);
      setEditingRx(null);
      await load();
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || 'Failed to save prescription';
      setFormError(typeof detail === 'string' ? detail : 'Failed to save prescription');
    } finally {
      setSaving(false);
    }
  };

  // Whether we should show the customer-search screen instead of a history.
  // Only in search mode and only until a customer is picked / preset.
  const showSearchScreen = searchable && !effectiveCustomerId;

  // ---- Customer search screen (panel/search mode) -------------------------
  const searchScreen = (
    <div className="flex-1 overflow-y-auto p-4">
      <div className="relative mb-4">
        <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
        <input
          type="text"
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search a customer by name or mobile..."
          className="input-field w-full pl-9"
        />
      </div>
      {query.trim().length > 0 && query.trim().length < 3 && (
        <p className="text-sm text-gray-500 px-1">Type at least 3 characters to search.</p>
      )}
      {isSearching ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-7 h-7 animate-spin text-teal-600" />
        </div>
      ) : searchErr ? (
        <div className="flex flex-col items-center justify-center py-12 text-red-500">
          <AlertTriangle className="w-9 h-9 mb-2 opacity-60" />
          <p>{searchErr}</p>
        </div>
      ) : results.length > 0 ? (
        <div className="border border-gray-200 rounded-lg divide-y divide-gray-100">
          {results.map((c) => (
            <button
              key={c.id}
              onClick={() => setSelected({ id: c.id, name: c.name || c.customer_name || c.full_name })}
              className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-gray-50 transition-colors"
            >
              <div className="w-9 h-9 rounded-full bg-blue-100 flex items-center justify-center flex-shrink-0">
                <User className="w-5 h-5 text-blue-600" />
              </div>
              <div className="min-w-0">
                <p className="font-medium text-gray-900 truncate">{c.name || c.customer_name || c.full_name || 'Customer'}</p>
                <p className="text-xs text-gray-500 flex items-center gap-1">
                  <Phone className="w-3 h-3" /> {c.phone || '—'}
                </p>
              </div>
            </button>
          ))}
        </div>
      ) : query.trim().length >= 3 ? (
        <div className="flex flex-col items-center justify-center py-12 text-gray-500">
          <Users className="w-10 h-10 mb-2 opacity-50" />
          <p>No customers match "{query.trim()}".</p>
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center py-12 text-gray-400">
          <Eye className="w-10 h-10 mb-2 opacity-40" />
          <p>Search a customer to view their family prescription history.</p>
        </div>
      )}
    </div>
  );

  // ---- History body (shared by modal + panel) -----------------------------
  const historyBody = (
    <>
      {/* Body */}
        <div className="flex-1 overflow-y-auto p-4 space-y-5">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 animate-spin text-teal-600" />
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center py-12 text-red-500">
              <AlertTriangle className="w-10 h-10 mb-2 opacity-60" />
              <p>{error}</p>
              <button onClick={load} className="mt-3 btn-outline">Retry</button>
            </div>
          ) : members.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-gray-500">
              <Users className="w-10 h-10 mb-2 opacity-50" />
              <p>No family members on this account.</p>
              <button onClick={() => openNew(defaultPatientId || effectiveCustomerId)} className="mt-3 btn-primary flex items-center gap-2">
                <Plus className="w-4 h-4" /> New prescription
              </button>
            </div>
          ) : (
            members.map((member) => (
              <div key={member.patient_id || 'unlinked'} className="border border-gray-200 rounded-lg overflow-hidden">
                {/* Member header */}
                <div className="flex items-center justify-between px-4 py-3 bg-gray-50 border-b border-gray-200">
                  <div className="flex items-center gap-3">
                    <div className="w-9 h-9 rounded-full bg-blue-100 flex items-center justify-center">
                      <User className="w-5 h-5 text-blue-600" />
                    </div>
                    <div>
                      <p className="font-medium text-gray-900">{member.name || 'Unlinked patient'}</p>
                      <p className="text-xs text-gray-500">
                        {member.relation || 'Patient'} · {member.prescription_count} Rx
                        {member.valid_count > 0 && <span className="text-green-600"> · {member.valid_count} valid</span>}
                      </p>
                    </div>
                  </div>
                  <button
                    onClick={() => openNew(member.patient_id)}
                    className="btn sm primary flex items-center gap-1"
                    title="Start a fresh, blank prescription for this patient"
                  >
                    <Plus className="w-4 h-4" /> New prescription
                  </button>
                </div>

                {/* Rx list (read-only history; Edit/Print are explicit actions) */}
                {member.prescriptions.length === 0 ? (
                  <div className="px-4 py-6 text-center text-sm text-gray-500">
                    No prescriptions yet for this patient.
                  </div>
                ) : (
                  <div className="divide-y divide-gray-100">
                    {member.prescriptions.map((rx) => {
                      const v = rxValidity(rx);
                      const re = rx.right_eye || rx.rightEye || {};
                      const le = rx.left_eye || rx.leftEye || {};
                      return (
                        <div key={rx.prescription_id || rx.id} className="px-4 py-3">
                          <div className="flex items-start justify-between gap-3 mb-2">
                            <div className="flex items-center gap-2 text-sm">
                              <Calendar className="w-4 h-4 text-gray-400" />
                              <span className="font-medium text-gray-900">{fmtDate(rx)}</span>
                              {rx.rx_kind === 'CONTACT_LENS' && (
                                <span className="px-2 py-0.5 text-xs bg-purple-100 text-purple-700 rounded">Contact Lens</span>
                              )}
                            </div>
                            <span
                              className={clsx(
                                'flex items-center gap-1 text-xs font-medium',
                                v.expired ? 'text-red-600' : v.daysLeft !== null && v.daysLeft <= 30 ? 'text-amber-600' : 'text-green-600',
                              )}
                            >
                              {v.expired ? <AlertTriangle className="w-3 h-3" /> : v.daysLeft !== null && v.daysLeft <= 30 ? <Clock className="w-3 h-3" /> : <CheckCircle className="w-3 h-3" />}
                              {v.label}
                            </span>
                          </div>

                          {/* Powers (read-only) */}
                          <div className="grid grid-cols-2 gap-3 text-xs mb-2">
                            <div className="bg-gray-50 rounded p-2">
                              <span className="text-gray-500">OD: </span>
                              <span className="font-medium">{fmtPower(re.sph ?? re.sphere)} / {fmtPower(re.cyl ?? re.cylinder)} / {re.axis ?? '-'}</span>
                            </div>
                            <div className="bg-gray-50 rounded p-2">
                              <span className="text-gray-500">OS: </span>
                              <span className="font-medium">{fmtPower(le.sph ?? le.sphere)} / {fmtPower(le.cyl ?? le.cylinder)} / {le.axis ?? '-'}</span>
                            </div>
                          </div>

                          {/* Optometrist NAME (backlog #2 — never a raw id) */}
                          {optometristName(rx) && (
                            <div className="flex items-center gap-1.5 text-xs text-gray-500 mb-2">
                              <User className="w-3.5 h-3.5" />
                              <span>By {optometristName(rx)}</span>
                            </div>
                          )}

                          {/* Per-Rx actions */}
                          <div className="flex items-center gap-3">
                            <button
                              onClick={() => openEdit(member.patient_id, rx)}
                              className="text-sm text-teal-600 hover:text-teal-700 flex items-center gap-1"
                            >
                              <Pencil className="w-4 h-4" /> Edit
                            </button>
                            <button
                              onClick={() => printA5(rx)}
                              className="text-sm text-gray-500 hover:text-teal-600 flex items-center gap-1"
                            >
                              <Printer className="w-4 h-4" /> Print / View
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
    </>
  );

  // Header — adapts the subtitle + a "Back to search" affordance for search mode.
  const header = (
    <div className="flex items-center justify-between p-4 border-b border-gray-200">
      <div className="flex items-center gap-3">
        {searchable && effectiveCustomerId && !customerId && (
          <button
            onClick={() => { setSelected(null); setMembers([]); }}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
            title="Back to search"
            aria-label="Back to search"
          >
            <ArrowLeft className="w-5 h-5 text-gray-500" />
          </button>
        )}
        <div className="w-11 h-11 bg-teal-100 rounded-full flex items-center justify-center">
          <Eye className="w-6 h-6 text-teal-600" />
        </div>
        <div>
          <h2 className="text-lg font-bold text-gray-900">Prescriptions &amp; History</h2>
          <p className="text-sm text-gray-500">
            {showSearchScreen
              ? 'Search a customer to view their family Rx history'
              : `${effectiveCustomerName || 'Customer'} · grouped by family member`}
          </p>
        </div>
      </div>
      {!isPanel && (
        <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg transition-colors" title="Close" aria-label="Close">
          <X className="w-5 h-5 text-gray-500" />
        </button>
      )}
    </div>
  );

  const innerContent = (
    <>
      {header}
      {showSearchScreen ? searchScreen : historyBody}
      {!isPanel && (
        <div className="flex items-center justify-end gap-3 p-4 border-t border-gray-200 bg-gray-50">
          <button onClick={onClose} className="btn-outline">Close</button>
        </div>
      )}
    </>
  );

  // Form overlay shared by both modes.
  const formOverlay = (
    <>
      {/* Create / Edit form (shared PrescriptionForm). For a NEW Rx, initialData
          is undefined so the form opens BLANK — "new" is never "edit-last". */}
      {formOpen && (
        <div className="fixed inset-0 z-[60]">
          {formError && (
            <div className="fixed top-4 left-1/2 -translate-x-1/2 z-[70] max-w-md w-full px-3">
              <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700 flex items-start gap-2 shadow">
                <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                <div>
                  <p className="font-medium">{editingRx ? 'Failed to update prescription' : 'Failed to create prescription'}</p>
                  <p className="text-xs mt-0.5">{formError}</p>
                </div>
                <button onClick={() => setFormError(null)} className="ml-auto text-red-400 hover:text-red-600" title="Dismiss" aria-label="Dismiss error"><X className="w-4 h-4" /></button>
              </div>
            </div>
          )}
          <PrescriptionForm
            allowContactLens={false}
            initialData={editingRx ? rxToFormInitial(editingRx) : undefined}
            submitLabel={editingRx ? 'Save changes' : 'Save prescription'}
            onSubmit={handleFormSubmit}
            onCancel={() => { if (!saving) { setFormOpen(false); setEditingRx(null); setFormError(null); } }}
          />
        </div>
      )}
    </>
  );

  // Panel mode: render inline (no scrim) so it can live inside a page tab.
  if (isPanel) {
    return (
      <div className="card overflow-hidden flex flex-col max-h-[78vh]">
        {innerContent}
        {formOverlay}
      </div>
    );
  }

  // Modal mode (default): the scrim + dialog overlay opened from a queue row.
  return (
    <div className="scrim modal-overlay">
      <div className="dialog modal w-full max-w-3xl max-h-[92vh] flex flex-col">
        {innerContent}
      </div>
      {formOverlay}
    </div>
  );
}

export default ClinicPrescriptionHistory;
