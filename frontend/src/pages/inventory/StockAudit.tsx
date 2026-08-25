// ============================================================================
// IMS 2.0 - Stock Audit / Count Sheet  ·  v2 reskin (slice 2c)
// ============================================================================
// Physical stock count, variance analysis, shrinkage tracking. Reskinned to
// the v2 aesthetic (docs/design/inventory.html Cycle count tab): inv-body
// shell, stat-strip, count-banner for in-progress sessions, card/tbl
// primitives, sessions grouped by display zone/fixture (the fixture system
// from v2-2a/2b). Same backend wiring (inventoryApi.getStockCounts /
// startStockCount / completeStockCount). BV brand tokens only.

import { useState, useEffect, useMemo } from 'react';
import { Plus, BarChart3, CheckCircle, Clock, Loader2, RefreshCw, Printer, Barcode } from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { inventoryApi } from '../../services/api';
import { StockCountScanningInterface } from '../../components/inventory/StockCountScanningInterface';

interface StockAudit {
  count_id: string;
  audit_number: string;
  category: string;
  zone?: string;
  status: 'in_progress' | 'completed' | 'reconciling' | 'reconciled';
  created_at: string;
  created_by_name: string;
  items_counted: number;
  variance_percentage?: number;
  shrinkage_percentage?: number;
  shrinkage_units?: number;
  shrinkage_value?: number;
  overage_units?: number;
  overage_value?: number;
  lines_without_cost?: number;
  lines_moved_during_count?: number;
  units_voided?: number;
  lines_skipped_moved?: number;
  units_not_voided?: number;
  shrinkage_value_written_off?: number;
  reconciled_by?: string;
  reconciled_at?: string;
  variances?: AuditVariance[];
}

interface AuditVariance {
  product_id: string;
  product_name: string;
  sku: string;
  system_quantity: number;
  system_quantity_now?: number;
  physical_quantity: number;
  variance: number;
  variance_percentage: number;
  unit_cost?: number;
  variance_value?: number;
  /** Stock left or arrived while the session was open (sold at the till, a
   *  delivery received, a return taken back). The difference between the
   *  opening snapshot and the shelf is not a loss, so it is never written
   *  off and never counted into the rupee figure. */
  moved_during_count?: boolean;
}

// Rupees, Indian grouping. A count is only useful when the answer is money.
const money = (n?: number) =>
  `₹${Math.round(Math.abs(n || 0)).toLocaleString('en-IN')}`;

const statusChip = (status: string): string => {
  switch (status) {
    case 'in_progress':
      return 'info';
    case 'completed':
      return 'warn';
    case 'reconciled':
      return 'ok';
    default:
      return '';
  }
};

const statusLabel = (status: string): string => {
  switch (status) {
    case 'in_progress':
      return 'In progress';
    case 'completed':
      return 'Counted — awaiting write-off';
    case 'reconciling':
      return 'Writing off…';
    case 'reconciled':
      return 'Written off';
    default:
      return status;
  }
};

const getStatusIcon = (status: string) => {
  switch (status) {
    case 'in_progress':
      return <BarChart3 className="w-3.5 h-3.5" />;
    case 'reconciled':
      return <CheckCircle className="w-3.5 h-3.5" />;
    default:
      return <Clock className="w-3.5 h-3.5" />;
  }
};

// Label for the zone/fixture grouping header. Falls back to "Unzoned".
const zoneLabel = (zone?: string) => (zone && zone.trim() ? zone.trim() : 'Unzoned · whole store');

