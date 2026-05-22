// ============================================================================
// IMS 2.0 - Employee Salary Setup (Payroll Phase 1)
// ============================================================================
// Structured-CTC salary master: list / create / edit per-employee salary
// configs, view the state Professional Tax slabs, and bulk-import via CSV.

import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { payrollApi, grossOf, type SalaryConfig, type PtSlab } from '../../services/api/payroll';
import { entitiesApi, type Entity } from '../../services/api/entities';
import { storeApi } from '../../services/api/stores';

interface StoreOption {
  store_id: string;
  store_code?: string;
  store_name?: string;
}

const EMPTY_FORM: SalaryConfig = {
  employee_id: '',
  entity_id: '',
  store_id: '',
  designation: '',
  basic: 0,
  hra: 0,
  conveyance: 0,
  medical: 0,
  special_allowance: 0,
  pf_applicable: true,
  pf_wage_ceiling_cap: true,
  esi_applicable: null,
  pt_applicable: true,
  tds_monthly: 0,
  uan: '',
  esi_ip_number: '',
  pan: '',
  bank_account_no: '',
  bank_ifsc: '',
  bank_name: '',
};

// esi_applicable is tri-state: null = auto (by gross), true = yes, false = no
function esiToSelect(v: boolean | null | undefined): string {
  if (v === true) return 'yes';
  if (v === false) return 'no';
  return 'auto';
}
function selectToEsi(v: string): boolean | null {
  if (v === 'yes') return true;
  if (v === 'no') return false;
  return null;
}

