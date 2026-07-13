// ============================================================================
// IMS 2.0 — Customer Loyalty Ledger page
// ============================================================================
// Full transaction history for one customer, with paginated table + tier
// summary card. Linked from a customer's detail panel and from the
// /customers/loyalty admin dashboard.
//
// Read-only view. Adjustments are SUPERADMIN-gated and live on the
// customer detail panel; this page is the audit trail.

import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Award, ArrowLeft, RefreshCw, Filter } from 'lucide-react';

import {
  loyaltyApi,
  type LoyaltyAccountResponse,
  type LoyaltyLedgerResponse,
  type LoyaltyTier,
  type LoyaltyTxnType,
} from '../../services/api/loyalty';

const TIER_TOKENS: Record<LoyaltyTier, string> = {
  BRONZE: 'bg-amber-50 text-amber-700 ring-amber-200',
  SILVER: 'bg-gray-100 text-gray-700 ring-gray-200',
  GOLD: 'bg-yellow-50 text-yellow-700 ring-yellow-200',
  PLATINUM: 'bg-blue-50 text-blue-700 ring-blue-200',
};

const TYPE_TOKENS: Record<LoyaltyTxnType, string> = {
  EARN: 'bg-green-50 text-green-700',
  REDEEM: 'bg-blue-50 text-blue-700',
  EXPIRE: 'bg-amber-50 text-amber-700',
  ADJUST: 'bg-gray-100 text-gray-700',
};

const PAGE_SIZE = 25;

