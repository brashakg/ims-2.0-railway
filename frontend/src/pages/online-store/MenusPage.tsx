// ============================================================================
// IMS 2.0 - Online Store - Website menu (self-maintaining, owner 2026-10-29)
// ============================================================================
// "A self-maintaining menu system -- something in IMS that generates menus
// directly from collections that have products, rather than static pushes."
//
// The website's main menu is BUILT by IMS (backend services/generated_menu.py)
// from a TEMPLATE of ordered sections and the IMS collections that have
// products ON THE WEBSITE. It is rebuilt after every sync (01:00 / 09:00 IST
// and every "Sync now") and pushed only when it changed. A collection with no
// products is left out and comes back by itself when it gets one.
//
// This screen:
//   - TEMPLATE (left): section titles, order, which groups, Top-N, static
//     links. ADMIN / SUPERADMIN save it; generated items are never hand-edited.
//   - LIVE PREVIEW (right): the menu IMS would build right now, with product
//     counts per item, and the "hidden because empty" list.
//   - STATUS: last generated / last pushed + result; "Regenerate & push now"
//     (the push roles: ADMIN / SUPERADMIN, same as every other push).
//   - OTHER MENUS: read-only (footer menus etc. are not touched by IMS).
// Light theme only -- no non-ASCII in copy.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Menu as MenuIcon,
  Plus,
  Save,
  Loader2,
  Trash2,
  ArrowLeft,
  ArrowUp,
  ArrowDown,
  RefreshCw,
  Info,
  UploadCloud,
  EyeOff,
  ChevronRight,
  ChevronDown,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import {
  menusApi,
  pushApi,
  generatedMenusApi,
  type EcomMenu,
  type GeneratedMenu,
  type GeneratedMenuItem,
  type MenuGroup,
  type MenuGroupSource,
  type MenuLink,
  type MenuSection,
  type MenuTemplate,
} from '../../services/api/onlineStore';
import OnlineStoreSyncBanner, {
  formatPushResult,
  type OnlineStoreSyncBannerHandle,
} from '../../components/online-store/OnlineStoreSyncBanner';
import { getErrorMessage } from '../../utils/errorHandler';

const SOURCE_LABELS: Record<MenuGroupSource, string> = {
  gender: "By gender (Men's, Women's, Unisex, Kids, Polarized)",
  shape: 'By shape (most products first)',
  price: 'By price (lowest first)',
  top_brands: 'Top brands (most products first)',
  brands_az: 'All brands A-Z',
};

const KIND_LABELS: Record<MenuSection['kind'], string> = {
  collection: 'One collection',
  links: 'Fixed links',
  panel: 'Mega-menu panel',
};

function fmtIST(iso?: string | null): string {
  if (!iso) return 'never';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', dateStyle: 'medium', timeStyle: 'short' }) + ' IST';
}

function move<T>(arr: T[], i: number, dir: -1 | 1): T[] {
  const j = i + dir;
  if (j < 0 || j >= arr.length) return arr;
  const next = arr.slice();
  [next[i], next[j]] = [next[j], next[i]];
  return next;
}

function newSection(kind: MenuSection['kind']): MenuSection {
  if (kind === 'collection') return { kind, title: 'New section', handle: '' };
  if (kind === 'links') return { kind, title: 'New section', url: '#', links: [{ title: 'Link', url: '/' }] };
  return { kind, title: 'New panel', url: '#', groups: [{ title: 'Brands', source: 'brands_az' }] };
}

