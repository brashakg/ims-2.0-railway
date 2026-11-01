// ============================================================================
// IMS 2.0 - One transfer box, scanned piece by piece (S3, board TransferBox)
// ============================================================================
// The SAME panel packs and receives: at the sending shop you scan pieces OUT
// into the box (the whole list goes to the server on every scan), add the
// courier + tracking no. and ship; at the receiving shop you scan each piece
// IN -- a damaged one goes to quarantine there, with its reason -- and Accept.
// A piece never scanned in goes on "Missing on the way". Every rule lives on
// the server (routers/transfers.py); this panel only shows its answers.

import { useState } from 'react';
import { Loader2, ScanLine, Send, Trash2, CheckCircle } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { inventoryApi } from '../../services/api';
import { getErrorMessage } from '../../utils/errorHandler';
import * as gates from './transferPermissions';

export interface BoxLine {
  id?: string;
  product_id: string;
  product_name: string;
  quantity_requested?: number;
  packed_barcodes?: string[];
  shipped_stock_ids?: string[];
  shipped_barcodes?: string[];
  received_stock_ids?: string[];
  damaged_stock_ids?: string[];
}

export interface BoxTransfer {
  id: string;
  status: string;
  from_location_id: string;
  from_location_name: string;
  to_location_id: string;
  to_location_name: string;
  items: BoxLine[];
  courier_name?: string;
  tracking_number?: string;
  tax_invoice_number?: string;
  eway_bill_number?: string;
  vehicle_number?: string;
}

const DAMAGE_REASONS = ['Broken hinge', 'Scratched lens', 'Bent frame', 'Box crushed'];

const errMsg = (e: unknown) => getErrorMessage(e, 'Action failed');

// Each shipped piece of a line with where it stands: received / damaged /
// not scanned yet. Barcodes pair with stock ids only when every piece had one.
export function boxPieces(line: BoxLine) {
  const ids = line.shipped_stock_ids || [];
  const codes = line.shipped_barcodes || [];
  const received = new Set(line.received_stock_ids || []);
  const damaged = new Set(line.damaged_stock_ids || []);
  return ids.map((sid, n) => ({
    stockId: sid,
    barcode: codes.length === ids.length ? codes[n] : sid,
    state: damaged.has(sid) ? 'damaged' : received.has(sid) ? 'received' : 'waiting',
  }));
}

