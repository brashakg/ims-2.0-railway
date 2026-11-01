// ============================================================================
// IMS 2.0 - Reorder (/inventory/reorders) - stock redesign S5
// ============================================================================
// ONE list replaces Buy Desk, Replenishment and Low stock (owner 2026-10-22/23;
// their old addresses forward here). Every number is the server's
// (GET /inventory/reorder-list): the suggested buy is THE buy rule -- the
// shop's level if set, else its last 30 days' sales, minus stock and SENT
// orders. Edit a number, untick what you don't want, press one button: one
// DRAFT purchase order per vendor and shop, through the one PO door
// (POST /vendors/purchase-orders, which prices the lines). Other shops'
// spare pieces shared out to a row are shown beside the buy: a transfer
// instead. A row with no vendor (none preferred, no past order) sits under
// "Choose vendor" until one is picked, as the retired screens asked.

import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Settings } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { COST_ROLES } from '../../components/common/CostCell';
import { ReorderPointModal, type ReorderPointData } from '../../components/inventory/ReorderPointModal';
import { reorderApi, vendorsApi } from '../../services/api';
import type { ReorderRow, ReorderSkipped } from '../../services/api/inventory';
import { getErrorMessage } from '../../utils/errorHandler';
import { userSeesAllStores } from '../../utils/storeAccess';
import { PURCHASE_ROLES } from '../purchase/purchaseRoles';
import { REORDER_LEVEL_ROLES } from './inventoryRoles';
import { useInventoryStores } from './inventoryQueries';
import { isOnlineStore } from './storeMode';

type Band = 'critical' | 'low' | 'all';

const keyOf = (r: { product_id: string; store_id: string }) => `${r.product_id}|${r.store_id}`;
const isCritical = (r: ReorderRow) => r.band === 'out-of-stock' || r.band === 'critical';
const money = (n: number) => `₹ ${Math.round(n).toLocaleString('en-IN')}`;

function runsOut(days: number | null) {
  if (days == null) return <span style={{ color: 'var(--ink-4)' }}>—</span>;
  if (days <= 0) return <span style={{ color: 'var(--err)' }}>out now</span>;
  if (days < 14) return `${days} day${days === 1 ? '' : 's'}`;
  return `${Math.round(days / 7)} weeks`;
}

function bandChip(r: ReorderRow) {
  if (isCritical(r)) return <span className="chip err">Critical</span>;
  if (r.band === 'low') return <span className="chip warn">Low</span>;
  return null;
}

/** What the catalogue still lacks, in words a person reads. */
const GAP_WORDS: Record<string, string> = {
  hsn_code: 'HSN', gst_rate: 'GST rate', mrp: 'MRP', offer_price: 'selling price',
  cost_price: 'cost', brand_name: 'brand', model_no: 'model number', colour_code: 'colour code',
  size: 'size', category: 'category', MRP_BELOW_OFFER: 'MRP below the selling price',
};

