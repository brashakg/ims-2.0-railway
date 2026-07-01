// ============================================================================
// IMS 2.0 - Online Store - Store health  (BVI Phase 5)
// ============================================================================
// The pre-cutover readiness dashboard behind the Online Store shell's "Store
// health" card (SECTIONS[8]). READ-ONLY. It answers one question: before any
// product goes live online, is the catalog ready?
//
//   - a composite readiness score (green / amber / red gauge),
//   - per-check status cards: orphan SKUs, attribute coverage, barcode match,
//   - a "fixes needed" list with counts (largest first).
//
// Reads: GET /api/v1/online-store/store-health (via onlineStoreApi.getStoreHealth,
// which NEVER throws — a stale-deploy 404 / non-gated 403 resolves to a zeroed,
// "not available" envelope so the screen always renders). Gated
// SUPERADMIN / ADMIN / CATALOG_MANAGER / DESIGN_MANAGER at the route (App.tsx),
// matching the rest of the module. Light theme only.

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity,
  ArrowLeft,
  RefreshCw,
  Loader2,
  Unlink,
  ClipboardList,
  ScanBarcode,
  ListChecks,
  CheckCircle2,
  AlertTriangle,
} from 'lucide-react';
import { onlineStoreApi, type StoreHealth } from '../../services/api/onlineStore';

// ---------------------------------------------------------------------------
// Small presentation helpers
// ---------------------------------------------------------------------------

type Tone = 'green' | 'amber' | 'red';

/** Map a 0-100 score to a traffic-light tone (>=85 green, >=60 amber, else red). */
function toneFor(pct: number): Tone {
  if (pct >= 85) return 'green';
  if (pct >= 60) return 'amber';
  return 'red';
}

const TONE_TEXT: Record<Tone, string> = {
  green: 'text-green-700',
  amber: 'text-amber-700',
  red: 'text-red-700',
};
const TONE_RING: Record<Tone, string> = {
  green: 'text-green-500',
  amber: 'text-amber-500',
  red: 'text-red-500',
};
const TONE_BAR: Record<Tone, string> = {
  green: 'bg-green-500',
  amber: 'bg-amber-500',
  red: 'bg-red-500',
};
const TONE_PILL: Record<Tone, string> = {
  green: 'bg-green-100 text-green-800 border-green-200',
  amber: 'bg-amber-100 text-amber-800 border-amber-200',
  red: 'bg-red-100 text-red-800 border-red-200',
};

function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  try {
    return n.toLocaleString('en-IN');
  } catch {
    return String(n);
  }
}

function fmtPct(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  return `${Math.round(n * 10) / 10}%`;
}

// ---------------------------------------------------------------------------
// Readiness gauge (SVG donut)
// ---------------------------------------------------------------------------

