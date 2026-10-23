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
import { useNavigate } from 'react-router-dom';
import { Plus, BarChart3, CheckCircle, Clock, Loader2, RefreshCw, Printer, Barcode } from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { COST_ROLES } from '../../components/common/CostCell';
import { useToast } from '../../context/ToastContext';
import { inventoryApi } from '../../services/api';
// Shop time: naive backend stamps are UTC (audit F53).
import { formatDateIST, formatDateTimeIST } from '../../utils/datetime';
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
  /** Coverage: how much of what the session EXPECTED was actually walked.
   *  A count of 1 product out of 400 used to complete as "everything
   *  matched" with a Rs 0 missing tile for a shelf nobody looked at. */
  products_expected?: number;
  products_counted?: number;
  products_missed?: number;
  coverage_percentage?: number;
  full_count?: boolean;
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
// The server sends these figures at cost to COST_ROLES only (owner ruling
// 2026-10-12); a figure it did not send reads as a dash, never as Rs 0.
const money = (n?: number) =>
  n == null ? '—' : `₹${Math.round(Math.abs(n)).toLocaleString('en-IN')}`;

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
  const { user, hasRole } = useAuth();
  // Money at cost (what we paid for the missing stock): COST_ROLES only.
  const showCost = hasRole(COST_ROLES);
  const toast = useToast();
  const navigate = useNavigate();

  const [audits, setAudits] = useState<StockAudit[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showNewAuditModal, setShowNewAuditModal] = useState(false);
  const [newCategory, setNewCategory] = useState('');
  const [newZone, setNewZone] = useState('');
  const [starting, setStarting] = useState(false);
  const [openSheet, setOpenSheet] = useState<string | null>(null);

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
        products_expected: c.products_expected,
        products_counted: c.products_counted,
        products_missed: c.products_missed,
        coverage_percentage: c.coverage_percentage,
        full_count: c.full_count,
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
      await inventoryApi.completeStockCount(countId);
      toast.success('Count finished.');
      navigate(`/inventory/audit/${countId}`);
    } catch (err: any) {
      // The server refuses a count with nothing counted ("nothing has been
      // counted"). Swallowing that into a generic failure hid the one message
      // the counter actually needs.
      toast.error(err?.response?.data?.detail || 'Failed to finish the stock count');
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
            {showCost ? money(missingValue) : missingUnits}
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
                    {audit.audit_number} · started {formatDateTimeIST(audit.created_at)}
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
                  <CheckCircle className="w-4 h-4" /> Finish count
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
                    role="link"
                    tabIndex={0}
                    onClick={() => navigate(`/inventory/audit/${audit.count_id}`)}
                    onKeyDown={(e) => e.key === 'Enter' && navigate(`/inventory/audit/${audit.count_id}`)}
                    className="card cursor-pointer transition-all hover:shadow-md"
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
                          {formatDateIST(audit.created_at)}
                        </p>
                      </div>
                      <div>
                        <p className="text-xs" style={{ color: 'var(--ink-4)' }}>Items counted</p>
                        <p className="font-medium" style={{ color: 'var(--ink)' }}>{audit.items_counted}</p>
                        {audit.products_expected != null && audit.status !== 'in_progress' && (
                          <p
                            className="text-xs"
                            style={{ color: audit.full_count ? 'var(--ok)' : 'var(--warn)' }}
                          >
                            {audit.products_counted} of {audit.products_expected} products
                            {audit.full_count ? ' · full count' : ' · part-counted'}
                          </p>
                        )}
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