export function ReorderListPage() {
  const { user, hasRole } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const showCost = hasRole(COST_ROLES);
  const canDraft = hasRole(PURCHASE_ROLES);
  const canSetShopLevel = hasRole(REORDER_LEVEL_ROLES);
  const canEditProduct = hasRole(['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER']);
  const allShops = userSeesAllStores(user?.roles);

  // An admin starts on every shop; anyone else on their own shop.
  const [shop, setShop] = useState<string>(allShops ? '' : user?.activeStoreId || '');
  const storesQ = useInventoryStores(allShops, user?.storeIds);
  const shops = (storesQ.data ?? []).filter((s) => !isOnlineStore(s));

  const listQ = useQuery({
    queryKey: ['inventory', 'reorder-list', shop || 'all'] as const,
    queryFn: () => reorderApi.getList(shop || undefined),
  });
  const rows = useMemo(() => listQ.data?.rows ?? [], [listQ.data]);
  const skipped = listQ.data?.skipped ?? [];

  const [vendor, setVendor] = useState('');
  const [band, setBand] = useState<Band>('all');
  // What the person changed: a typed quantity, an unticked row. Untouched rows
  // follow the server (ticked when there is something to buy and no other
  // shop's spares cover it).
  const [qty, setQty] = useState<Record<string, number>>({});
  const [ticked, setTicked] = useState<Record<string, boolean>>({});
  // The vendor picked for a row the server has none for.
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [settingsFor, setSettingsFor] = useState<ReorderRow | ReorderSkipped | null>(null);

  const vendors = useMemo(() => {
    const seen = new Map<string, string>();
    rows.forEach((r) => r.vendor_id && seen.set(r.vendor_id, r.vendor_name || r.vendor_id));
    return [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [rows]);
  const byVendor = rows.filter((r) => !vendor || r.vendor_id === vendor);
  const counts = {
    critical: byVendor.filter(isCritical).length,
    low: byVendor.filter((r) => r.band === 'low').length,
    all: byVendor.length,
  };
  const inBand = byVendor.filter((r) =>
    band === 'all' ? true : band === 'critical' ? isCritical(r) : r.band === 'low',
  );
  const shown = inBand.filter((r) => r.vendor_id);
  const noVendor = inBand.filter((r) => !r.vendor_id);

  // Every vendor on file, for the "Choose vendor" pickers (only when needed).
  const vendorListQ = useQuery({
    queryKey: ['vendors', 'reorder-picker'] as const,
    queryFn: async () => {
      const resp = (await vendorsApi.getVendors({ limit: 1000 })) as
        | { vendors?: Array<Record<string, unknown>> }
        | Array<Record<string, unknown>>;
      const list = Array.isArray(resp) ? resp : resp?.vendors || [];
      return list
        .map((v) => ({ id: String(v.vendor_id || ''), name: String(v.trade_name || v.legal_name || v.vendor_id || '') }))
        .filter((v) => v.id)
        .sort((a, b) => a.name.localeCompare(b.name));
    },
    enabled: canDraft && rows.some((r) => !r.vendor_id),
  });
  const vendorName = (id: string) =>
    vendorListQ.data?.find((v) => v.id === id)?.name || vendors.find(([v]) => v === id)?.[1] || id;

  const qtyOf = (r: ReorderRow) => qty[keyOf(r)] ?? r.buy;
  const vendorOf = (r: ReorderRow) => r.vendor_id || picked[keyOf(r)] || '';
  // Untouched rows: ticked when there is something to buy that no transfer
  // and no draft made earlier already covers (it stays editable).
  const isTicked = (r: ReorderRow) =>
    ticked[keyOf(r)] ?? (r.buy > 0 && !r.transfer_instead && r.in_draft < r.buy);
  const chosen = [...shown, ...noVendor].filter((r) => isTicked(r) && qtyOf(r) > 0 && vendorOf(r));

  // One draft per vendor AND shop (an order is delivered to one shop).
  const groupMap = new Map<string, { vendor_id: string; store_id: string; label: string; lines: ReorderRow[] }>();
  for (const r of chosen) {
    const v = vendorOf(r);
    const k = `${v}|${r.store_id}`;
    if (!groupMap.has(k)) {
      groupMap.set(k, {
        vendor_id: v,
        store_id: r.store_id,
        label: `${r.vendor_id ? r.vendor_name || v : vendorName(v)}${shop ? '' : ` · ${r.store_name}`}`,
        lines: [],
      });
    }
    groupMap.get(k)!.lines.push(r);
  }
  const groups = [...groupMap.values()];

  const groupValue = (lines: ReorderRow[]) => {
    const priced = lines.filter((l) => l.unit_cost);
    return {
      value: priced.reduce((sum, l) => sum + (l.unit_cost ?? 0) * qtyOf(l), 0),
      unpriced: lines.length - priced.length,
    };
  };

  const makeDrafts = async () => {
    if (groups.length === 0) return;
    setBusy(true);
    let made = 0;
    try {
      // One vendor at a time; each made draft leaves the selection at once, so
      // pressing again after a failure never makes a second draft for it.
      for (const g of groups) {
        await vendorsApi.createPurchaseOrder({
          vendor_id: g.vendor_id,
          delivery_store_id: g.store_id,
          notes: 'Drafted from the Reorder list',
          items: g.lines.map((l) => ({
            product_id: l.product_id,
            product_name: l.name,
            sku: l.sku,
            quantity: qtyOf(l),
          })),
        });
        made += 1;
        setTicked((prev) => ({ ...prev, ...Object.fromEntries(g.lines.map((l) => [keyOf(l), false])) }));
      }
      toast.success(`${made} draft order${made === 1 ? '' : 's'} made. Check each one and press "Send to vendor".`);
      navigate('/purchase/orders');
    } catch (err) {
      toast.error(
        getErrorMessage(err, 'Could not make the draft order') +
          (made ? ` (${made} draft${made === 1 ? '' : 's'} already made; the rest are still ticked)` : ''),
      );
      void queryClient.invalidateQueries({ queryKey: ['inventory', 'reorder-list'] });
    } finally {
      setBusy(false);
    }
  };

  // The reorder settings: this shop's level (managers, admins) and the
  // product-wide order quantity / max stock / lead time (catalogue roles).
  // Two writes, each only for the roles the server lets make it.
  const saveSettings = async (data: ReorderPointData) => {
    if (!settingsFor) return;
    const failures: string[] = [];
    if (canSetShopLevel && data.reorderPoint !== settingsFor.level) {
      try {
        await reorderApi.setShopLevel(data.productId, settingsFor.store_id, data.reorderPoint);
      } catch (err) {
        failures.push(getErrorMessage(err, 'Could not save the reorder level'));
      }
    }
    // Only the product-wide fields the person changed (the modal sends no others).
    if (canEditProduct && Object.keys(data.product).length > 0) {
      try {
        await reorderApi.updateReorderSettings(data.productId, data.product);
      } catch (err) {
        failures.push(getErrorMessage(err, 'Could not save the product settings'));
      }
    }
    void queryClient.invalidateQueries({ queryKey: ['inventory', 'reorder-list'] });
    if (failures.length) throw new Error(failures.join('; '));
    toast.success('Reorder settings saved');
  };

  // One table for both groups; a row with no vendor gets a picker instead.
  const renderTable = (list: ReorderRow[], pick: boolean) => (
    <div className="overflow-x-auto">
      <table className="tbl" style={{ minWidth: 1080 }}>
        <thead>
          <tr>
            <th style={{ width: 32 }}><span className="sr-only">Include</span></th>
            <th>Product</th>
            <th>Vendor · shop</th>
            <th className="right">Shop level</th>
            <th>Basis</th>
            <th className="right">Sold 30 days</th>
            <th className="right">In stock</th>
            <th className="right">On order (sent)</th>
            <th className="right">Runs out in</th>
            <th style={{ textAlign: 'center' }}>Buy</th>
            {showCost && <th className="right">Est. value at cost</th>}
            <th><span className="sr-only">Settings</span></th>
          </tr>
        </thead>
        <tbody>
          {list.map((r) => {
            const k = keyOf(r);
            const n = qtyOf(r);
            return (
              <tr key={k}>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`Include ${r.name} at ${r.store_name}`}
                    checked={isTicked(r)}
                    onChange={(e) => setTicked((prev) => ({ ...prev, [k]: e.target.checked }))}
                  />
                </td>
                <td>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{r.name}</span>
                    {bandChip(r)}
                  </div>
                  {r.transfer_from.length > 0 && (
                    <div className="text-xs mt-1" style={{ color: 'var(--info)' }}>
                      {r.transfer_from.map((t) => `${t.store_name} can send ${t.qty}`).join(', ')}
                      {r.transfer_instead ? ' - transfer instead of buying. ' : '. '}
                      <Link to="/inventory/transfers">Start a transfer</Link>
                    </div>
                  )}
                </td>
                <td>
                  {pick ? (
                    canDraft ? (
                      <select
                        className="input-field"
                        value={picked[k] || ''}
                        aria-label={`Vendor for ${r.name} at ${r.store_name}`}
                        onChange={(e) => setPicked((prev) => ({ ...prev, [k]: e.target.value }))}
                      >
                        <option value="">Choose vendor…</option>
                        {(vendorListQ.data ?? []).map((v) => (
                          <option key={v.id} value={v.id}>{v.name}</option>
                        ))}
                      </select>
                    ) : (
                      <span style={{ color: 'var(--ink-4)' }}>no vendor yet</span>
                    )
                  ) : (
                    <>
                      {r.vendor_name || r.vendor_id}
                      {r.vendor_source === 'last order' && (
                        <span className="text-xs" style={{ color: 'var(--ink-4)' }}> (last order)</span>
                      )}
                    </>
                  )}
                  {' · '}{r.store_name}
                </td>
                <td className="right">
                  {r.level == null ? <span style={{ color: 'var(--ink-4)' }}>not set</span> : <span className="figure">{r.level}</span>}
                </td>
                <td><span className="chip">{r.basis === 'level' ? 'level' : '30-day sales'}</span></td>
                <td className="right figure">{r.sold_30d}</td>
                <td className="right figure" style={{ color: r.on_hand <= 0 ? 'var(--err)' : undefined }}>{r.on_hand}</td>
                <td className="right">
                  <span className="figure">{r.on_order}</span>
                  {r.in_draft > 0 && <div className="text-xs" style={{ color: 'var(--ink-4)' }}>+{r.in_draft} in draft</div>}
                </td>
                <td className="right">{runsOut(r.runs_out_days)}</td>
                <td style={{ textAlign: 'center' }}>
                  <input
                    type="number"
                    min={0}
                    step={1}
                    inputMode="numeric"
                    className="input-field figure"
                    style={{ width: 64, textAlign: 'center' }}
                    aria-label={`Quantity to buy of ${r.name} at ${r.store_name}`}
                    value={n}
                    onChange={(e) => {
                      const v = Math.max(0, Math.floor(Number(e.target.value) || 0));
                      setQty((prev) => ({ ...prev, [k]: v }));
                    }}
                  />
                </td>
                {showCost && (
                  <td className="right figure">
                    {r.unit_cost ? money(r.unit_cost * n) : <span style={{ color: 'var(--ink-4)' }}>no cost</span>}
                  </td>
                )}
                <td>
                  <button
                    type="button"
                    className="btn"
                    aria-label={`Reorder settings for ${r.name} at ${r.store_name}`}
                    onClick={() => setSettingsFor(r)}
                  >
                    <Settings className="w-4 h-4" />
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );

  const shopName = shop ? shops.find((s) => s.id === shop)?.name || shop : 'all shops';

  return (
    <div className="inv-body space-y-4">
      <div className="inv-head" style={{ alignItems: 'flex-end', flexWrap: 'wrap', gap: 16 }}>
        <div style={{ flex: '1 1 320px' }}>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Reorder · {shopName}</div>
          <h1>What to buy this week</h1>
          <p className="text-sm mt-1" style={{ color: 'var(--ink-4)' }}>
            One list replaces Buy Desk, Replenishment and Low stock. Edit the number, untick what you
            don't want, press one button.
          </p>
          {(listQ.data?.auto_reorder_off ?? 0) > 0 && (
            <span className="chip mt-2" title="Never suggested here until someone turns auto-reorder on">
              {listQ.data!.auto_reorder_off} product{listQ.data!.auto_reorder_off === 1 ? ' has' : 's have'} auto-reorder off
            </span>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <label className="flex flex-col gap-1">
            <span className="eyebrow">For shop</span>
            <select className="input-field" value={shop} onChange={(e) => setShop(e.target.value)} aria-label="For shop">
              {allShops && <option value="">All shops</option>}
              {shops.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="eyebrow">Vendor</span>
            <select className="input-field" value={vendor} onChange={(e) => setVendor(e.target.value)} aria-label="Vendor">
              <option value="">All vendors</option>
              {vendors.map(([id, name]) => (
                <option key={id} value={id}>{name}</option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <div className="seg" role="group" aria-label="Show" style={{ width: 'fit-content' }}>
        {(['critical', 'low', 'all'] as Band[]).map((b) => (
          <button key={b} type="button" className={band === b ? 'on' : ''} aria-pressed={band === b} onClick={() => setBand(b)}>
            {b === 'critical' ? 'Critical' : b === 'low' ? 'Low' : 'All'}{' '}
            <span className={`chip ${b === 'critical' ? 'err' : b === 'low' ? 'warn' : ''}`}>{counts[b]}</span>
          </button>
        ))}
      </div>

      <div className="card" style={{ overflow: 'hidden', padding: 0 }}>
        {listQ.isPending ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-7 h-7 animate-spin" style={{ color: 'var(--bv)' }} />
          </div>
        ) : listQ.isError ? (
          <p className="p-4 text-sm" role="alert" style={{ color: 'var(--err)' }}>
            Could not load the reorder list. Nothing is suggested until it loads - try again in a minute.
          </p>
        ) : shown.length === 0 && noVendor.length === 0 ? (
          <p className="p-6 text-sm text-center" style={{ color: 'var(--ink-4)' }}>
            Nothing to buy{vendor ? ' from this vendor' : ''} right now.
          </p>
        ) : (
          <>
            {shown.length > 0 && renderTable(shown, false)}
            {noVendor.length > 0 && (
              <section style={{ borderTop: '1px solid var(--line)' }}>
                <div className="flex flex-wrap items-center gap-2 px-4 py-3">
                  <span className="font-semibold">Choose vendor</span>
                  <span className="chip warn">{noVendor.length}</span>
                  <span className="text-xs" style={{ color: 'var(--ink-4)' }}>
                    no preferred vendor and no past order: pick who to buy from before drafting
                  </span>
                </div>
                {renderTable(noVendor, true)}
              </section>
            )}
          </>
        )}

        {skipped.length > 0 && (
          <div style={{ borderTop: '1px solid var(--line)', background: 'var(--bg-sunk)' }}>
            <div className="flex flex-wrap items-center gap-2 px-4 py-3">
              <span className="font-semibold">Not suggested</span>
              <span className="chip">{skipped.length} skipped</span>
              <span className="text-xs" style={{ color: 'var(--ink-4)' }}>these rules keep a product off the list</span>
            </div>
            {skipped.map((s) => (
              <div
                key={keyOf(s)}
                className="flex flex-wrap items-center gap-3 px-4 py-2"
                style={{ borderTop: '1px solid var(--line)' }}
              >
                <span style={{ flex: '1 1 240px' }}>
                  {s.name}
                  <span className="text-xs" style={{ color: 'var(--ink-4)' }}> · {s.store_name}</span>
                </span>
                {s.reason === 'catalogue incomplete' ? (
                  <>
                    <span className="chip err">
                      catalogue incomplete: can't purchase
                      {s.missing?.length ? ` · Missing: ${s.missing.map((m) => GAP_WORDS[m] || m.replace(/_/g, ' ')).join(', ')}` : ''}
                    </span>
                    {canEditProduct ? (
                      <Link to={`/catalog?focus=${encodeURIComponent(s.product_id)}`} className="text-sm">Fix in Catalogue</Link>
                    ) : (
                      <span className="text-xs" style={{ color: 'var(--ink-4)' }}>ask the catalogue manager</span>
                    )}
                  </>
                ) : s.reason === 'auto-reorder off' ? (
                  <>
                    <span className="chip">auto-reorder off</span>
                    {canEditProduct && (
                      <button type="button" className="btn" onClick={() => setSettingsFor(s)}>Turn on</button>
                    )}
                  </>
                ) : (
                  <span className="chip">{s.reason}</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-3">
        <div className="card p-4 space-y-2" style={{ flex: '999 1 520px', minWidth: 0 }}>
          <p className="font-semibold">How the suggested number is worked out</p>
          <p className="text-sm" style={{ color: 'var(--ink-3)', lineHeight: 1.6 }}>
            If the shop has a minimum level for the product, buy back to one above it (rounded up to the
            product's pack size, never above its max stock). If not, buy enough for the next 30 days at
            the last 30 days' sales speed. Stock in the shop and pieces on SENT orders are taken off; orders
            still in draft are shown but not taken off. "Runs out in" = stock ÷ daily sales. Other shops'
            spare pieces are shared out over the short shops, the emptiest first, and shown on the row.
            {showCost ? ' Value is at cost, never MRP.' : ''}
          </p>
        </div>
        <div className="card p-4 space-y-3" style={{ flex: '1 1 300px' }}>
          {groups.length === 0 ? (
            <p className="text-sm" style={{ color: 'var(--ink-4)' }}>Nothing ticked to order.</p>
          ) : (
            groups.map((g) => {
              const pieces = g.lines.reduce((sum, l) => sum + qtyOf(l), 0);
              const { value, unpriced } = groupValue(g.lines);
              return (
                <div key={`${g.vendor_id}|${g.store_id}`} className="flex justify-between gap-2 text-sm">
                  <span style={{ color: 'var(--ink-3)' }}>{g.label} · {pieces} piece{pieces === 1 ? '' : 's'}</span>
                  {showCost && (
                    <span className="figure">
                      {money(value)}
                      {unpriced > 0 && <span className="text-xs" style={{ color: 'var(--warn)' }}> +{unpriced} no cost</span>}
                    </span>
                  )}
                </div>
              );
            })
          )}
          {canDraft ? (
            <button
              type="button"
              className="btn primary lg"
              style={{ width: '100%' }}
              disabled={busy || groups.length === 0}
              onClick={makeDrafts}
            >
              {busy ? 'Making drafts…' : 'Make draft orders'}
            </button>
          ) : (
            <p className="text-xs" style={{ color: 'var(--ink-4)' }}>
              A manager or the accounts team makes the draft orders from this list.
            </p>
          )}
          <p className="text-xs" style={{ color: 'var(--ink-4)', lineHeight: 1.5 }}>
            Drafts land in Purchase › Purchase orders, one per vendor and shop. The store manager checks each
            one and presses "Send to vendor". No approval step.
          </p>
        </div>
      </div>

      {settingsFor && (
        <ReorderPointModal
          isOpen
          onClose={() => setSettingsFor(null)}
          product={{
            id: settingsFor.product_id,
            sku: settingsFor.sku,
            name: `${settingsFor.name} · ${settingsFor.store_name}`,
            brand: 'brand' in settingsFor ? settingsFor.brand : '',
            currentStock: 'on_hand' in settingsFor ? settingsFor.on_hand : 0,
            reorderPoint: settingsFor.level,
            stockStatus: 'band' in settingsFor ? settingsFor.band : undefined,
            reorderQuantity: settingsFor.reorder_quantity,
            maxStock: settingsFor.max_stock,
            leadTimeDays: settingsFor.lead_time_days,
            soldLast30Days: 'sold_30d' in settingsFor ? settingsFor.sold_30d : 0,
          }}
          onSave={saveSettings}
          productWideLocked={!canEditProduct}
          shopLevelLocked={!canSetShopLevel}
        />
      )}
    </div>
  );
}

export default ReorderListPage;
