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
import { Plus, BarChart3, CheckCircle, Clock, Loader2, RefreshCw, Printer } from 'lucide-react';
import clsx from 'clsx';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { inventoryApi } from '../../services/api';

interface StockAudit {
  count_id: string;
  audit_number: string;
  category: string;
  zone?: string;
  status: 'in_progress' | 'completed';
  created_at: string;
  created_by_name: string;
  items_counted: number;
  variance_percentage?: number;
  shrinkage_percentage?: number;
  variances?: AuditVariance[];
}

interface AuditVariance {
  product_id: string;
  product_name: string;
  sku: string;
  system_quantity: number;
  physical_quantity: number;
  variance: number;
  variance_percentage: number;
}

const statusChip = (status: string): string => {
  switch (status) {
    case 'in_progress':
      return 'info';
    case 'completed':
      return 'ok';
    default:
      return '';
  }
};

const getStatusIcon = (status: string) => {
  switch (status) {
    case 'in_progress':
      return <BarChart3 className="w-3.5 h-3.5" />;
    case 'completed':
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
      toast.success(`Stock count completed! Variance: ${result.variance_percentage || 0}%`);
      loadAudits();
    } catch {
      toast.error('Failed to complete stock count');
    }
  };

  const completedAudits = audits.filter((a) => a.status === 'completed');
  const inProgressAudits = audits.filter((a) => a.status === 'in_progress');

  const avgShrinkage =
    completedAudits.length > 0
      ? (
          completedAudits.reduce((sum, a) => sum + (a.shrinkage_percentage || 0), 0) /
          completedAudits.length
        ).toFixed(2)
      : '0.00';

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
          <div className="l">Completed</div>
          <div className="v" style={{ color: 'var(--ok)' }}>{completedAudits.length}</div>
          <div className="d good">signed off</div>
        </div>
        <div>
          <div className="l">Avg shrinkage</div>
          <div className="v" style={{ color: Number(avgShrinkage) > 1 ? 'var(--warn)' : 'var(--ink)' }}>
            {avgShrinkage}%
          </div>
          <div className="d">{Number(avgShrinkage) > 1 ? 'above tolerance' : 'within tolerance'}</div>
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
            <div key={audit.count_id} className="count-banner">
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
              <button className="btn" onClick={() => window.print()}>
                <Printer className="w-4 h-4" /> Count sheet
              </button>
              <button className="btn accent" onClick={() => handleCompleteAudit(audit.count_id)}>
                <CheckCircle className="w-4 h-4" /> Complete count
              </button>
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
                        {audit.status === 'in_progress' ? 'In progress' : 'Completed'}
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
                    {selectedAudit === audit.count_id && audit.status === 'completed' && (
                      <div className="mt-4 pt-4 space-y-3" style={{ borderTop: '1px solid var(--line)' }}>
                        <div className="grid grid-cols-2 gap-3">
                          <div className="rounded-lg p-3" style={{ background: 'var(--bg-sunk)' }}>
                            <p className="text-xs" style={{ color: 'var(--ink-4)' }}>Overall variance</p>
                            <p
                              className="font-bold text-lg"
                              style={{ color: Math.abs(audit.variance_percentage || 0) > 5 ? 'var(--err)' : 'var(--ok)' }}
                            >
                              {audit.variance_percentage?.toFixed(2)}%
                            </p>
                          </div>
                          <div className="rounded-lg p-3" style={{ background: 'var(--bg-sunk)' }}>
                            <p className="text-xs" style={{ color: 'var(--ink-4)' }}>Shrinkage</p>
                            <p
                              className="font-bold text-lg"
                              style={{ color: (audit.shrinkage_percentage || 0) > 1 ? 'var(--warn)' : 'var(--ok)' }}
                            >
                              {audit.shrinkage_percentage?.toFixed(2)}%
                            </p>
                          </div>
                        </div>

                        {audit.variances && audit.variances.filter((v) => v.variance !== 0).length > 0 ? (
                          <div className="overflow-x-auto">
                          <table className="tbl">
                            <thead>
                              <tr>
                                <th>Product</th>
                                <th className="right">System</th>
                                <th className="right">Counted</th>
                                <th className="right">Δ</th>
                              </tr>
                            </thead>
                            <tbody>
                              {audit.variances
                                .filter((v) => v.variance !== 0)
                                .map((v, i) => (
                                  <tr key={v.product_id || i}>
                                    <td>
                                      <span className="font-medium" style={{ color: 'var(--ink)' }}>{v.product_name || v.sku}</span>
                                    </td>
                                    <td className="right mono">{v.system_quantity}</td>
                                    <td className="right mono">{v.physical_quantity}</td>
                                    <td className="right">
                                      <span className={clsx('chip', v.variance < 0 ? 'err' : 'ok')}>
                                        {v.variance > 0 ? `+${v.variance}` : v.variance}
                                      </span>
                                    </td>
                                  </tr>
                                ))}
                            </tbody>
                          </table>
                          </div>
                        ) : (
                          <p className="text-sm italic" style={{ color: 'var(--ink-4)' }}>
                            No variances found — perfect match!
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
                    <option value="">All categories</option>
                    <option value="FRAMES">Frames</option>
                    <option value="SUNGLASSES">Sunglasses</option>
                    <option value="RX_LENSES">Rx Lenses</option>
                    <option value="CONTACT_LENSES">Contact Lenses</option>
                    <option value="WRIST_WATCHES">Watches</option>
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
