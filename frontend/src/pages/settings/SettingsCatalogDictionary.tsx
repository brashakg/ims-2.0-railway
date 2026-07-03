// ============================================================================
// IMS 2.0 - Settings: Catalog Dictionary
// ============================================================================
// The owner-editable "dictionary" for Add-Product attribute fields, in TWO
// scopes per field:
//   - "All categories": one shared list (e.g. gender, country_of_origin).
//   - Per-category overrides: a list saved for one category REPLACES the
//     shared list there — so same-named fields that bleed across categories
//     (lens_material on Sunglass vs Optical Lens, power on CL vs Readers,
//     dial_colour on Watch vs Clock) each get their own dictionary.
// The Catalog Add-Product form then renders selects restricted to EXACTLY the
// effective values, and the backend enforces the same lists at create/update
// (case-insensitive; saved casing wins).
//
// Brand Name / Sub Brand are deliberately NOT edited here — their source of
// truth is the Brand Master tab. A field/scope with NO saved list stays
// free-form (or keeps its built-in app defaults) until you configure it.
//
// BV brand tokens only (bv / bv-600 / bv-50 / bv-soft). No mock data.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { BookOpenCheck, Globe, Loader2, Plus, Save, Search, Sparkles, X } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import {
  catalogDictionaryApi,
  type CatalogDictionaryResponse,
} from '../../services/api/catalogDictionary';
import { productApi, type CategoryRegistryEntry } from '../../services/api/products';
import { CATEGORY_FIELDS } from '../catalog/productAddShared';

const ALL_SCOPE = '*';

interface FieldCategory {
  code: string; // canonical, e.g. "SUNGLASS" — the scope key
  name: string; // display, e.g. "Sunglass"
  prefix: string; // FE short code, e.g. "SG" — keys CATEGORY_FIELDS defaults
}

interface FieldRow {
  name: string;
  label: string;
  categories: FieldCategory[];
}

const EMPTY_DICT: CatalogDictionaryResponse = {
  fields: {},
  by_category: {},
  brand_managed_fields: ['brand_name', 'subbrand'],
};

