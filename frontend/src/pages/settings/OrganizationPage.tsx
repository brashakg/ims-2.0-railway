// ============================================================================
// IMS 2.0 - Organization Setup (merged Entities + Stores)
// ============================================================================
// One hierarchical screen: legal entity (PAN) -> its GSTINs -> its stores.
// ADMIN / SUPERADMIN only. Replaces the separate Entities + Store-setup tabs.
// A store's GSTIN is derived server-side from its entity by state, so it is
// shown read-only here.

import { useCallback, useEffect, useMemo, useState, type ReactNode, type Dispatch, type SetStateAction } from 'react';
import {
  Building2, Plus, Pencil, ChevronDown, ChevronRight, MapPin, X, Loader2,
  Store as StoreIcon, Landmark, FileText,
} from 'lucide-react';
import { entitiesApi, type Entity, type BankAccount, type OrgMeta } from '../../services/api/entities';
import { orgStoreApi, type Store, type StorePayload } from '../../services/api/stores';
import { useToast } from '../../context/ToastContext';

const BRANDS = ['BETTER_VISION', 'WIZOPT'];
const STORE_TYPES = ['RETAIL', 'HQ', 'WAREHOUSE'];
const STORE_CATEGORIES = [
  'FRAME', 'OPTICAL_LENS', 'CONTACT_LENS', 'COLORED_CONTACT_LENS', 'READING_GLASSES',
  'SUNGLASS', 'WATCH', 'SMARTWATCH', 'WALL_CLOCK', 'HEARING_AID', 'ACCESSORIES', 'SERVICES',
];

function errMsg(e: unknown, fallback: string): string {
  if (e && typeof e === 'object' && 'response' in e) {
    const r = (e as { response?: { data?: { detail?: string } } }).response;
    if (r?.data?.detail) return r.data.detail;
  }
  return e instanceof Error ? e.message : fallback;
}

const inputCls =
  'w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200';
const labelCls = 'block text-xs font-medium text-gray-600 mb-1';

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <label className={labelCls}>{label}</label>
      {children}
    </div>
  );
}

