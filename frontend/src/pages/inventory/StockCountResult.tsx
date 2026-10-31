// ============================================================================
// IMS 2.0 - Stock count result  (/inventory/audit/:countId)
// ============================================================================
// Owner ruling S1. After Finish -- and only then -- the count shows what did
// not match: coverage (styles counted vs in scope, "part-counted"), styles
// whose stock moved during the count ("count again"), the EXACT missing
// pieces by unit barcode with value at cost, and the extra pieces (never
// added to stock automatically).
//
// Write-offs are ADMIN / SUPERADMIN only at every value (owner ruling
// 2026-08-25). A manager SENDS the count to the admin instead. The server
// enforces both; this screen only offers each person their own door.

import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { CheckCircle, Loader2, Send } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { COST_ROLES } from '../../components/common/CostCell';
import { useToast } from '../../context/ToastContext';
import { inventoryApi } from '../../services/api';
import { formatDateIST, formatDateTimeIST } from '../../utils/datetime';
import { getErrorMessage } from '../../utils/errorHandler';

interface MissingPiece {
  unit_id: string;
  barcode: string;
  arrived_at?: string;
  unit_cost?: number;
}

interface ExtraPiece {
  barcode: string;
  recorded_store_id?: string;
  recorded_status?: string;
}

interface ResultLine {
  product_id: string;
  product_name: string;
  sku: string;
  system_quantity_now?: number;
  moved_during_count?: boolean;
  missing_units?: MissingPiece[];
  missing_unidentified?: number;
  extra_units?: ExtraPiece[];
  reserved_not_checked?: MissingPiece[];
  added_during_count?: Array<MissingPiece & { scanned?: boolean }>;
  typed_quantity?: number | null;
}

/** A style a whole-scope count found NOTHING of (owner ruling M6). */
interface NoneFoundStyle {
  product_id: string;
  product_name: string;
  sku: string;
  moved_during_count?: boolean;
  missing_units?: MissingPiece[];
  reserved_not_checked?: MissingPiece[];
}

// Rupees, Indian grouping. A figure the server did not send (cost is masked
// for roles outside COST_ROLES) reads as a dash, never as Rs 0.
const money = (n?: number) => (n == null ? '—' : `₹${Math.round(Math.abs(n)).toLocaleString('en-IN')}`);

