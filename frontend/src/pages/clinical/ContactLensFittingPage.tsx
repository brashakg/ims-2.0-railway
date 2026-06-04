// ============================================================================
// IMS 2.0 - Contact Lens Fitting Page
// ============================================================================
// A contact-lens fitting is a DISTINCT clinical record from a spectacle Rx:
// it is fit by base-curve (BC) + diameter (DIA) rather than PD, and the powers
// are vertex-adjusted to sit on the cornea. The backend already models this
// fully (POST /prescriptions with rx_kind=CONTACT_LENS, per-eye CLEyeData,
// modality/brand, range validation, and a CL-specific print card). This page
// wires that backend to a real workflow: find the customer -> capture the
// fitting -> it lands in the SAME prescriptions collection, filterable by
// rx_kind, and prints the contact-lens card. (Clinic initiative C6-A.)

import { useState, useCallback } from 'react';
import {
  Eye,
  User,
  Search,
  Plus,
  Printer,
  AlertCircle,
  Loader2,
  X,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import { customerApi, prescriptionApi } from '../../services/api';

// Kept in sync with backend prescriptions.CL_MODALITIES.
const MODALITIES = ['DAILY', 'FORTNIGHTLY', 'MONTHLY', 'QUARTERLY', 'YEARLY', 'COLOR'] as const;

type EyeForm = {
  cl_power: string;
  cl_cyl: string;
  cl_axis: string;
  cl_add: string;
  base_curve: string;
  diameter: string;
  acuity: string;
};

const EMPTY_EYE: EyeForm = {
  cl_power: '', cl_cyl: '', cl_axis: '', cl_add: '', base_curve: '', diameter: '', acuity: '',
};

interface CustomerLite {
  id: string;
  name: string;
  phone: string;
  patients?: Array<{ id?: string; patient_id?: string; name?: string; mobile?: string; relation?: string }>;
}

function cid(c: any): string {
  return c?.id || c?.customer_id || c?._id || '';
}

// Build a CLEyeData payload: only filled fields are sent (undefined keys are
// dropped by axios, so the backend sees None and skips range-checks for them).
// Returns null when the eye is entirely blank so a one-eye fitting is allowed.
function eyePayload(e: EyeForm): Record<string, number> | null {
  const out: Record<string, number> = {};
  const num = (v: string) => (v.trim() === '' ? undefined : Number(v));
  const f = num(e.cl_power); if (f !== undefined && Number.isFinite(f)) out.cl_power = f;
  const c = num(e.cl_cyl); if (c !== undefined && Number.isFinite(c)) out.cl_cyl = c;
  const ax = num(e.cl_axis); if (ax !== undefined && Number.isFinite(ax)) out.cl_axis = Math.round(ax);
  const ad = num(e.cl_add); if (ad !== undefined && Number.isFinite(ad)) out.cl_add = ad;
  const bc = num(e.base_curve); if (bc !== undefined && Number.isFinite(bc)) out.base_curve = bc;
  const di = num(e.diameter); if (di !== undefined && Number.isFinite(di)) out.diameter = di;
  if (e.acuity.trim()) (out as any).acuity = e.acuity.trim();
  return Object.keys(out).length ? out : null;
}

export function ContactLensFittingPage() {
  const toast = useToast();
  const { user } = useAuth();

  // --- customer search / selection ---
  const [searchQuery, setSearchQuery] = useState('');
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<CustomerLite[]>([]);
  const [selected, setSelected] = useState<CustomerLite | null>(null);
  const [patientId, setPatientId] = useState<string>('');

  // --- fittings list for the selected customer ---
  const [fittings, setFittings] = useState<any[]>([]);
  const [loadingFittings, setLoadingFittings] = useState(false);

  // --- new-fitting form ---
  const [showForm, setShowForm] = useState(false);
  const [right, setRight] = useState<EyeForm>({ ...EMPTY_EYE });
  const [left, setLeft] = useState<EyeForm>({ ...EMPTY_EYE });
  const [brand, setBrand] = useState('');
  const [series, setSeries] = useState('');
  const [modality, setModality] = useState<string>('MONTHLY');
  const [color, setColor] = useState('');
  const [validityMonths, setValidityMonths] = useState('12');
  const [remarks, setRemarks] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const runSearch = useCallback(async () => {
    const q = searchQuery.trim();
    if (!q) return;
    setSearching(true);
    try {
      const data = await customerApi.getCustomers({ search: q, storeId: user?.activeStoreId, limit: 20 });
      const list: any[] = data?.customers ?? (Array.isArray(data) ? data : []);
      setResults(
        list.map((c) => ({ id: cid(c), name: c.name || c.customer_name || 'Unnamed', phone: c.mobile || c.phone || '', patients: c.patients })),
      );
    } catch {
      toast.error('Customer search failed');
    } finally {
      setSearching(false);
    }
  }, [searchQuery, user?.activeStoreId, toast]);

  const loadFittings = useCallback(async (customerId: string) => {
    setLoadingFittings(true);
    try {
      const data = await prescriptionApi.getPrescriptions(customerId);
      const list: any[] = data?.prescriptions ?? (Array.isArray(data) ? data : []);
      setFittings(list.filter((rx) => (rx.rx_kind || rx.rxKind || 'SPECTACLE') === 'CONTACT_LENS'));
    } catch {
      setFittings([]);
    } finally {
      setLoadingFittings(false);
    }
  }, []);

  const selectCustomer = (c: CustomerLite) => {
    setSelected(c);
    setPatientId('');
    setResults([]);
    setShowForm(false);
    loadFittings(c.id);
  };

  const resetForm = () => {
    setRight({ ...EMPTY_EYE });
    setLeft({ ...EMPTY_EYE });
    setBrand(''); setSeries(''); setModality('MONTHLY'); setColor('');
    setValidityMonths('12'); setRemarks('');
  };

  const submitFitting = async () => {
    if (!selected) return;
    const clRight = eyePayload(right);
    const clLeft = eyePayload(left);
    if (!clRight && !clLeft) {
      toast.warning('Enter at least one eye’s contact-lens parameters');
      return;
    }
    if (!user?.activeStoreId) {
      toast.error('Select a store before recording a fitting');
      return;
    }
    setSubmitting(true);
    try {
      await prescriptionApi.createPrescription({
        rx_kind: 'CONTACT_LENS',
        patient_id: patientId || selected.id,
        customer_id: selected.id,
        source: 'TESTED_AT_STORE',
        optometrist_id: user?.id || undefined,
        validity_months: Math.max(6, Math.min(24, Number(validityMonths) || 12)),
        cl_right: clRight,
        cl_left: clLeft,
        cl_brand: brand.trim() || undefined,
        cl_series: series.trim() || undefined,
        modality,
        color: color.trim() || undefined,
        remarks: remarks.trim() || undefined,
      });
      toast.success('Contact lens fitting saved');
      setShowForm(false);
      resetForm();
      loadFittings(selected.id);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      toast.error(typeof detail === 'string' ? detail : 'Could not save the fitting');
    } finally {
      setSubmitting(false);
    }
  };

  const printFitting = async (prescriptionId: string) => {
    try {
      const html = await prescriptionApi.getPrintHtml(prescriptionId);
      if (!html) { toast.error('Nothing to print'); return; }
      const w = window.open('', '_blank');
      if (w) { w.document.write(html); w.document.close(); }
    } catch {
      toast.error('Print failed');
    }
  };

  const eyeGrid = (label: string, eye: EyeForm, set: (e: EyeForm) => void) => (
    <div className="border border-gray-200 rounded-lg p-3">
      <p className="font-medium text-gray-900 mb-2">{label}</p>
      <div className="grid grid-cols-2 gap-2">
        {([
          ['cl_power', 'Power (D)'],
          ['cl_cyl', 'Cyl (toric)'],
          ['cl_axis', 'Axis (0–180)'],
          ['cl_add', 'Add (multifocal)'],
          ['base_curve', 'BC (mm)'],
          ['diameter', 'DIA (mm)'],
        ] as Array<[keyof EyeForm, string]>).map(([key, lbl]) => (
          <label key={key} className="text-xs text-gray-600">
            {lbl}
            <input
              type="number"
              step={key === 'cl_axis' ? '1' : '0.25'}
              value={eye[key]}
              onChange={(e) => set({ ...eye, [key]: e.target.value })}
              className="input-field mt-0.5"
            />
          </label>
        ))}
        <label className="text-xs text-gray-600 col-span-2">
          Visual acuity (e.g. 6/6)
          <input
            type="text"
            value={eye.acuity}
            onChange={(e) => set({ ...eye, acuity: e.target.value })}
            className="input-field mt-0.5"
          />
        </label>
      </div>
    </div>
  );

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Contact Lens Fitting</h1>
          <p className="text-gray-500">Record contact-lens prescriptions (BC / DIA / power) and print the fitting card</p>
        </div>
        {selected && (
          <button onClick={() => { setShowForm((s) => !s); resetForm(); }} className="btn-primary flex items-center gap-2">
            <Plus className="w-4 h-4" />
            New Fitting
          </button>
        )}
      </div>

      {/* Search */}
      <div className="card">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') runSearch(); }}
              className="input-field pl-10"
              placeholder="Search customer by name or phone…"
            />
          </div>
          <button onClick={runSearch} disabled={searching} className="btn-primary flex items-center gap-2">
            {searching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
            Search
          </button>
        </div>

        {results.length > 0 && (
          <div className="mt-3 divide-y divide-gray-100 border border-gray-100 rounded-lg">
            {results.map((c) => (
              <button
                key={c.id}
                onClick={() => selectCustomer(c)}
                className="w-full text-left px-3 py-2 hover:bg-gray-50 flex items-center justify-between"
              >
                <span className="flex items-center gap-2">
                  <User className="w-4 h-4 text-gray-400" />
                  <span className="font-medium text-gray-900">{c.name}</span>
                  <span className="badge-info">Account</span>
                </span>
                <span className="text-sm text-gray-500">{c.phone}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Selected customer + fittings */}
      {selected && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <div className="w-9 h-9 bg-teal-100 rounded-full flex items-center justify-center">
                <User className="w-5 h-5 text-teal-600" />
              </div>
              <div>
                <p className="font-medium text-gray-900">{selected.name}</p>
                <p className="text-xs text-gray-500">{selected.phone}</p>
              </div>
            </div>
            {selected.patients && selected.patients.length > 0 && (
              <label className="text-xs text-gray-600">
                Family member
                <select
                  value={patientId}
                  onChange={(e) => setPatientId(e.target.value)}
                  className="input-field mt-0.5 min-w-[12rem]"
                >
                  <option value="">{selected.name} (account holder)</option>
                  {selected.patients.map((p, i) => {
                    const pid = p.id || p.patient_id || '';
                    return (
                      <option key={pid || i} value={pid}>
                        {p.name}{p.relation ? ` — ${p.relation}` : ''}
                      </option>
                    );
                  })}
                </select>
              </label>
            )}
          </div>

          {loadingFittings ? (
            <div className="py-8 text-center text-gray-500"><Loader2 className="w-5 h-5 animate-spin inline" /> Loading fittings…</div>
          ) : fittings.length === 0 ? (
            <div className="py-8 text-center text-gray-500">No contact-lens fittings yet for this customer.</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-gray-500 border-b border-gray-100">
                    <th className="py-2 pr-3">Date</th>
                    <th className="py-2 pr-3">Brand / Modality</th>
                    <th className="py-2 pr-3">R (Pwr · BC · DIA)</th>
                    <th className="py-2 pr-3">L (Pwr · BC · DIA)</th>
                    <th className="py-2 pr-3 text-right">Print</th>
                  </tr>
                </thead>
                <tbody>
                  {fittings.map((rx) => {
                    const r = rx.cl_right || {}; const l = rx.cl_left || {};
                    const cell = (e: any) => `${e.cl_power ?? '—'} · ${e.base_curve ?? '—'} · ${e.diameter ?? '—'}`;
                    const date = (rx.test_date || rx.testDate || rx.created_at || '').slice(0, 10);
                    return (
                      <tr key={rx.prescription_id || rx.id} className="border-b border-gray-50">
                        <td className="py-2 pr-3">{date || '—'}</td>
                        <td className="py-2 pr-3">
                          <span className="font-medium text-gray-900">{rx.cl_brand || '—'}</span>
                          {rx.modality ? <span className="badge-info ml-2">{rx.modality}</span> : null}
                        </td>
                        <td className="py-2 pr-3 text-gray-700">{cell(r)}</td>
                        <td className="py-2 pr-3 text-gray-700">{cell(l)}</td>
                        <td className="py-2 pr-3 text-right">
                          <button
                            onClick={() => printFitting(rx.prescription_id || rx.id)}
                            className="text-teal-600 hover:text-teal-800 inline-flex items-center gap-1"
                          >
                            <Printer className="w-4 h-4" /> Print
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* New fitting form */}
      {selected && showForm && (
        <div className="card border-teal-200">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-medium text-gray-900">New Contact Lens Fitting</h3>
            <button onClick={() => setShowForm(false)} className="text-gray-400 hover:text-gray-600" title="Close" aria-label="Close"><X className="w-5 h-5" /></button>
          </div>

          <div className="grid grid-cols-1 tablet:grid-cols-2 gap-3">
            {eyeGrid('Right eye (OD)', right, setRight)}
            {eyeGrid('Left eye (OS)', left, setLeft)}
          </div>

          <div className="grid grid-cols-2 tablet:grid-cols-3 gap-3 mt-3">
            <label className="text-xs text-gray-600">
              Brand
              <input type="text" value={brand} onChange={(e) => setBrand(e.target.value)} className="input-field mt-0.5" placeholder="Acuvue, Bausch+Lomb…" />
            </label>
            <label className="text-xs text-gray-600">
              Series
              <input type="text" value={series} onChange={(e) => setSeries(e.target.value)} className="input-field mt-0.5" placeholder="Oasys, Biotrue…" />
            </label>
            <label className="text-xs text-gray-600">
              Modality
              <select value={modality} onChange={(e) => setModality(e.target.value)} className="input-field mt-0.5">
                {MODALITIES.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
            <label className="text-xs text-gray-600">
              Colour (cosmetic)
              <input type="text" value={color} onChange={(e) => setColor(e.target.value)} className="input-field mt-0.5" placeholder="optional" />
            </label>
            <label className="text-xs text-gray-600">
              Validity (months)
              <input type="number" min={6} max={24} value={validityMonths} onChange={(e) => setValidityMonths(e.target.value)} className="input-field mt-0.5" />
            </label>
            <label className="text-xs text-gray-600 col-span-2 tablet:col-span-1">
              Remarks
              <input type="text" value={remarks} onChange={(e) => setRemarks(e.target.value)} className="input-field mt-0.5" placeholder="trial notes…" />
            </label>
          </div>

          <div className="flex justify-end gap-2 mt-4">
            <button onClick={() => setShowForm(false)} className="btn-secondary">Cancel</button>
            <button onClick={submitFitting} disabled={submitting} className="btn-primary flex items-center gap-2">
              {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
              Save Fitting
            </button>
          </div>
        </div>
      )}

      {/* Prompt when nothing selected */}
      {!selected && (
        <div className="card bg-blue-50 border-blue-200">
          <div className="flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
            <div className="text-blue-900">
              <p className="font-medium mb-1">Search for a customer to begin</p>
              <p className="text-sm text-blue-700">
                A contact-lens fitting captures base curve (BC), diameter (DIA), power, and modality, and is saved
                alongside the customer’s spectacle prescriptions — filterable and printable as a contact-lens card.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Reference cards */}
      <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
        <div className="card">
          <h3 className="font-medium text-gray-900 mb-3">Common Lens Types</h3>
          <div className="space-y-2 text-sm">
            {[['Daily Disposable', '1 day'], ['Bi-weekly', '2 weeks'], ['Monthly', '1 month'], ['Extended Wear', 'Up to 30 days']].map(([k, v]) => (
              <div key={k} className="flex items-center justify-between p-2 bg-gray-50 rounded">
                <span className="text-gray-700">{k}</span>
                <span className="text-gray-500">{v}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <h3 className="font-medium text-gray-900 mb-3">Typical Measurements</h3>
          <div className="space-y-2 text-sm">
            <div className="p-2 bg-gray-50 rounded">
              <div className="flex justify-between mb-1"><span className="text-gray-700">Base Curve (BC)</span><span className="text-gray-500">8.0 – 9.0 mm</span></div>
              <p className="text-xs text-gray-500">Curvature of the lens</p>
            </div>
            <div className="p-2 bg-gray-50 rounded">
              <div className="flex justify-between mb-1"><span className="text-gray-700">Diameter (DIA)</span><span className="text-gray-500">13.8 – 14.5 mm</span></div>
              <p className="text-xs text-gray-500">Overall size of the lens</p>
            </div>
            <div className="p-2 bg-gray-50 rounded">
              <div className="flex justify-between mb-1"><span className="text-gray-700">Power</span><span className="text-gray-500">-30.00 to +30.00</span></div>
              <p className="text-xs text-gray-500">Refractive correction</p>
            </div>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2 text-xs text-gray-400">
        <Eye className="w-3.5 h-3.5" /> Fittings are stored with the customer’s prescriptions and never overwrite a spectacle Rx.
      </div>
    </div>
  );
}

export default ContactLensFittingPage;
