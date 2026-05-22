// ============================================================================
// IMS 2.0 - Catalog Autopilot (Phase 1)
// ============================================================================
// Enter brand + model -> search prioritised sources -> review candidates with
// confidence scores -> approve. Phase 1 searches our own online catalog
// (dedup/enrich); the credentialed web sources (brand site, myLuxottica) are
// scaffolded and activate in Phase 1b.

import { useEffect, useState } from 'react';
import { Search, Loader2, Check, X as XIcon, ShieldCheck, AlertTriangle, Globe, FileText, Eye } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import {
  catalogAutopilotApi,
  type AutopilotCandidate,
  type AutopilotSource,
} from '../../services/api/catalogAutopilot';
import clsx from 'clsx';

const DECISION_LABEL: Record<string, string> = {
  APPROVE: 'Approved', REJECT: 'Rejected', SPECS_ONLY: 'Specs only', NEEDS_REVIEW: 'Needs review',
};

export default function CatalogAutopilotPage() {
  const toast = useToast();
  const [brand, setBrand] = useState('');
  const [model, setModel] = useState('');
  const [color, setColor] = useState('');
  const [size, setSize] = useState('');
  const [loading, setLoading] = useState(false);
  const [sources, setSources] = useState<AutopilotSource[]>([]);
  const [candidates, setCandidates] = useState<AutopilotCandidate[]>([]);
  const [searched, setSearched] = useState(false);
  const [rights, setRights] = useState<Record<string, boolean>>({});
  const [decided, setDecided] = useState<Record<string, string>>({});

  useEffect(() => {
    catalogAutopilotApi.getSources().then((r) => setSources(r.sources || [])).catch(() => setSources([]));
  }, []);

  const runSearch = async () => {
    if (!brand.trim() || !model.trim()) { toast.error('Brand and model are required'); return; }
    setLoading(true); setSearched(false);
    try {
      const r = await catalogAutopilotApi.createJob({ brand: brand.trim(), model: model.trim(), color: color.trim(), size: size.trim() });
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

  return (
    <div className="max-w-5xl mx-auto space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">Catalog Autopilot</h1>
        <p className="text-sm text-gray-500">
          Enter a brand + model — we search authorized sources, score matches, and you approve before publishing.
          Phase 1 checks our own online catalog; brand-site &amp; myLuxottica scraping arrive in Phase 1b.
        </p>
      </div>

      {/* Search form */}
      <div className="card p-4">
        <div className="grid grid-cols-1 md:grid-cols-5 gap-3 items-end">
          <Field label="Brand *"><input className="input-field" value={brand} onChange={(e) => setBrand(e.target.value)} placeholder="Ray-Ban" /></Field>
          <Field label="Model *"><input className="input-field" value={model} onChange={(e) => setModel(e.target.value)} placeholder="RB4105" /></Field>
          <Field label="Color code"><input className="input-field" value={color} onChange={(e) => setColor(e.target.value)} placeholder="6019" /></Field>
          <Field label="Size"><input className="input-field" value={size} onChange={(e) => setSize(e.target.value)} placeholder="54-16" /></Field>
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
        </div>
      )}

      {/* Candidates */}
      {searched && candidates.length === 0 && (
        <div className="card p-10 text-center text-gray-500">
          <Search className="w-9 h-9 mx-auto mb-2 opacity-40" />
          <p>No candidates yet. Phase 1 only searches our existing online catalog; the brand-site &amp; myLuxottica
          sources light up once their credentials/selectors are configured (Phase 1b).</p>
        </div>
      )}

      <div className="space-y-3">
        {candidates.map((c) => {
          const authorized = c.source_class === 'AUTHORIZED';
          const pct = Math.round((c.score || 0) * 100);
          const dec = decided[c.candidate_id] || c.decision;
          return (
            <div key={c.candidate_id} className="card p-4">
              <div className="flex items-start gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={clsx('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium',
                      authorized ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700')}>
                      {authorized ? <ShieldCheck className="w-3 h-3" /> : <AlertTriangle className="w-3 h-3" />}
                      {authorized ? 'Authorized' : 'Unverified'}
                    </span>
                    <span className="text-xs text-gray-500">{c.source}</span>
                    <span className={clsx('text-xs font-semibold px-2 py-0.5 rounded-full',
                      pct >= 90 ? 'bg-green-100 text-green-700' : pct >= 70 ? 'bg-yellow-100 text-yellow-700' : 'bg-gray-100 text-gray-600')}>
                      {pct}% match
                    </span>
                    {c.existing_shopify_product_id && (
                      <span className="inline-flex items-center gap-1 text-xs text-blue-700 bg-blue-50 px-2 py-0.5 rounded-full">
                        <Globe className="w-3 h-3" /> Already online{c.existing_status ? ` (${c.existing_status})` : ''}
                      </span>
                    )}
                  </div>
                  <p className="font-medium text-gray-900 mt-1 truncate">{c.title || `${c.brand} ${c.model}`}</p>
                  <p className="text-sm text-gray-500">{c.brand} · {c.model}{c.color ? ` · ${c.color}` : ''}{c.size ? ` · ${c.size}` : ''}</p>
                  {c.matched && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {Object.entries(c.matched).map(([k, v]) => (
                        <span key={k} className={clsx('text-[11px] px-1.5 py-0.5 rounded',
                          v ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-400 line-through')}>{k}</span>
                      ))}
                    </div>
                  )}
                  {c.url && (
                    <a href={c.url} target="_blank" rel="noopener noreferrer" className="text-xs text-bv-red-600 hover:underline inline-flex items-center gap-1 mt-1">
                      <Eye className="w-3 h-3" /> source
                    </a>
                  )}
                  {!authorized && (
                    <label className="flex items-center gap-2 mt-2 text-xs text-gray-600">
                      <input type="checkbox" checked={!!rights[c.candidate_id]}
                        onChange={(e) => setRights((r) => ({ ...r, [c.candidate_id]: e.target.checked }))} />
                      I confirm we have the right to use this image (required to approve an unverified source)
                    </label>
                  )}
                </div>

                <div className="flex flex-col items-end gap-2 flex-shrink-0">
                  {dec ? (
                    <span className="text-xs font-medium text-gray-600">{DECISION_LABEL[dec] || dec}</span>
                  ) : (
                    <div className="flex flex-col gap-1.5">
                      <button onClick={() => decide(c, 'APPROVE')}
                        disabled={!authorized && !rights[c.candidate_id]}
                        className="px-3 py-1 rounded-md bg-green-600 text-white text-xs inline-flex items-center gap-1 hover:bg-green-700 disabled:opacity-50">
                        <Check className="w-3.5 h-3.5" /> Approve
                      </button>
                      <button onClick={() => decide(c, 'SPECS_ONLY')}
                        className="px-3 py-1 rounded-md bg-gray-100 text-gray-700 text-xs inline-flex items-center gap-1 hover:bg-gray-200">
                        <FileText className="w-3.5 h-3.5" /> Specs only
                      </button>
                      <button onClick={() => decide(c, 'REJECT')}
                        className="px-3 py-1 rounded-md bg-red-600 text-white text-xs inline-flex items-center gap-1 hover:bg-red-700">
                        <XIcon className="w-3.5 h-3.5" /> Reject
                      </button>
                    </div>
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

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-xs font-medium text-gray-600 mb-1">{label}</span>
      {children}
    </label>
  );
}