export default function OrganizationPage() {
  const toast = useToast();
  const [entities, setEntities] = useState<Entity[]>([]);
  const [storesByEntity, setStoresByEntity] = useState<Record<string, Store[]>>({});
  const [meta, setMeta] = useState<OrgMeta>({ state_codes: [], entity_types: [] });
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const [entityModal, setEntityModal] = useState<Entity | 'new' | null>(null);
  const [storeModal, setStoreModal] = useState<{ entity: Entity; store: Store | 'new' } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [eRes, sRes, mRes] = await Promise.all([
        entitiesApi.list(true),
        orgStoreApi.list(),
        entitiesApi.meta().catch(() => ({ state_codes: [], entity_types: [] } as OrgMeta)),
      ]);
      setEntities(eRes.entities || []);
      setMeta(mRes);
      const grouped: Record<string, Store[]> = {};
      for (const s of sRes.stores || []) {
        const eid = (s.entity_id as string) || '_unassigned';
        (grouped[eid] = grouped[eid] || []).push(s);
      }
      setStoresByEntity(grouped);
    } catch (e) {
      toast.error(errMsg(e, 'Failed to load organization'));
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const stateName = useCallback(
    (code?: string) => meta.state_codes.find((s) => s.code === code)?.name || code || '',
    [meta],
  );

  if (loading) {
    return (
      <div className="p-8 flex items-center gap-2 text-gray-500">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading organization...
      </div>
    );
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          <Building2 className="w-5 h-5" /> Organization
        </h1>
        <button
          type="button"
          onClick={() => setEntityModal('new')}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-white bg-bv hover:bg-bv-600 rounded-lg px-3 py-1.5"
        >
          <Plus className="w-4 h-4" /> Add entity
        </button>
      </div>
      <p className="text-sm text-gray-500 mb-5">
        Legal entities (PAN) &rarr; their GSTINs (per state) &rarr; the stores under each.
      </p>

      {entities.length === 0 ? (
        <div className="text-sm text-gray-500 border border-dashed border-gray-300 rounded-lg p-8 text-center">
          No legal entities yet. Add one to begin.
        </div>
      ) : (
        <div className="space-y-3">
          {entities.map((ent) => {
            const stores = storesByEntity[ent.entity_id] || [];
            const open = expanded[ent.entity_id];
            return (
              <div key={ent.entity_id} className="border border-gray-200 rounded-lg bg-white">
                {/* Entity header */}
                <div className="flex items-center justify-between p-3">
                  <button
                    type="button"
                    onClick={() => setExpanded((p) => ({ ...p, [ent.entity_id]: !p[ent.entity_id] }))}
                    className="flex items-center gap-2 text-left flex-1 min-w-0"
                  >
                    {open ? <ChevronDown className="w-4 h-4 text-gray-400" /> : <ChevronRight className="w-4 h-4 text-gray-400" />}
                    <Landmark className="w-4 h-4 text-bv shrink-0" />
                    <span className="font-medium text-gray-900 truncate">{ent.name}</span>
                    {ent.entity_type && (
                      <span className="text-xs text-gray-500 bg-gray-100 rounded px-1.5 py-0.5">{ent.entity_type}</span>
                    )}
                    {ent.pan && <span className="text-xs text-gray-400">PAN {ent.pan}</span>}
                    <span className="text-xs text-gray-400">
                      {(ent.gstins?.length || 0)} GSTIN &middot; {stores.length} store{stores.length === 1 ? '' : 's'}
                    </span>
                    {ent.is_active === false && (
                      <span className="text-xs text-red-600 bg-red-50 rounded px-1.5 py-0.5">inactive</span>
                    )}
                  </button>
                  <div className="flex items-center gap-2 shrink-0">
                    <button type="button" onClick={() => setStoreModal({ entity: ent, store: 'new' })}
                      className="inline-flex items-center gap-1 text-xs text-bv hover:bg-bv-50 rounded px-2 py-1">
                      <Plus className="w-3 h-3" /> Store
                    </button>
                    <button type="button" onClick={() => setEntityModal(ent)}
                      className="inline-flex items-center gap-1 text-xs text-gray-600 hover:bg-gray-100 rounded px-2 py-1">
                      <Pencil className="w-3 h-3" /> Edit
                    </button>
                  </div>
                </div>

                {open && (
                  <div className="border-t border-gray-100 p-3 space-y-3">
                    {/* GSTINs */}
                    {(ent.gstins?.length || 0) > 0 && (
                      <div className="flex flex-wrap gap-2">
                        {ent.gstins!.map((g) => (
                          <span key={g.gstin} className="text-xs border border-gray-200 rounded px-2 py-1 text-gray-700">
                            <span className="font-mono">{g.gstin}</span>
                            <span className="text-gray-400"> &middot; {g.state_name || stateName(g.state_code)}</span>
                            {g.is_primary && <span className="text-bv"> &middot; primary</span>}
                          </span>
                        ))}
                      </div>
                    )}
                    {/* Stores */}
                    {stores.length === 0 ? (
                      <p className="text-xs text-gray-400">No stores under this entity yet.</p>
                    ) : (
                      <ul className="divide-y divide-gray-100 border border-gray-100 rounded-lg">
                        {stores.map((s) => (
                          <li key={s.store_id} className="flex items-center justify-between p-2.5 text-sm">
                            <div className="flex items-center gap-2 min-w-0">
                              <StoreIcon className="w-4 h-4 text-gray-400 shrink-0" />
                              <span className="font-medium text-gray-800">{s.store_name}</span>
                              <span className="text-xs text-gray-400">{s.store_code}</span>
                              {s.city && <span className="text-xs text-gray-400 flex items-center gap-0.5"><MapPin className="w-3 h-3" />{s.city}</span>}
                              {s.gstin && <span className="text-xs font-mono text-gray-500">{s.gstin}</span>}
                              {s.store_type && s.store_type !== 'RETAIL' && (
                                <span className="text-xs bg-gray-100 rounded px-1.5 py-0.5">{s.store_type}</span>
                              )}
                              {s.is_active === false && <span className="text-xs text-red-600">inactive</span>}
                            </div>
                            <button type="button" onClick={() => setStoreModal({ entity: ent, store: s })}
                              className="inline-flex items-center gap-1 text-xs text-gray-600 hover:bg-gray-100 rounded px-2 py-1 shrink-0">
                              <Pencil className="w-3 h-3" /> Edit
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {entityModal && (
        <EntityModal
          entity={entityModal === 'new' ? null : entityModal}
          meta={meta}
          onClose={() => setEntityModal(null)}
          onSaved={() => { setEntityModal(null); load(); }}
        />
      )}
      {storeModal && (
        <StoreModal
          entity={storeModal.entity}
          store={storeModal.store === 'new' ? null : storeModal.store}
          meta={meta}
          onClose={() => setStoreModal(null)}
          onSaved={() => { setStoreModal(null); load(); }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Entity modal
// ---------------------------------------------------------------------------
function EntityModal({
  entity, meta, onClose, onSaved,
}: { entity: Entity | null; meta: OrgMeta; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState<Partial<Entity>>(() => entity ?? {
    name: '', gstins: [], bank_accounts: [], invoice: {},
  });
  const set = (k: keyof Entity, v: unknown) => setForm((p) => ({ ...p, [k]: v }));
  const gstins = form.gstins || [];
  const banks = form.bank_accounts || [];

  const save = async () => {
    if (!form.name || form.name.trim().length < 2) { toast.error('Entity name is required'); return; }
    setSaving(true);
    try {
      if (entity) await entitiesApi.update(entity.entity_id, form);
      else await entitiesApi.create(form);
      toast.success(entity ? 'Entity updated' : 'Entity created');
      onSaved();
    } catch (e) {
      toast.error(errMsg(e, 'Failed to save entity'));
    } finally { setSaving(false); }
  };

  return (
    <Modal title={entity ? `Edit ${entity.name}` : 'New legal entity'} onClose={onClose}>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Display name *"><input className={inputCls} value={form.name || ''} onChange={(e) => set('name', e.target.value)} /></Field>
        <Field label="Legal name"><input className={inputCls} value={form.legal_name || ''} onChange={(e) => set('legal_name', e.target.value)} /></Field>
        <Field label="Entity type">
          <select className={inputCls} value={form.entity_type || ''} onChange={(e) => set('entity_type', e.target.value || undefined)}>
            <option value="">--</option>
            {meta.entity_types.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </Field>
        <Field label="PAN"><input className={inputCls} value={form.pan || ''} onChange={(e) => set('pan', e.target.value.toUpperCase())} placeholder="AAAAA9999A" /></Field>
        <Field label="TAN"><input className={inputCls} value={form.tan || ''} onChange={(e) => set('tan', e.target.value.toUpperCase())} placeholder="AAAA99999A" /></Field>
        <Field label="CIN / LLPIN"><input className={inputCls} value={form.cin || form.llpin || ''} onChange={(e) => set(form.entity_type === 'LLP' ? 'llpin' : 'cin', e.target.value.toUpperCase())} /></Field>
        <Field label="Udyam / MSME"><input className={inputCls} value={form.udyam || ''} onChange={(e) => set('udyam', e.target.value)} /></Field>
        <Field label="Registered phone"><input className={inputCls} value={form.registered_phone || ''} onChange={(e) => set('registered_phone', e.target.value)} /></Field>
        <div className="col-span-2"><Field label="Registered address"><input className={inputCls} value={form.registered_address || ''} onChange={(e) => set('registered_address', e.target.value)} /></Field></div>
      </div>

      {/* GSTINs */}
      <SectionHeader icon={<FileText className="w-3.5 h-3.5" />} title="GSTINs (one per state)"
        onAdd={() => set('gstins', [...gstins, { gstin: '', state_code: '', is_primary: gstins.length === 0 }])} />
      {gstins.map((g, i) => (
        <div key={i} className="grid grid-cols-12 gap-2 mb-2 items-center">
          <input className={`${inputCls} col-span-5 font-mono`} placeholder="15-digit GSTIN" value={g.gstin}
            onChange={(e) => updateArr(setForm, 'gstins', i, { gstin: e.target.value.toUpperCase() })} />
          <select className={`${inputCls} col-span-4`} value={g.state_code}
            onChange={(e) => updateArr(setForm, 'gstins', i, { state_code: e.target.value })}>
            <option value="">state...</option>
            {meta.state_codes.map((s) => <option key={s.code} value={s.code}>{s.code} {s.name}</option>)}
          </select>
          <label className="col-span-2 text-xs flex items-center gap-1">
            <input type="radio" checked={!!g.is_primary} onChange={() => set('gstins', gstins.map((x, j) => ({ ...x, is_primary: j === i })))} /> primary
          </label>
          <button type="button" className="col-span-1 text-gray-400 hover:text-red-600" onClick={() => set('gstins', gstins.filter((_, j) => j !== i))}><X className="w-4 h-4" /></button>
        </div>
      ))}

      {/* Bank accounts */}
      <SectionHeader icon={<Landmark className="w-3.5 h-3.5" />} title="Bank accounts"
        onAdd={() => set('bank_accounts', [...banks, { account_no: '', ifsc: '' } as BankAccount])} />
      {banks.map((b, i) => (
        <div key={i} className="grid grid-cols-12 gap-2 mb-2 items-center">
          <input className={`${inputCls} col-span-3`} placeholder="Account no" value={b.account_no} onChange={(e) => updateArr(setForm, 'bank_accounts', i, { account_no: e.target.value })} />
          <input className={`${inputCls} col-span-3`} placeholder="IFSC" value={b.ifsc} onChange={(e) => updateArr(setForm, 'bank_accounts', i, { ifsc: e.target.value.toUpperCase() })} />
          <input className={`${inputCls} col-span-3`} placeholder="Bank name" value={b.bank_name || ''} onChange={(e) => updateArr(setForm, 'bank_accounts', i, { bank_name: e.target.value })} />
          <select className={`${inputCls} col-span-2`} value={b.gstin || ''} onChange={(e) => updateArr(setForm, 'bank_accounts', i, { gstin: e.target.value || undefined })}>
            <option value="">GSTIN</option>
            {gstins.filter((g) => g.gstin).map((g) => <option key={g.gstin} value={g.gstin}>{g.state_code}</option>)}
          </select>
          <button type="button" className="col-span-1 text-gray-400 hover:text-red-600" onClick={() => set('bank_accounts', banks.filter((_, j) => j !== i))}><X className="w-4 h-4" /></button>
        </div>
      ))}

      {/* Invoice identity */}
      <SectionHeader icon={<FileText className="w-3.5 h-3.5" />} title="Invoice identity (entity default)" />
      <div className="grid grid-cols-2 gap-3">
        <Field label="Signatory name"><input className={inputCls} value={form.invoice?.signatory_name || ''} onChange={(e) => set('invoice', { ...form.invoice, signatory_name: e.target.value })} /></Field>
        <Field label="Signatory designation"><input className={inputCls} value={form.invoice?.signatory_designation || ''} onChange={(e) => set('invoice', { ...form.invoice, signatory_designation: e.target.value })} /></Field>
        <div className="col-span-2"><Field label="Invoice footer"><input className={inputCls} value={form.invoice?.footer_text || ''} onChange={(e) => set('invoice', { ...form.invoice, footer_text: e.target.value })} /></Field></div>
        <div className="col-span-2"><Field label="Terms & conditions"><textarea className={inputCls} rows={2} value={form.invoice?.terms || ''} onChange={(e) => set('invoice', { ...form.invoice, terms: e.target.value })} /></Field></div>
      </div>

      {entity && (
        <label className="flex items-center gap-2 text-sm mt-3">
          <input type="checkbox" checked={form.is_active !== false} onChange={(e) => set('is_active', e.target.checked)} /> Active
        </label>
      )}

      <ModalFooter saving={saving} onClose={onClose} onSave={save} />
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Store modal
// ---------------------------------------------------------------------------
function StoreModal({
  entity, store, meta, onClose, onSaved,
}: { entity: Entity; store: Store | null; meta: OrgMeta; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState<StorePayload>(() => store ?? {
    entity_id: entity.entity_id, brand: BRANDS[0], store_type: 'RETAIL',
    geofence_radius_m: 500, enabled_categories: [],
  });
  const set = (k: keyof StorePayload, v: unknown) => setForm((p) => ({ ...p, [k]: v }));
  const cats = form.enabled_categories || [];

  // The entity's GSTIN for the chosen state (what the store will bill under).
  const derivedGstin = useMemo(() => {
    const g = (entity.gstins || []).find((x) => x.state_code === form.state_code);
    return g?.gstin || (entity.gstins || []).find((x) => x.is_primary)?.gstin || '';
  }, [entity, form.state_code]);

  const save = async () => {
    if (!form.store_code || !form.store_name) { toast.error('Store code and name are required'); return; }
    if (!form.entity_id) { toast.error('Entity is required'); return; }
    setSaving(true);
    try {
      if (store) await orgStoreApi.update(store.store_id, form);
      else await orgStoreApi.create(form);
      toast.success(store ? 'Store updated' : 'Store created');
      onSaved();
    } catch (e) {
      toast.error(errMsg(e, 'Failed to save store'));
    } finally { setSaving(false); }
  };

  return (
    <Modal title={store ? `Edit ${store.store_name}` : `New store under ${entity.name}`} onClose={onClose}>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Store code *"><input className={inputCls} value={form.store_code || ''} onChange={(e) => set('store_code', e.target.value.toUpperCase())} /></Field>
        <Field label="Store name *"><input className={inputCls} value={form.store_name || ''} onChange={(e) => set('store_name', e.target.value)} /></Field>
        <Field label="Brand">
          <select className={inputCls} value={form.brand || ''} onChange={(e) => set('brand', e.target.value)}>
            {BRANDS.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
        </Field>
        <Field label="Store type">
          <select className={inputCls} value={form.store_type || 'RETAIL'} onChange={(e) => set('store_type', e.target.value)}>
            {STORE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </Field>
        <Field label="State">
          <select className={inputCls} value={form.state_code || ''} onChange={(e) => { const sc = e.target.value; set('state_code', sc); set('state', meta.state_codes.find((s) => s.code === sc)?.name); }}>
            <option value="">state...</option>
            {meta.state_codes.map((s) => <option key={s.code} value={s.code}>{s.code} {s.name}</option>)}
          </select>
        </Field>
        <Field label="GSTIN (derived from entity)"><input className={`${inputCls} bg-gray-50 font-mono`} value={derivedGstin} readOnly placeholder="set state to derive" /></Field>
        <Field label="City"><input className={inputCls} value={form.city || ''} onChange={(e) => set('city', e.target.value)} /></Field>
        <Field label="PIN code"><input className={inputCls} value={form.pincode || ''} onChange={(e) => set('pincode', e.target.value)} /></Field>
        <Field label="Phone"><input className={inputCls} value={form.phone || ''} onChange={(e) => set('phone', e.target.value)} /></Field>
        <Field label="WhatsApp"><input className={inputCls} value={form.whatsapp || ''} onChange={(e) => set('whatsapp', e.target.value)} /></Field>
        <div className="col-span-2"><Field label="Address"><input className={inputCls} value={form.address || ''} onChange={(e) => set('address', e.target.value)} /></Field></div>
        <Field label="Locality"><input className={inputCls} value={form.locality || ''} onChange={(e) => set('locality', e.target.value)} /></Field>
        <Field label="Landmark"><input className={inputCls} value={form.landmark || ''} onChange={(e) => set('landmark', e.target.value)} /></Field>
      </div>

      <SectionHeader icon={<MapPin className="w-3.5 h-3.5" />} title="Geo-fence (staff log in within radius)" />
      <div className="grid grid-cols-3 gap-3">
        <Field label="Latitude"><input className={inputCls} type="number" step="any" value={form.latitude ?? ''} onChange={(e) => set('latitude', e.target.value === '' ? null : parseFloat(e.target.value))} /></Field>
        <Field label="Longitude"><input className={inputCls} type="number" step="any" value={form.longitude ?? ''} onChange={(e) => set('longitude', e.target.value === '' ? null : parseFloat(e.target.value))} /></Field>
        <Field label="Radius (m)"><input className={inputCls} type="number" value={form.geofence_radius_m ?? 500} onChange={(e) => set('geofence_radius_m', e.target.value === '' ? null : parseInt(e.target.value, 10))} /></Field>
      </div>

      <SectionHeader icon={<StoreIcon className="w-3.5 h-3.5" />} title="Operations" />
      <div className="grid grid-cols-3 gap-3">
        <Field label="Working hours"><input className={inputCls} placeholder="10:00-20:00" value={form.working_hours || ''} onChange={(e) => set('working_hours', e.target.value)} /></Field>
        <Field label="Weekly off"><input className={inputCls} placeholder="TUESDAY" value={form.weekly_off || ''} onChange={(e) => set('weekly_off', e.target.value.toUpperCase())} /></Field>
        <Field label="Opening date"><input className={inputCls} type="date" value={form.opening_date || ''} onChange={(e) => set('opening_date', e.target.value)} /></Field>
        <Field label="Region / zone"><input className={inputCls} value={form.region || ''} onChange={(e) => set('region', e.target.value)} /></Field>
        <Field label="UPI VPA"><input className={inputCls} value={form.upi_vpa || ''} onChange={(e) => set('upi_vpa', e.target.value)} /></Field>
        <Field label="Cost center"><input className={inputCls} value={form.cost_center || ''} onChange={(e) => set('cost_center', e.target.value)} /></Field>
        <Field label="Invoice prefix"><input className={inputCls} placeholder="BV-RNC" value={form.invoice_prefix || ''} onChange={(e) => set('invoice_prefix', e.target.value.toUpperCase())} /></Field>
      </div>

      <SectionHeader icon={<StoreIcon className="w-3.5 h-3.5" />} title="Enabled categories" />
      <div className="flex flex-wrap gap-2">
        {STORE_CATEGORIES.map((c) => (
          <label key={c} className={`text-xs border rounded px-2 py-1 cursor-pointer ${cats.includes(c) ? 'bg-bv-50 border-bv text-bv' : 'border-gray-200 text-gray-600'}`}>
            <input type="checkbox" className="hidden" checked={cats.includes(c)}
              onChange={() => set('enabled_categories', cats.includes(c) ? cats.filter((x) => x !== c) : [...cats, c])} />
            {c}
          </label>
        ))}
      </div>

      {store && (
        <label className="flex items-center gap-2 text-sm mt-3">
          <input type="checkbox" checked={form.is_active !== false} onChange={(e) => set('is_active', e.target.checked)} /> Active
        </label>
      )}

      <ModalFooter saving={saving} onClose={onClose} onSave={save} />
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Shared bits
// ---------------------------------------------------------------------------
function updateArr<T>(
  setForm: Dispatch<SetStateAction<T>>,
  key: keyof T,
  index: number,
  patch: Record<string, unknown>,
) {
  setForm((p) => {
    const arr = [...((p[key] as unknown as unknown[]) || [])] as Record<string, unknown>[];
    arr[index] = { ...arr[index], ...patch };
    return { ...p, [key]: arr };
  });
}

function SectionHeader({ icon, title, onAdd }: { icon: ReactNode; title: string; onAdd?: () => void }) {
  return (
    <div className="flex items-center justify-between mt-4 mb-2">
      <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide flex items-center gap-1.5">{icon}{title}</h4>
      {onAdd && (
        <button type="button" onClick={onAdd} className="inline-flex items-center gap-1 text-xs text-bv hover:underline">
          <Plus className="w-3 h-3" /> Add
        </button>
      )}
    </div>
  );
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="fixed inset-0 bg-black/30 flex items-start justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl my-8">
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3 sticky top-0 bg-white rounded-t-xl">
          <h3 className="font-semibold text-gray-900">{title}</h3>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-700"><X className="w-5 h-5" /></button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

function ModalFooter({ saving, onClose, onSave }: { saving: boolean; onClose: () => void; onSave: () => void }) {
  return (
    <div className="flex items-center justify-end gap-2 mt-5 pt-3 border-t border-gray-100">
      <button type="button" onClick={onClose} className="text-sm text-gray-600 hover:bg-gray-100 rounded-lg px-3 py-1.5">Cancel</button>
      <button type="button" onClick={onSave} disabled={saving}
        className="inline-flex items-center gap-1.5 text-sm font-medium text-white bg-bv hover:bg-bv-600 rounded-lg px-4 py-1.5 disabled:opacity-60">
        {saving && <Loader2 className="w-4 h-4 animate-spin" />} Save
      </button>
    </div>
  );
}