export function SalarySetupPage() {
  const { user } = useAuth();
  const toast = useToast();
  const roles = user?.roles || [];
  const canEdit = roles.includes('ADMIN') || roles.includes('SUPERADMIN');

  const [configs, setConfigs] = useState<SalaryConfig[]>([]);
  const [entities, setEntities] = useState<Entity[]>([]);
  const [stores, setStores] = useState<StoreOption[]>([]);
  const [ptSlabs, setPtSlabs] = useState<PtSlab[]>([]);
  const [loading, setLoading] = useState(true);

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<SalaryConfig>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  const [importOpen, setImportOpen] = useState(false);
  const [csvText, setCsvText] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [cfg, ents, pt, st] = await Promise.all([
        payrollApi.listConfigs(),
        entitiesApi.list().catch(() => ({ entities: [] as Entity[], total: 0 })),
        payrollApi.listPtSlabs().catch(() => ({ pt_slabs: [] as PtSlab[], total: 0, source: '' })),
        storeApi.getStores().catch(() => ({ stores: [] as StoreOption[] })),
      ]);
      setConfigs(cfg.configs || []);
      setEntities(ents.entities || []);
      setPtSlabs(pt.pt_slabs || []);
      setStores((st?.stores || st || []) as StoreOption[]);
    } catch {
      toast.error('Failed to load salary configs');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  const entityName = (id?: string | null) =>
    entities.find((e) => e.entity_id === id)?.name || (id ? id : '—');

  const openCreate = () => {
    setForm({ ...EMPTY_FORM, store_id: user?.activeStoreId || '' });
    setEditing(false);
    setModalOpen(true);
  };

  const openEdit = (c: SalaryConfig) => {
    setForm({ ...EMPTY_FORM, ...c });
    setEditing(true);
    setModalOpen(true);
  };

  const setField = (k: keyof SalaryConfig, v: string | number | boolean | null) =>
    setForm((f) => ({ ...f, [k]: v }));

  const num = (v: string) => (v === '' ? 0 : Number(v));

  const save = async () => {
    if (!form.employee_id.trim()) {
      toast.error('Employee ID is required');
      return;
    }
    if (!form.basic || form.basic <= 0) {
      toast.error('Basic must be greater than 0');
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await payrollApi.updateConfig(form.employee_id, form);
        toast.success('Salary config updated');
      } else {
        await payrollApi.createConfig(form);
        toast.success('Salary config created');
      }
      setModalOpen(false);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const seedPt = async () => {
    try {
      const r = await payrollApi.seedPtSlabs();
      toast.success(`PT slabs ready (${r.seeded} newly seeded)`);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'PT seed failed');
    }
  };

  const runImport = async () => {
    const rows = parseCsv(csvText);
    if (rows.length === 0) {
      toast.error('No valid rows found. Need a header row with at least employee_id and basic.');
      return;
    }
    setSaving(true);
    try {
      const r = await payrollApi.bulkConfigs(rows);
      toast.success(`Imported ${r.total} (${r.created} new, ${r.updated} updated)`);
      setImportOpen(false);
      setCsvText('');
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Import failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Salary Setup</h1>
          <p className="text-sm text-gray-500">
            Structured-CTC salary master. Statutory deductions are computed at payroll run.
          </p>
        </div>
        {canEdit && (
          <div className="flex flex-wrap gap-2">
            <button className="btn-secondary" onClick={seedPt}>Seed PT slabs</button>
            <button className="btn-secondary" onClick={() => setImportOpen(true)}>Bulk import (CSV)</button>
            <button className="btn-primary" onClick={openCreate}>+ Add salary</button>
          </div>
        )}
      </div>

      {/* PT slabs strip */}
      <div className="card p-4">
        <div className="text-sm font-medium text-gray-700 mb-2">Professional Tax slabs</div>
        {ptSlabs.length === 0 ? (
          <p className="text-sm text-gray-500">
            No PT slabs configured. {canEdit && 'Click "Seed PT slabs" to load Jharkhand + Maharashtra defaults.'}
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {ptSlabs.map((s) => (
              <span key={s.state_code} className="badge badge-info">
                {s.state_name || s.state_code} · {s.basis}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Configs table */}
      <div className="card overflow-x-auto">
        {loading ? (
          <div className="p-6 text-center text-gray-500">Loading…</div>
        ) : configs.length === 0 ? (
          <div className="p-6 text-center text-gray-500">
            No salary configs yet. {canEdit && 'Add one or bulk-import via CSV.'}
          </div>
        ) : (
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-gray-600">
              <tr>
                <th className="px-3 py-2 text-left">Employee</th>
                <th className="px-3 py-2 text-left">Designation</th>
                <th className="px-3 py-2 text-left">Entity</th>
                <th className="px-3 py-2 text-right">Basic</th>
                <th className="px-3 py-2 text-right">Gross</th>
                <th className="px-3 py-2 text-center">PF/ESI/PT</th>
                {canEdit && <th className="px-3 py-2"></th>}
              </tr>
            </thead>
            <tbody>
              {configs.map((c) => (
                <tr key={c.employee_id} className="border-t border-gray-100">
                  <td className="px-3 py-2 font-medium text-gray-900">{c.employee_id}</td>
                  <td className="px-3 py-2 text-gray-600">{c.designation || '—'}</td>
                  <td className="px-3 py-2 text-gray-600">{entityName(c.entity_id)}</td>
                  <td className="px-3 py-2 text-right">₹{(c.basic || 0).toLocaleString('en-IN')}</td>
                  <td className="px-3 py-2 text-right font-medium">₹{grossOf(c).toLocaleString('en-IN')}</td>
                  <td className="px-3 py-2 text-center text-xs text-gray-500">
                    {c.pf_applicable ? 'PF' : '—'} / {c.esi_applicable === false ? '—' : 'ESI'} / {c.pt_applicable ? 'PT' : '—'}
                  </td>
                  {canEdit && (
                    <td className="px-3 py-2 text-right">
                      <button className="text-bv-red-600 hover:underline" onClick={() => openEdit(c)}>Edit</button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Create / Edit modal */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="card w-full max-w-2xl max-h-[90vh] overflow-y-auto p-5">
            <h2 className="text-lg font-semibold mb-4">
              {editing ? `Edit salary — ${form.employee_id}` : 'Add salary config'}
            </h2>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Employee ID *">
                <input className="input-field" value={form.employee_id} disabled={editing}
                  onChange={(e) => setField('employee_id', e.target.value)} />
              </Field>
              <Field label="Designation">
                <input className="input-field" value={form.designation || ''}
                  onChange={(e) => setField('designation', e.target.value)} />
              </Field>
              <Field label="Entity">
                <select className="input-field" value={form.entity_id || ''}
                  onChange={(e) => setField('entity_id', e.target.value)}>
                  <option value="">{entities.length ? '— select —' : '— no entities yet —'}</option>
                  {entities.map((en) => <option key={en.entity_id} value={en.entity_id}>{en.name}</option>)}
                </select>
                {entities.length === 0 && (
                  <span className="block text-xs text-gray-400 mt-1">Create entities in Settings &gt; Entities first.</span>
                )}
              </Field>
              <Field label="Store">
                <select className="input-field" value={form.store_id || ''}
                  onChange={(e) => setField('store_id', e.target.value)}>
                  <option value="">{stores.length ? '— select —' : '— no stores found —'}</option>
                  {stores.map((s) => (
                    <option key={s.store_id} value={s.store_id}>
                      {[s.store_code, s.store_name].filter(Boolean).join(' · ') || s.store_id}
                    </option>
                  ))}
                </select>
              </Field>

              <Field label="Basic *">
                <input type="number" className="input-field" value={form.basic || 0}
                  onChange={(e) => setField('basic', num(e.target.value))} />
              </Field>
              <Field label="HRA">
                <input type="number" className="input-field" value={form.hra || 0}
                  onChange={(e) => setField('hra', num(e.target.value))} />
              </Field>
              <Field label="Conveyance">
                <input type="number" className="input-field" value={form.conveyance || 0}
                  onChange={(e) => setField('conveyance', num(e.target.value))} />
              </Field>
              <Field label="Medical">
                <input type="number" className="input-field" value={form.medical || 0}
                  onChange={(e) => setField('medical', num(e.target.value))} />
              </Field>
              <Field label="Special allowance">
                <input type="number" className="input-field" value={form.special_allowance || 0}
                  onChange={(e) => setField('special_allowance', num(e.target.value))} />
              </Field>
              <Field label="Gross (auto)">
                <input className="input-field bg-gray-50" disabled value={`₹${grossOf(form).toLocaleString('en-IN')}`} />
              </Field>

              <Field label="PF applicable">
                <select className="input-field" value={form.pf_applicable ? 'yes' : 'no'}
                  onChange={(e) => setField('pf_applicable', e.target.value === 'yes')}>
                  <option value="yes">Yes</option>
                  <option value="no">No</option>
                </select>
              </Field>
              <Field label="PF on ₹15k ceiling">
                <select className="input-field" value={form.pf_wage_ceiling_cap ? 'yes' : 'no'}
                  onChange={(e) => setField('pf_wage_ceiling_cap', e.target.value === 'yes')}>
                  <option value="yes">Yes (cap at 15,000)</option>
                  <option value="no">No (on actual basic)</option>
                </select>
              </Field>
              <Field label="ESI applicable">
                <select className="input-field" value={esiToSelect(form.esi_applicable)}
                  onChange={(e) => setField('esi_applicable', selectToEsi(e.target.value))}>
                  <option value="auto">Auto (gross ≤ 21k)</option>
                  <option value="yes">Yes</option>
                  <option value="no">No</option>
                </select>
              </Field>
              <Field label="PT applicable">
                <select className="input-field" value={form.pt_applicable ? 'yes' : 'no'}
                  onChange={(e) => setField('pt_applicable', e.target.value === 'yes')}>
                  <option value="yes">Yes</option>
                  <option value="no">No</option>
                </select>
              </Field>
              <Field label="TDS / month (manual)">
                <input type="number" className="input-field" value={form.tds_monthly || 0}
                  onChange={(e) => setField('tds_monthly', num(e.target.value))} />
              </Field>
              <Field label="PAN">
                <input className="input-field" value={form.pan || ''}
                  onChange={(e) => setField('pan', e.target.value)} />
              </Field>
              <Field label="UAN (PF)">
                <input className="input-field" value={form.uan || ''}
                  onChange={(e) => setField('uan', e.target.value)} />
              </Field>
              <Field label="ESI IP number">
                <input className="input-field" value={form.esi_ip_number || ''}
                  onChange={(e) => setField('esi_ip_number', e.target.value)} />
              </Field>
              <Field label="Bank account no.">
                <input className="input-field" value={form.bank_account_no || ''}
                  onChange={(e) => setField('bank_account_no', e.target.value)} />
              </Field>
              <Field label="Bank IFSC">
                <input className="input-field" value={form.bank_ifsc || ''}
                  onChange={(e) => setField('bank_ifsc', e.target.value)} />
              </Field>
            </div>
            <div className="flex justify-end gap-2 mt-5">
              <button className="btn-secondary" onClick={() => setModalOpen(false)} disabled={saving}>Cancel</button>
              <button className="btn-primary" onClick={save} disabled={saving}>
                {saving ? 'Saving…' : editing ? 'Update' : 'Create'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Bulk CSV import modal */}
      {importOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="card w-full max-w-xl p-5">
            <h2 className="text-lg font-semibold mb-2">Bulk import (CSV)</h2>
            <p className="text-sm text-gray-500 mb-3">
              First line is the header. Supported columns: <code>employee_id, basic, hra, conveyance,
              medical, special_allowance, designation, entity_id, store_id, pan, uan, tds_monthly</code>.
              <code>employee_id</code> and <code>basic</code> are required.
            </p>
            <textarea
              className="input-field h-48 font-mono text-xs"
              placeholder={'employee_id,basic,hra,conveyance,special_allowance\nEMP-001,20000,8000,1600,5000'}
              value={csvText}
              onChange={(e) => setCsvText(e.target.value)}
            />
            <div className="flex justify-end gap-2 mt-4">
              <button className="btn-secondary" onClick={() => setImportOpen(false)} disabled={saving}>Cancel</button>
              <button className="btn-primary" onClick={runImport} disabled={saving}>
                {saving ? 'Importing…' : 'Import'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs font-medium text-gray-600 mb-1">{label}</span>
      {children}
    </label>
  );
}

const NUMERIC_COLS = new Set([
  'basic', 'hra', 'conveyance', 'medical', 'special_allowance', 'tds_monthly',
]);

/** Minimal CSV parser (no quoted-comma support) -> SalaryConfig[]. */
function parseCsv(text: string): SalaryConfig[] {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  if (lines.length < 2) return [];
  const headers = lines[0].split(',').map((h) => h.trim());
  const out: SalaryConfig[] = [];
  for (let i = 1; i < lines.length; i++) {
    const cells = lines[i].split(',').map((c) => c.trim());
    const row: Record<string, string | number> = {};
    headers.forEach((h, idx) => {
      const raw = cells[idx] ?? '';
      row[h] = NUMERIC_COLS.has(h) ? Number(raw || 0) : raw;
    });
    if (!row.employee_id || !row.basic || Number(row.basic) <= 0) continue;
    out.push(row as unknown as SalaryConfig);
  }
  return out;
}

export default SalarySetupPage;