export function TransferBox({
  transfer,
  actor,
  onChanged,
  onDone,
}: {
  transfer: BoxTransfer;
  actor: gates.TransferActor;
  onChanged: (t: BoxTransfer) => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [damaged, setDamaged] = useState(false);
  const [reason, setReason] = useState('');
  const [courier, setCourier] = useState(transfer.courier_name || '');
  const [tracking, setTracking] = useState(transfer.tracking_number || '');
  const [eway, setEway] = useState(transfer.eway_bill_number || '');
  const [vehicle, setVehicle] = useState(transfer.vehicle_number || '');

  const packing = gates.canPack(actor, transfer);
  const receiving = gates.canReceive(actor, transfer);
  const packed = transfer.items.flatMap((l) => l.packed_barcodes || []);
  const pieces = transfer.items.flatMap((l) => boxPieces(l).map((p) => ({ ...p, line: l })));
  const inCount = pieces.filter((p) => p.state !== 'waiting').length;
  const damagedCount = pieces.filter((p) => p.state === 'damaged').length;
  const missingCount = pieces.length - inCount;

  const act = async (fn: () => Promise<{ transfer?: BoxTransfer; message?: string }>, ok?: string) => {
    setBusy(true);
    try {
      const res = await fn();
      if (res?.transfer) onChanged(res.transfer);
      if (ok || res?.message) toast.success(ok || String(res.message));
      return true;
    } catch (e) {
      toast.error(errMsg(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const pack = (codes: string[]) => act(() => inventoryApi.packTransfer(transfer.id, codes));

  const onScan = async () => {
    const c = code.trim();
    if (!c || busy) return;
    if (packing) {
      if (await pack([...packed, c])) setCode('');
      return;
    }
    if (damaged && !reason.trim()) {
      toast.error('Say what is wrong with the damaged piece.');
      return;
    }
    const done = await act(() =>
      inventoryApi.scanInPiece(transfer.id, { barcode: c, damaged, damageReason: reason.trim() }),
    );
    if (done) {
      setCode('');
      setDamaged(false);
      setReason('');
    }
  };

  const ship = () => {
    if (!courier.trim() || !tracking.trim()) {
      toast.error('Add the courier and the tracking no. before the box ships.');
      return;
    }
    return act(
      () =>
        inventoryApi.shipTransfer(transfer.id, {
          courierName: courier.trim(),
          trackingNumber: tracking.trim(),
        }),
      'Box shipped',
    );
  };

  const accept = async () => {
    if (await act(() => inventoryApi.completeTransfer(transfer.id), 'Box accepted')) onDone();
  };

  const scanBox = (packing || receiving) && (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <input
          autoFocus
          value={code}
          onChange={(e) => setCode(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onScan()}
          placeholder={packing ? 'Scan a piece out' : 'Scan a piece in'}
          className="input-field flex-1 min-w-[180px] font-mono"
          aria-label={packing ? 'Scan a piece out' : 'Scan a piece in'}
        />
        <button onClick={onScan} disabled={busy} className="btn-primary flex items-center gap-2">
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <ScanLine className="w-4 h-4" />}
          {packing ? 'Add to box' : 'Scan in'}
        </button>
      </div>
      {receiving && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={damaged} onChange={(e) => setDamaged(e.target.checked)} />
            Damaged in the box
          </label>
          {damaged && (
            <>
              <input
                list="transfer-damage-reasons"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="What is wrong?"
                className="input-field flex-1 min-w-[160px]"
                aria-label="What is wrong with the damaged piece"
              />
              <datalist id="transfer-damage-reasons">
                {DAMAGE_REASONS.map((r) => (
                  <option key={r} value={r} />
                ))}
              </datalist>
            </>
          )}
          <span className="text-xs text-gray-500 w-full">
            Damaged pieces go to Quarantine at {transfer.to_location_name}, not to the shelf.
          </span>
        </div>
      )}
    </div>
  );

  return (
    <div className="space-y-4">
      {scanBox}

      {packing || pieces.length === 0 ? (
        <div className="border border-gray-200 rounded-lg divide-y divide-gray-100">
          {transfer.items.map((line, i) => (
            <div key={line.id ?? i} className="p-3 text-sm">
              <div className="flex justify-between gap-2">
                <span className="font-medium text-gray-900">{line.product_name}</span>
                <span className="text-gray-600">
                  {(line.packed_barcodes || []).length} of {line.quantity_requested ?? 0} in the box
                </span>
              </div>
              <div className="flex flex-wrap gap-2 mt-2">
                {(line.packed_barcodes || []).map((bc) => (
                  <span
                    key={bc}
                    className="inline-flex items-center gap-1 px-2 py-1 rounded bg-gray-100 font-mono text-xs"
                  >
                    {bc}
                    {packing && (
                      <button
                        onClick={() => pack(packed.filter((x) => x !== bc))}
                        disabled={busy}
                        aria-label={`Take ${bc} out of the box`}
                        className="text-gray-500 hover:text-red-600"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    )}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="border border-gray-200 rounded-lg divide-y divide-gray-100">
          <div className="p-3 text-sm font-medium text-gray-900">
            {inCount} of {pieces.length} in
          </div>
          {pieces.map((p) => (
            <div key={p.stockId} className="p-3 flex flex-wrap justify-between gap-2 text-sm">
              <span className="text-gray-900">{p.line.product_name}</span>
              <span className="font-mono text-xs text-gray-600">{p.barcode}</span>
              <span
                className={
                  p.state === 'received'
                    ? 'text-green-700'
                    : p.state === 'damaged'
                    ? 'text-red-700'
                    : 'text-gray-500'
                }
              >
                {p.state === 'received' ? 'received' : p.state === 'damaged' ? 'received damaged' : 'not scanned yet'}
              </span>
            </div>
          ))}
        </div>
      )}

      {gates.isSourceSide(actor, transfer) && ['packed', 'in_transit'].includes(transfer.status) && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {transfer.status === 'packed' && (
            <>
              <input
                value={courier}
                onChange={(e) => setCourier(e.target.value)}
                placeholder="Courier"
                aria-label="Courier"
                className="input-field"
              />
              <input
                value={tracking}
                onChange={(e) => setTracking(e.target.value)}
                placeholder="Tracking no."
                aria-label="Tracking no."
                className="input-field"
              />
            </>
          )}
          <input
            value={eway}
            onChange={(e) => setEway(e.target.value)}
            placeholder="E-way bill no. (when the value needs one)"
            aria-label="E-way bill no."
            maxLength={20}
            className="input-field"
          />
          <input
            value={vehicle}
            onChange={(e) => setVehicle(e.target.value)}
            placeholder="Vehicle no."
            aria-label="Vehicle no."
            maxLength={20}
            className="input-field"
          />
          <button
            onClick={() =>
              act(
                () =>
                  inventoryApi.updateTransfer(transfer.id, {
                    eway_bill_number: eway.trim(),
                    vehicle_number: vehicle.trim(),
                  }),
                'Saved',
              )
            }
            disabled={busy}
            className="btn-outline sm:col-span-2"
          >
            Save e-way bill and vehicle no.
          </button>
        </div>
      )}

      <div className="flex flex-wrap justify-end gap-2">
        {gates.canShip(actor, transfer) && (
          <button onClick={ship} disabled={busy} className="btn-primary flex items-center gap-2">
            <Send className="w-4 h-4" />
            Ship {packed.length} pieces
          </button>
        )}
        {gates.canComplete(actor, transfer) && (
          <button onClick={accept} disabled={busy} className="btn-primary flex items-center gap-2">
            <CheckCircle className="w-4 h-4" />
            Accept {inCount}
            {damagedCount ? ` (${damagedCount} damaged)` : ''}
            {missingCount ? `, report ${missingCount} missing` : ''}
          </button>
        )}
      </div>
      {gates.canComplete(actor, transfer) && missingCount > 0 && (
        <p className="text-xs text-gray-500 text-right">
          A piece not scanned in goes on "Missing on the way" on the Transfers page.
        </p>
      )}
    </div>
  );
}
