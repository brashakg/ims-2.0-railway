// ============================================================================
// IMS 2.0 - Catalog page: sort, filters and rows per page, in the ADDRESS
// ============================================================================
// Owner 2026-10-12 (mockup artboard Main). Every choice lives in the query
// string so Back, refresh and a shared link keep it; an unknown value falls
// back safely to "not chosen". The server does the sorting and filtering
// (GET /products via services/catalog_list); this module only reads / writes
// the address and turns it into request parameters, chips and plain words.
// Pure functions -- pinned by __tests__/catalogListState.test.ts.

import { formatTimeIST, istDayString } from '../../utils/datetime';

export const SORT_OPTIONS = [
  { value: 'edited', label: 'Recently edited', phrase: 'recently edited first' },
  { value: 'newest', label: 'Newest added', phrase: 'newest first' },
  { value: 'oldest', label: 'Oldest added', phrase: 'oldest first' },
  { value: 'name_asc', label: 'Name A-Z', phrase: 'by name A-Z' },
  { value: 'name_desc', label: 'Name Z-A', phrase: 'by name Z-A' },
  { value: 'brand_asc', label: 'Brand A-Z', phrase: 'by brand A-Z' },
  { value: 'mrp_desc', label: 'MRP high to low', phrase: 'MRP high to low' },
  { value: 'mrp_asc', label: 'MRP low to high', phrase: 'MRP low to high' },
] as const;
export type SortKey = (typeof SORT_OPTIONS)[number]['value'];
/** Owner ruling 2026-10-12: Recently edited is the default. */
export const DEFAULT_SORT: SortKey = 'edited';
/** The Needs review queue keeps its order of before: newest import first,
 *  the order the review editor also walks when it fetches the next item. */
export const defaultSort = (segment: 'catalog' | 'review' = 'catalog'): SortKey =>
  segment === 'review' ? 'newest' : DEFAULT_SORT;

/** The five flag types (backend services/catalog_flags.FLAG_TYPES). */
export const FLAG_TYPES = [
  { value: 'needs_revision', label: 'Needs revision' },
  { value: 'photo_redo', label: 'Needs photo redo' },
  { value: 'price_check', label: 'Price check' },
  { value: 'wrong_category', label: 'Wrong category / duplicate' },
  { value: 'other', label: 'Something else' },
] as const;
export type FlagType = (typeof FLAG_TYPES)[number]['value'];
export type FlagFilter = '' | 'any' | 'mine' | 'archived' | FlagType;

export const flagLabel = (t: string): string =>
  FLAG_TYPES.find((f) => f.value === t)?.label ?? t;

/** Attribute filters (snake_case keys on the product's attributes). */
export const ATTRIBUTE_FILTERS = [
  { key: 'frame_color', label: 'Frame colour', any: 'Any colour' },
  { key: 'lens_colour', label: 'Lens colour', any: 'Any colour' },
  { key: 'gender', label: 'Gender', any: 'Any' },
  { key: 'shape', label: 'Shape', any: 'Any shape' },
  { key: 'frame_type', label: 'Frame type', any: 'Any type' },
  { key: 'frame_material', label: 'Material', any: 'Any material' },
] as const;
export type AttributeKey = (typeof ATTRIBUTE_FILTERS)[number]['key'];

export const PAGE_SIZES = [25, 50, 100, 200] as const;
export const DEFAULT_PAGE_SIZE = 50;
export const PAGE_SIZE_STORAGE_KEY = 'ims.catalog.rowsPerPage';

export const QUICK_DATES = [
  { value: '', label: 'Any time' },
  { value: 'today', label: 'Today' },
  { value: 'yesterday', label: 'Yesterday' },
  { value: '7d', label: 'Last 7 days' },
  { value: 'month', label: 'This month' },
] as const;
export type QuickDate = (typeof QUICK_DATES)[number]['value'];

export interface CatalogFilters {
  q: string;
  sort: SortKey;
  category: string;
  brand: string;
  frame_color: string;
  lens_colour: string;
  gender: string;
  shape: string;
  frame_type: string;
  frame_material: string;
  /** created_by user id */
  added_by: string;
  /** updated_by user id, or 'none' = never edited */
  edited_by: string;
  date_on: 'added' | 'edited';
  /** India days, YYYY-MM-DD, both included */
  from: string;
  to: string;
  flag: FlagFilter;
  photo: '' | 'has' | 'missing';
  inactive: boolean;
  rows: number;
}