export default function LoyaltyLedger() {
  const { customerId } = useParams<{ customerId: string }>();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();

  const [account, setAccount] = useState<LoyaltyAccountResponse | null>(null);
  const [ledger, setLedger] = useState<LoyaltyLedgerResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const typeFilter = (params.get('type') as LoyaltyTxnType | null) || null;
  const skip = parseInt(params.get('skip') || '0', 10);

  const refresh = () => {
    if (!customerId) return;
    setLoading(true);
    setError(null);
    Promise.all([
      loyaltyApi.getAccount(customerId),
      loyaltyApi.getLedger(customerId, {
        limit: PAGE_SIZE,
        skip,
        type: typeFilter ?? undefined,
      }),
    ])
      .then(([acc, led]) => {
        setAccount(acc);
        setLedger(led);
      })
      .catch((err) => {
        setError(err?.message || 'Failed to load loyalty ledger');
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customerId, typeFilter, skip]);

  const totalPages = useMemo(() => {
    if (!ledger) return 1;
    return Math.max(1, Math.ceil(ledger.total / PAGE_SIZE));
  }, [ledger]);
  const currentPage = Math.floor(skip / PAGE_SIZE) + 1;

  const tier: LoyaltyTier = (account?.account?.tier ?? 'BRONZE') as LoyaltyTier;

  const setFilter = (next: LoyaltyTxnType | null) => {
    const newParams = new URLSearchParams(params);
    if (next) newParams.set('type', next); else newParams.delete('type');
    newParams.set('skip', '0');
    setParams(newParams);
  };

  const setSkip = (next: number) => {
    const newParams = new URLSearchParams(params);
    newParams.set('skip', String(Math.max(0, next)));
    setParams(newParams);
  };

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      {/* Top bar */}
      <div className="flex items-center justify-between">
        <button
          onClick={() => navigate(-1)}
          className="inline-flex items-center gap-1 text-sm text-gray-700 hover:text-gray-900"
        >
          <ArrowLeft className="w-4 h-4" /> Back
        </button>
        <button
          onClick={refresh}
          className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg border border-gray-200 text-sm text-gray-700 bg-white hover:bg-gray-50"
        >
          <RefreshCw className="w-4 h-4" /> Refresh
        </button>
      </div>

      {/* Account summary card */}
      <div className="bg-white border border-gray-200 rounded-xl p-5 shadow-sm">
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2 text-gray-700">
              <Award className="w-5 h-5 text-bv-red-600" />
              <span className="text-sm font-medium">Loyalty Account</span>
            </div>
            <h1 className="text-2xl font-semibold text-gray-900 mt-1">
              {customerId}
            </h1>
          </div>
          <span
            className={`inline-flex px-3 py-1 rounded-full text-xs font-semibold ring-1 ring-inset ${TIER_TOKENS[tier]}`}
          >
            {tier}
          </span>
        </div>

        {account && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
            <Stat label="Current balance" value={account.account.balance_points} accent />
            <Stat label="Lifetime earned" value={account.account.lifetime_earned} />
            <Stat label="Lifetime redeemed" value={account.account.lifetime_redeemed} />
            <Stat label="Expiring < 30 days" value={account.expiring_soon_points ?? 0} amber />
          </div>
        )}
      </div>

      {/* Filter bar */}
      <div className="flex items-center gap-2 flex-wrap">
        <Filter className="w-4 h-4 text-gray-500" />
        <FilterChip label="All" active={!typeFilter} onClick={() => setFilter(null)} />
        <FilterChip label="Earn" active={typeFilter === 'EARN'} onClick={() => setFilter('EARN')} />
        <FilterChip label="Redeem" active={typeFilter === 'REDEEM'} onClick={() => setFilter('REDEEM')} />
        <FilterChip label="Expire" active={typeFilter === 'EXPIRE'} onClick={() => setFilter('EXPIRE')} />
        <FilterChip label="Adjust" active={typeFilter === 'ADJUST'} onClick={() => setFilter('ADJUST')} />
      </div>

      {/* Ledger table */}
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="text-left py-2.5 px-4 font-semibold">When</th>
              <th className="text-left py-2.5 px-4 font-semibold">Type</th>
              <th className="text-right py-2.5 px-4 font-semibold">Points</th>
              <th className="text-right py-2.5 px-4 font-semibold">Rupees</th>
              <th className="text-left py-2.5 px-4 font-semibold">Reason</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={5} className="py-10 px-4 text-center text-gray-500">
                  Loading…
                </td>
              </tr>
            )}
            {!loading && error && (
              <tr>
                <td colSpan={5} className="py-10 px-4 text-center text-red-600">
                  {error}
                </td>
              </tr>
            )}
            {!loading && !error && ledger && ledger.items.length === 0 && (
              <tr>
                <td colSpan={5} className="py-10 px-4 text-center text-gray-500">
                  No loyalty activity yet.
                </td>
              </tr>
            )}
            {!loading &&
              !error &&
              ledger?.items.map((row) => (
                <tr key={row.txn_id} className="border-t border-gray-100">
                  <td className="py-2.5 px-4 text-gray-700">
                    {row.created_at
                      ? new Date(row.created_at).toLocaleString('en-IN')
                      : '-'}
                  </td>
                  <td className="py-2.5 px-4">
                    <span
                      className={`inline-flex px-2 py-0.5 rounded text-xs font-semibold ${TYPE_TOKENS[row.type]}`}
                    >
                      {row.type}
                    </span>
                  </td>
                  <td
                    className={`py-2.5 px-4 text-right font-semibold ${
                      row.type === 'EARN' || (row.type === 'ADJUST' && row.points > 0)
                        ? 'text-green-700'
                        : 'text-gray-900'
                    }`}
                  >
                    {row.type === 'REDEEM' || row.type === 'EXPIRE'
                      ? `−${row.points.toLocaleString('en-IN')}`
                      : `+${row.points.toLocaleString('en-IN')}`}
                  </td>
                  <td className="py-2.5 px-4 text-right text-gray-700">
                    {row.rupee_value
                      ? `₹${row.rupee_value.toLocaleString('en-IN')}`
                      : '-'}
                  </td>
                  <td className="py-2.5 px-4 text-gray-700">{row.reason}</td>
                </tr>
              ))}
          </tbody>
        </table>

        {/* Pagination */}
        {!loading && ledger && ledger.total > PAGE_SIZE && (
          <div className="flex items-center justify-between px-4 py-3 bg-gray-50 border-t border-gray-100 text-sm text-gray-700">
            <span>
              Page {currentPage} of {totalPages} · {ledger.total} entries
            </span>
            <div className="flex gap-2">
              <button
                disabled={skip === 0}
                onClick={() => setSkip(skip - PAGE_SIZE)}
                className="px-3 py-1 rounded border border-gray-200 bg-white disabled:opacity-50"
              >
                Previous
              </button>
              <button
                disabled={skip + PAGE_SIZE >= ledger.total}
                onClick={() => setSkip(skip + PAGE_SIZE)}
                className="px-3 py-1 rounded border border-gray-200 bg-white disabled:opacity-50"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================================
// Bits
// ============================================================================

interface StatProps {
  label: string;
  value: number;
  accent?: boolean;
  amber?: boolean;
}

function Stat({ label, value, accent, amber }: StatProps) {
  return (
    <div className="bg-gray-50 rounded-lg p-3">
      <p className="text-xs text-gray-500">{label}</p>
      <p
        className={`text-xl font-semibold ${
          accent ? 'text-bv-red-700' : amber ? 'text-amber-700' : 'text-gray-900'
        }`}
      >
        {value.toLocaleString('en-IN')}
      </p>
    </div>
  );
}

interface FilterChipProps {
  label: string;
  active: boolean;
  onClick: () => void;
}

function FilterChip({ label, active, onClick }: FilterChipProps) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1 rounded-full text-xs font-medium border ${
        active
          ? 'bg-bv-red-600 text-white border-bv-red-600'
          : 'bg-white text-gray-700 border-gray-200 hover:bg-gray-50'
      }`}
    >
      {label}
    </button>
  );
}
