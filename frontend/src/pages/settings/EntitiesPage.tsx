// ============================================================================
// IMS 2.0 - Legal Entities (Payroll Phase 1)
// ============================================================================
// Manage legal entities (PAN), their GSTINs and statutory registrations, and
// which stores belong to each entity. ADMIN / SUPERADMIN only.

import { useState, useEffect, useCallback } from 'react';
import { useToast } from '../../context/ToastContext';
import { validateGstin, validateIfsc, firstError } from '../../utils/validators';
import { entitiesApi, type Entity, type GstinEntry } from '../../services/api/entities';
import { storeApi } from '../../services/api';

interface StoreLite {
  store_id: string;
  store_name?: string;
  entity_id?: string | null;
}

const EMPTY: Entity = {
  entity_id: '',
  name: '',
  legal_name: '',
  pan: '',
  tan: '',
  registered_address: '',
  gstins: [],
  pf: { registered: false, establishment_code: '' },
  esi: { registered: false, code: '' },
  bank_account_no: '',
  bank_ifsc: '',
  bank_name: '',
};

export function EntitiesPage() {
  const toast = useToast();
  const [entities, setEntities] = useState<Entity[]>([]);
  const [allStores, setAllStores] = useState<StoreLite[]>([]);
  const [loading, setLoading] = useState(true);

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Entity>(EMPTY);
  const [saving, setSaving] = useState(false);

  const [storesFor, setStoresFor] = useState<Entity | null>(null);
  const [assignTarget, setAssignTarget] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const ents = await entitiesApi.list(true);
      setEntities(ents.entities || []);
      try {
        const raw: unknown = await storeApi.getStores();
        const arr = Array.isArray(raw)
          ? raw
          : ((raw as { stores?: unknown[]; data?: unknown[] }).stores ??
             (raw as { data?: unknown[] }).data ?? []);
        setAllStores((arr as StoreLite[]) || []);
      } catch {
        setAllStores([]);
      }
    } catch {
      toast.error('Failed to load entities');
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  const openCreate = () => {
    setForm({ ...EMPTY, gstins: [], pf: { registered: false, establishment_code: '' }, esi: { registered: false, code: '' } });
    setEditing(false);
    setModalOpen(true);
  };

  const openEdit = (e: Entity) => {
    setForm({
      ...EMPTY,
      ...e,
      gstins: e.gstins || [],
      pf: e.pf || { registered: false, establishment_code: '' },
      esi: e.esi || { registered: false, code: '' },
    });
    setEditing(true);
    setModalOpen(true);
  };

  const setF = (k: keyof Entity, v: unknown) => setForm((f) => ({ ...f, [k]: v }));

  const setGstin = (idx: number, key: keyof GstinEntry, v: string) =>
    setForm((f) => {
      const g = [...(f.gstins || [])];
      g[idx] = { ...g[idx], [key]: v };
      return { ...f, gstins: g };
    });
  const addGstin = () =>
    setForm((f) => ({ ...f, gstins: [...(f.gstins || []), { gstin: '', state_code: '', state_name: '' }] }));
  const removeGstin = (idx: number) =>
    setForm((f) => ({ ...f, gstins: (f.gstins || []).filter((_, i) => i !== idx) }));

  const save = async () => {
    if (!form.name.trim()) {
      toast.error('Entity name is required');
      return;
    }
    const fieldErr = firstError(
      ...(form.gstins || []).map((g) => validateGstin(g.gstin)),
      validateIfsc(form.bank_ifsc),
    );
    if (fieldErr) { toast.error(fieldErr); return; }
    setSaving(true);
    try {
      if (editing) {
        await entitiesApi.update(form.entity_id, form);
        toast.success('Entity updated');
      } else {
        await entitiesApi.create(form);
        toast.success('Entity created');
      }
      setModalOpen(false);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const storesInEntity = (eid: string) => allStores.filter((s) => s.entity_id === eid);

  const assignStore = async () => {
    if (!storesFor || !assignTarget) return;
    try {
      await entitiesApi.assignStore(storesFor.entity_id, assignTarget);
      toast.success('Store assigned');
      setAssignTarget('');
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Assign failed');
    }
  };

  const unassignStore = async (storeId: string) => {
    if (!storesFor) return;
    try {
      await entitiesApi.unassignStore(storesFor.entity_id, storeId);
      toast.success('Store unassigned');
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Unassign failed');
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">Legal Entities</h1>
          <p className="text-sm text-gray-500">
            One entity (PAN) groups stores and can hold multiple GSTINs. Payroll & GST filings group by entity.
          </p>
        </div>
        <button className="btn-primary" onClick={openCreate}>+ Add entity</button>
      </div>

      <div className="card overflow-x-auto">
        {loading ? (
          <div className="p-6 text-center text-gray-500">Loading…</div>
        ) : entities.length === 0 ? (
          <div className="p-6 text-center text-gray-500">No entities yet. Add your first legal entity.</div>
        ) : (
          <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-gray-600">
              <tr>
                <th className="px-3 py-2 text-left">Name</th>
                <th className="px-3 py-2 text-left">PAN</th>
                <th className="px-3 py-2 text-center">GSTINs</th>
                <th className="px-3 py-2 text-center">PF / ESI</th>
                <th className="px-3 py-2 text-center">Stores</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {entities.map((e) => (
                <tr key={e.entity_id} className="border-t border-gray-100">
                  <td className="px-3 py-2">
                    <div className="font-medium text-gray-900">{e.name}</div>
                    <div className="text-xs text-gray-400">{e.legal_name}</div>
                  </td>
                  <td className="px-3 py-2 text-gray-600">{e.pan || '—'}</td>
                  <td className="px-3 py-2 text-center">{e.gstins?.length || 0}</td>
                  <td className="px-3 py-2 text-center text-xs">
                    {e.pf?.registered ? 'PF' : '—'} / {e.esi?.registered ? 'ESI' : '—'}
                  </td>
                  <td className="px-3 py-2 text-center">{storesInEntity(e.entity_id).length}</td>
                  <td className="px-3 py-2 text-right space-x-3">
                    <button className="text-bv-red-600 hover:underline" onClick={() => openEdit(e)}>Edit</button>
                    <button className="text-gray-600 hover:underline" onClick={() => { setStoresFor(e); setAssignTarget(''); }}>Stores</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>

      {/* Create / Edit modal */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="card w-full max-w-2xl max-h-[90vh] overflow-y-auto p-5">
            <h2 className="text-lg font-semibold mb-4">{editing ? `Edit — ${form.name}` : 'Add legal entity'}</h2>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Display name *"><input className="input-field" value={form.name} onChange={(e) => setF('name', e.target.value)} /></Field>
              <Field label="Legal name"><input className="input-field" value={form.legal_name || ''} onChange={(e) => setF('legal_name', e.target.value)} /></Field>
              <Field label="PAN"><input className="input-field" value={form.pan || ''} onChange={(e) => setF('pan', e.target.value)} /></Field>
              <Field label="TAN (for TDS)"><input className="input-field" value={form.tan || ''} onChange={(e) => setF('tan', e.target.value)} /></Field>
              <div className="col-span-2">
                <Field label="Registered address"><input className="input-field" value={form.registered_address || ''} onChange={(e) => setF('registered_address', e.target.value)} /></Field>
              </div>
            </div>

            {/* GSTINs */}
            <div className="mt-4">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-medium text-gray-600">GSTINs (one per state)</span>
                <button className="text-xs text-bv-red-600 hover:underline" onClick={addGstin}>+ Add GSTIN</button>
              </div>
              {(form.gstins || []).length === 0 && <p className="text-xs text-gray-400">No GSTINs added.</p>}
              {(form.gstins || []).map((g, i) => (
                <div key={i} className="flex gap-2 mb-2">
                  <input className="input-field flex-1" placeholder="GSTIN" value={g.gstin} onChange={(e) => setGstin(i, 'gstin', e.target.value)} />
                  <input className="input-field w-20" placeholder="State" value={g.state_code} onChange={(e) => setGstin(i, 'state_code', e.target.value)} />
                  <input className="input-field flex-1" placeholder="State name" value={g.state_name || ''} onChange={(e) => setGstin(i, 'state_name', e.target.value)} />
                  <button className="text-gray-400 hover:text-bv-red-600 px-2" onClick={() => removeGstin(i)}>✕</button>
                </div>
              ))}
            </div>

            {/* Statutory + bank */}
            <div className="grid grid-cols-2 gap-3 mt-4">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={!!form.pf?.registered} onChange={(e) => setF('pf', { ...form.pf, registered: e.target.checked })} />
                PF (EPF) registered
              </label>
              <Field label="PF establishment code"><input className="input-field" value={form.pf?.establishment_code || ''} onChange={(e) => setF('pf', { ...form.pf, registered: !!form.pf?.registered, establishment_code: e.target.value })} /></Field>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={!!form.esi?.registered} onChange={(e) => setF('esi', { ...form.esi, registered: e.target.checked })} />
                ESI registered
              </label>
              <Field label="ESI code"><input className="input-field" value={form.esi?.code || ''} onChange={(e) => setF('esi', { ...form.esi, registered: !!form.esi?.registered, code: e.target.value })} /></Field>
              <Field label="Bank account no."><input className="input-field" value={form.bank_account_no || ''} onChange={(e) => setF('bank_account_no', e.target.value)} /></Field>
              <Field label="Bank IFSC"><input className="input-field" value={form.bank_ifsc || ''} onChange={(e) => setF('bank_ifsc', e.target.value)} /></Field>
            </div>

            <div className="flex justify-end gap-2 mt-5">
              <button className="btn-secondary" onClick={() => setModalOpen(false)} disabled={saving}>Cancel</button>
              <button className="btn-primary" onClick={save} disabled={saving}>{saving ? 'Saving…' : editing ? 'Update' : 'Create'}</button>
            </div>
          </div>
        </div>
      )}

      {/* Manage stores modal */}
      {storesFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="card w-full max-w-lg p-5">
            <h2 className="text-lg font-semibold mb-1">Stores — {storesFor.name}</h2>
            <p className="text-xs text-gray-500 mb-4">Assign stores to this legal entity.</p>

            <div className="space-y-2 mb-4">
              {storesInEntity(storesFor.entity_id).length === 0 ? (
                <p className="text-sm text-gray-400">No stores assigned yet.</p>
              ) : (
                storesInEntity(storesFor.entity_id).map((s) => (
                  <div key={s.store_id} className="flex items-center justify-between border border-gray-100 rounded px-3 py-2">
                    <span className="text-sm">{s.store_name || s.store_id}</span>
                    <button className="text-xs text-gray-500 hover:text-bv-red-600" onClick={() => unassignStore(s.store_id)}>Unassign</button>
                  </div>
                ))
              )}
            </div>

            <div className="flex gap-2">
              <select className="input-field flex-1" value={assignTarget} onChange={(e) => setAssignTarget(e.target.value)}>
                <option value="">— select a store —</option>
                {allStores
                  .filter((s) => s.entity_id !== storesFor.entity_id)
                  .map((s) => <option key={s.store_id} value={s.store_id}>{s.store_name || s.store_id}</option>)}
              </select>
              <button className="btn-primary" onClick={assignStore} disabled={!assignTarget}>Assign</button>
            </div>

            <div className="flex justify-end mt-5">
              <button className="btn-secondary" onClick={() => setStoresFor(null)}>Done</button>
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

export default EntitiesPage;