const TEXT_KEYS = [
  'q', 'category', 'brand', 'frame_color', 'lens_colour', 'gender', 'shape',
  'frame_type', 'frame_material', 'added_by', 'edited_by',
] as const;

const DAY = /^\d{4}-\d{2}-\d{2}$/;
/** A calendar day that exists. Date.parse rolls 2026-02-30 over to 2 March
 *  instead of failing, so the day must survive the round trip unchanged;
 *  year 0000 is no day either. */
export const isDay = (s: string): boolean => {
  if (!DAY.test(s) || s < '0001-01-01') return false;
  const d = new Date(`${s}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
};

/** The Needs review list (imported docs, GET /catalog/products) applies
 *  only these; every other filter is "not chosen" there, so no chip claims
 *  a filter that list never applied (a shared link, or the legacy
 *  /catalog?segment=review redirect, can carry any key). */
const REVIEW_KEYS = ['q', 'sort', 'category', 'brand', 'photo', 'rows'] as const;

/** Read the page address. Unknown or broken values fall back to "not
 *  chosen"; a From after To drops the range. `storedRows` is this device's
 *  remembered rows-per-page, used when the address has none. `segment`
 *  'review' keeps only what the Needs review list applies. Flags: Archived
 *  is the admins' option (`canSeeArchived`); for anyone else it is "not
 *  chosen", never a list the server refuses. */
export function readFilters(
  sp: URLSearchParams,
  storedRows?: number | null,
  segment: 'catalog' | 'review' = 'catalog',
  canSeeArchived = false
): CatalogFilters {
  // 120 characters: the server's own cap on a filter value (catalog_list.
  // VALUE_MAX), so every option it offers survives the address.
  const text = (k: string) => (sp.get(k) || '').trim().slice(0, 120);
  const sort = SORT_OPTIONS.some((o) => o.value === sp.get('sort'))
    ? (sp.get('sort') as SortKey)
    : defaultSort(segment);
  const flagRaw = sp.get('flag') || '';
  const flag = (['any', 'mine', ...(canSeeArchived ? ['archived'] : [])].includes(flagRaw) ||
  FLAG_TYPES.some((f) => f.value === flagRaw)
    ? flagRaw
    : '') as FlagFilter;
  const photoRaw = sp.get('photo') || '';
  let from = isDay(text('from')) ? text('from') : '';
  let to = isDay(text('to')) ? text('to') : '';
  if (from && to && from > to) {
    from = '';
    to = '';
  }
  const rowsRaw = Number(sp.get('rows'));
  const rows = (PAGE_SIZES as readonly number[]).includes(rowsRaw)
    ? rowsRaw
    : (PAGE_SIZES as readonly number[]).includes(Number(storedRows))
      ? Number(storedRows)
      : DEFAULT_PAGE_SIZE;
  const f = {
    sort,
    date_on: sp.get('date_on') === 'edited' ? 'edited' : 'added',
    from,
    to,
    flag,
    photo: photoRaw === 'has' || photoRaw === 'missing' ? photoRaw : '',
    inactive: sp.get('inactive') === '1',
    rows,
  } as CatalogFilters;
  for (const k of TEXT_KEYS) f[k] = text(k);
  if (segment === 'review') {
    const none = readFilters(new URLSearchParams(), rows);
    const scoped = { ...none } as Record<string, unknown>;
    for (const k of REVIEW_KEYS) scoped[k] = f[k];
    return scoped as unknown as CatalogFilters;
  }
  return f;
}

/** Write the filters into the address, keeping every other key (page,
 *  focus). Defaults are left out, except rows per page: the address ALWAYS
 *  carries it (50 too), so a page number means the same rows on every
 *  device and in every shared link. */
export function writeFilters(
  f: CatalogFilters,
  prev: URLSearchParams,
  segment: 'catalog' | 'review' = 'catalog'
): URLSearchParams {
  const sp = new URLSearchParams(prev);
  const put = (k: string, v: string | boolean | number, isDefault: boolean) => {
    if (isDefault || v === '' || v === false) sp.delete(k);
    else sp.set(k, v === true ? '1' : String(v));
  };
  for (const k of TEXT_KEYS) put(k, f[k], false);
  put('sort', f.sort, f.sort === defaultSort(segment));
  // Kept even before any day is picked: choosing Last edited first and the
  // days second must not fall back to Date added in between.
  put('date_on', f.date_on, f.date_on === 'added');
  put('from', f.from, false);
  put('to', f.to, false);
  put('flag', f.flag, false);
  put('photo', f.photo, false);
  put('inactive', f.inactive, false);
  put('rows', f.rows, false);
  return sp;
}

/** The GET /products parameters for these filters (the spine list). */
export function toProductParams(f: CatalogFilters) {
  const opt = (s: string) => s || undefined;
  return {
    search: opt(f.q),
    category: opt(f.category),
    brand: opt(f.brand),
    sort: f.sort,
    frame_color: opt(f.frame_color),
    lens_colour: opt(f.lens_colour),
    gender: opt(f.gender),
    shape: opt(f.shape),
    frame_type: opt(f.frame_type),
    frame_material: opt(f.frame_material),
    created_by: opt(f.added_by),
    updated_by: opt(f.edited_by),
    date_on: f.from || f.to ? f.date_on : undefined,
    date_from: opt(f.from),
    date_to: opt(f.to),
    flag: f.flag || undefined,
    photo: f.photo || undefined,
    is_active: (f.inactive ? 'all' : 'true') as 'all' | 'true',
  };
}

// ---- India days ------------------------------------------------------------

const addDays = (day: string, n: number): string => {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};

/** Today in India as YYYY-MM-DD. */
export const istToday = (now: Date = new Date()): string => istDayString(now) as string;

/** A quick pick's range. The quick picks and the More filters range set the
 *  SAME From / To (one rule): choosing one replaces the other. */
export function quickRange(pick: QuickDate, now: Date = new Date()): { from: string; to: string } {
  const today = istToday(now);
  switch (pick) {
    case 'today':
      return { from: today, to: today };
    case 'yesterday':
      return { from: addDays(today, -1), to: addDays(today, -1) };
    case '7d':
      return { from: addDays(today, -6), to: today };
    case 'month':
      return { from: `${today.slice(0, 8)}01`, to: today };
    default:
      return { from: '', to: '' };
  }
}

/** Which quick pick the current Date added range is: '' (any time), a pick,
 *  or 'custom' (a range from More filters, or Last edited). */
export function quickPickOf(f: CatalogFilters, now: Date = new Date()): QuickDate | 'custom' {
  if (!f.from && !f.to) return '';
  if (f.date_on !== 'added') return 'custom';
  for (const q of QUICK_DATES) {
    if (!q.value) continue;
    const r = quickRange(q.value, now);
    if (r.from === f.from && r.to === f.to) return q.value;
  }
  return 'custom';
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** '2026-10-01' -> '1 Oct' (the year added when it is not this year). */
export function dayLabel(day: string, now: Date = new Date()): string {
  const [y, m, d] = day.split('-');
  const base = `${Number(d)} ${MONTHS[Number(m) - 1]}`;
  return y === istToday(now).slice(0, 4) ? base : `${base} ${y}`;
}

/** 'Added 1 Oct - 12 Oct' / 'Edited from 1 Oct' / 'Added 12 Oct'. */
export function dateChipLabel(f: CatalogFilters, now: Date = new Date()): string {
  const verb = f.date_on === 'edited' ? 'Edited' : 'Added';
  if (f.from && f.to) {
    return f.from === f.to
      ? `${verb} ${dayLabel(f.from, now)}`
      : `${verb} ${dayLabel(f.from, now)} - ${dayLabel(f.to, now)}`;
  }
  if (f.from) return `${verb} from ${dayLabel(f.from, now)}`;
  return `${verb} until ${dayLabel(f.to, now)}`;
}

/** The ADDED column's moment in India time: 'Today, 12:39 pm',
 *  'Yesterday, 6:14 pm', '10 Oct, 4:05 pm'. '-' when unknown. */
export function whenIST(value: unknown, now: Date = new Date()): string {
  const day = istDayString(value as string);
  if (!day) return '-';
  return `${relativeDay(day, now)}, ${formatTimeIST(value as string)}`;
}

/** 'Today' / 'Yesterday' / '11 Oct' for an India day. */
export function relativeDay(day: string, now: Date = new Date()): string {
  const today = istToday(now);
  if (day === today) return 'Today';
  if (day === addDays(today, -1)) return 'Yesterday';
  return dayLabel(day, now);
}

/** The ADDED column, both lines: who added it and when, then who last
 *  edited it or 'Not edited since'. Names are the server's (users first);
 *  the edit's day is the server's last_edited_at (catalog_list.
 *  LAST_EDITED_AT), the same moment the Recently edited sort and the Last
 *  edited range read -- never updated_at, which moves on writes that are
 *  not edits. */
export function addedColumn(
  doc: Record<string, unknown>,
  now: Date = new Date()
): { by: string; when: string; edited: string } {
  const by = String(doc.created_by_name || doc.created_by || '').trim() || '-';
  const when = whenIST(doc.created_at, now);
  const editor = String(doc.updated_by_name || doc.updated_by || '').trim();
  const editedDay = istDayString(doc.last_edited_at as string);
  const edited =
    doc.updated_by && editedDay
      ? `Edited by ${editor}, ${relativeDay(editedDay, now)}`
      : doc.created_at
        ? 'Not edited since'
        : '-';
  return { by, when, edited };
}

// ---- Chips -------------------------------------------------------------------

export interface PersonOption {
  value: string;
  label: string;
  count: number;
}

export interface FilterChip {
  key: string;
  label: string;
}

/** One removable chip per chosen filter (sort and rows are not filters). */
export function activeChips(
  f: CatalogFilters,
  people: { added: PersonOption[]; edited: PersonOption[] },
  categoryLabel: (v: string) => string = (v) => v,
  now: Date = new Date()
): FilterChip[] {
  const chips: FilterChip[] = [];
  const nameOf = (list: PersonOption[], id: string) =>
    list.find((p) => p.value === id)?.label || id;
  if (f.q) chips.push({ key: 'q', label: `Search: ${f.q}` });
  if (f.category) chips.push({ key: 'category', label: `Category: ${categoryLabel(f.category)}` });
  if (f.brand) chips.push({ key: 'brand', label: `Brand: ${f.brand}` });
  for (const a of ATTRIBUTE_FILTERS) {
    if (f[a.key]) chips.push({ key: a.key, label: `${a.label}: ${f[a.key]}` });
  }
  if (f.added_by) chips.push({ key: 'added_by', label: `Added by: ${nameOf(people.added, f.added_by)}` });
  if (f.edited_by) {
    chips.push({
      key: 'edited_by',
      label:
        f.edited_by === 'none'
          ? 'Never edited'
          : `Last edited by: ${nameOf(people.edited, f.edited_by)}`,
    });
  }
  if (f.from || f.to) chips.push({ key: 'dates', label: dateChipLabel(f, now) });
  if (f.flag) chips.push({ key: 'flag', label: `Flags: ${flagFilterLabel(f.flag)}` });
  if (f.photo) chips.push({ key: 'photo', label: f.photo === 'has' ? 'Has photo' : 'Photo missing' });
  if (f.inactive) chips.push({ key: 'inactive', label: 'Including inactive' });
  return chips;
}

export function flagFilterLabel(flag: FlagFilter): string {
  if (flag === 'any') return 'Any flag open';
  if (flag === 'mine') return 'Flagged on my entries';
  if (flag === 'archived') return 'Archived (deleted)';
  return flagLabel(flag);
}

/** The filters with one chip removed. */
export function removeChip(f: CatalogFilters, key: string): CatalogFilters {
  if (key === 'dates') return { ...f, from: '', to: '', date_on: 'added' };
  if (key === 'inactive') return { ...f, inactive: false };
  return { ...f, [key]: '' } as CatalogFilters;
}

/** Clear all: every filter off; the sort and rows per page stay. */
export function clearFilters(f: CatalogFilters): CatalogFilters {
  return { ...readFilters(new URLSearchParams(), f.rows), sort: f.sort };
}

/** The result line: '38 of 481 products, recently edited first'. */
export function resultLine(shown: number, total: number | null, sort: SortKey): string {
  const phrase = SORT_OPTIONS.find((o) => o.value === sort)?.phrase ?? '';
  const n = shown.toLocaleString('en-IN');
  const of = total !== null && total >= shown ? ` of ${total.toLocaleString('en-IN')}` : '';
  return `${n}${of} product${(total ?? shown) === 1 ? '' : 's'}, ${phrase}`;
}

/** How many choices sit behind More filters (the button's badge). */
export function moreFiltersCount(f: CatalogFilters): number {
  return (
    [f.lens_colour, f.gender, f.shape, f.frame_type, f.frame_material, f.edited_by].filter(Boolean)
      .length + (quickPickOf(f) === 'custom' ? 1 : 0)
  );
}
