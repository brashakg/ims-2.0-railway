// ============================================================================
// IMS 2.0 - Catalog page: the selection bar, Flag dialog, Delete (archive)
// ============================================================================
// Owner 2026-10-12, mockup artboards Main + FlagDialog. A LIGHT GREY bar
// (owner comment: grey, dark text; dark Flag button, red-outline Delete)
// shows when products are ticked. Flags go to the server's one door
// (POST /catalog/flags: one task per person who added the products);
// Delete (archive) is the existing soft delete in bulk, admins only, and its
// confirm dialog first asks the server what it would do (units still at the
// shops, listings live on the website that will be taken down).

import { useEffect, useState } from 'react';
import { Flag, Loader2, RotateCcw, Trash2 } from 'lucide-react';
import { catalogBulkApi, type BulkRowResult } from '../../services/api/catalog';
import { FLAG_TYPES, type FlagType } from './catalogListState';

function useEscape(onClose: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
}

function Dialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  useEscape(onClose);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/40 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="flex w-full max-w-lg flex-col gap-4 rounded-2xl bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-lg font-bold text-gray-900">{title}</h2>
        {children}
      </div>
    </div>
  );
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** "Mansi Kumari (3 products), Akshay Ovhal (1 product)" from the picked
 *  rows' adders. The server falls back to a catalogue manager when an adder
 *  has left; it says so in its answer. */