export function StockAudit() {
  const { user } = useAuth();
  const toast = useToast();

  const [audits, setAudits] = useState<StockAudit[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedAudit, setSelectedAudit] = useState<string | null>(null);
  const [showNewAuditModal, setShowNewAuditModal] = useState(false);
  const [newCategory, setNewCategory] = useState('');
  const [newZone, setNewZone] = useState('');
  const [starting, setStarting] = useState(false);
  const [openSheet, setOpenSheet] = useState<string | null>(null);
  const [writingOff, setWritingOff] = useState<string | null>(null);

  const storeId = user?.activeStoreId || '';

  useEffect(() => {
    if (storeId) loadAudits();
  }, [storeId]);

  const loadAudits = async () => {
    setIsLoading(true);
    try {
      const result = await inventoryApi.getStockCounts(storeId);
      const counts: StockAudit[] = (result?.counts || []).map((c: any) => ({
        count_id: c.count_id || c.id || '',
        audit_number: c.audit_number || '',
        category: c.category || 'All',
        zone: c.zone,
        status: c.status || 'in_progress',
        created_at: c.created_at || '',
        created_by_name: c.created_by_name || c.created_by || '',
        items_counted: c.items_counted || 0,
        variance_percentage: c.variance_percentage,
        shrinkage_percentage: c.shrinkage_percentage,
        shrinkage_units: c.shrinkage_units,
        shrinkage_value: c.shrinkage_value,
        overage_units: c.overage_units,
        overage_value: c.overage_value,
        lines_without_cost: c.lines_without_cost,
        lines_moved_during_count: c.lines_moved_during_count,
        units_voided: c.units_voided,
        lines_skipped_moved: c.lines_skipped_moved,
        units_not_voided: c.units_not_voided,
        shrinkage_value_written_off: c.shrinkage_value_written_off,
        reconciled_by: c.reconciled_by,
        reconciled_at: c.reconciled_at,
        variances: c.variances || [],
      }));
      setAudits(counts);
    } catch {
      toast.error('Failed to load stock counts');
    } finally {
      setIsLoading(false);
    }
  };

  const handleStartAudit = async () => {
    setStarting(true);
    try {
      await inventoryApi.startStockCount({
        category: newCategory || undefined,
        zone: newZone || undefined,
      });
      toast.success('Stock count started!');
      setShowNewAuditModal(false);
      setNewCategory('');
      setNewZone('');
      loadAudits();
    } catch {
      toast.error('Failed to start stock count');
    } finally {
      setStarting(false);
    }
  };

  const handleCompleteAudit = async (countId: string) => {
    try {
      const result = await inventoryApi.completeStockCount(countId);
      const short = result.shrinkage_units || 0;
      const over = result.overage_units || 0;
      const moved = result.lines_moved_during_count || 0;
      // Stock that moved while the session was open is not a loss, but the
      // counter has to know those lines were left out of the figures.
      const movedNote = moved
        ? ` ${moved} line${moved === 1 ? '' : 's'} moved during the count (sold or received) — count ${moved === 1 ? 'it' : 'those'} again.`
        : '';
      if (!short && !over) {
        toast.success(`Count complete — ${result.items_counted} lines, everything matched.${movedNote}`);
      } else {
        toast.success(
          `Count complete — ${short} short (${money(result.shrinkage_value)}), ` +
            `${over} over (${money(result.overage_value)}).${movedNote}`
        );
      }
      loadAudits();
    } catch (err: any) {
      // The server refuses a count with no lines recorded ("nothing has been
      // counted"). Swallowing that into a generic failure hid the one message
      // the counter actually needs.
      toast.error(err?.response?.data?.detail || 'Failed to complete stock count');
    }
  };

  // Owner ruling 2026-08-25 (#8): the write-off is ADMIN / SUPERADMIN only, at
  // every value. The server enforces it; hiding the button just avoids
  // offering a manager a door that will 403.
  const canWriteOff = (user?.roles || []).some((r) => r === 'ADMIN' || r === 'SUPERADMIN');

  const handleWriteOff = async (audit: StockAudit) => {
    const short = audit.shrinkage_units || 0;
    const ok = window.confirm(
      `Write off ${short} missing unit${short === 1 ? '' : 's'} (${money(audit.shrinkage_value)} at cost) ` +
        `found by ${audit.audit_number}?

` +
        'This removes them from stock permanently and records the loss. ' +
        'Anything sold, transferred or returned since the count will be left alone.'
    );
    if (!ok) return;
    setWritingOff(audit.count_id);
    try {
      const result = await inventoryApi.reconcileStockCount(audit.count_id);
      const left = result.units_not_voided || 0;
      const skipped = result.lines_skipped_moved || 0;
      toast.success(
        `Wrote off ${result.units_voided || 0} unit(s), ${money(result.shrinkage_value_written_off)}.` +
          (left ? ` ${left} could not be written off (moved since the count) - count again.` : '') +
          (skipped
            ? ` ${skipped} line${skipped === 1 ? '' : 's'} left alone — that stock moved while the count was open.`
            : '')
      );
      loadAudits();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Could not write off the missing stock');
    } finally {
      setWritingOff(null);
    }
  };

  const completedAudits = audits.filter((a) => a.status === 'completed');
  const reconciledAudits = audits.filter((a) => a.status === 'reconciled');
  const inProgressAudits = audits.filter((a) => a.status === 'in_progress');

  // What this store is missing, in rupees, across every count that has been
  // completed. The shrinkage record used to be read by nothing, anywhere.
  const missingValue = audits.reduce((sum, a) => sum + (a.shrinkage_value || 0), 0);
  const missingUnits = audits.reduce((sum, a) => sum + (a.shrinkage_units || 0), 0);
  const awaitingWriteOff = completedAudits.reduce((sum, a) => sum + (a.shrinkage_units || 0), 0);

  // Group completed/idle sessions by zone (display-fixture system). The
  // count sheet "groups by fixture instead of by shelf range" per the v2
  // design — zone is the closest field the count API carries.
  const zoneGroups = useMemo(() => {
    const groups = new Map<string, StockAudit[]>();
    for (const a of audits) {
      if (a.status === 'in_progress') continue; // surfaced as banners above
      const key = zoneLabel(a.zone);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(a);
    }
    return Array.from(groups.entries());
  }, [audits]);

  return (
    <div className="inv-body">
      {/* Header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Inventory · audit</div>
          <h1>Count the floor.</h1>
          <p className="text-sm mt-1" style={{ color: 'var(--ink-4)' }}>
            Physical stock count and variance analysis, grouped by display zone.
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={loadAudits} disabled={isLoading} className="btn">
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Refresh
          </button>
          <button onClick={() => setShowNewAuditModal(true)} className="btn accent">
            <Plus className="w-4 h-4" />
            New stock count
          </button>
        </div>
      </div>

      {/* Summary stat strip */}
      <div className="stat-strip">
        <div>
          <div className="l">Total counts</div>
          <div className="v">{audits.length}</div>
          <div className="d">this store</div>
        </div>
        <div>
          <div className="l">In progress</div>
          <div className="v" style={{ color: 'var(--info)' }}>{inProgressAudits.length}</div>
          <div className="d">open sessions</div>
        </div>
        <div>
          <div className="l">Written off</div>
          <div className="v" style={{ color: 'var(--ok)' }}>{reconciledAudits.length}</div>
          <div className="d good">corrected in stock</div>
        </div>
        <div>
          <div className="l">Stock missing</div>
          <div className="v" style={{ color: missingUnits > 0 ? 'var(--err)' : 'var(--ink)' }}>
            {money(missingValue)}
          </div>
          <div className="d">
            {missingUnits} unit{missingUnits === 1 ? '' : 's'} found short
            {awaitingWriteOff > 0 ? ` · ${awaitingWriteOff} awaiting write-off` : ''}
          </div>
        </div>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="card flex items-center justify-center py-12">
          <Loader2 className="w-7 h-7 animate-spin" style={{ color: 'var(--bv)' }} />
        </div>
      )}

      {/* In-progress count banners */}
      {!isLoading && inProgressAudits.length > 0 && (
        <div className="space-y-3 mb-5">
          {inProgressAudits.map((audit) => (
            <div key={audit.count_id}>
              <div className="count-banner">
                <div className="icn">C</div>
                <div>
                  <div className="t">
                    Cycle count in progress
                    {audit.zone ? ` · ${audit.zone}` : ''}
                    {audit.category && audit.category !== 'All' ? ` · ${audit.category}` : ''}
                  </div>
                  <div className="s">
                    {audit.audit_number} · started {audit.created_at ? new Date(audit.created_at).toLocaleString('en-IN') : '—'}
                    {audit.created_by_name ? ` by ${audit.created_by_name}` : ''} · {audit.items_counted} SKUs counted
                  </div>
                </div>
                <span className="flex-1" />
                <button
                  className={clsx('btn', openSheet !== audit.count_id && 'accent')}
                  onClick={() => setOpenSheet(openSheet === audit.count_id ? null : audit.count_id)}
                >
                  <Barcode className="w-4 h-4" />
                  {openSheet === audit.count_id ? 'Hide count sheet' : 'Count stock'}
                </button>
                <button className="btn" onClick={() => window.print()}>
                  <Printer className="w-4 h-4" /> Print
                </button>
                <button className="btn" onClick={() => handleCompleteAudit(audit.count_id)}>
                  <CheckCircle className="w-4 h-4" /> Complete count
                </button>
              </div>

              {/* The step that did not exist: record what is actually on the
                  shelf, line by line, onto this session. */}
              {openSheet === audit.count_id && (
                <div className="mt-3">
                  <StockCountScanningInterface countId={audit.count_id} onRecorded={loadAudits} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Empty state */}
      {!isLoading && audits.length === 0 && (
        <div className="card text-center py-12">
          <BarChart3 className="w-10 h-10 mx-auto mb-3" style={{ color: 'var(--ink-5)' }} />
          <p className="font-medium" style={{ color: 'var(--ink-3)' }}>No stock counts yet</p>
          <p className="text-sm mt-1" style={{ color: 'var(--ink-5)' }}>
            Start a new physical stock count to track inventory accuracy.
          </p>
        </div>
      )}

      {/* Session list grouped by display zone / fixture */}
      {!isLoading && zoneGroups.length > 0 && (
        <div className="space-y-5">
          {zoneGroups.map(([zone, list]) => (
            <div key={zone}>
              {/* Zone section header strip (count sheet groups by fixture/zone) */}
              <div className="fl-floor-head" style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
                <span className="ttl" style={{ font: '600 13px/1 var(--font-sans)', color: 'var(--ink)' }}>{zone}</span>
                <span className="meta" style={{ font: '500 10.5px/1 var(--font-mono)', color: 'var(--ink-4)', textTransform: 'uppercase', letterSpacing: '.08em' }}>
                  {list.length} session{list.length > 1 ? 's' : ''}
                </span>
                <span style={{ flex: 1, height: 1, background: 'var(--line)' }} />
              </div>

              <div className="space-y-3">
                {list.map((audit) => (
                  <div
                    key={audit.count_id}
                    onClick={() => setSelectedAudit(selectedAudit === audit.count_id ? null : audit.count_id)}
                    className={clsx('card cursor-pointer transition-all', selectedAudit === audit.count_id ? 'ring-2' : 'hover:shadow-md')}
                    style={selectedAudit === audit.count_id ? { boxShadow: '0 0 0 2px var(--bv)' } : undefined}
                  >
                    <div className="flex items-start justify-between mb-3">
                      <div>
                        <p className="font-semibold mono" style={{ color: 'var(--ink)' }}>{audit.audit_number}</p>
                        <p className="text-sm" style={{ color: 'var(--ink-4)' }}>
                          {audit.category || 'All categories'}
                          {audit.zone && ` · ${audit.zone}`}
                        </p>
                      </div>
                      <span className={clsx('chip', statusChip(audit.status))}>
                        {getStatusIcon(audit.status)}
                        {statusLabel(audit.status)}
                      </span>
                    </div>

                    <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4 text-sm">
                      <div>
                        <p className="text-xs" style={{ color: 'var(--ink-4)' }}>Created</p>
                        <p className="font-medium" style={{ color: 'var(--ink)' }}>
                          {audit.created_at ? new Date(audit.created_at).toLocaleDateString('en-IN') : '—'}
                        </p>
                      </div>
                      <div>
                        <p className="text-xs" style={{ color: 'var(--ink-4)' }}>Items counted</p>
                        <p className="font-medium" style={{ color: 'var(--ink)' }}>{audit.items_counted}</p>
                      </div>
                      <div>
                        <p className="text-xs" style={{ color: 'var(--ink-4)' }}>By</p>
                        <p className="font-medium" style={{ color: 'var(--ink)' }}>{audit.created_by_name || '—'}</p>
                      </div>
                      {audit.status === 'completed' && (
                        <div>
                          <p className="text-xs" style={{ color: 'var(--ink-4)' }}>Variance</p>
                          <p
                            className="font-medium"
                            style={{ color: Math.abs(audit.variance_percentage || 0) > 5 ? 'var(--err)' : 'var(--ok)' }}
                          >
                            {audit.variance_percentage?.toFixed(2)}%
                          </p>
                        </div>
                      )}
                    </div>

                    {/* Expanded detail */}
                    {selectedAudit === audit.count_id && audit.status !== 'in_progress' && (
                      <div
                        className="mt-4 pt-4 space-y-3"
                        style={{ borderTop: '1px solid var(--line)' }}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <div className="grid grid-cols-2 gap-3">
                          <div className="rounded-lg p-3" style={{ background: 'var(--bg-sunk)' }}>
                            <p className="text-xs" style={{ color: 'var(--ink-4)' }}>Missing (short)</p>
                            <p
                              className="font-bold text-lg"
                              style={{ color: (audit.shrinkage_units || 0) > 0 ? 'var(--err)' : 'var(--ok)' }}
                            >
                              {audit.shrinkage_units || 0} unit{(audit.shrinkage_units || 0) === 1 ? '' : 's'} · {money(audit.shrinkage_value)}
                            </p>
                            <p className="text-xs" style={{ color: 'var(--ink-4)' }}>at cost price</p>
                          </div>
                          <div className="rounded-lg p-3" style={{ background: 'var(--bg-sunk)' }}>
                            <p className="text-xs" style={{ color: 'var(--ink-4)' }}>Extra (over)</p>
                            <p
                              className="font-bold text-lg"
                              style={{ color: (audit.overage_units || 0) > 0 ? 'var(--warn)' : 'var(--ok)' }}
                            >
                              {audit.overage_units || 0} unit{(audit.overage_units || 0) === 1 ? '' : 's'} · {money(audit.overage_value)}
                            </p>
                            <p className="text-xs" style={{ color: 'var(--ink-4)' }}>never added to stock automatically</p>
                          </div>
                        </div>

                        {(audit.lines_without_cost || 0) > 0 && (
                          <p className="text-sm" style={{ color: 'var(--warn)' }}>
                            {audit.lines_without_cost} line{(audit.lines_without_cost || 0) === 1 ? ' has' : 's have'} no
                            cost price on the product, so the rupee figure above is lower than the real loss.
                          </p>
                        )}

                        {/* The write-off. Before this it had no button anywhere in
                            the app, so nothing a count found was ever corrected. */}
                        {audit.status === 'completed' && (audit.shrinkage_units || 0) > 0 && (
                          canWriteOff ? (
                            <button
                              className="btn accent"
                              disabled={writingOff === audit.count_id}
                              onClick={() => handleWriteOff(audit)}
                            >
                              {writingOff === audit.count_id ? (
                                <Loader2 className="w-4 h-4 animate-spin" />
                              ) : (
                                <CheckCircle className="w-4 h-4" />
                              )}
                              Write off {audit.shrinkage_units} missing unit
                              {(audit.shrinkage_units || 0) === 1 ? '' : 's'} ({money(audit.shrinkage_value)})
                            </button>
                          ) : (
                            <p className="text-sm" style={{ color: 'var(--ink-4)' }}>
                              An admin has to write this off — counting is yours, removing stock from the books is not.
                            </p>
                          )
                        )}

                        {audit.status === 'reconciled' && (
                          <p className="text-sm" style={{ color: 'var(--ink-3)' }}>
                            Written off: {audit.units_voided || 0} unit{(audit.units_voided || 0) === 1 ? '' : 's'} ·{' '}
                            {money(audit.shrinkage_value_written_off)}
                            {(audit.units_not_voided || 0) > 0 && (
                              <span style={{ color: 'var(--warn)' }}>
                                {' '}· {audit.units_not_voided} left alone (moved during the count) — count those again.
                              </span>
                            )}
                            {audit.reconciled_at ? ` · ${new Date(audit.reconciled_at).toLocaleString('en-IN')}` : ''}
                          </p>
                        )}

                        {audit.variances && audit.variances.filter((v) => v.variance !== 0 || v.moved_during_count).length > 0 ? (
                          <div className="overflow-x-auto">
                          <table className="tbl">
                            <thead>
                              <tr>
                                <th>Product</th>
                                <th className="right">Expected</th>
                                <th className="right">Counted</th>
                                <th className="right">Δ</th>
                                <th className="right">Value</th>
                              </tr>
                            </thead>
                            <tbody>
                              {audit.variances
                                .filter((v) => v.variance !== 0 || v.moved_during_count)
                                .map((v, i) => (
                                  <tr key={v.product_id || i}>
                                    <td>
                                      <span className="font-medium" style={{ color: 'var(--ink)' }}>{v.product_name || v.sku}</span>
                                      {v.moved_during_count && (
                                        <span className="block text-xs" style={{ color: 'var(--warn)' }}>
                                          Books now say {v.system_quantity_now} — stock moved while the count was open
                                        </span>
                                      )}
                                    </td>
                                    <td className="right mono">{v.system_quantity}</td>
                                    <td className="right mono">{v.physical_quantity}</td>
                                    <td className="right">
                                      {/* A line whose stock moved mid-count is not a
                                          shortage: nothing is written off and nothing
                                          is added to the rupee figure. */}
                                      <span
                                        className={clsx('chip', v.moved_during_count ? 'warn' : v.variance < 0 ? 'err' : 'ok')}
                                      >
                                        {v.moved_during_count
                                          ? 'Moved — count again'
                                          : v.variance > 0
                                            ? `+${v.variance}`
                                            : v.variance}
                                      </span>
                                    </td>
                                    <td
                                      className="right mono"
                                      style={{ color: v.moved_during_count ? 'var(--ink-4)' : v.variance < 0 ? 'var(--err)' : 'var(--ink-3)' }}
                                    >
                                      {v.moved_during_count ? '—' : v.unit_cost ? money(v.variance_value) : '—'}
                                    </td>
                                  </tr>
                                ))}
                            </tbody>
                          </table>
                          </div>
                        ) : (
                          <p className="text-sm italic" style={{ color: 'var(--ink-4)' }}>
                            Every counted line matched the books.
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* New count modal */}
      {showNewAuditModal && (
        <div className="fixed inset-0 flex items-center justify-center z-50 p-4" style={{ background: 'rgba(20,20,19,0.45)' }}>
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-md">
            <div className="p-6">
              <h3 className="text-lg font-bold mb-4" style={{ color: 'var(--ink)' }}>Start new stock count</h3>

              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium mb-1" style={{ color: 'var(--ink-2)' }}>Category (optional)</label>
                  <select value={newCategory} onChange={(e) => setNewCategory(e.target.value)} className="input w-full">
                    {/* Values are the CANONICAL spine categories (what products
                        store) — legacy plurals (FRAMES/SUNGLASSES) never matched
                        any product. */}
                    <option value="">All categories</option>
                    <option value="FRAME">Frames</option>
                    <option value="SUNGLASS">Sunglasses</option>
                    <option value="OPTICAL_LENS">Rx Lenses</option>
                    <option value="CONTACT_LENS">Contact Lenses</option>
                    <option value="WATCH">Watches</option>
                    <option value="ACCESSORIES">Accessories</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1" style={{ color: 'var(--ink-2)' }}>Zone / fixture (optional)</label>
                  <input
                    type="text"
                    value={newZone}
                    onChange={(e) => setNewZone(e.target.value)}
                    placeholder="e.g. W-01 Wall, Counter C-01, CL fridge"
                    className="input w-full"
                  />
                  <p className="text-xs mt-1" style={{ color: 'var(--ink-4)' }}>
                    Scope the count to one display fixture for a focused count sheet.
                  </p>
                </div>
              </div>

              <div className="mt-6 flex gap-3">
                <button onClick={() => setShowNewAuditModal(false)} className="btn flex-1">
                  Cancel
                </button>
                <button onClick={handleStartAudit} disabled={starting} className="btn accent flex-1">
                  {starting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                  Start count
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default StockAudit;
