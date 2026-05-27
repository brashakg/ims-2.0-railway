// ============================================================================
// IMS 2.0 - Display Fixture Form Modal (v2-2b)
// ============================================================================
// Create / edit modal for a display fixture. Used in two modes:
//   - Create: caller passes storeId; on save calls displayFixturesApi.create.
//   - Edit:   caller passes an existing fixture; on save calls .update.
// The owner picks type + floor + zone + capacity + merch tags + optional attrs
// (mannequin/spotlit/temp_ctrl/no_qr/key_holder/notes). A 409 from the server
// (duplicate code) is surfaced inline under the `code` field; a 400 from the
// validator is surfaced as a banner.

import { useEffect, useState } from 'react';
import { X, Loader2 } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import {
  displayFixturesApi,
  type DisplayFixture,
  type FixtureType,
  type FixtureFloor,
  type FixtureZone,
  type CatalogMerchType,
} from '../../services/api/displayFixtures';

const FIXTURE_TYPES: { value: FixtureType; label: string }[] = [
  { value: 'window',   label: 'Window display' },
  { value: 'wall',     label: 'Wall display' },
  { value: 'pillar',   label: 'Pillar display' },
  { value: 'counter',  label: 'Counter display' },
  { value: 'cabinet',  label: 'Locked cabinet' },
  { value: 'gondola',  label: 'Gondola / island' },
  { value: 'drawer',   label: 'Drawer / overflow' },
  { value: 'fridge',   label: 'Fridge (CL)' },
];

const FLOORS: { value: FixtureFloor; label: string }[] = [
  { value: 'ground',  label: 'Ground floor' },
  { value: 'storage', label: 'Storage' },
  { value: 'clinic',  label: 'Clinic chamber' },
];

const ZONES: { value: FixtureZone; label: string }[] = [
  { value: 'A', label: 'Zone A — premium' },
  { value: 'B', label: 'Zone B — mid' },
  { value: 'C', label: 'Zone C — value' },
  { value: '-', label: 'No zone (-)' },
];

const MERCH_TAGS: CatalogMerchType[] = ['Frame', 'Lens', 'CL', 'Access.'];

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onSaved: (fixture: DisplayFixture) => void;
  // Either edit an existing fixture, or create new for a store.
  fixture?: DisplayFixture;
  storeId?: string;
}

// Strict subset of the FE form state. We hold strings for the inputs and
// coerce on save -- avoids the common pitfall where a number input dispatches
// '0' on a fresh mount and clobbers a server-side default.
interface FormState {
  code: string;
  name: string;
  type: FixtureType;
  floor: FixtureFloor;
  zone: FixtureZone;
  capacity: string;
  lockable: boolean;
  merch: CatalogMerchType[];
  mannequin: boolean;
  spotlit: boolean;
  temp_ctrl: string;
  no_qr: boolean;
  key_holder: string;
  notes: string;
}

const emptyForm = (): FormState => ({
  code: '',
  name: '',
  type: 'wall',
  floor: 'ground',
  zone: '-',
  capacity: '10',
  lockable: false,
  merch: [],
  mannequin: false,
  spotlit: false,
  temp_ctrl: '',
  no_qr: false,
  key_holder: '',
  notes: '',
});

const fromFixture = (f: DisplayFixture): FormState => ({
  code: f.code,
  name: f.name,
  type: f.type,
  floor: f.floor,
  zone: f.zone,
  capacity: String(f.capacity ?? 10),
  lockable: !!f.lockable,
  merch: Array.isArray(f.merch) ? f.merch : [],
  mannequin: !!f.mannequin,
  spotlit: !!f.spotlit,
  temp_ctrl: f.temp_ctrl ?? '',
  no_qr: !!f.no_qr,
  key_holder: f.key_holder ?? '',
  notes: f.notes ?? '',
});

