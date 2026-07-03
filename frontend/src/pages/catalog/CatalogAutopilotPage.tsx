// ============================================================================
// IMS 2.0 - Catalog Autopilot (Phase 1)
// ============================================================================
// Enter brand + model -> search prioritised sources -> review candidates with
// confidence scores -> approve OR create a product from one. Phase 1 searches
// our own online catalog (dedup/enrich); the credentialed web sources (brand
// site, myLuxottica) + AI enrichment activate when their config is set on the
// server. When a search returns nothing, we read the sources status and explain
// WHY (which sources are off + how to turn them on) instead of a blank screen.

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Search, Loader2, Check, X as XIcon, ShieldCheck, AlertTriangle, Globe,
  FileText, Sparkles, PackagePlus, Info, Gauge, ExternalLink,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import {
  catalogAutopilotApi,
  AI_ENRICH_SOURCE,
  type AutopilotCandidate,
  type AutopilotSource,
} from '../../services/api/catalogAutopilot';
import { stashAutopilotPrefill, candidateReferences, CATEGORIES } from './productAddShared';
import clsx from 'clsx';

const DECISION_LABEL: Record<string, string> = {
  APPROVE: 'Approved', REJECT: 'Rejected', SPECS_ONLY: 'Specs only', NEEDS_REVIEW: 'Needs review',
};

// Human label for a candidate's source badge, keyed on the source id the
// backend stamps. Falls back to the AUTHORIZED/UNVERIFIED class so a brand-new
// source the backend adds still renders something sensible.
function sourceBadge(c: AutopilotCandidate): { label: string; ai: boolean; authorized: boolean } {
  const authorized = c.source_class === 'AUTHORIZED';
  if (c.source === AI_ENRICH_SOURCE) return { label: 'AI-suggested', ai: true, authorized };
  if (c.source === 'internal_bvi') return { label: 'Catalog', ai: false, authorized };
  if (c.source === 'brand_site' || c.source === 'myluxottica') return { label: 'Brand site', ai: false, authorized };
  if (c.source === 'marketplace') return { label: 'Web (unverified)', ai: false, authorized };
  return { label: authorized ? 'Authorized' : 'Unverified', ai: false, authorized };
}

// Confidence to surface: the backend's explicit `confidence` if present, else
// the existing match `score`. Both are 0..1. Returns null when neither exists.
function confidencePct(c: AutopilotCandidate): number | null {
  const v = c.confidence ?? c.score;
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return null;
  return Math.round(Number(v) * 100);
}