// ---------------------------------------------------------------------------
// Links editor (static links: About Us, Shop all)
// ---------------------------------------------------------------------------
function LinksEditor({
  links,
  onChange,
  disabled,
}: {
  links: MenuLink[];
  onChange: (next: MenuLink[]) => void;
  disabled: boolean;
}) {
  return (
    <div className="space-y-1.5">
      {links.map((l, i) => (
        <div key={i} className="flex flex-wrap items-center gap-1.5">
          <input
            className="input-field text-xs flex-1 min-w-[120px]"
            value={l.title}
            disabled={disabled}
            aria-label="Link title"
            onChange={(e) => onChange(links.map((x, k) => (k === i ? { ...x, title: e.target.value } : x)))}
          />
          <input
            className="input-field text-xs font-mono flex-[2] min-w-[160px]"
            value={l.url}
            disabled={disabled}
            aria-label="Link address"
            onChange={(e) => onChange(links.map((x, k) => (k === i ? { ...x, url: e.target.value } : x)))}
          />
          {!disabled && (
            <button
              type="button"
              className="p-1 rounded hover:bg-gray-100 text-gray-500"
              title="Remove link"
              onClick={() => onChange(links.filter((_, k) => k !== i))}
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      ))}
      {!disabled && (
        <button
          type="button"
          className="text-xs text-bv-red-600 hover:underline inline-flex items-center gap-1"
          onClick={() => onChange([...links, { title: 'Link', url: '/' }])}
        >
          <Plus className="w-3 h-3" /> Add link
        </button>
      )}
    </div>
  );
}

// The Top-N input: the raw text is kept while typing (clearing it to type
// "15" must not snap to 10 and produce "105"); a whole number 1-100 is taken
// as it is typed, anything else is clamped on blur (empty -> the default 10).
function TopNInput({ value, disabled, onChange }: { value?: number; disabled: boolean; onChange: (n: number) => void }) {
  const [raw, setRaw] = useState(String(value ?? 10));
  useEffect(() => setRaw(String(value ?? 10)), [value]);
  const commit = () => {
    const n = Math.round(Number(raw));
    const next = raw.trim() === '' || !Number.isFinite(n) ? 10 : Math.min(100, Math.max(1, n));
    setRaw(String(next));
    if (next !== (value ?? 10)) onChange(next);
  };
  return (
    <input
      type="number"
      min={1}
      max={100}
      className="input-field text-xs w-16"
      value={raw}
      disabled={disabled}
      aria-label="How many brands"
      onChange={(e) => {
        setRaw(e.target.value);
        const n = Number(e.target.value);
        if (e.target.value.trim() !== '' && Number.isInteger(n) && n >= 1 && n <= 100) onChange(n);
      }}
      onBlur={commit}
    />
  );
}

// A heading's own link: the URL it opens when its collection is not shown
// (links / panel sections), and for a panel the collection it links to.
function HeadingFields({ section, ro, onChange }: { section: MenuSection; ro: boolean; onChange: (s: MenuSection) => void }) {
  if (section.kind === 'collection') return null;
  return (
    <div className="pl-8 mb-2 flex flex-wrap gap-2">
      {section.kind === 'panel' && (
        <label className="flex flex-wrap items-center gap-1.5 text-xs text-gray-600 flex-1 min-w-[180px]">
          Heading collection
          <input
            className="input-field text-xs font-mono flex-1 min-w-0"
            value={section.handle ?? ''}
            disabled={ro}
            placeholder="none"
            aria-label="Heading collection handle"
            onChange={(e) => onChange({ ...section, handle: e.target.value || undefined })}
          />
        </label>
      )}
      <label className="flex flex-wrap items-center gap-1.5 text-xs text-gray-600 flex-1 min-w-[180px]">
        Heading link
        <input
          className="input-field text-xs font-mono flex-1 min-w-0"
          value={section.url ?? ''}
          disabled={ro}
          placeholder="#"
          aria-label="Heading URL"
          onChange={(e) => onChange({ ...section, url: e.target.value })}
        />
      </label>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Template editor (one card per section)
// ---------------------------------------------------------------------------
function SectionCard({
  section,
  index,
  total,
  canEdit,
  onChange,
  onMove,
  onRemove,
}: {
  section: MenuSection;
  index: number;
  total: number;
  canEdit: boolean;
  onChange: (s: MenuSection) => void;
  onMove: (dir: -1 | 1) => void;
  onRemove: () => void;
}) {
  const ro = !canEdit;
  const setGroups = (groups: MenuGroup[]) => onChange({ ...(section as any), groups });
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3" data-testid="template-section">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <span className="text-[11px] uppercase tracking-wide text-gray-400 w-6">{index + 1}</span>
        <input
          className="input-field text-sm font-medium flex-1 min-w-0"
          value={section.title}
          disabled={ro}
          aria-label="Section title"
          onChange={(e) => onChange({ ...section, title: e.target.value } as MenuSection)}
        />
        <span className="text-[11px] text-gray-500 whitespace-nowrap">{KIND_LABELS[section.kind]}</span>
        {canEdit && (
          <div className="flex items-center">
            <button type="button" className="p-1 rounded hover:bg-gray-100 text-gray-500 disabled:opacity-30"
              disabled={index === 0} onClick={() => onMove(-1)} title="Move up">
              <ArrowUp className="w-3.5 h-3.5" />
            </button>
            <button type="button" className="p-1 rounded hover:bg-gray-100 text-gray-500 disabled:opacity-30"
              disabled={index === total - 1} onClick={() => onMove(1)} title="Move down">
              <ArrowDown className="w-3.5 h-3.5" />
            </button>
            <button type="button" className="p-1 rounded hover:bg-gray-100 text-gray-500" onClick={onRemove}
              title="Remove section">
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      </div>

      <HeadingFields section={section} ro={ro} onChange={onChange} />

      {section.kind === 'collection' && (
        <label className="flex flex-wrap items-center gap-2 text-xs text-gray-600 pl-8">
          Collection handle
          <input
            className="input-field text-xs font-mono flex-1 min-w-0"
            value={section.handle}
            disabled={ro}
            onChange={(e) => onChange({ ...section, handle: e.target.value })}
          />
        </label>
      )}

      {section.kind === 'links' && (
        <div className="pl-8">
          <LinksEditor links={section.links} disabled={ro} onChange={(links) => onChange({ ...section, links })} />
        </div>
      )}

      {section.kind === 'panel' && (
        <div className="pl-8 space-y-2">
          {section.groups.map((g, gi) => (
            <div key={gi} className="rounded-md border border-gray-100 bg-gray-50 p-2 space-y-1.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <input
                  className="input-field text-xs flex-1 min-w-[110px]"
                  value={g.title}
                  disabled={ro}
                  aria-label="Group title"
                  onChange={(e) => setGroups(section.groups.map((x, k) => (k === gi ? { ...x, title: e.target.value } : x)))}
                />
                <select
                  className="input-field text-xs flex-[2] min-w-[160px]"
                  value={g.source}
                  disabled={ro}
                  aria-label="Group source"
                  onChange={(e) =>
                    setGroups(section.groups.map((x, k) => (k === gi ? { ...x, source: e.target.value as MenuGroupSource } : x)))
                  }
                >
                  {(Object.keys(SOURCE_LABELS) as MenuGroupSource[]).map((src) => (
                    <option key={src} value={src}>
                      {SOURCE_LABELS[src]}
                    </option>
                  ))}
                </select>
                {(g.source === 'gender' || g.source === 'shape') && (
                  <select
                    className="input-field text-xs w-32"
                    value={g.category ?? 'sunglasses'}
                    disabled={ro}
                    aria-label="Group category"
                    onChange={(e) =>
                      setGroups(section.groups.map((x, k) => (k === gi ? { ...x, category: e.target.value } : x)))
                    }
                  >
                    <option value="sunglasses">Sunglasses</option>
                    <option value="eyeglasses">Eyeglasses</option>
                  </select>
                )}
                {g.source === 'top_brands' && (
                  <label className="text-xs text-gray-600 inline-flex items-center gap-1">
                    N
                    <TopNInput
                      value={g.limit}
                      disabled={ro}
                      onChange={(n) => setGroups(section.groups.map((x, k) => (k === gi ? { ...x, limit: n } : x)))}
                    />
                  </label>
                )}
                {canEdit && (
                  <span className="flex items-center">
                    <button type="button" className="p-1 rounded hover:bg-gray-200 text-gray-500 disabled:opacity-30"
                      disabled={gi === 0} onClick={() => setGroups(move(section.groups, gi, -1))} title="Move group up">
                      <ArrowUp className="w-3 h-3" />
                    </button>
                    <button type="button" className="p-1 rounded hover:bg-gray-200 text-gray-500 disabled:opacity-30"
                      disabled={gi === section.groups.length - 1} onClick={() => setGroups(move(section.groups, gi, 1))}
                      title="Move group down">
                      <ArrowDown className="w-3 h-3" />
                    </button>
                    <button type="button" className="p-1 rounded hover:bg-gray-200 text-gray-500"
                      onClick={() => setGroups(section.groups.filter((_, k) => k !== gi))} title="Remove group">
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </span>
                )}
              </div>
              {((g.links?.length ?? 0) > 0 || canEdit) && (
                <div>
                  <p className="text-[11px] text-gray-500 mb-1">Fixed links after the collections</p>
                  <LinksEditor
                    links={g.links ?? []}
                    disabled={ro}
                    onChange={(links) => setGroups(section.groups.map((x, k) => (k === gi ? { ...x, links } : x)))}
                  />
                </div>
              )}
            </div>
          ))}
          {canEdit && (
            <button
              type="button"
              className="text-xs text-bv-red-600 hover:underline inline-flex items-center gap-1"
              onClick={() => setGroups([...section.groups, { title: 'New group', source: 'shape', category: 'sunglasses' }])}
            >
              <Plus className="w-3 h-3" /> Add group
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Preview tree (read-only) with product counts
// ---------------------------------------------------------------------------
function PreviewTree({ items, depth = 0 }: { items: GeneratedMenuItem[]; depth?: number }) {
  return (
    <ul className={depth === 0 ? 'space-y-1' : 'mt-1 space-y-0.5 border-l border-gray-200 ml-2 pl-3'}>
      {items.map((it, i) => (
        <li key={`${it.title}-${i}`} data-testid={depth === 0 ? 'preview-top' : undefined}>
          <div className="flex items-center gap-2 text-sm">
            <span className={depth === 0 ? 'font-semibold text-gray-900' : 'text-gray-800'}>{it.title}</span>
            {typeof it.count === 'number' && (
              <span className="text-[11px] rounded-full bg-gray-100 px-1.5 py-0.5 text-gray-600">
                {it.count} products
              </span>
            )}
            <span className="text-[11px] font-mono text-gray-400 truncate">
              {it.handle ? `/collections/${it.handle}` : it.url && it.url !== '#' ? it.url : ''}
            </span>
          </div>
          {it.children && it.children.length > 0 && <PreviewTree items={it.children} depth={depth + 1} />}
        </li>
      ))}
    </ul>
  );
}

function ReadOnlyTree({ items }: { items: EcomMenu['items'] }) {
  if (!items || items.length === 0) return <p className="text-xs text-gray-500">No items.</p>;
  return (
    <ul className="space-y-0.5 text-xs">
      {items.map((it) => (
        <li key={it.id}>
          <span className="text-gray-800">{it.title}</span>
          <span className="ml-2 font-mono text-gray-400">{it.url ?? ''}</span>
          {it.children && it.children.length > 0 && (
            <div className="ml-3 border-l border-gray-200 pl-3">
              <ReadOnlyTree items={it.children} />
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

export default function MenusPage() {
  const toast = useToast();
  const { hasRole } = useAuth();
  // Template + push: ADMIN / SUPERADMIN (backend is the real gate).
  const canEdit = hasRole(['SUPERADMIN', 'ADMIN']);
  const canPublish = hasRole(['SUPERADMIN', 'ADMIN']);
  const bannerRef = useRef<OnlineStoreSyncBannerHandle>(null);

  const [loading, setLoading] = useState(true);
  const [generated, setGenerated] = useState<GeneratedMenu[] | null>(null);
  const [others, setOthers] = useState<EcomMenu[]>([]);
  const [draft, setDraft] = useState<MenuTemplate | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [pushing, setPushing] = useState(false);
  const [openOther, setOpenOther] = useState<string | null>(null);
  const [otherTree, setOtherTree] = useState<EcomMenu | null>(null);
  const [otherState, setOtherState] = useState<'loading' | 'ready' | 'failed'>('loading');

  const menu = generated && generated.length > 0 ? generated[0] : null;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [gen, all] = await Promise.all([generatedMenusApi.list(), menusApi.list()]);
      setGenerated(gen);
      setOthers(all.filter((m) => !(gen ?? []).some((g) => g.menu_id === m.id)));
      setDraft(gen && gen.length > 0 ? gen[0].template : null);
      setDirty(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const sections = useMemo(() => draft?.sections ?? [], [draft]);
  const setSections = (next: MenuSection[]) => {
    setDraft({ sections: next });
    setDirty(true);
  };

  const saveTemplate = async () => {
    if (!menu || !draft) return;
    setSaving(true);
    try {
      await generatedMenusApi.saveTemplate(menu.menu_id, draft);
      toast.success('Template saved - the preview below is rebuilt from it.');
      await load();
    } catch (e: any) {
      toast.error(getErrorMessage(e, 'Could not save the template'));
    } finally {
      setSaving(false);
    }
  };

  const pushNow = async () => {
    if (!menu) return;
    if (dirty && !window.confirm('The template has unsaved changes. Push the SAVED template anyway?')) return;
    setPushing(true);
    try {
      const r = await pushApi.pushMenu(menu.menu_id);
      const msg = formatPushResult(`Menu ${menu.handle}`, r);
      if (r.ok) toast.success(msg);
      else toast.error(msg);
      bannerRef.current?.refresh?.();
      await load();
    } catch (e: any) {
      toast.error(getErrorMessage(e, 'Push failed'));
    } finally {
      setPushing(false);
    }
  };

  const toggleOther = async (id: string) => {
    if (openOther === id) {
      setOpenOther(null);
      setOtherTree(null);
      return;
    }
    setOpenOther(id);
    setOtherTree(null);
    setOtherState('loading');
    let tree: EcomMenu | null = null;
    try {
      tree = await menusApi.get(id);
    } catch {
      tree = null;
    }
    setOtherTree(tree);
    setOtherState(tree ? 'ready' : 'failed');
  };

  const preview = menu?.preview ?? null;
  const lp = menu?.last_push ?? null;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        <div>
          <div className="flex items-center gap-2 text-xs text-gray-500 mb-1">
            <Link to="/online-store" className="inline-flex items-center gap-1 hover:text-gray-700">
              <ArrowLeft className="w-3.5 h-3.5" /> Online Store
            </Link>
            <span>/</span>
            <span className="text-gray-700">Menus</span>
          </div>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            <MenuIcon className="w-5 h-5" /> Website menu
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={load} className="btn-outline inline-flex items-center gap-1.5 text-sm"
            title="Reload">
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
          {canEdit && menu && (
            <button type="button" onClick={saveTemplate} disabled={saving || !dirty}
              className="btn-outline inline-flex items-center gap-1.5 text-sm"
              title={dirty ? 'Save the template' : 'No unsaved changes'}>
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              Save template
            </button>
          )}
          {canPublish && menu && (
            <button type="button" onClick={pushNow} disabled={pushing}
              className="btn-primary inline-flex items-center gap-1.5 text-sm"
              title="Rebuild the menu from the collections with products now and push it">
              {pushing ? <Loader2 className="w-4 h-4 animate-spin" /> : <UploadCloud className="w-4 h-4" />}
              Regenerate &amp; push now
            </button>
          )}
        </div>
      </div>
      <p className="text-sm text-gray-500 mb-4 max-w-3xl" data-testid="self-maintaining-note">
        This menu builds itself from the collections that have products on the website. It updates itself after
        every sync (01:00 and 09:00 IST, and every Sync now) and is pushed only when it changed. A collection with
        no products is left out and comes back by itself when it gets one - nobody edits the items by hand.
      </p>

      <OnlineStoreSyncBanner ref={bannerRef} className="mb-4" />

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-gray-500 p-4">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading the menu...
        </div>
      ) : generated === null ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          The menu could not be read from the server. Refresh to try again.
        </div>
      ) : !menu ? (
        <div className="rounded-xl border border-gray-200 bg-white p-6 text-center" data-testid="no-generated-menu">
          <MenuIcon className="w-7 h-7 text-gray-300 mx-auto mb-2" />
          <p className="text-sm font-medium text-gray-700">The self-maintaining menu is not set up yet.</p>
          <p className="text-xs text-gray-500 mt-1">
            It is created once by the IMS team (the approved Sunglasses / Shop by Brand / About Us design); the
            website keeps its hand-made menu until then.
          </p>
        </div>
      ) : (
        <>
          {/* Status */}
          <div className="rounded-xl border border-gray-200 bg-white p-4 mb-4 grid grid-cols-1 gap-3 sm:grid-cols-4 text-xs"
            data-testid="menu-status">
            <div>
              <div className="text-gray-500">Menu</div>
              <div className="font-mono text-gray-900">{menu.handle}</div>
              <div className="text-gray-500">{menu.title}</div>
            </div>
            <div>
              <div className="text-gray-500">Last generated</div>
              <div className="text-gray-900">{fmtIST(menu.last_generated_at)}</div>
            </div>
            <div>
              <div className="text-gray-500">Last pushed to the website</div>
              <div className="text-gray-900">{fmtIST(menu.last_pushed_at)}</div>
              {preview && (
                <div className={preview.changed_since_push ? 'text-amber-700' : 'text-green-700'}>
                  {preview.changed_since_push ? 'Changes waiting for the next push' : 'Website is up to date'}
                </div>
              )}
            </div>
            <div data-testid="last-push">
              <div className="text-gray-500">Last push attempt</div>
              {lp ? (
                <div className={lp.ok ? 'text-gray-900' : 'text-red-700'}>
                  {lp.mode === 'LIVE' ? 'LIVE' : 'dry-run (SIMULATED)'} - {lp.ok ? 'ok' : 'FAILED'} - {fmtIST(lp.at)}
                  {!lp.ok && (lp.error || lp.reason) ? <div>{lp.error || lp.reason}</div> : null}
                  {!lp.ok ? <div className="text-gray-500">Retried at the next sync.</div> : null}
                </div>
              ) : (
                <div className="text-gray-900">not pushed yet</div>
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {/* Template */}
            <section className="rounded-xl border border-gray-200 bg-gray-50 p-3 space-y-2 h-fit min-w-0">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-gray-900">Template</h2>
                {!canEdit && <span className="text-[11px] text-gray-500">read-only for your role</span>}
              </div>
              {sections.map((s, i) => (
                <SectionCard
                  key={i}
                  section={s}
                  index={i}
                  total={sections.length}
                  canEdit={canEdit}
                  onChange={(next) => setSections(sections.map((x, k) => (k === i ? next : x)))}
                  onMove={(dir) => setSections(move(sections, i, dir))}
                  onRemove={() => setSections(sections.filter((_, k) => k !== i))}
                />
              ))}
              {canEdit && (
                <div className="flex flex-wrap items-center gap-2 pt-1">
                  <span className="text-xs text-gray-500">Add a section:</span>
                  {(Object.keys(KIND_LABELS) as MenuSection['kind'][]).map((k) => (
                    <button key={k} type="button" className="btn-outline text-xs inline-flex items-center gap-1"
                      onClick={() => setSections([...sections, newSection(k)])}>
                      <Plus className="w-3 h-3" /> {KIND_LABELS[k]}
                    </button>
                  ))}
                </div>
              )}
            </section>

            {/* Preview */}
            <section className="space-y-4 min-w-0">
              <div className="rounded-xl border border-gray-200 bg-white p-4" data-testid="menu-preview">
                <h2 className="text-sm font-semibold text-gray-900 mb-1">Live preview</h2>
                <p className="text-[11px] text-gray-500 mb-3 flex items-start gap-1">
                  <Info className="w-3 h-3 mt-0.5 shrink-0" /> What the website menu is built as right now from the
                  SAVED template, with the products each collection shows on the website.
                </p>
                {preview && preview.items.length > 0 ? (
                  <PreviewTree items={preview.items} />
                ) : (
                  <p className="text-xs text-gray-500">No collection has products on the website yet.</p>
                )}
              </div>
              <div className="rounded-xl border border-gray-200 bg-white p-4" data-testid="hidden-list">
                <h2 className="text-sm font-semibold text-gray-900 mb-1 flex items-center gap-1.5">
                  <EyeOff className="w-4 h-4 text-gray-400" /> Hidden because empty ({preview?.hidden.length ?? 0})
                </h2>
                <p className="text-[11px] text-gray-500 mb-2">Each one appears by itself once it has products.</p>
                {preview && preview.hidden.length > 0 ? (
                  <ul className="space-y-0.5 text-xs">
                    {preview.hidden.map((h, i) => (
                      <li key={i} className="text-gray-700">
                        <span className="text-gray-500">{h.section}:</span> {h.title}{' '}
                        <span className="text-gray-400">- {h.reason}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-gray-500">Nothing hidden.</p>
                )}
              </div>
            </section>
          </div>
        </>
      )}

      {/* Other menus: read-only */}
      {others.length > 0 && (
        <section className="mt-6 rounded-xl border border-gray-200 bg-white overflow-hidden" data-testid="other-menus">
          <div className="px-4 py-2 border-b border-gray-200 bg-gray-50">
            <span className="text-xs font-medium text-gray-600">Other menus (read-only - IMS does not change them)</span>
          </div>
          <ul className="divide-y divide-gray-100">
            {others.map((m) => (
              <li key={m.id} className="px-4 py-2">
                <button type="button" className="flex items-center gap-1.5 text-sm text-gray-800"
                  onClick={() => toggleOther(m.id)}>
                  {openOther === m.id ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                  {m.title} <span className="font-mono text-xs text-gray-400">{m.handle}</span>
                </button>
                {openOther === m.id && (
                  <div className="mt-2 pl-6">
                    {otherState === 'ready' && otherTree ? (
                      <ReadOnlyTree items={otherTree.items} />
                    ) : otherState === 'failed' ? (
                      <p className="text-xs text-red-700">Could not load this menu.</p>
                    ) : (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