export function StockCountResult() {
  const { countId = '' } = useParams();
  const { user, hasRole } = useAuth();
  const showCost = hasRole(COST_ROLES);
  const canWriteOff = (user?.roles || []).some((r) => r === 'ADMIN' || r === 'SUPERADMIN');
  const toast = useToast();
  const [doc, setDoc] = useState<any>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setDoc(await inventoryApi.getStockCount(countId));
    } catch {
      setFailed(true);
    }
  }, [countId]);

  useEffect(() => {
    load();
  }, [load]);

  if (failed) {
    return (
      <div className="inv-body">
        <div className="card p-6">
          <h1 className="text-lg font-semibold">Count not found</h1>
          <Link to="/inventory/audit" className="text-sm">Back to stock counts</Link>
        </div>
      </div>
    );
  }
  if (!doc) {
    return (
      <div className="inv-body">
        <div className="card flex items-center justify-center py-12">
          <Loader2 className="w-7 h-7 animate-spin" style={{ color: 'var(--bv)' }} />
        </div>
      </div>
    );
  }
  if (doc.status === 'in_progress') {
    // Blind until Finish: nothing below exists yet.
    return (
      <div className="inv-body">
        <div className="card p-6 space-y-2">
          <h1 className="text-lg font-semibold">This count is still open</h1>
          <p className="text-sm" style={{ color: 'var(--ink-4)' }}>
            The result appears after Finish. Nobody sees expected numbers before that.
          </p>
          <Link to="/inventory/audit" className="btn">Back to the count</Link>
        </div>
      </div>
    );
  }

  const lines: ResultLine[] = doc.variances || [];
  const missing = lines.flatMap((l) => (l.missing_units || []).map((m) => ({ ...m, line: l })));
  const unidentified = lines.filter((l) => (l.missing_unidentified || 0) > 0);
  const extras = lines.flatMap((l) => (l.extra_units || []).map((x) => ({ ...x, line: l })));
  const moved = lines.filter((l) => l.moved_during_count);
  const notCounted: Array<{ product_id: string; product_name: string; sku: string }> = doc.not_counted || [];
  const reserved = lines.flatMap((l) => (l.reserved_not_checked || []).map((m) => ({ ...m, line: l })));
  const added = lines.flatMap((l) => (l.added_during_count || []).map((m) => ({ ...m, line: l })));
  const noneFound: NoneFoundStyle[] = (doc.none_found || []).filter((n: NoneFoundStyle) => (n.missing_units || []).length > 0);
  const noneFoundPieces = noneFound.reduce((s, n) => s + (n.missing_units || []).length, 0);
  // A zone is a label, not a scope: a zone count never writes off (B1).
  const zoneCount = doc.write_off_allowed === false;
  const missingValue = showCost ? missing.reduce((s, m) => s + (m.unit_cost || 0), 0) : undefined;
  const noCost = missing.some((m) => !m.unit_cost);
  const request = doc.write_off_request;

  const writeOff = async (includeNoneFound = false) => {
    if (missing.length > 0 && !window.confirm(
      `Write off ${missing.length} piece${missing.length === 1 ? '' : 's'}? ` +
        'Only pieces that have not moved since this count are written off; any that were sold or moved drop out.'
    )) return;
    // The second, explicit confirmation for styles nobody found any of.
    // Round 2: the coverage is in the question (the server also refuses
    // below the SUPERADMIN-set floor, default 90%); the value only for the
    // cost roles.
    const noneFoundValue = noneFound.reduce(
      (s, n) => s + (n.missing_units || []).reduce((t, m) => t + (m.unit_cost || 0), 0), 0);
    if (includeNoneFound && !window.confirm(
      `Counted ${doc.products_counted ?? 0} of ${doc.products_expected ?? 0} styles. ` +
        `Write off ${noneFoundPieces} piece${noneFoundPieces === 1 ? '' : 's'} of ` +
        `${noneFound.length} style${noneFound.length === 1 ? '' : 's'}` +
        `${showCost ? ` worth ${money(noneFoundValue)} at cost` : ''}?`
    )) return;
    setBusy(true);
    try {
      const r = await inventoryApi.reconcileStockCount(countId, undefined, includeNoneFound);
      const left = r.units_not_voided || 0;
      toast.success(
        `Wrote off ${r.units_voided || 0} piece(s), ${money(r.shrinkage_value_written_off)}.` +
          (left ? ` ${left} had moved since the count and were left alone.` : '')
      );
      load();
    } catch (err: any) {
      toast.error(getErrorMessage(err, 'Could not write off'));
    } finally {
      setBusy(false);
    }
  };

  const finishStuck = async () => {
    setBusy(true);
    try {
      const r = await inventoryApi.finishStuckWriteOff(countId);
      toast.success(`Finished: ${r.units_voided || 0} piece(s) had already been written off. Nothing more was touched.`);
      load();
    } catch (err: any) {
      toast.error(getErrorMessage(err, 'Could not finish the stuck write-off'));
    } finally {
      setBusy(false);
    }
  };

  const sendForWriteOff = async () => {
    setBusy(true);
    try {
      await inventoryApi.requestStockCountWriteOff(countId);
      toast.success('Sent to the admin for write-off.');
      load();
    } catch (err: any) {
      toast.error(getErrorMessage(err, 'Could not send for write-off'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="inv-body space-y-4">
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>
            <Link to="/inventory/audit">Stock count</Link> · Result · {doc.audit_number}
          </div>
          <h1>Count finished: here is what didn't match</h1>
          <p className="text-sm mt-1" style={{ color: 'var(--ink-4)' }}>
            {doc.zone ? `${doc.zone} · ` : ''}
            {doc.completed_at ? `Finished ${formatDateTimeIST(doc.completed_at)}. ` : ''}
            Nothing changes in stock until the admin decides.
          </p>
        </div>
      </div>

      <div className="stat-strip">
        <div><div className="l">Scanned</div><div className="v">{doc.pieces_scanned ?? 0}</div></div>
        <div><div className="l">Missing</div><div className="v" style={{ color: missing.length ? 'var(--err)' : 'var(--ink)' }}>{missing.length}</div></div>
        <div><div className="l">Extra</div><div className="v">{extras.length}</div></div>
      </div>

      {/* Coverage: a part-counted shelf is never a clean one. */}
      <div className="card p-4 space-y-2">
        <p className="font-semibold">
          Coverage{' '}
          {doc.products_expected == null ? (
            <span style={{ color: 'var(--warn)' }}>unknown — the expected styles could not be read when this count opened</span>
          ) : (
            <span style={{ color: doc.full_count ? 'var(--ok)' : 'var(--warn)' }}>
              {doc.products_counted} of {doc.products_expected} styles counted
              {doc.full_count ? '' : `, ${doc.products_missed} not counted (part-counted)`}
            </span>
          )}
        </p>
        {notCounted.length > 0 && (
          <ul className="text-sm space-y-1">
            {notCounted.map((n) => (
              <li key={n.product_id}>{n.product_name} <span style={{ color: 'var(--ink-4)' }}>{n.sku}</span> · not counted</li>
            ))}
          </ul>
        )}
      </div>

      {moved.length > 0 && (
        <div className="card p-4 space-y-2">
          <p className="font-semibold">Moved during the count: count again</p>
          <ul className="text-sm space-y-1">
            {moved.map((l) => (
              <li key={l.product_id}>
                {l.product_name} — stock was sold, received or moved while the count was open (books now say {l.system_quantity_now})
              </li>
            ))}
          </ul>
          <p className="text-xs" style={{ color: 'var(--ink-4)' }}>These styles are left out of the missing list until they are counted again.</p>
        </div>
      )}

      <div className="card p-4 space-y-2">
        <p className="font-semibold">Missing: the exact pieces not found ({missing.length})</p>
        {missing.length === 0 ? (
          <p className="text-sm" style={{ color: 'var(--ink-4)' }}>No piece was named missing.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="tbl">
              <thead>
                <tr>
                  <th>Barcode</th>
                  <th>Product</th>
                  <th>Arrived</th>
                  {showCost && <th className="right">Value at cost</th>}
                </tr>
              </thead>
              <tbody>
                {missing.map((m) => (
                  <tr key={m.unit_id}>
                    <td className="mono">{m.barcode}</td>
                    <td>{m.line.product_name}</td>
                    <td>{m.arrived_at ? formatDateIST(m.arrived_at) : '—'}</td>
                    {showCost && <td className="right mono">{money(m.unit_cost)}</td>}
                  </tr>
                ))}
                {showCost && (
                  <tr>
                    <td colSpan={3} className="font-semibold">Total</td>
                    <td className="right mono font-semibold">{money(missingValue)}</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
        {showCost && noCost && (
          <p className="text-sm" style={{ color: 'var(--warn)' }}>Some pieces have no cost, so the rupee figure is too low.</p>
        )}
        {unidentified.map((l) => (
          <p key={l.product_id} className="text-sm" style={{ color: 'var(--warn)' }}>
            {l.product_name}: {l.missing_unidentified} short, but a quantity was typed, so which pieces cannot be
            told. Scan this style to name them; it cannot be written off piece by piece until then.
          </p>
        ))}
      </div>

      {zoneCount && (
        <div className="card p-4">
          <p className="font-semibold" style={{ color: 'var(--warn)' }}>
            Zone count: recount the whole shop or category to write off.
          </p>
          <p className="text-sm" style={{ color: 'var(--ink-4)' }}>
            A zone is a label on the count; the system expects the whole shop's pieces, so pieces kept
            elsewhere would show as missing here. Nothing from this count can be written off.
          </p>
        </div>
      )}

      {reserved.length > 0 && (
        <div className="card p-4 space-y-1">
          <p className="font-semibold">Reserved, not checked ({reserved.length})</p>
          <p className="text-xs" style={{ color: 'var(--ink-4)' }}>Held for a customer order - may be in the order drawer. Never written off from a count.</p>
          {reserved.map((m) => (
            <p key={m.unit_id} className="text-sm"><span className="mono">{m.barcode}</span> {m.line.product_name}</p>
          ))}
        </div>
      )}

      {added.length > 0 && (
        <div className="card p-4 space-y-1">
          <p className="font-semibold">Added during the count ({added.length})</p>
          <p className="text-xs" style={{ color: 'var(--ink-4)' }}>Received while the count was open - not counted for or against this count.</p>
          {added.map((m) => (
            <p key={m.unit_id} className="text-sm"><span className="mono">{m.barcode}</span> {m.line.product_name}{m.scanned ? ' · scanned' : ''}</p>
          ))}
        </div>
      )}

      {noneFound.length > 0 && (
        <div className="card p-4 space-y-2">
          <p className="font-semibold">None found ({noneFound.length} style{noneFound.length === 1 ? '' : 's'}, {noneFoundPieces} pieces)</p>
          <p className="text-xs" style={{ color: 'var(--ink-4)' }}>Nobody scanned or typed any piece of these styles.</p>
          <ul className="text-sm space-y-1">
            {noneFound.map((n) => (
              <li key={n.product_id}>
                {n.product_name} <span style={{ color: 'var(--ink-4)' }}>{n.sku}</span> ·{' '}
                {(n.missing_units || []).map((m) => m.barcode).join(', ')}
              </li>
            ))}
          </ul>
        </div>
      )}

      {extras.length > 0 && (
        <div className="card p-4 space-y-2">
          <p className="font-semibold">Extra: scanned here, not on our list ({extras.length})</p>
          <ul className="text-sm space-y-1">
            {extras.map((x) => (
              <li key={x.barcode}>
                <span className="mono">{x.barcode}</span> {x.line.product_name}
                {x.recorded_store_id && x.recorded_store_id !== doc.store_id
                  ? ` · our records say it is at ${x.recorded_store_id}`
                  : x.recorded_status
                    ? ` · our records say ${x.recorded_status.toLowerCase()}`
                    : ''}
                {' · never added to stock automatically'}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Each person's own door. */}
      <div className="card p-4 space-y-2">
        {doc.status === 'completed' && !zoneCount && missing.length + noneFoundPieces > 0 && (
          canWriteOff ? (
            <>
              {request && (
                <p className="text-sm">Sent for write-off by {request.requested_by_name || request.requested_by}
                  {request.requested_at ? `, ${formatDateTimeIST(request.requested_at)}` : ''}.</p>
              )}
              {missing.length > 0 && (
                <button className="btn accent" disabled={busy} onClick={() => writeOff(false)}>
                  {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle className="w-4 h-4" />}
                  Write off these {missing.length} piece{missing.length === 1 ? '' : 's'}
                </button>
              )}
              {noneFoundPieces > 0 && (
                <button className="btn" disabled={busy} onClick={() => writeOff(true)}>
                  Write off these {missing.length} and the {noneFoundPieces} pieces of styles with nothing found
                </button>
              )}
              <p className="text-xs" style={{ color: 'var(--ink-4)' }}>
                Only pieces that have not moved since this count are written off. If one was sold or moved since, it drops out and you are told.
              </p>
            </>
          ) : request ? (
            <p className="text-sm">
              Sent to admin for write-off{request.requested_at ? ` on ${formatDateTimeIST(request.requested_at)}` : ''}. The admin decides.
            </p>
          ) : (
            <>
              <button className="btn accent" disabled={busy} onClick={sendForWriteOff}>
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                Send {missing.length + noneFoundPieces} piece{missing.length + noneFoundPieces === 1 ? '' : 's'} to admin for write-off
              </button>
              <p className="text-xs" style={{ color: 'var(--ink-4)' }}>
                Managers can't write off. The admin sees these barcodes; only pieces that have not moved since this count can be written off.
              </p>
            </>
          )
        )}
        {doc.status === 'reconciling' && (
          canWriteOff ? (
            <button className="btn" disabled={busy} onClick={finishStuck}>Finish this stuck write-off</button>
          ) : (
            <p className="text-sm">This write-off stopped part-way — an admin has to finish it.</p>
          )
        )}
        {doc.status === 'reconciled' && (
          <p className="text-sm">
            Written off: {doc.units_voided || 0} piece{(doc.units_voided || 0) === 1 ? '' : 's'} · {money(doc.shrinkage_value_written_off)}
            {(doc.units_not_voided || 0) > 0 ? ` · ${doc.units_not_voided} left alone (moved since the count)` : ''}
            {doc.reconciled_at ? ` · ${formatDateTimeIST(doc.reconciled_at)}` : ''}
          </p>
        )}
        <Link to="/inventory/audit" className="btn">Count history</Link>
      </div>
    </div>
  );
}

export default StockCountResult;