export function tasksGoTo(docs: Array<Record<string, unknown>>): string {
  const counts = new Map<string, number>();
  for (const d of docs) {
    const who = String(d.created_by_name || d.created_by || 'the catalogue manager');
    counts.set(who, (counts.get(who) || 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([who, n]) => `${who} (${plural(n, 'product')})`)
    .join(', ');
}

export function FlagDialog({
  docs,
  initialType,
  onClose,
  onDone,
}: {
  docs: Array<Record<string, unknown>>;
  initialType: FlagType;
  onClose: () => void;
  onDone: (message: string, ok: boolean) => void;
}) {
  const [type, setType] = useState<FlagType>(initialType);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const needsNote = type === 'other';
  const n = docs.length;

  const submit = async () => {
    if (needsNote && !note.trim()) {
      setError("Write a note for 'Something else' so the cataloguer knows what to fix.");
      return;
    }
    setBusy(true);
    setError('');
    try {
      const res = await catalogBulkApi.flag(
        docs.map((d) => String(d.product_id)),
        type,
        note.trim()
      );
      const done = res.results.filter((r) => r.status === 'done').length;
      const already = res.results.filter((r) => r.status === 'already').length;
      const refused = res.results.filter((r) => r.status === 'refused').length;
      const parts = [`${plural(done, 'product')} flagged`];
      if (res.tasks.length) parts.push(`${plural(res.tasks.length, 'task')} sent`);
      if (already) parts.push(`${already} already had this flag`);
      if (refused) parts.push(`${refused} could not be flagged`);
      onDone(parts.join(' - '), refused === 0);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not flag the products.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog title={`Flag ${plural(n, 'product')}`} onClose={onClose}>
      <p className="-mt-2 text-sm text-gray-600">
        The person who added each product gets a task. The flag clears when they mark it fixed.
        The products keep selling meanwhile.
      </p>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-2 text-[11px] font-semibold uppercase tracking-[.06em] text-gray-500">
          What needs doing
        </legend>
        {FLAG_TYPES.map((t) => (
          <label
            key={t.value}
            className={
              'flex min-h-[40px] cursor-pointer items-center gap-2.5 rounded-lg border px-3 text-sm ' +
              (type === t.value ? 'border-2 border-gray-900 bg-gray-50' : 'border-gray-200')
            }
          >
            <input
              type="radio"
              name="flag-type"
              checked={type === t.value}
              onChange={() => setType(t.value)}
              className="h-4 w-4"
            />
            {t.label}
          </label>
        ))}
      </fieldset>
      <label className="flex flex-col gap-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-[.06em] text-gray-500">
          Note for the cataloguer{needsNote ? ' (needed)' : ' (optional)'}
        </span>
        <textarea
          rows={3}
          maxLength={500}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="What is wrong? e.g. lens colour says Grey, the photo shows Green"
          className="input-field h-auto py-2"
        />
      </label>
      <p className="rounded-lg bg-gray-50 px-3 py-2 text-sm text-gray-700">
        Tasks go to: <span className="font-semibold">{tasksGoTo(docs)}</span>
      </p>
      {error && (
        <p className="text-sm text-red-700" role="alert">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onClose} className="btn-secondary" disabled={busy}>
          Cancel
        </button>
        <button
          type="button"
          onClick={() => void submit()}
          disabled={busy}
          className="inline-flex h-10 items-center gap-1.5 rounded-lg bg-[#CD201A] px-4 text-sm font-semibold text-white hover:bg-[#b11b16] disabled:opacity-60"
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Flag className="h-4 w-4" />}
          Flag and send tasks
        </button>
      </div>
    </Dialog>
  );
}

export function ArchiveDialog({
  ids,
  onClose,
  onDone,
}: {
  ids: string[];
  onClose: () => void;
  onDone: (message: string, ok: boolean) => void;
}) {
  const [preview, setPreview] = useState<BulkRowResult[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  // Ask once per set of ids (the caller hands a fresh array every render).
  const idsKey = ids.join(',');
  useEffect(() => {
    let alive = true;
    catalogBulkApi
      .archive(idsKey.split(','), false)
      .then((r) => {
        if (alive) setPreview(r.results);
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : 'Could not check the products.');
      });
    return () => {
      alive = false;
    };
  }, [idsKey]);

  const ready = (preview || []).filter((r) => r.status === 'ready');
  const inStock = ready.filter((r) => (r.in_stock_units || 0) > 0);
  const units = inStock.reduce((s, r) => s + (r.in_stock_units || 0), 0);
  const live = ready.filter((r) => r.live_online);
  const refused = (preview || []).filter((r) => r.status === 'refused');

  const apply = async () => {
    setBusy(true);
    try {
      const res = await catalogBulkApi.archive(
        ready.map((r) => r.product_id),
        true
      );
      const done = res.results.filter((r) => r.status === 'done').length;
      const no = res.results.filter((r) => r.status === 'refused');
      onDone(
        `${plural(done, 'product')} archived` +
          (no.length ? ` - ${no.length} refused (${no[0].reason})` : ''),
        no.length === 0
      );
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not archive the products.');
      setBusy(false);
    }
  };

  return (
    <Dialog title={`Delete (archive) ${plural(ids.length, 'product')}?`} onClose={onClose}>
      {!preview && !error && (
        <p className="flex items-center gap-2 text-sm text-gray-600">
          <Loader2 className="h-4 w-4 animate-spin" /> Checking stock and the website...
        </p>
      )}
      {preview && (
        <div className="space-y-2 text-sm text-gray-700">
          <p>
            Archived products leave the catalogue and the till. An admin can bring them back from
            Flags: Archived (deleted) - they do not go back on the website by themselves.
          </p>
          {inStock.length > 0 ? (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-amber-900">
              {plural(inStock.length, 'product')} still {inStock.length === 1 ? 'has' : 'have'}{' '}
              {plural(units, 'unit')} at the shops:{' '}
              {inStock.map((r) => r.name || r.product_id).join(', ')}.
            </p>
          ) : (
            <p>None of them has units left at the shops.</p>
          )}
          {live.length > 0 ? (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-red-900">
              Live on the website, will be taken down: {live.map((r) => r.name || r.product_id).join(', ')}.
            </p>
          ) : (
            <p>None of them is live on the website.</p>
          )}
          {refused.length > 0 && (
            <p className="text-gray-500">
              Skipped: {refused.map((r) => `${r.name || r.product_id} (${r.reason})`).join(', ')}.
            </p>
          )}
        </div>
      )}
      {error && (
        <p className="text-sm text-red-700" role="alert">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onClose} className="btn-secondary" disabled={busy}>
          Cancel
        </button>
        <button
          type="button"
          onClick={() => void apply()}
          disabled={busy || !preview || ready.length === 0}
          className="inline-flex h-10 items-center gap-1.5 rounded-lg bg-red-700 px-4 text-sm font-semibold text-white hover:bg-red-800 disabled:opacity-60"
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
          Delete (archive) {plural(ready.length, 'product')}
        </button>
      </div>
    </Dialog>
  );
}

/** The light grey bar shown while products are ticked. */
export function SelectionBar({
  count,
  cap,
  canFlag,
  canArchive,
  archivedView,
  flagType,
  onFlagType,
  onFlag,
  onArchive,
  onRestore,
  onClear,
  busy,
}: {
  count: number;
  cap: number;
  canFlag: boolean;
  canArchive: boolean;
  archivedView: boolean;
  flagType: FlagType;
  onFlagType: (t: FlagType) => void;
  onFlag: () => void;
  onArchive: () => void;
  onRestore: () => void;
  onClear: () => void;
  busy: boolean;
}) {
  return (
    <div
      className="sticky bottom-4 z-10 flex flex-wrap items-center gap-3 rounded-xl border border-gray-300 bg-gray-100 px-4 py-3 text-gray-900 shadow-lg"
      data-testid="selection-bar"
    >
      <span className="text-sm">
        <span className="font-semibold">{count}</span> selected
        {count >= cap && <span className="ml-1 text-xs text-gray-500">(max {cap} at a time)</span>}
      </span>
      {canFlag && !archivedView && (
        <>
          <span className="h-5 w-px bg-gray-300" aria-hidden="true" />
          <label className="flex items-center gap-2 text-sm">
            Flag as
            <select
              value={flagType}
              onChange={(e) => onFlagType(e.target.value as FlagType)}
              className="input-field w-52"
            >
              {FLAG_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={onFlag}
            disabled={busy}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-gray-900 px-3 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-60"
          >
            <Flag className="h-4 w-4" /> Flag {plural(count, 'product')}
          </button>
        </>
      )}
      {canArchive && (
        <>
          <span className="h-5 w-px bg-gray-300" aria-hidden="true" />
          {archivedView ? (
            <button
              type="button"
              onClick={onRestore}
              disabled={busy}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-gray-400 bg-white px-3 text-sm font-medium text-gray-900 hover:bg-gray-50 disabled:opacity-60"
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
              Restore
            </button>
          ) : (
            <button
              type="button"
              onClick={onArchive}
              disabled={busy}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-red-600 bg-white px-3 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-60"
            >
              <Trash2 className="h-4 w-4" /> Delete (archive)
            </button>
          )}
        </>
      )}
      <button
        type="button"
        onClick={onClear}
        disabled={busy}
        className="ml-auto rounded-lg px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-200"
      >
        Clear selection
      </button>
    </div>
  );
}
