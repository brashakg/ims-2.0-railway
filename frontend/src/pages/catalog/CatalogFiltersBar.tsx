// ============================================================================
// IMS 2.0 - Catalog page toolbar: search, Sort by, filters, More filters
// ============================================================================
// Owner 2026-10-12, mockup artboard Main. Every choice is written into the
// page address by the page (catalogListState); the options and their counts
// come from the server (GET /products/facets), never a hard-coded list.

import { useState, type ReactNode } from 'react';
import { Search, SlidersHorizontal, X } from 'lucide-react';
import clsx from 'clsx';
import type { CatalogFacets } from '../../services/api/products';
import { MultiSelect, type MultiOption as Option } from '../../components/common/MultiSelect';
import { CATEGORY_BROWSE_OPTIONS } from '../../utils/categoryNormalize';
import {
  ATTRIBUTE_FILTERS,
  FLAG_TYPES,
  isDay,
  joinList,
  listOf,
  QUICK_DATES,
  SORT_OPTIONS,
  activeChips,
  moreFiltersCount,
  quickPickOf,
  quickRange,
  removeChip,
  clearFilters,
  type AttributeKey,
  type CatalogFilters,
  type QuickDate,
  type SortKey,
} from './catalogListState';

const label = 'flex flex-col gap-1 text-[11px] font-medium uppercase tracking-[.06em] text-gray-500';

/** Chrome reports a year still being typed as 0002, 0020, 0202: no day yet.
 *  A value already applied (the address) is never "still typing". */
const typing = (v: string, applied: string) => v !== applied && v !== '' && v < '1000';

/** From / To, India days, both included. Each box keeps what is typed in it
 *  (a box reset to the address mid-year would wipe the digits), and the pair
 *  is applied once both are whole days in order; From after To says so and
 *  applies nothing. The address changing from outside (a chip's X, Clear
 *  all, a quick pick) puts the boxes back to it, and the message goes with
 *  the value that caused it. */
function DayRange({
  from,
  to,
  onApply,
  children,
}: {
  from: string;
  to: string;
  onApply: (from: string, to: string) => void;
  /** Shown after the boxes, before the message. */
  children?: ReactNode;
}) {
  const [draft, setDraft] = useState({ from, to });
  const [applied, setApplied] = useState({ from, to });
  if (applied.from !== from || applied.to !== to) {
    setApplied({ from, to });
    setDraft({ from, to });
  }
  const ordered = (d: { from: string; to: string }) => !(d.from && d.to && d.from > d.to);
  const whole = (d: { from: string; to: string }) =>
    !typing(d.from, from) && !typing(d.to, to) && [d.from, d.to].every((v) => !v || isDay(v));
  const change = (next: { from: string; to: string }) => {
    setDraft(next);
    if (whole(next) && ordered(next) && (next.from !== from || next.to !== to)) {
      onApply(next.from, next.to);
    }
  };
  const refused = whole(draft) && !ordered(draft);
  return (
    <>
      <label className={label}>
        <span>From</span>
        <input
          type="date"
          value={draft.from}
          max={draft.to || undefined}
          onChange={(e) => change({ ...draft, from: e.target.value })}
          className="input-field w-40 normal-case tracking-normal"
        />
      </label>
      <label className={label}>
        <span>To</span>
        <input
          type="date"
          value={draft.to}
          min={draft.from || undefined}
          onChange={(e) => change({ ...draft, to: e.target.value })}
          className="input-field w-40 normal-case tracking-normal"
        />
      </label>
      {children}
      {refused && (
        <p className="basis-full text-sm text-red-700" role="alert">
          The From date is after the To date. Pick a From date on or before the To date.
        </p>
      )}
    </>
  );
}

export interface CatalogFiltersBarProps {
  segment: 'catalog' | 'review';
  filters: CatalogFilters;
  onChange: (next: CatalogFilters) => void;
  facets: CatalogFacets | null;
  /** Brand choices for the imported (review) segment (Brand Master). */
  reviewBrands: string[];
  search: string;
  onSearch: (text: string) => void;
  onSearchFlush: () => void;
  /** /catalog/missing-photos: the photo filter is the address itself. */
  photoFixed: boolean;
  isAdmin: boolean;
}