function ReadinessGauge({ pct }: { pct: number }) {
  const tone = toneFor(pct);
  const clamped = Math.max(0, Math.min(100, pct));
  const r = 52;
  const c = 2 * Math.PI * r;
  const dash = (clamped / 100) * c;
  return (
    <div className="relative w-32 h-32 shrink-0">
      <svg viewBox="0 0 120 120" className="w-full h-full -rotate-90">
        <circle cx="60" cy="60" r={r} fill="none" stroke="currentColor" strokeWidth="12" className="text-gray-100" />
        <circle
          cx="60"
          cy="60"
          r={r}
          fill="none"
          stroke="currentColor"
          strokeWidth="12"
          strokeLinecap="round"
          strokeDasharray={`${dash} ${c - dash}`}
          className={TONE_RING[tone]}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className={'text-2xl font-semibold ' + TONE_TEXT[tone]}>{Math.round(clamped)}</span>
        <span className="text-[11px] text-gray-400">/ 100</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// A per-check status card (icon, headline %, sub-stats, tone pill)
// ---------------------------------------------------------------------------

function CheckCard({
  icon: Icon,
  title,
  pct,
  goodLabel,
  detail,
}: {
  icon: typeof Activity;
  title: string;
  pct: number;
  goodLabel: string;
  detail: React.ReactNode;
}) {
  const tone = toneFor(pct);
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 flex flex-col">
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center justify-center w-8 h-8 rounded-lg bg-gray-100 text-gray-700">
            <Icon className="w-4 h-4" />
          </span>
          <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
        </div>
        <span
          className={
            'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap ' +
            TONE_PILL[tone]
          }
        >
          {tone === 'green' ? <CheckCircle2 className="w-3 h-3" /> : <AlertTriangle className="w-3 h-3" />}
          {tone === 'green' ? goodLabel : 'Needs work'}
        </span>
      </div>
      <div className="flex items-baseline gap-2 mb-2">
        <span className={'text-2xl font-semibold ' + TONE_TEXT[tone]}>{fmtPct(pct)}</span>
      </div>
      <div className="h-2 w-full rounded-full bg-gray-100 overflow-hidden mb-3">
        <div className={'h-full rounded-full ' + TONE_BAR[tone]} style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
      </div>
      <div className="text-xs text-gray-500 space-y-1 mt-auto">{detail}</div>
    </div>
  );
}

// ===========================================================================
// Page
// ===========================================================================
export default function OnlineStoreHealthPage() {
  const [health, setHealth] = useState<StoreHealth | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const h = await onlineStoreApi.getStoreHealth();
      setHealth(h);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const readiness = health?.readiness_pct ?? 0;
  const readinessTone = toneFor(readiness);
  const orphanFree = health?.sub_scores?.orphan_free_pct ?? 0;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Header + breadcrumb */}
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        <div>
          <div className="flex items-center gap-2 text-xs text-gray-500 mb-1">
            <Link to="/online-store" className="inline-flex items-center gap-1 hover:text-gray-700">
              <ArrowLeft className="w-3.5 h-3.5" /> Online Store
            </Link>
            <span>/</span>
            <span className="text-gray-700">Store health</span>
          </div>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            <Activity className="w-5 h-5" /> Store health
          </h1>
        </div>
        <button
          type="button"
          onClick={load}
          className="btn-outline inline-flex items-center gap-1.5 text-sm"
          title="Reload"
        >
          <RefreshCw className={'w-4 h-4 ' + (loading ? 'animate-spin' : '')} /> Refresh
        </button>
      </div>
      <p className="text-sm text-gray-500 mb-4 max-w-3xl">
        The readiness checks before any product goes live online — orphan SKUs, attribute coverage and
        barcode-match status. Fix what is flagged here first; a higher score means a cleaner, safer
        Shopify cutover. This is a read-only view.
      </p>

      {loading ? (
        <div className="rounded-xl border border-gray-200 bg-white p-6 flex items-center gap-2 text-sm text-gray-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading store health…
        </div>
      ) : (
        <>
          {/* Not-available note (stale backend / outside the gate) */}
          {health && !health.available && (
            <div className="mb-4 rounded-xl border border-blue-200 bg-blue-50 p-3 text-xs text-blue-800">
              The store-health backend isn’t reachable yet — showing zeros. Live readiness appears once
              the module backend is deployed.
            </div>
          )}

          {/* Readiness summary */}
          <div className="mb-6 rounded-xl border border-gray-200 bg-white p-5 flex flex-wrap items-center gap-6">
            <ReadinessGauge pct={readiness} />
            <div className="flex-1 min-w-[220px]">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-sm font-semibold text-gray-900">Overall readiness</span>
                <span
                  className={
                    'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ' +
                    TONE_PILL[readinessTone]
                  }
                >
                  {readinessTone === 'green' ? 'Ready' : readinessTone === 'amber' ? 'Almost' : 'Not ready'}
                </span>
              </div>
              <p className="text-xs text-gray-500 mb-3">
                {fmtInt(health?.total_products)} online-eligible product
                {(health?.total_products ?? 0) === 1 ? '' : 's'} assessed. The score is an equal blend of
                attribute coverage, barcode match, and the orphan-free rate.
              </p>
              <div className="grid grid-cols-3 gap-3 max-w-md">
                {[
                  { label: 'Coverage', pct: health?.sub_scores?.coverage_pct ?? 0 },
                  { label: 'Barcode', pct: health?.sub_scores?.barcode_pct ?? 0 },
                  { label: 'Orphan-free', pct: orphanFree },
                ].map((s) => {
                  const t = toneFor(s.pct);
                  return (
                    <div key={s.label}>
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[11px] text-gray-500">{s.label}</span>
                        <span className={'text-[11px] font-medium ' + TONE_TEXT[t]}>{fmtPct(s.pct)}</span>
                      </div>
                      <div className="h-1.5 w-full rounded-full bg-gray-100 overflow-hidden">
                        <div className={'h-full rounded-full ' + TONE_BAR[t]} style={{ width: `${Math.max(0, Math.min(100, s.pct))}%` }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Per-check cards */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 mb-6">
            {/* Orphan SKUs — score is the orphan-free rate. */}
            <CheckCard
              icon={Unlink}
              title="Orphan SKUs"
              pct={orphanFree}
              goodLabel="No orphans"
              detail={
                <>
                  <div className="flex justify-between">
                    <span>Orphaned products</span>
                    <span className="font-medium text-gray-700">{fmtInt(health?.orphans.orphan_count)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>No Shopify mapping</span>
                    <span className="text-gray-600">{fmtInt(health?.orphans.no_mapping)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Not in a collection</span>
                    <span className="text-gray-600">{fmtInt(health?.orphans.not_in_collection)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Missing spine link</span>
                    <span className="text-gray-600">{fmtInt(health?.orphans.missing_spine)}</span>
                  </div>
                </>
              }
            />

            {/* Attribute coverage — score is the overall coverage %. */}
            <CheckCard
              icon={ClipboardList}
              title="Attribute coverage"
              pct={health?.coverage.overall_pct ?? 0}
              goodLabel="Well covered"
              detail={
                <>
                  {(
                    [
                      ['HSN code', health?.coverage.hsn_pct],
                      ['Category', health?.coverage.category_pct],
                      ['Brand', health?.coverage.brand_pct],
                      ['Barcode', health?.coverage.barcode_pct],
                      ['Image', health?.coverage.image_pct],
                    ] as [string, number | undefined][]
                  ).map(([label, pct]) => (
                    <div key={label} className="flex justify-between">
                      <span>{label}</span>
                      <span className={'font-medium ' + TONE_TEXT[toneFor(pct ?? 0)]}>{fmtPct(pct)}</span>
                    </div>
                  ))}
                </>
              }
            />

            {/* Barcode match — present AND unique. */}
            <CheckCard
              icon={ScanBarcode}
              title="Barcode match"
              pct={health?.barcode_match_pct ?? 0}
              goodLabel="All matched"
              detail={
                <>
                  <div className="flex justify-between">
                    <span>With a barcode</span>
                    <span className="text-gray-600">{fmtInt(health?.barcode_match.with_barcode)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Missing barcode</span>
                    <span className="text-gray-600">{fmtInt(health?.barcode_match.missing_barcode)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Duplicate barcode</span>
                    <span className="text-gray-600">{fmtInt(health?.barcode_match.duplicate_barcode)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Unique + matched</span>
                    <span className="font-medium text-gray-700">{fmtInt(health?.barcode_match.unique_matched)}</span>
                  </div>
                </>
              }
            />
          </div>

          {/* Fixes needed */}
          <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
            <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-100">
              <ListChecks className="w-4 h-4 text-gray-500" />
              <h2 className="text-sm font-semibold text-gray-900">Fixes needed</h2>
              {health && health.fixes_needed.length > 0 && (
                <span className="ml-auto text-xs text-gray-500">
                  {health.fixes_needed.length} issue{health.fixes_needed.length !== 1 ? 's' : ''}
                </span>
              )}
            </div>
            {!health || health.fixes_needed.length === 0 ? (
              <div className="p-8 text-center text-gray-500">
                <CheckCircle2 className="w-8 h-8 mx-auto mb-2 text-green-500" />
                <p className="text-sm">Nothing to fix — every online-eligible product is ready.</p>
              </div>
            ) : (
              <ul className="divide-y divide-gray-100">
                {health.fixes_needed.map((f, idx) => (
                  <li key={(f.check || f.issue) + idx} className="flex items-center gap-3 px-4 py-2.5">
                    <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0" />
                    <span className="text-sm text-gray-700 flex-1">
                      <span className="font-semibold text-gray-900">{fmtInt(f.count)}</span>{' '}
                      product{f.count !== 1 ? 's' : ''} {f.issue}
                    </span>
                    <span className="inline-flex items-center rounded-full bg-amber-50 text-amber-700 border border-amber-200 px-2 py-0.5 text-[11px] font-medium">
                      {fmtInt(f.count)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <p className="mt-6 text-xs text-gray-400">
            Online Store module · Store health. A read-only pre-cutover readiness view. Products are
            edited from the catalog; collections and mappings from the Online Store sections.
          </p>
        </>
      )}
    </div>
  );
}
