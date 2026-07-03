// ============================================================================
// IMS 2.0 - Settings: Catalog Dictionary
// ============================================================================
// The owner-editable "dictionary" for Add-Product attribute fields: for any
// field you save a value list for here, the Catalog Add-Product form renders
// a select restricted to EXACTLY these values, and the backend enforces the
// same list at create/update (case-insensitive; saved casing wins).
//
// Brand Name / Sub Brand are deliberately NOT edited here — their source of
// truth is the Brand Master tab (brands + per-category applicability). A
// field with NO saved list stays free-form (or keeps its built-in app
// defaults) until you configure it.
//
// BV brand tokens only (bv / bv-600 / bv-50 / bv-soft). No mock data.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { BookOpenCheck, Loader2, Plus, Save, Search, Sparkles, X } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { catalogDictionaryApi } from '../../services/api/catalogDictionary';
import { productApi, type CategoryRegistryEntry } from '../../services/api/products';
import { CATEGORY_FIELDS } from '../catalog/productAddShared';

interface FieldRow {
  name: string;
  label: string;
  categories: string[]; // display names of categories using this field
  appDefaults: string[]; // union of local hardcoded options (suggestion seed)
}

export function CatalogDictionarySection() {
  const toast = useToast();

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [registry, setRegistry] = useState<CategoryRegistryEntry[]>([]);
  const [saved, setSaved] = useState<Record<string, string[]>>({});
  const [brandManaged, setBrandManaged] = useState<string[]>(['brand_name', 'subbrand']);

  const [selected, setSelected] = useState<string | null>(null);
  const [working, setWorking] = useState<string[]>([]);
  const [dirty, setDirty] = useState(false);
  const [newValue, setNewValue] = useState('');
  const [query, setQuery] = useState('');
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [dict, reg] = await Promise.all([
        catalogDictionaryApi.list(),
        productApi.getCategoryRegistry(),
      ]);
      setSaved(dict.fields || {});
      if (dict.brand_managed_fields?.length) setBrandManaged(dict.brand_managed_fields);
      setRegistry(reg.categories || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load the catalog dictionary');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Field inventory: every attribute field across every category (deduped),
  // excluding the Brand-Master-managed ones. App defaults come from the local
  // CATEGORY_FIELDS UI metadata so "Start from app defaults" can seed a list.
  const fields: FieldRow[] = useMemo(() => {
    const byName = new Map<string, FieldRow>();
    registry.forEach((cat) => {
      (cat.fields || []).forEach((f) => {
        if (brandManaged.includes(f.name)) return;
        const row = byName.get(f.name) || {
          name: f.name,
          label: f.label || f.name,
          categories: [],
          appDefaults: [],
        };
        if (!row.categories.includes(cat.name)) row.categories.push(cat.name);
        byName.set(f.name, row);
      });
    });
    Object.values(CATEGORY_FIELDS).forEach((catFields) => {
      catFields.forEach((f) => {
        const row = byName.get(f.name);
        if (row && f.options) {
          f.options.forEach((o) => {
            if (!row.appDefaults.some((d) => d.toLowerCase() === o.toLowerCase())) {
              row.appDefaults.push(o);
            }
          });
        }
      });
    });
    const q = query.trim().toLowerCase();
    return Array.from(byName.values())
      .filter((r) => !q || r.name.toLowerCase().includes(q) || r.label.toLowerCase().includes(q))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [registry, brandManaged, query]);

  const selectedRow = useMemo(
    () => fields.find((f) => f.name === selected) || null,
    [fields, selected],
  );

  const pick = (name: string) => {
    if (dirty && !window.confirm('Discard unsaved changes to the current field?')) return;
    setSelected(name);
    setWorking(saved[name] ? [...saved[name]] : []);
    setDirty(false);
    setNewValue('');
  };

  const addValue = () => {
    const v = newValue.trim();
    if (!v) return;
    if (working.some((w) => w.toLowerCase() === v.toLowerCase())) {
      toast.warning(`"${v}" is already in the list.`);
      return;
    }
    setWorking((prev) => [...prev, v]);
    setNewValue('');
    setDirty(true);
  };

  const removeValue = (v: string) => {
    setWorking((prev) => prev.filter((w) => w !== v));
    setDirty(true);
  };

  const seedDefaults = () => {
    if (!selectedRow) return;
    const merged = [...working];
    selectedRow.appDefaults.forEach((d) => {
      if (!merged.some((w) => w.toLowerCase() === d.toLowerCase())) merged.push(d);
    });
    setWorking(merged);
    setDirty(true);
  };

  const save = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      const r = await catalogDictionaryApi.save(selected, working);
      setSaved((prev) => ({ ...prev, [selected]: r.items }));
      setWorking([...r.items]);
      setDirty(false);
      toast.success(
        r.items.length
          ? `${selectedRow?.label || selected}: ${r.items.length} allowed value${r.items.length === 1 ? '' : 's'} saved. Only these can be chosen in Catalog now.`
          : `${selectedRow?.label || selected} is free-form again (no list configured).`
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="card flex items-center gap-2 text-ink-3">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading catalog dictionary…
      </div>
    );
  }
  if (error) {
    return <div className="card text-red-600 text-sm">{error}</div>;
  }

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="flex items-center gap-2 mb-1">
          <BookOpenCheck className="w-5 h-5 text-bv" />
          <h2 className="text-lg font-semibold text-ink">Catalog Dictionary</h2>
        </div>
        <p className="text-sm text-ink-4">
          Save an allowed-value list for any Add-Product field and the Catalog screen will only
          offer those values — enforced on the server too, so nothing outside your list can be
          saved. A field without a list stays free-form.{' '}
          <strong>Brand Name and Sub Brand are managed in the Brand Master tab</strong> (with
          per-category applicability), not here.
        </p>
      </div>

      <div className="grid grid-cols-1 laptop:grid-cols-[320px_1fr] gap-4 items-start">
        {/* Field list */}
        <div className="card !p-3">
          <div className="relative mb-2">
            <Search className="w-4 h-4 text-ink-4 absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input
              className="input-field w-full !pl-8"
              placeholder="Search fields…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <div className="max-h-[480px] overflow-y-auto space-y-0.5">
            {fields.map((f) => {
              const count = (saved[f.name] || []).length;
              const on = f.name === selected;
              return (
                <button
                  key={f.name}
                  type="button"
                  onClick={() => pick(f.name)}
                  className={
                    'w-full flex items-center justify-between gap-2 px-2.5 py-2 rounded-lg text-left text-sm transition ' +
                    (on ? 'bg-bv-50 text-bv font-medium ring-1 ring-bv' : 'hover:bg-gray-50 text-ink')
                  }
                >
                  <span className="min-w-0">
                    <span className="block truncate">{f.label}</span>
                    <span className="block text-[11px] text-ink-4 truncate">
                      {f.categories.slice(0, 3).join(', ')}
                      {f.categories.length > 3 ? ` +${f.categories.length - 3}` : ''}
                    </span>
                  </span>
                  <span
                    className={
                      'shrink-0 px-1.5 py-0.5 rounded-full text-[11px] font-medium ' +
                      (count > 0 ? 'bg-bv-50 text-bv' : 'bg-gray-100 text-ink-4')
                    }
                  >
                    {count > 0 ? `${count} values` : 'free-form'}
                  </span>
                </button>
              );
            })}
            {fields.length === 0 && (
              <p className="text-sm text-ink-4 px-2 py-4">No fields match your search.</p>
            )}
          </div>
        </div>

        {/* Editor */}
        <div className="card">
          {!selectedRow ? (
            <p className="text-sm text-ink-4 py-8 text-center">
              Pick a field on the left to define its allowed values.
            </p>
          ) : (
            <>
              <div className="flex items-start justify-between gap-3 mb-3">
                <div>
                  <h3 className="font-semibold text-ink">{selectedRow.label}</h3>
                  <p className="text-xs text-ink-4">
                    Used by: {selectedRow.categories.join(', ')}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={save}
                  disabled={!dirty || saving}
                  className="btn-primary flex items-center gap-2 disabled:opacity-50"
                >
                  {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                  Save list
                </button>
              </div>

              <div className="flex flex-wrap items-center gap-2 mb-3">
                <input
                  className="input-field flex-1 min-w-[200px]"
                  placeholder={`Add an allowed value for ${selectedRow.label}…`}
                  value={newValue}
                  onChange={(e) => setNewValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      addValue();
                    }
                  }}
                />
                <button type="button" onClick={addValue} className="btn-secondary flex items-center gap-1.5">
                  <Plus className="w-4 h-4" /> Add
                </button>
                {selectedRow.appDefaults.length > 0 && (
                  <button
                    type="button"
                    onClick={seedDefaults}
                    className="btn-outline flex items-center gap-1.5"
                    title={`Prefill with the app's built-in suggestions (${selectedRow.appDefaults.length})`}
                  >
                    <Sparkles className="w-4 h-4" /> Start from app defaults
                  </button>
                )}
              </div>

              {working.length === 0 ? (
                <p className="text-sm text-ink-4 border border-dashed border-gray-200 rounded-lg px-3 py-6 text-center">
                  No values yet — this field is <strong>free-form</strong> in Catalog. Add values and
                  save to restrict it.
                </p>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {working.map((v) => (
                    <span
                      key={v}
                      className="inline-flex items-center gap-1 pl-2.5 pr-1 py-1 rounded-full bg-gray-100 text-ink text-sm"
                    >
                      {v}
                      <button
                        type="button"
                        onClick={() => removeValue(v)}
                        aria-label={`Remove ${v}`}
                        className="p-0.5 rounded-full hover:bg-gray-200 text-ink-4 hover:text-red-600"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </span>
                  ))}
                </div>
              )}

              {dirty && (
                <p className="text-xs text-amber-600 mt-3">
                  Unsaved changes — click <strong>Save list</strong> to apply them to Catalog.
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