export function CatalogDictionarySection() {
  const toast = useToast();

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [registry, setRegistry] = useState<CategoryRegistryEntry[]>([]);
  const [saved, setSaved] = useState<CatalogDictionaryResponse>(EMPTY_DICT);

  const [selected, setSelected] = useState<string | null>(null);
  const [scope, setScope] = useState<string>(ALL_SCOPE);
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
      setSaved(dict);
      setRegistry(reg.categories || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load the catalog dictionary');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Field inventory: every attribute field across every category (deduped),
  // excluding the Brand-Master-managed ones.
  const fields: FieldRow[] = useMemo(() => {
    const byName = new Map<string, FieldRow>();
    registry.forEach((cat) => {
      (cat.fields || []).forEach((f) => {
        if (saved.brand_managed_fields.includes(f.name)) return;
        const row = byName.get(f.name) || { name: f.name, label: f.label || f.name, categories: [] };
        if (!row.categories.some((c) => c.code === cat.code)) {
          row.categories.push({ code: cat.code, name: cat.name, prefix: cat.sku_prefix });
        }
        byName.set(f.name, row);
      });
    });
    const q = query.trim().toLowerCase();
    return Array.from(byName.values())
      .filter((r) => !q || r.name.toLowerCase().includes(q) || r.label.toLowerCase().includes(q))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [registry, saved.brand_managed_fields, query]);

  const selectedRow = useMemo(
    () => fields.find((f) => f.name === selected) || null,
    [fields, selected],
  );

  const savedFor = useCallback(
    (field: string, sc: string): string[] =>
      sc === ALL_SCOPE
        ? saved.fields[field] || []
        : saved.by_category[sc]?.[field] || [],
    [saved],
  );

  // Built-in app defaults for the seed button: per-category from that
  // category's CATEGORY_FIELDS metadata; union across categories for "All".
  const appDefaults = useMemo(() => {
    if (!selectedRow) return [];
    const cats = scope === ALL_SCOPE
      ? selectedRow.categories
      : selectedRow.categories.filter((c) => c.code === scope);
    const out: string[] = [];
    cats.forEach((c) => {
      (CATEGORY_FIELDS[c.prefix] || []).forEach((f) => {
        if (f.name === selectedRow.name && f.options) {
          f.options.forEach((o) => {
            if (!out.some((d) => d.toLowerCase() === o.toLowerCase())) out.push(o);
          });
        }
      });
    });
    return out;
  }, [selectedRow, scope]);

  const guardDirty = () =>
    !dirty || window.confirm('Discard unsaved changes to the current list?');

  const pickField = (name: string) => {
    if (!guardDirty()) return;
    setSelected(name);
    setScope(ALL_SCOPE);
    setWorking([...savedFor(name, ALL_SCOPE)]);
    setDirty(false);
    setNewValue('');
  };

  const pickScope = (sc: string) => {
    if (!selected || sc === scope || !guardDirty()) return;
    setScope(sc);
    setWorking([...savedFor(selected, sc)]);
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
    const merged = [...working];
    appDefaults.forEach((d) => {
      if (!merged.some((w) => w.toLowerCase() === d.toLowerCase())) merged.push(d);
    });
    setWorking(merged);
    setDirty(true);
  };

  const save = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      const category = scope === ALL_SCOPE ? undefined : scope;
      const r = await catalogDictionaryApi.save(selected, working, category);
      setSaved((prev) => {
        if (!category) {
          return { ...prev, fields: { ...prev.fields, [selected]: r.items } };
        }
        return {
          ...prev,
          by_category: {
            ...prev.by_category,
            [category]: { ...(prev.by_category[category] || {}), [selected]: r.items },
          },
        };
      });
      setWorking([...r.items]);
      setDirty(false);
      const scopeName = category
        ? selectedRow?.categories.find((c) => c.code === category)?.name || category
        : 'all categories';
      toast.success(
        r.items.length
          ? `${selectedRow?.label || selected} (${scopeName}): ${r.items.length} allowed value${r.items.length === 1 ? '' : 's'} saved.`
          : `${selectedRow?.label || selected} (${scopeName}): list cleared — falls back to ${category ? 'the All-categories list (or free-form)' : 'free-form'}.`
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

  const globalCount = selected ? savedFor(selected, ALL_SCOPE).length : 0;

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="flex items-center gap-2 mb-1">
          <BookOpenCheck className="w-5 h-5 text-bv" />
          <h2 className="text-lg font-semibold text-ink">Catalog Dictionary</h2>
        </div>
        <p className="text-sm text-ink-4">
          Save an allowed-value list for any Add-Product field and Catalog will only offer those
          values — enforced on the server too. Each field has an <strong>All categories</strong>{' '}
          list plus optional <strong>per-category lists</strong> that override it, so shared field
          names (lens material, power, colours…) never bleed between categories.{' '}
          <strong>Brand Name and Sub Brand are managed in the Brand Master tab</strong>, not here.
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
              const gCount = (saved.fields[f.name] || []).length;
              const overrides = f.categories.filter(
                (c) => (saved.by_category[c.code]?.[f.name] || []).length > 0
              ).length;
              const on = f.name === selected;
              const badge =
                gCount > 0 || overrides > 0
                  ? `${gCount > 0 ? `${gCount} values` : 'free-form'}${overrides > 0 ? ` · ${overrides} override${overrides === 1 ? '' : 's'}` : ''}`
                  : 'free-form';
              return (
                <button
                  key={f.name}
                  type="button"
                  onClick={() => pickField(f.name)}
                  className={
                    'w-full flex items-center justify-between gap-2 px-2.5 py-2 rounded-lg text-left text-sm transition ' +
                    (on ? 'bg-bv-50 text-bv font-medium ring-1 ring-bv' : 'hover:bg-gray-50 text-ink')
                  }
                >
                  <span className="min-w-0">
                    <span className="block truncate">{f.label}</span>
                    <span className="block text-[11px] text-ink-4 truncate">
                      {f.categories.slice(0, 3).map((c) => c.name).join(', ')}
                      {f.categories.length > 3 ? ` +${f.categories.length - 3}` : ''}
                    </span>
                  </span>
                  <span
                    className={
                      'shrink-0 px-1.5 py-0.5 rounded-full text-[11px] font-medium ' +
                      (gCount > 0 || overrides > 0 ? 'bg-bv-50 text-bv' : 'bg-gray-100 text-ink-4')
                    }
                  >
                    {badge}
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
                    Used by: {selectedRow.categories.map((c) => c.name).join(', ')}
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

              {/* Scope tabs: All categories + one per category using this field */}
              <div className="flex flex-wrap gap-1.5 mb-3">
                {[{ code: ALL_SCOPE, name: 'All categories' } as { code: string; name: string }]
                  .concat(selectedRow.categories)
                  .map((c) => {
                    const on = scope === c.code;
                    const count = savedFor(selectedRow.name, c.code).length;
                    return (
                      <button
                        key={c.code}
                        type="button"
                        onClick={() => pickScope(c.code)}
                        className={
                          'inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-lg border transition ' +
                          (on
                            ? 'bg-bv-50 text-bv border-bv'
                            : 'bg-white text-ink-3 border-line hover:bg-bv-50 hover:text-bv')
                        }
                      >
                        {c.code === ALL_SCOPE && <Globe className="w-3.5 h-3.5" />}
                        {c.name}
                        {count > 0 && (
                          <span className="px-1 py-px rounded-full bg-bv-50 text-bv text-[10px]">
                            {count}
                          </span>
                        )}
                      </button>
                    );
                  })}
              </div>

              {scope !== ALL_SCOPE && working.length === 0 && globalCount > 0 && !dirty && (
                <p className="text-xs text-ink-4 mb-2">
                  No override here — this category currently uses the{' '}
                  <strong>All-categories list ({globalCount} values)</strong>. Add values below to
                  give it its own list.
                </p>
              )}

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
                {appDefaults.length > 0 && (
                  <button
                    type="button"
                    onClick={seedDefaults}
                    className="btn-outline flex items-center gap-1.5"
                    title={`Prefill with the app's built-in suggestions (${appDefaults.length})`}
                  >
                    <Sparkles className="w-4 h-4" /> Start from app defaults
                  </button>
                )}
              </div>

              {working.length === 0 ? (
                <p className="text-sm text-ink-4 border border-dashed border-gray-200 rounded-lg px-3 py-6 text-center">
                  No values in this scope —{' '}
                  {scope === ALL_SCOPE
                    ? <>the field is <strong>free-form</strong> wherever no category override exists.</>
                    : <>this category falls back to the All-categories list{globalCount === 0 && ' (none — free-form)'}.</>}
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
