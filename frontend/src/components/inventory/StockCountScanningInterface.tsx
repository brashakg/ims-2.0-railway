import { getErrorMessage } from '../../utils/errorHandler';
// ============================================================================
// IMS 2.0 - Stock Count: scan every piece
// ============================================================================
// Owner ruling S1. Each scan of a unit barcode ADDS ONE piece; the same label
// scanned twice counts once (the server answers `added: false`). A style whose
// piece has no label is found in the catalogue and given a TYPED quantity
// (0 = "none of this style here") -- it is added to the scans, never in place
// of them. A scan used to carry a typed "physical count" that REPLACED the
// style's whole line.
//
// THE COUNT IS BLIND (owner ruling 2026-08-25): while the session is open the
// counter sees only "pieces scanned so far" and their own typed answers -- no
// expected quantity, no variance and no list of styles still to find (that
// list is the missing list). The server withholds all of it; this screen
// never renders it from any source either.

import { useState, useEffect, useCallback, useRef } from 'react';
import { Barcode, AlertCircle, Loader2, Search } from 'lucide-react';
import { inventoryApi, productApi } from '../../services/api';

interface Props {
  /** The in-progress count session every scan is written onto. */
  countId: string;
  /** Fired after a piece or a typed quantity is saved. */
  onRecorded?: (itemsCounted: number) => void;
}

/** A style the counter has answered for: scanned pieces + typed pieces. */
interface CountedStyle {
  product_id: string;
  product_name: string;
  sku: string;
  scanned: number;
  typed: number | null;
}

interface StyleHit {
  product_id: string;
  name: string;
  sku: string;
}

/** What the counter has answered so far, from the OPEN session. Reads only
 *  the counter's own answers (`scans`, typed `items`) -- never any expected
 *  figure, even if an old server sent one. */
export function countedStyles(doc: any): CountedStyle[] {
  const byId = new Map<string, CountedStyle>();
  const row = (pid: string, name: string, sku: string) => {
    if (!byId.has(pid)) byId.set(pid, { product_id: pid, product_name: name, sku, scanned: 0, typed: null });
    return byId.get(pid)!;
  };
  for (const it of doc?.items || []) {
    if (!it?.product_id) continue;
    const r = row(it.product_id, it.product_name || '', it.sku || '');
    const typed = it.typed_quantity ?? it.counted_quantity;
    r.typed = typed == null ? null : Number(typed);
  }
  for (const sc of doc?.scans || []) {
    if (!sc?.product_id) continue;
    row(sc.product_id, sc.product_name || '', sc.sku || '').scanned += 1;
  }
  return Array.from(byId.values()).sort((a, b) => a.product_name.localeCompare(b.product_name));
}

