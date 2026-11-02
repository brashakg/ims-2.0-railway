// ============================================================================
// IMS 2.0 - Transfers page cards (S3, board Transfer)
// ============================================================================
// "Ask another shop for stock": the quantity is capped at what is FREE there
// (reserved pieces cannot be asked for; the online shop is no destination --
// the server refuses both, this card shows its numbers). "Missing on the way":
// pieces scanned out and never in, seen by both shops' managers and the admin
// until found or written off (the write-off is admin-only).

import { useEffect, useState } from 'react';
import { Loader2, Send, AlertTriangle } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { inventoryApi, productApi } from '../../services/api';
import { storeApi } from '../../services/api/stores';
import { isOnlineStore, isOnlineStoreId } from '../../utils/storeMode';
import { getErrorMessage } from '../../utils/errorHandler';
import * as gates from './transferPermissions';

interface Hit {
  product_id: string;
  name: string;
  sku: string;
}

export function AskForStock({ onAsked }: { onAsked: () => void }) {
  const { user } = useAuth();
  const toast = useToast();
  const [shops, setShops] = useState<{ id: string; name: string }[]>([]);
  const [here, setHere] = useState('');
  const [hereOnline, setHereOnline] = useState(false);
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<Hit[]>([]);
  const [product, setProduct] = useState<Hit | null>(null);
  const [from, setFrom] = useState('');
  const [qty, setQty] = useState(1);
  const [free, setFree] = useState<{ free: number; reserved: number } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    storeApi
      .getStores()
      .then((res: any) => {
        const list = (Array.isArray(res) ? res : res?.stores || res?.data || []).map((s: any) => ({
          id: s.store_id || s.id,
          name: s.store_name || s.name || '',
          store_type: s.store_type || '',
        }));
        const mine = list.find((s: any) => s.id === user?.activeStoreId);
        setHere(mine?.name || user?.activeStoreId || '');
        setHereOnline(isOnlineStore(mine));
        setShops(list.filter((s: any) => s.id !== user?.activeStoreId && !isOnlineStore(s)));
      })
      .catch(() => setShops([]));
  }, [user?.activeStoreId]);

  useEffect(() => {
    if (product || query.trim().length < 2) {
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
          list
            .slice(0, 8)
            .map((p) => ({
              product_id: String(p.product_id ?? p.id ?? ''),
              name: String(p.name ?? `${p.brand ?? ''} ${p.model ?? ''}`.trim()),
              sku: String(p.sku ?? ''),
            }))
            .filter((h) => h.product_id),
        );
      })
      .catch(() => setHits([]));
    return () => {
      cancelled = true;
    };
  }, [query, product]);

  useEffect(() => {
    setFree(null);
    if (!product || !from) return;
    inventoryApi
      .getFreeStock(product.product_id, from)
      .then((f) => {
        setFree(f);
        setQty((q) => Math.max(1, Math.min(q, f.free)));
      })
      .catch(() => setFree(null));
  }, [product, from]);

  const fromName = shops.find((s) => s.id === from)?.name || '';
  const send = async () => {
    if (!product || !from || !free || qty < 1 || qty > free.free) return;
    setBusy(true);
    try {
      await inventoryApi.createTransfer({
        fromStoreId: from,
        fromStoreName: fromName,
        toStoreId: user?.activeStoreId || '',
        toStoreName: here,
        items: [{ productId: product.product_id, productName: product.name, sku: product.sku, quantity: qty }],
      });
      toast.success(`Asked ${fromName} for ${qty} × ${product.name}`);
      setProduct(null);
      setQuery('');
      setQty(1);
      onAsked();
    } catch (e) {
      toast.error(getErrorMessage(e, 'Could not send the request'));
    } finally {
      setBusy(false);
    }
  };

  // Stock is asked FOR the active shop: with no shop chosen, or the online shop
  // (it holds no stock of its own), there is nowhere for it to go.
  if (!user?.activeStoreId || isOnlineStoreId(user.activeStoreId) || hereOnline) return null;

  return (
    <div className="card space-y-3">
      <p className="font-medium text-gray-900">Ask another shop for stock</p>
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        <div className="relative md:col-span-2">
          <input
            value={product ? product.name : query}
            onChange={(e) => {
              setProduct(null);
              setQuery(e.target.value);
            }}
            placeholder="Product"
            aria-label="Product"
            className="input-field w-full"
          />
          {hits.length > 0 && (
            <div className="absolute z-10 mt-1 w-full bg-white border border-gray-200 rounded-lg shadow">
              {hits.map((h) => (
                <button
                  key={h.product_id}
                  onClick={() => setProduct(h)}
                  className="block w-full text-left px-3 py-2 text-sm hover:bg-gray-50"
                >
                  {h.name} <span className="text-gray-500">{h.sku}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <select
          value={from}
          onChange={(e) => setFrom(e.target.value)}
          aria-label="From shop"
          className="input-field"
        >
          <option value="">From shop</option>
          {shops.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <div className="flex gap-2">
          <input
            type="number"
            min={1}
            max={free?.free ?? 1}
            value={qty}
            onChange={(e) => setQty(Math.max(1, Math.min(free?.free ?? 1, parseInt(e.target.value) || 1)))}
            aria-label="Quantity"
            className="input-field w-20 text-center"
          />
          <button
            onClick={send}
            disabled={busy || !product || !from || !free || free.free < 1}
            className="btn-primary flex items-center gap-2"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            Send request
          </button>
        </div>
      </div>
      {free && (
        <p className="text-sm text-gray-600">
          {fromName} has {free.free} free
          {free.reserved ? ` (${free.reserved} more reserved for a customer and can't be asked for)` : ''}.
          The quantity can't go above what's free.
        </p>
      )}
    </div>
  );
}

interface MissingPiece {
  stock_id: string;
  barcode: string;
  product_name?: string;
  status: string;
  transfer_id: string;
  transfer_number: string;
  from_location_id: string;
  from_location_name: string;
  to_location_id: string;
  to_location_name: string;
}

export function MissingOnTheWay({ actor, refreshKey }: { actor: gates.TransferActor; refreshKey: number }) {
  const toast = useToast();
  const [pieces, setPieces] = useState<MissingPiece[]>([]);
  const [busy, setBusy] = useState(false);

  const load = () =>
    inventoryApi
      .getMissingPieces()
      .then((r: { pieces?: MissingPiece[] }) => setPieces(r?.pieces || []))
      .catch(() => setPieces([]));

  useEffect(() => {
    load();
  }, [refreshKey]);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try {
      await fn();
      toast.success(ok);
      await load();
    } catch (e) {
      toast.error(getErrorMessage(e, 'Action failed'));
    } finally {
      setBusy(false);
    }
  };

  if (!pieces.length) return null;
  return (
    <div className="card space-y-2">
      <p className="font-medium text-gray-900 flex items-center gap-2">
        <AlertTriangle className="w-4 h-4 text-amber-600" />
        Missing on the way ({pieces.length})
      </p>
      <p className="text-xs text-gray-500">
        Scanned out, never scanned in. Both managers and the admin see this until it's found or written off.
      </p>
      {pieces.map((p) => {
        const t = { status: '', from_location_id: p.from_location_id, to_location_id: p.to_location_id };
        return (
          <div key={`${p.transfer_id}:${p.stock_id}`} className="flex flex-wrap items-center gap-2 border-t border-gray-100 pt-2 text-sm">
            <span className="font-mono text-xs">{p.barcode}</span>
            <span className="text-gray-900">{p.product_name}</span>
            <span className="text-gray-500">
              #{p.transfer_number} {p.from_location_name} → {p.to_location_name}
            </span>
            {p.status === 'write_off_requested' && <span className="text-amber-700">sent to admin</span>}
            <span className="flex-1" />
            {(['destination', 'source'] as const)
              .filter((at) => gates.canMarkFound(actor, t, at))
              .map((at) => (
                <button
                  key={at}
                  disabled={busy}
                  onClick={() =>
                    // Found back at the sender = never delivered: between two
                    // GSTINs that raises a credit note, so it is confirmed.
                    (at === 'destination' ||
                      window.confirm(
                        `Found ${p.barcode} back at ${p.from_location_name}? It goes back on that shelf and, ` +
                          'between two GSTINs, a credit note is raised. This cannot be undone.',
                      )) &&
                    act(() => inventoryApi.foundMissingPiece(p.transfer_id, p.stock_id, at), 'Marked found')
                  }
                  className="btn-outline text-sm"
                >
                  Found at {at === 'source' ? p.from_location_name : p.to_location_name}
                </button>
              ))}
            {gates.canWriteOff(actor) ? (
              <button
                disabled={busy}
                onClick={() =>
                  window.confirm(
                    `Write off ${p.barcode}? It is removed from ${p.from_location_name}'s stock and, between two ` +
                      'GSTINs, a credit note is raised. This cannot be undone.',
                  ) && act(() => inventoryApi.writeOffMissingPiece(p.transfer_id, p.stock_id), 'Written off')
                }
                className="btn-outline text-sm text-red-600 border-red-200"
              >
                Write off
              </button>
            ) : (
              p.status === 'missing' &&
              gates.canRequestWriteOff(actor, t) && (
                <button
                  disabled={busy}
                  onClick={() =>
                    act(
                      () => inventoryApi.requestMissingWriteOff(p.transfer_id, p.stock_id),
                      'Sent to the admin for write-off',
                    )
                  }
                  className="btn-outline text-sm"
                >
                  Send to admin for write-off
                </button>
              )
            )}
          </div>
        );
      })}
    </div>
  );
}