export default function CatalogAutopilotPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const [brand, setBrand] = useState('');
  const [model, setModel] = useState('');
  const [color, setColor] = useState('');
  const [size, setSize] = useState('');
  // v2: category-aware search — the backend refines queries with it and
  // stamps it on every candidate so the Add-Product mapper never guesses.
  const [category, setCategory] = useState('');
  const [loading, setLoading] = useState(false);
  const [sources, setSources] = useState<AutopilotSource[]>([]);
  const [candidates, setCandidates] = useState<AutopilotCandidate[]>([]);
  const [searched, setSearched] = useState(false);
  const [rights, setRights] = useState<Record<string, boolean>>({});
  const [decided, setDecided] = useState<Record<string, string>>({});

  useEffect(() => {
    catalogAutopilotApi.getSources().then((r) => setSources(r.sources || [])).catch(() => setSources([]));
  }, []);

  const enabledSources = useMemo(() => sources.filter((s) => s.enabled), [sources]);
  const disabledSources = useMemo(() => sources.filter((s) => !s.enabled), [sources]);

  const runSearch = async () => {
    if (!brand.trim() || !model.trim()) { toast.error('Brand and model are required'); return; }
    setLoading(true); setSearched(false);
    try {
      const r = await catalogAutopilotApi.createJob({ brand: brand.trim(), model: model.trim(), color: color.trim(), size: size.trim(), category });
      setCandidates(r.candidates || []);
      setSources(r.sources || sources);
      setDecided({});
      setSearched(true);
      if (!r.candidate_count) toast.info('No candidates from active sources yet.');
    } catch {
      toast.error('Search failed');
    } finally {
      setLoading(false);
    }
  };

  const decide = async (c: AutopilotCandidate, decision: 'APPROVE' | 'REJECT' | 'SPECS_ONLY' | 'NEEDS_REVIEW') => {
    try {
      await catalogAutopilotApi.decide(c.candidate_id, { decision, rights_confirmed: !!rights[c.candidate_id] });
      setDecided((d) => ({ ...d, [c.candidate_id]: decision }));
      toast.success(DECISION_LABEL[decision]);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || 'Action failed');
    }
  };

  // The payoff: hand this candidate's fields to the Add Product (Quick Add)
  // flow. We stash it in sessionStorage and navigate with ?prefill=autopilot;
  // QuickAddPage reads + clears it and prefills the form.
  const createProductFrom = (c: AutopilotCandidate) => {
    if (!stashAutopilotPrefill(c)) {
      toast.error('Could not open the Add Product form. Try again.');
      return;
    }
    navigate('/catalog/add?prefill=autopilot');
  };

  return (
    <div className="max-w-5xl mx-auto space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">Catalog Autopilot</h1>
        <p className="text-sm text-gray-500">
          Enter a brand + model — we search authorized sources, score matches, and you approve or create a
          product before publishing. We check your online catalog and the brand's regional site live; AI
          enrichment, myLuxottica &amp; marketplace sources activate once configured on the server.
        </p>
      </div>

      {/* Search form */}
      <div className="card p-4">
        <div className="grid grid-cols-1 md:grid-cols-6 gap-3 items-end">
          <Field label="Brand *"><input className="input-field" value={brand} onChange={(e) => setBrand(e.target.value)} placeholder="Ray-Ban" /></Field>
          <Field label="Model *"><input className="input-field" value={model} onChange={(e) => setModel(e.target.value)} placeholder="RB4105" /></Field>
          <Field label="Color code"><input className="input-field" value={color} onChange={(e) => setColor(e.target.value)} placeholder="6019" /></Field>
          <Field label="Size"><input className="input-field" value={size} onChange={(e) => setSize(e.target.value)} placeholder="54-16" /></Field>
          <Field label="Category">
            <select className="input-field" title="Search category" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">Auto-detect</option>
              {CATEGORIES.map((c) => (
                <option key={c.code} value={c.code}>{c.name}</option>
              ))}
            </select>
          </Field>
          <button onClick={runSearch} disabled={loading} className="btn-primary inline-flex items-center justify-center gap-2 h-[42px]">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />} Search
          </button>
        </div>
      </div>

      {/* Source status */}
      {sources.length > 0 && (
        <div className="card p-4">
          <div className="text-xs font-medium text-gray-500 uppercase mb-2">Sources (priority order)</div>
          <div className="flex flex-wrap gap-2">
            {sources.map((s) => (
              <span key={s.name} title={s.reason}
                className={clsx('inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs',
                  s.enabled ? 'bg-emerald-50 text-emerald-700' : 'bg-gray-100 text-gray-500')}>
                <span className={clsx('w-1.5 h-1.5 rounded-full', s.enabled ? 'bg-emerald-500' : 'bg-gray-400')} />
                {s.priority}. {s.label}
                {s.source_class === 'UNVERIFIED' && <AlertTriangle className="w-3 h-3 text-amber-500" />}
              </span>
            ))}
          </div>
          {enabledSources.length === 0 && (
            <p className="mt-2 text-xs text-amber-700 flex items-start gap-1.5">
              <AlertTriangle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
              No sources are active right now. Searches will return nothing until at least one source below is
              configured on the server.
            </p>
          )}
        </div>
      )}

      {/* Empty state — explain WHY it is empty using the live sources status, so
          a zero-result search reads as "works, here is why" not "broken". */}
      {searched && candidates.length === 0 && (
        <SourcesAwareEmptyState enabled={enabledSources} disabled={disabledSources} />
      )}

      <div className="space-y-3">
        {candidates.map((c) => {
          const badge = sourceBadge(c);
          const authorized = badge.authorized;
          const pct = confidencePct(c);
          const dec = decided[c.candidate_id] || c.decision;
          const lowConfidence = pct !== null && pct < 70;
          const needsVerify = Boolean(c.needs_review) || lowConfidence;
          return (
            <div key={c.candidate_id} className="card p-4">
              <div className="flex items-start gap-4">
                {c.image_urls && c.image_urls.length > 0 && (
                  <img
                    src={c.image_urls[0]}
                    // Strip the Referer so hotlink-protected brand thumbnails render.
                    referrerPolicy="no-referrer"
                    loading="lazy"
                    alt={c.title || `${c.brand ?? ''} ${c.model ?? ''}`.trim()}
                    className={clsx('w-16 h-16 rounded-lg object-cover border flex-shrink-0', authorized ? 'border-gray-200' : 'border-amber-300')}
                    onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
                  />
                )}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    {/* Source badge — AI-suggested rows get a distinct violet pill. */}
                    <span className={clsx('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium',
                      badge.ai ? 'bg-violet-100 text-violet-700'
                        : authorized ? 'bg-emerald-100 text-emerald-700'
                        : 'bg-amber-100 text-amber-700')}>
                      {badge.ai ? <Sparkles className="w-3 h-3" />
                        : authorized ? <ShieldCheck className="w-3 h-3" />
                        : <AlertTriangle className="w-3 h-3" />}
                      {badge.label}
                    </span>
                    <span className="text-xs text-gray-500">{c.source}</span>
                    {pct !== null && (
                      <span className={clsx('inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full',
                        pct >= 90 ? 'bg-green-100 text-green-700' : pct >= 70 ? 'bg-yellow-100 text-yellow-700' : 'bg-gray-100 text-gray-600')}>
                        <Gauge className="w-3 h-3" />
                        {pct}% {c.confidence != null ? 'confidence' : 'match'}
                      </span>
                    )}
                    {c.existing_shopify_product_id && (
                      <span className="inline-flex items-center gap-1 text-xs text-blue-700 bg-blue-50 px-2 py-0.5 rounded-full">
                        <Globe className="w-3 h-3" /> Already online{c.existing_status ? ` (${c.existing_status})` : ''}
                      </span>
                    )}
                  </div>
                  <p className="font-medium text-gray-900 mt-1 truncate">{c.title || `${c.brand} ${c.model}`}</p>
                  <p className="text-sm text-gray-500">
                    {c.brand} · {c.model}{c.color ? ` · ${c.color}` : ''}{c.size ? ` · ${c.size}` : ''}
                    {c.category ? ` · ${c.category}` : ''}
                  </p>

                  {/* Verify hint — low confidence or backend-flagged needs_review. */}
                  {needsVerify && (
                    <p className="text-[11px] text-amber-600 mt-1 flex items-start gap-1">
                      <Info className="w-3 h-3 mt-0.5 flex-shrink-0" />
                      {c.needs_review
                        ? 'Flagged for review — double-check the details before using.'
                        : 'Lower-confidence match — verify the model/specs before using.'}
                    </p>
                  )}

                  {c.usp && (
                    <p className="text-xs text-gray-700 mt-1.5 flex items-start gap-1">
                      <Sparkles className="w-3 h-3 text-bv-red-500 mt-0.5 flex-shrink-0" />
                      <span className="line-clamp-2">{c.usp}</span>
                    </p>
                  )}
                  {c.description && c.description !== c.usp && (
                    <p className="text-xs text-gray-500 mt-1 line-clamp-2">{c.description}</p>
                  )}

                  {/* Suggested HSN / GST (e.g. from AI enrichment). */}
                  {(c.suggested_hsn || c.suggested_gst_rate != null) && (
                    <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1.5">
                      {c.suggested_hsn && (
                        <span className="text-[11px] text-gray-600">
                          <span className="text-gray-400">Suggested HSN:</span> {c.suggested_hsn}
                        </span>
                      )}
                      {c.suggested_gst_rate != null && (
                        <span className="text-[11px] text-gray-600">
                          <span className="text-gray-400">GST:</span> {c.suggested_gst_rate}%
                        </span>
                      )}
                    </div>
                  )}

                  {c.specs && Object.keys(c.specs).length > 0 && (
                    <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1.5">
                      {Object.entries(c.specs)
                        .filter(([k, v]) => k !== 'category' && v != null && String(v).trim() !== '')
                        .slice(0, 6)
                        .map(([k, v]) => (
                          <span key={k} className="text-[11px] text-gray-500">
                            <span className="text-gray-400">{k}:</span> {String(v)}
                          </span>
                        ))}
                    </div>
                  )}
                  {!authorized && (c.image_urls?.length ?? 0) > 0 && (
                    <p className="text-[11px] text-amber-600 mt-1">Image from an unverified source — confirm rights below before using it.</p>
                  )}
                  {c.matched && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {Object.entries(c.matched).map(([k, v]) => (
                        <span key={k} className={clsx('text-[11px] px-1.5 py-0.5 rounded',
                          v ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-400 line-through')}>{k}</span>
                      ))}
                    </div>
                  )}
                  {/* v2 reference chips: the exact page(s) this data came from. */}
                  {candidateReferences(c).length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-1.5">
                      {candidateReferences(c).map((r) => (
                        <a
                          key={r.url}
                          href={r.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          title={r.url}
                          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[11px] bg-gray-100 text-gray-600 hover:bg-gray-200 hover:text-gray-800 max-w-[220px]"
                        >
                          <span className="truncate">{r.domain}</span>
                          <ExternalLink className="w-2.5 h-2.5 shrink-0" />
                        </a>
                      ))}
                    </div>
                  )}
                  {!authorized && (
                    <label className="flex items-center gap-2 mt-2 text-xs text-gray-600">
                      <input type="checkbox" checked={!!rights[c.candidate_id]}
                        onChange={(e) => setRights((r) => ({ ...r, [c.candidate_id]: e.target.checked }))} />
                      I confirm we have the right to use this image (required to approve an unverified source)
                    </label>
                  )}
                </div>

                <div className="flex flex-col items-stretch gap-2 flex-shrink-0 w-[152px]">
                  {/* Primary payoff — always available. Turns the candidate into a
                      prefilled Add Product form (a real product), independent of
                      the approve/reject review flow. */}
                  <button onClick={() => createProductFrom(c)}
                    className="px-3 py-1.5 rounded-md bg-bv-red-600 text-white text-xs inline-flex items-center justify-center gap-1.5 hover:bg-bv-red-700">
                    <PackagePlus className="w-3.5 h-3.5" /> Create product
                  </button>

                  {dec ? (
                    <span className="text-xs font-medium text-gray-600 text-center">{DECISION_LABEL[dec] || dec}</span>
                  ) : (
                    <>
                      <button onClick={() => decide(c, 'APPROVE')}
                        disabled={!authorized && !rights[c.candidate_id]}
                        className="px-3 py-1 rounded-md bg-green-600 text-white text-xs inline-flex items-center justify-center gap-1 hover:bg-green-700 disabled:opacity-50">
                        <Check className="w-3.5 h-3.5" /> Approve
                      </button>
                      <button onClick={() => decide(c, 'SPECS_ONLY')}
                        className="px-3 py-1 rounded-md bg-gray-100 text-gray-700 text-xs inline-flex items-center justify-center gap-1 hover:bg-gray-200">
                        <FileText className="w-3.5 h-3.5" /> Specs only
                      </button>
                      <button onClick={() => decide(c, 'REJECT')}
                        className="px-3 py-1 rounded-md bg-red-600 text-white text-xs inline-flex items-center justify-center gap-1 hover:bg-red-700">
                        <XIcon className="w-3.5 h-3.5" /> Reject
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Helpful empty state that reads the live sources status. Two cases:
//  - some sources ARE active -> "no match for this brand+model; try X"
//  - NO sources active        -> name the disabled sources + how to enable them
function SourcesAwareEmptyState({ enabled, disabled }: { enabled: AutopilotSource[]; disabled: AutopilotSource[] }) {
  const noneActive = enabled.length === 0;
  return (
    <div className="card p-8 text-center">
      <Search className="w-9 h-9 mx-auto mb-2 opacity-40 text-gray-400" />
      {noneActive ? (
        <>
          <p className="text-gray-700 font-medium">No candidates — every source is turned off.</p>
          <p className="text-sm text-gray-500 mt-1 max-w-xl mx-auto">
            Catalog Autopilot has no active source to search, so it can't find anything yet. Ask an admin to
            enable at least one source on the server:
          </p>
        </>
      ) : (
        <>
          <p className="text-gray-700 font-medium">No matches for that brand + model.</p>
          <p className="text-sm text-gray-500 mt-1 max-w-xl mx-auto">
            We searched {enabled.length === 1 ? 'the active source' : `all ${enabled.length} active sources`}
            {' '}({enabled.map((s) => s.label).join(', ')}) and found nothing. Try a different model number or
            spelling, or enable more sources to widen the search:
          </p>
        </>
      )}

      {disabled.length > 0 && (
        <ul className="mt-3 inline-block text-left text-sm text-gray-600 space-y-1.5">
          {disabled.map((s) => (
            <li key={s.name} className="flex items-start gap-2">
              <span className="mt-1 w-1.5 h-1.5 rounded-full bg-gray-300 flex-shrink-0" />
              <span>
                <span className="font-medium text-gray-700">{s.label}</span>
                {s.reason ? <span className="text-gray-500"> — {s.reason}</span> : null}
              </span>
            </li>
          ))}
        </ul>
      )}

      {disabled.length === 0 && !noneActive && (
        <p className="text-xs text-gray-400 mt-3">All sources are already active.</p>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs font-medium text-gray-600 mb-1">{label}</span>
      {children}
    </label>
  );
}