export function StockCountScanningInterface({ countId, onRecorded }: Props) {
  const [barcode, setBarcode] = useState('');
  const [pieces, setPieces] = useState(0);
  const [last, setLast] = useState<string>('');
  const [error, setError] = useState('');
  const [styles, setStyles] = useState<CountedStyle[]>([]);
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<StyleHit[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const scanRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const doc = await inventoryApi.getStockCount(countId);
      setPieces(doc?.pieces_scanned ?? (doc?.scans || []).length);
      setStyles(countedStyles(doc));
    } catch {
      setError('Could not load this count. Refresh and try again.');
    }
  }, [countId]);

  useEffect(() => {
    load();
  }, [load]);

  // Scans are NOT serialised: a scanner gun fires faster than a round trip,
  // and the server adds each piece with one guarded write, so concurrent
  // scans are safe. The counter shown is the highest the server reported.
  const scan = async (code: string) => {
    setError('');
    try {
      const res = await inventoryApi.scanCountPiece(countId, code);
      if (!res?.recorded) {
        setError('That piece was NOT saved. Scan it again.');
        return;
      }
      setPieces((p) => Math.max(p, res.pieces_scanned ?? p));
      setLast(
        res.added
          ? `${res.product_name} · scanned`
          : `${res.product_name} · already scanned, counted once`
      );
      onRecorded?.(res.items_counted ?? 0);
      load();
    } catch (err: any) {
      setError(getErrorMessage(err, `Barcode ${code} not found`));
    }
  };

  const onScanKey = (e: React.KeyboardEvent) => {
    if (e.key !== 'Enter') return;
    const code = barcode.trim();
    if (!code) return;
    setBarcode('');
    scan(code);
  };

  // Find a style whose piece has no label.
  useEffect(() => {
    if (query.trim().length < 2) {
      setHits([]);
      return;
    }
    let cancelled = false;
    productApi
      .searchProducts(query.trim())
      .then((res: unknown) => {
        if (cancelled) return;
        const r = res as { products?: unknown[] } | unknown[];
        const list = (Array.isArray(r) ? r : r?.products ?? []) as Array<Record<string, unknown>>;
        setHits(
          list.slice(0, 8).map((p) => ({
            product_id: String(p.product_id ?? p.id ?? p._id ?? ''),
            name: String(p.name ?? `${p.brand ?? ''} ${p.model ?? ''}`.trim()),
            sku: String(p.sku ?? ''),
          })).filter((h) => h.product_id)
        );
      })
      .catch(() => setHits([]));
    return () => {
      cancelled = true;
    };
  }, [query]);

  const pickStyle = (h: StyleHit) => {
    setStyles((prev) =>
      prev.some((s) => s.product_id === h.product_id)
        ? prev
        : [...prev, { product_id: h.product_id, product_name: h.name, sku: h.sku, scanned: 0, typed: null }]
    );
    setQuery('');
    setHits([]);
  };

  const saveTyped = async (s: CountedStyle) => {
    const raw = drafts[s.product_id];
    if (raw === undefined || raw === '') return;
    const qty = parseInt(raw, 10);
    if (Number.isNaN(qty) || qty < 0) {
      setError('Type a whole number of pieces (0 if none of this style is here).');
      return;
    }
    setSaving(s.product_id);
    setError('');
    try {
      const res = await inventoryApi.recordCountItem(countId, {
        product_id: s.product_id,
        product_name: s.product_name,
        sku: s.sku,
        counted_quantity: qty,
      });
      setDrafts((prev) => {
        const next = { ...prev };
        delete next[s.product_id];
        return next;
      });
      onRecorded?.(res?.items_counted ?? 0);
      load();
    } catch (err: any) {
      setError(getErrorMessage(err, 'Could not save that quantity'));
    } finally {
      setSaving(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="card p-4 space-y-3">
        <div className="flex items-end justify-between gap-3 flex-wrap">
          <div>
            <p className="font-semibold" style={{ color: 'var(--ink)' }}>Scan every piece</p>
            <p className="text-sm" style={{ color: 'var(--ink-4)' }}>
              Blind count: you don't see what the system expects until you press Finish. Each scan adds one.
            </p>
          </div>
          <div className="text-right">
            <p className="text-xs" style={{ color: 'var(--ink-4)' }}>Pieces scanned so far</p>
            <p className="text-2xl font-semibold" data-testid="pieces-scanned">{pieces}</p>
          </div>
        </div>
        <label className="block">
          <span className="sr-only">Scan a piece</span>
          <div className="flex items-center gap-2">
            <Barcode className="w-5 h-5 flex-shrink-0" style={{ color: 'var(--ink-4)' }} />
            <input
              ref={scanRef}
              type="text"
              value={barcode}
              onChange={(e) => setBarcode(e.target.value)}
              onKeyDown={onScanKey}
              placeholder="Scan a piece's barcode"
              aria-label="Scan a piece's barcode"
              className="input w-full"
              autoFocus
            />
          </div>
        </label>
        {last && <p className="text-sm" style={{ color: 'var(--ink-3)' }}>Last: {last}</p>}
        {error && (
          <div className="p-3 rounded text-sm flex items-center gap-2" style={{ background: 'var(--bg-sunk)', color: 'var(--err)' }}>
            <AlertCircle className="w-4 h-4" />
            {error}
          </div>
        )}
      </div>

      <div className="card p-4 space-y-3">
        <div>
          <p className="font-semibold" style={{ color: 'var(--ink)' }}>Piece has no label? Type a quantity for its style</p>
          <p className="text-sm" style={{ color: 'var(--ink-4)' }}>
            Type 0 to say "none of this style here", so it counts as counted, not skipped.
          </p>
        </div>
        <div className="relative">
          <div className="flex items-center gap-2">
            <Search className="w-4 h-4 flex-shrink-0" style={{ color: 'var(--ink-4)' }} />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find a style, e.g. Oakley OO9208"
              aria-label="Find a style"
              className="input w-full"
            />
          </div>
          {hits.length > 0 && (
            <ul className="mt-1 border rounded bg-white" style={{ borderColor: 'var(--line)' }}>
              {hits.map((h) => (
                <li key={h.product_id}>
                  <button type="button" className="w-full text-left px-3 py-2 text-sm hover:bg-gray-50" onClick={() => pickStyle(h)}>
                    {h.name} <span style={{ color: 'var(--ink-4)' }}>{h.sku}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        {styles.length > 0 && (
          <div className="overflow-x-auto">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Style</th>
                  <th className="right">Scanned</th>
                  <th className="right">Typed</th>
                </tr>
              </thead>
              <tbody>
                {styles.map((s) => (
                  <tr key={s.product_id}>
                    <td>
                      <span className="font-medium" style={{ color: 'var(--ink)' }}>{s.product_name || s.sku}</span>
                      <span className="block text-xs" style={{ color: 'var(--ink-4)' }}>{s.sku}</span>
                    </td>
                    <td className="right mono">{s.scanned}</td>
                    <td className="right">
                      {saving === s.product_id ? (
                        <Loader2 className="w-4 h-4 animate-spin inline" />
                      ) : (
                        <input
                          type="number"
                          min="0"
                          inputMode="numeric"
                          aria-label={`Typed quantity for ${s.product_name || s.sku}`}
                          className="input w-20 text-right"
                          placeholder={s.typed === null ? '—' : String(s.typed)}
                          value={drafts[s.product_id] ?? ''}
                          onChange={(e) => setDrafts((prev) => ({ ...prev, [s.product_id]: e.target.value }))}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') saveTyped(s);
                          }}
                          onBlur={() => saveTyped(s)}
                        />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