export function CatalogFiltersBar({
  segment,
  filters: f,
  onChange,
  facets,
  reviewBrands,
  search,
  onSearch,
  onSearchFlush,
  photoFixed,
  isAdmin,
}: CatalogFiltersBarProps) {
  const [moreOpen, setMoreOpen] = useState(() => moreFiltersCount(f) > 0);
  const spine = segment === 'catalog';
  const set = (patch: Partial<CatalogFilters>) => onChange({ ...f, ...patch });
  const moreCount = moreFiltersCount(f);
  const quick = quickPickOf(f);

  const people = {
    added: facets?.created_by || [],
    edited: (facets?.updated_by || []).filter((p) => p.value !== 'none'),
  };
  const chips = activeChips(f, people, (v) =>
    CATEGORY_BROWSE_OPTIONS.find((o) => o.value === v)?.label || v
  ).filter((c) => !(photoFixed && c.key === 'photo'));

  const brandOptions: Option[] | undefined = spine
    ? facets?.brand
    : reviewBrands.map((b) => ({ value: b }));

  const flagOptions: Option[] = [
    { value: 'any', label: 'Any flag open', count: facets?.flags?.open ?? 0 },
    { value: 'mine', label: 'Flagged on my entries', count: facets?.flags?.mine ?? 0 },
    ...FLAG_TYPES.map((t) => ({
      value: t.value,
      label: t.label,
      count: facets?.flags?.by_type?.[t.value] ?? 0,
    })),
    ...(isAdmin
      ? [{ value: 'archived', label: 'Archived (deleted)', count: facets?.flags?.archived ?? 0 }]
      : []),
  ];

  return (
    <div className="space-y-3" data-testid="catalog-filters">
      <div className="flex flex-wrap items-end gap-2">
        <label className={clsx(label, 'flex-1 min-w-[220px] max-w-md')}>
          <span>Search</span>
          <span className="relative block">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => onSearch(e.target.value)}
              onKeyDown={(e) => {
                // Barcode scanners type digits + Enter: flush the debounce.
                if (e.key === 'Enter') onSearchFlush();
              }}
              placeholder="Search name, SKU or scan a barcode"
              className="input-field w-full pl-9 normal-case tracking-normal"
            />
          </span>
        </label>
        <label className={label}>
          <span>Sort by</span>
          <select
            value={f.sort}
            onChange={(e) => set({ sort: e.target.value as SortKey })}
            className="input-field w-48 normal-case tracking-normal"
            aria-label="Sort by"
          >
            {SORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        {spine && (
          <button
            type="button"
            onClick={() => setMoreOpen((o) => !o)}
            aria-expanded={moreOpen}
            className="ml-auto inline-flex h-9 items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-3 text-sm font-medium text-gray-800 hover:bg-gray-50"
          >
            <SlidersHorizontal className="h-4 w-4" /> More filters
            {moreCount > 0 && (
              <span className="rounded-full bg-gray-900 px-1.5 text-[10px] font-semibold text-white">
                {moreCount}
              </span>
            )}
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-end gap-2">
        <MultiSelect
          title="Brand"
          anyLabel="All brands"
          value={f.brand}
          options={brandOptions}
          onChange={(v) => set({ brand: v })}
          width="w-44"
        />
        {spine && (
          <>
            <MultiSelect
              title="Frame colour"
              anyLabel="Any colour"
              value={f.frame_color}
              options={facets?.frame_color}
              onChange={(v) => set({ frame_color: v })}
            />
            {/* Owner 2026-11-01: both people filters on the main row, side by
                side -- ticks OR inside one, the two AND together. */}
            <MultiSelect
              title="Added by"
              anyLabel="Anyone"
              value={f.added_by}
              options={facets?.created_by}
              onChange={(v) => set({ added_by: v })}
            />
            <MultiSelect
              title="Last edited by"
              anyLabel="Anyone"
              value={f.edited_by}
              options={facets?.updated_by}
              onChange={(v) => set({ edited_by: v })}
            />
            <label className={label}>
              <span>Date added</span>
              <select
                value={quick}
                onChange={(e) => {
                  const pick = e.target.value as QuickDate;
                  set({ ...quickRange(pick), date_on: 'added' });
                }}
                className="input-field w-36 normal-case tracking-normal"
              >
                {QUICK_DATES.map((q) => (
                  <option key={q.value} value={q.value}>
                    {q.label}
                  </option>
                ))}
                {quick === 'custom' && (
                  <option value="custom" disabled>
                    Custom range
                  </option>
                )}
              </select>
            </label>
            <MultiSelect
              title="Flags - needs work"
              anyLabel="Any"
              value={f.flag}
              options={flagOptions}
              onChange={(v) => {
                // Archived (deleted) is a view of its own, never mixed with
                // live flags: ticking it clears the rest, ticking another
                // flag clears it.
                const next = v.split(',').filter(Boolean);
                const had = f.flag.split(',').includes('archived');
                const flag = !next.includes('archived') || next.length === 1
                  ? v
                  : had
                    ? next.filter((x) => x !== 'archived').join(',')
                    : 'archived';
                set({ flag });
              }}
              width="w-48"
            />
          </>
        )}
        {!photoFixed && (
          <label className={label}>
            <span>Photo</span>
            <select
              value={f.photo}
              onChange={(e) => set({ photo: e.target.value as CatalogFilters['photo'] })}
              className="input-field w-32 normal-case tracking-normal"
              aria-label="Photo"
            >
              <option value="">Any</option>
              <option value="has">Has photo</option>
              <option value="missing">Missing</option>
            </select>
          </label>
        )}
        {spine && (
          <label className="flex h-9 items-center gap-1.5 text-sm text-gray-600 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={f.inactive}
              onChange={(e) => set({ inactive: e.target.checked })}
              className="h-3.5 w-3.5 accent-gray-600"
            />
            Include inactive
          </label>
        )}
      </div>

      {spine && moreOpen && (
        <div className="space-y-3 rounded-xl border border-gray-200 bg-gray-50 p-3" data-testid="more-filters">
          <div className="flex flex-wrap items-end gap-2">
            {ATTRIBUTE_FILTERS.filter((a) => a.key !== 'frame_color').map((a) => (
              <MultiSelect
                key={a.key}
                title={a.label}
                anyLabel={a.any}
                value={f[a.key]}
                options={facets?.[a.key as AttributeKey]}
                onChange={(v) => set({ [a.key]: v } as Partial<CatalogFilters>)}
                width="w-36"
              />
            ))}
          </div>
          <div className="flex flex-wrap items-end gap-2" data-testid="date-range">
            <label className={label}>
              <span>Date range</span>
              <select
                value={f.date_on}
                onChange={(e) => set({ date_on: e.target.value as CatalogFilters['date_on'] })}
                className="input-field w-36 normal-case tracking-normal"
              >
                <option value="added">Date added</option>
                <option value="edited">Last edited</option>
              </select>
            </label>
            <DayRange from={f.from} to={f.to} onApply={(from, to) => set({ from, to })}>
              <span className="pb-2 text-xs text-gray-500">Both days included, India time.</span>
              {(f.from || f.to) && (
                <button
                  type="button"
                  onClick={() => set({ from: '', to: '', date_on: 'added' })}
                  className="pb-2 text-sm font-medium text-blue-700 underline"
                >
                  Clear dates
                </button>
              )}
            </DayRange>
          </div>
        </div>
      )}

      {/* Category chips (the ONE shared browse vocabulary) */}
      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          onClick={() => set({ category: '' })}
          className={clsx('ims-chip', f.category === '' && 'ims-chip--on')}
        >
          All
        </button>
        {CATEGORY_BROWSE_OPTIONS.map((o) => {
          // Multi-select too: each chip ticks / unticks its category.
          const cats = listOf(f.category);
          const on = cats.includes(o.value);
          return (
            <button
              key={o.value}
              type="button"
              aria-pressed={on}
              onClick={() =>
                set({ category: joinList(on ? cats.filter((c) => c !== o.value) : [...cats, o.value]) })
              }
              className={clsx('ims-chip', on && 'ims-chip--on')}
            >
              {o.label}
            </button>
          );
        })}
      </div>

      {chips.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5" data-testid="filter-chips">
          {chips.map((c) => (
            <span
              key={c.key}
              className="inline-flex items-center gap-1 rounded-full border border-gray-300 bg-white py-0.5 pl-2.5 pr-1 text-xs text-gray-800"
            >
              {c.label}
              <button
                type="button"
                onClick={() => onChange(removeChip(f, c.key))}
                aria-label={`Remove ${c.label} filter`}
                className="rounded-full p-0.5 text-gray-500 hover:bg-gray-100 hover:text-gray-900"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
          <button
            type="button"
            onClick={() => onChange(clearFilters(f))}
            className="ml-1 text-xs font-medium text-blue-700 underline"
          >
            Clear all
          </button>
        </div>
      )}
    </div>
  );
}