export function DisplayFixtureFormModal({ isOpen, onClose, onSaved, fixture, storeId }: Props) {
  const toast = useToast();
  const [form, setForm] = useState<FormState>(() => (fixture ? fromFixture(fixture) : emptyForm()));
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [saving, setSaving] = useState(false);
  const [codeError, setCodeError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const isEdit = !!fixture;

  // Re-seed when the modal opens for a different fixture.
  useEffect(() => {
    if (!isOpen) return;
    setForm(fixture ? fromFixture(fixture) : emptyForm());
    setCodeError(null);
    setBanner(null);
    setShowAdvanced(Boolean(fixture?.mannequin || fixture?.spotlit || fixture?.temp_ctrl || fixture?.no_qr || fixture?.key_holder));
  }, [isOpen, fixture]);

  if (!isOpen) return null;

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => {
    setForm(prev => ({ ...prev, [k]: v }));
    if (k === 'code') setCodeError(null);
  };

  const toggleMerch = (tag: CatalogMerchType) => {
    setForm(prev => ({
      ...prev,
      merch: prev.merch.includes(tag) ? prev.merch.filter(t => t !== tag) : [...prev.merch, tag],
    }));
  };

  const validate = (): string | null => {
    const code = form.code.trim();
    if (!code) return 'Code is required.';
    if (code.length < 3 || code.length > 10) return 'Code must be 3-10 characters.';
    if (!form.name.trim()) return 'Name is required.';
    const capN = Number(form.capacity);
    if (!Number.isFinite(capN) || capN < 1) return 'Capacity must be at least 1.';
    return null;
  };

  const onSubmit = async () => {
    setBanner(null);
    const v = validate();
    if (v) { setBanner(v); return; }
    setSaving(true);
    try {
      const code = form.code.trim().toUpperCase();
      // Build the payload. exclude_none on the backend strips undefined; on
      // empty optional text fields we send null explicitly so an edit can
      // clear them.
      const payload = {
        ...(isEdit ? {} : { store_id: storeId }),
        code,
        name: form.name.trim(),
        type: form.type,
        floor: form.floor,
        zone: form.zone,
        capacity: Number(form.capacity),
        lockable: form.lockable,
        merch: form.merch,
        // Optional advanced — only send when changed/non-empty to keep payload tight.
        mannequin: form.mannequin || undefined,
        spotlit: form.spotlit || undefined,
        temp_ctrl: form.temp_ctrl.trim() || undefined,
        no_qr: form.no_qr || undefined,
        key_holder: form.key_holder.trim() || undefined,
        notes: form.notes.trim() || undefined,
      };
      if (isEdit && fixture) {
        const { fixture: updated } = await displayFixturesApi.update(fixture.fixture_id, payload);
        toast.success(`Fixture ${code} updated`);
        if (updated) onSaved(updated);
        else onSaved({ ...fixture, ...payload, fixture_id: fixture.fixture_id } as DisplayFixture);
      } else {
        if (!storeId) {
          setBanner('No active store. Pick a store before adding a fixture.');
          setSaving(false);
          return;
        }
        const { fixture: created } = await displayFixturesApi.create(payload);
        toast.success(`Fixture ${code} created`);
        onSaved(created);
      }
      onClose();
    } catch (e) {
      const msg = (e instanceof Error ? e.message : String(e)) || 'Save failed';
      // Surface duplicate-code 409 next to the code field; other validation
      // errors land in the banner so the operator can see them.
      if (/already exists/i.test(msg) || /duplicate/i.test(msg) || /code/i.test(msg)) {
        setCodeError(msg);
      } else {
        setBanner(msg);
      }
    } finally {
      setSaving(false);
    }
  };

  const labelCls = 'block text-xs font-medium text-gray-600 uppercase tracking-wide mb-1';
  const inputCls = 'input-field text-sm w-full';

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-white border border-gray-200 rounded-xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col shadow-xl">
        <div className="flex items-center justify-between p-5 border-b border-gray-200">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">
              {isEdit ? `Edit fixture — ${fixture?.code}` : 'Add fixture'}
            </h2>
            <p className="text-sm text-gray-500 mt-0.5">
              Where this fixture lives, what fits, and how to find it on a count sheet.
            </p>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-900 p-1 -m-1" aria-label="Close">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-5 overflow-y-auto flex-1">
          {banner && (
            <div className="rounded-md border border-red-200 bg-red-50 text-red-700 text-sm px-3 py-2">
              {banner}
            </div>
          )}

          {/* Identity ----------------------------------------------------- */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className={labelCls}>Code <span className="text-red-600">*</span></label>
              <input
                value={form.code}
                onChange={e => set('code', e.target.value)}
                onBlur={e => set('code', e.target.value.trim().toUpperCase())}
                placeholder="W-01"
                className={inputCls}
                maxLength={10}
              />
              <p className={`text-[11px] mt-1 ${codeError ? 'text-red-600' : 'text-gray-500'}`}>
                {codeError ?? '3-10 chars, auto-uppercased on save.'}
              </p>
            </div>
            <div className="sm:col-span-2">
              <label className={labelCls}>Name <span className="text-red-600">*</span></label>
              <input
                value={form.name}
                onChange={e => set('name', e.target.value)}
                placeholder="Front wall display"
                className={inputCls}
              />
            </div>
          </div>

          {/* Type / floor / zone / capacity ------------------------------- */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className={labelCls}>Type</label>
              <select value={form.type} onChange={e => set('type', e.target.value as FixtureType)} className={inputCls}>
                {FIXTURE_TYPES.map(t => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className={labelCls}>Floor</label>
              <select value={form.floor} onChange={e => set('floor', e.target.value as FixtureFloor)} className={inputCls}>
                {FLOORS.map(f => (
                  <option key={f.value} value={f.value}>{f.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className={labelCls}>Zone</label>
              <select value={form.zone} onChange={e => set('zone', e.target.value as FixtureZone)} className={inputCls}>
                {ZONES.map(z => (
                  <option key={z.value} value={z.value}>{z.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className={labelCls}>Capacity</label>
              <input
                type="number"
                min={1}
                value={form.capacity}
                onChange={e => set('capacity', e.target.value)}
                className={inputCls}
              />
            </div>
          </div>

          {/* Lockable + Merch tags --------------------------------------- */}
          <div>
            <label className="inline-flex items-center gap-2 text-sm text-gray-700">
              <input type="checkbox" checked={form.lockable} onChange={e => set('lockable', e.target.checked)} />
              <span>Lockable / keyed</span>
            </label>
          </div>

          <div>
            <label className={labelCls}>Merch categories</label>
            <div className="flex flex-wrap gap-2">
              {MERCH_TAGS.map(tag => {
                const on = form.merch.includes(tag);
                return (
                  <button
                    type="button"
                    key={tag}
                    onClick={() => toggleMerch(tag)}
                    className={
                      'px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ' +
                      (on
                        ? 'bg-bv-50 text-gray-900 border-bv'
                        : 'bg-gray-100 text-gray-600 border-transparent hover:bg-gray-200')
                    }
                    style={on ? { borderColor: 'var(--bv)' } : undefined}
                  >
                    {tag}
                  </button>
                );
              })}
            </div>
            <p className="text-[11px] text-gray-500 mt-1">
              Empty = accepts any catalog type. Tags hint the GRN modal which fixtures fit each SKU.
            </p>
          </div>

          {/* Advanced (collapsible) -------------------------------------- */}
          <div className="border-t border-gray-200 pt-4">
            <button
              type="button"
              onClick={() => setShowAdvanced(v => !v)}
              className="text-xs font-medium text-gray-600 hover:text-gray-900"
            >
              {showAdvanced ? '- Hide' : '+ Show'} optional attributes
            </button>
            {showAdvanced && (
              <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-3">
                <label className="inline-flex items-center gap-2 text-sm text-gray-700">
                  <input type="checkbox" checked={form.mannequin} onChange={e => set('mannequin', e.target.checked)} />
                  <span>Has mannequin</span>
                </label>
                <label className="inline-flex items-center gap-2 text-sm text-gray-700">
                  <input type="checkbox" checked={form.spotlit} onChange={e => set('spotlit', e.target.checked)} />
                  <span>Spotlit</span>
                </label>
                <div>
                  <label className={labelCls}>Temp control</label>
                  <input
                    value={form.temp_ctrl}
                    onChange={e => set('temp_ctrl', e.target.value)}
                    placeholder="2-8C"
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className={labelCls}>Key holder</label>
                  <input
                    value={form.key_holder}
                    onChange={e => set('key_holder', e.target.value)}
                    placeholder="SM only"
                    className={inputCls}
                  />
                </div>
                <label className="inline-flex items-center gap-2 text-sm text-gray-700 sm:col-span-2">
                  <input type="checkbox" checked={form.no_qr} onChange={e => set('no_qr', e.target.checked)} />
                  <span>No QR / anti-theft tag</span>
                </label>
              </div>
            )}
          </div>

          {/* Notes -------------------------------------------------------- */}
          <div>
            <label className={labelCls}>Notes</label>
            <textarea
              value={form.notes}
              onChange={e => set('notes', e.target.value)}
              rows={2}
              placeholder="Anything staff should know — e.g. always face brand-side outward."
              className={inputCls}
            />
          </div>
        </div>

        <div className="p-5 border-t border-gray-200 flex justify-end gap-2">
          <button onClick={onClose} className="px-4 py-2 bg-gray-100 text-gray-700 rounded-lg text-sm hover:bg-gray-200">
            Cancel
          </button>
          <button
            onClick={onSubmit}
            disabled={saving}
            className="px-5 py-2 rounded-lg text-sm font-medium text-white disabled:opacity-50 flex items-center gap-2"
            style={{ background: 'var(--bv)' }}
            onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.background = 'var(--bv-600)'; }}
            onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = 'var(--bv)'; }}
          >
            {saving && <Loader2 className="w-4 h-4 animate-spin" />}
            {isEdit ? 'Save changes' : 'Add fixture'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default DisplayFixtureFormModal;
