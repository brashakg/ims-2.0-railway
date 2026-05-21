// ============================================================================
// IMS 2.0 — Growth Blueprint Page (R3)
// ============================================================================
// SUPERADMIN-only. Renders the LLM-narrated synthesis of R1+R2 outputs
// as print-friendly markdown. Spec: docs/TECHCHERRY_PORT_SCOPE.md §7.

import { useState, useEffect, type ReactElement } from 'react';
import { Sparkles, RefreshCw, Printer, ArrowLeft, AlertCircle, Clock } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { reportsApi } from '../../services/api/reports';
import api from '../../services/api/client';

interface LLMModel {
  id: string;
  label: string;
  tier?: 'free' | 'standard' | 'premium';
}

interface Blueprint {
  narrative_markdown: string;
  sections: string[];
  model_used: string | null;
  store_id: string;
  month: string;
  generated_at: string;
  from_cache: boolean;
  cache_age_hours?: number;
  error?: string;
}

// Minimal markdown → JSX renderer (no external dep). Handles ## headings,
// **bold**, numbered lists, bullets, paragraphs. The blueprint is structured
// markdown from the LLM — keep this focused, no HTML rendering.
function renderMarkdown(md: string): ReactElement {
  const lines = md.split('\n');
  const out: ReactElement[] = [];
  let listBuffer: string[] = [];
  let listType: 'ul' | 'ol' | null = null;

  const flushList = () => {
    if (listBuffer.length === 0) return;
    const Tag = listType === 'ol' ? 'ol' : 'ul';
    out.push(
      <Tag key={`list-${out.length}`} className="my-3 ml-6 space-y-1">
        {listBuffer.map((item, i) => (
          <li key={i} className="text-gray-800 leading-relaxed" dangerouslySetInnerHTML={{ __html: renderInline(item) }} />
        ))}
      </Tag>
    );
    listBuffer = [];
    listType = null;
  };

  const renderInline = (text: string): string => {
    // **bold**
    let s = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // `code`
    s = s.replace(/`([^`]+)`/g, '<code class="bg-gray-100 px-1 py-0.5 rounded text-sm font-mono">$1</code>');
    // *italic* (avoid double-stars edge)
    s = s.replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, '$1<em>$2</em>');
    return s;
  };

  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];
    if (/^#{1,6} /.test(ln)) {
      flushList();
      const level = (ln.match(/^#+/) || [''])[0].length;
      const text = ln.replace(/^#+\s*/, '');
      if (level === 1) {
        out.push(
          <h1 key={i} className="text-3xl font-bold text-gray-900 mt-6 mb-3 border-b border-gray-200 pb-2">{text}</h1>
        );
      } else if (level === 2) {
        out.push(
          <h2 key={i} className="text-xl font-semibold text-gray-900 mt-6 mb-2">{text}</h2>
        );
      } else {
        out.push(
          <h3 key={i} className="text-base font-semibold text-gray-900 mt-4 mb-2">{text}</h3>
        );
      }
    } else if (/^\d+\.\s/.test(ln)) {
      if (listType !== 'ol') flushList();
      listType = 'ol';
      listBuffer.push(ln.replace(/^\d+\.\s*/, ''));
    } else if (/^[-*]\s/.test(ln)) {
      if (listType !== 'ul') flushList();
      listType = 'ul';
      listBuffer.push(ln.replace(/^[-*]\s*/, ''));
    } else if (ln.trim() === '') {
      flushList();
    } else {
      flushList();
      out.push(
        <p key={i} className="text-gray-800 leading-relaxed my-2"
           dangerouslySetInnerHTML={{ __html: renderInline(ln) }} />
      );
    }
  }
  flushList();
  return <>{out}</>;
}

export function GrowthBlueprintPage() {
  const { user, hasRole } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [blueprint, setBlueprint] = useState<Blueprint | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [llmModels, setLlmModels] = useState<LLMModel[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>('');

  // SUPERADMIN-only — quietly redirect lower roles to /reports
  const isSuperadmin = hasRole(['SUPERADMIN']);

  useEffect(() => {
    if (!isSuperadmin) {
      navigate('/reports');
      return;
    }
    // Load model picker — same pattern as JarvisPage
    api.get<{ models: LLMModel[]; default: string | null }>('/jarvis/models')
      .then(({ data }) => {
        setLlmModels(data.models || []);
        if (data.default) setSelectedModel(data.default);
      })
      .catch(() => setLlmModels([]));
    // Auto-load on first visit (using cache if available)
    handleGenerate(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isSuperadmin]);

  const handleGenerate = async (force: boolean) => {
    setIsLoading(true);
    setError(null);
    try {
      const next = llmModels.find((m) => m.id === selectedModel);
      if (force && next?.tier === 'premium') {
        const ok = window.confirm(
          `${next.label} is the premium model — ~20× the cost of Haiku per query.\n\n` +
          `A Growth Blueprint typically uses 8-15k tokens. Generating fresh on Opus may cost ₹40-80.\n\n` +
          `Continue?`
        );
        if (!ok) {
          setIsLoading(false);
          return;
        }
      }
      const data = await reportsApi.getGrowthBlueprint(user?.activeStoreId, {
        model_id: selectedModel || undefined,
        nocache: force,
      });
      setBlueprint(data);
      if (data.error) {
        setError(data.error);
      } else if (data.from_cache) {
        toast.info(`Loaded from cache (${data.cache_age_hours?.toFixed(1) ?? '?'}h old)`);
      } else {
        toast.success('Blueprint generated');
      }
    } catch (e: any) {
      setError(e?.message || 'Failed to generate blueprint');
    } finally {
      setIsLoading(false);
    }
  };

  if (!isSuperadmin) return null;

  return (
    <div className="max-w-5xl mx-auto p-6 print:p-0 print:max-w-none">
      {/* Toolbar — hidden when printing */}
      <div className="flex items-center justify-between mb-6 print:hidden">
        <button
          onClick={() => navigate('/reports')}
          className="flex items-center gap-2 text-sm text-gray-600 hover:text-gray-900"
        >
          <ArrowLeft className="w-4 h-4" /> Reports
        </button>
        <div className="flex items-center gap-3">
          {llmModels.length > 1 && (
            <select
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
              className="text-sm border border-gray-300 rounded px-2 py-1"
              disabled={isLoading}
            >
              {llmModels.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}{m.tier === 'premium' ? ' ★' : ''}
                </option>
              ))}
            </select>
          )}
          <button
            onClick={() => handleGenerate(true)}
            disabled={isLoading}
            className="btn-primary flex items-center gap-2 text-sm"
          >
            <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
            {isLoading ? 'Generating…' : 'Regenerate'}
          </button>
          <button
            onClick={() => window.print()}
            disabled={!blueprint || isLoading}
            className="btn flex items-center gap-2 text-sm"
          >
            <Printer className="w-4 h-4" /> Print / PDF
          </button>
        </div>
      </div>

      {/* Title block */}
      <div className="mb-6 print:mb-8">
        <div className="flex items-center gap-2 text-bv-red-600 mb-2">
          <Sparkles className="w-5 h-5" />
          <span className="text-xs uppercase tracking-widest font-medium">Growth Blueprint · JARVIS Synthesis</span>
        </div>
        <h1 className="text-4xl font-bold text-gray-900">Strategic Plan · {blueprint?.month ?? new Date().toISOString().slice(0, 7)}</h1>
        <p className="text-sm text-gray-500 mt-2">
          {blueprint ? (
            <>
              Store {blueprint.store_id} · generated {new Date(blueprint.generated_at).toLocaleString('en-IN')}
              {blueprint.from_cache && (
                <span className="ml-2 text-amber-600 inline-flex items-center gap-1">
                  <Clock className="w-3 h-3" /> from cache ({blueprint.cache_age_hours?.toFixed(1)}h ago)
                </span>
              )}
              {blueprint.model_used && (
                <span className="ml-2 text-gray-400 font-mono text-xs">model: {blueprint.model_used}</span>
              )}
            </>
          ) : isLoading ? 'Composing…' : 'Click Regenerate to begin.'}
        </p>
      </div>

      {/* Error banner */}
      {error && (
        <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded flex items-start gap-3 print:hidden">
          <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
          <div className="text-sm">
            <div className="font-medium text-red-900 mb-1">Blueprint error</div>
            <div className="text-red-800">{error}</div>
          </div>
        </div>
      )}

      {/* Loading skeleton */}
      {isLoading && !blueprint && (
        <div className="space-y-4 animate-pulse">
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i}>
              <div className="h-6 bg-gray-200 rounded w-1/3 mb-3" />
              <div className="h-4 bg-gray-100 rounded w-full mb-1" />
              <div className="h-4 bg-gray-100 rounded w-11/12 mb-1" />
              <div className="h-4 bg-gray-100 rounded w-5/6" />
            </div>
          ))}
        </div>
      )}

      {/* Table of contents (sidebar in print) */}
      {blueprint && (
        <details className="mb-6 print:hidden">
          <summary className="cursor-pointer text-sm text-gray-600 hover:text-gray-900 font-medium">
            Table of contents ({blueprint.sections.length} sections)
          </summary>
          <ol className="mt-2 ml-4 space-y-1 text-sm text-gray-700">
            {blueprint.sections.map((s, i) => (
              <li key={i}>{i + 1}. {s}</li>
            ))}
          </ol>
        </details>
      )}

      {/* The narrative itself */}
      {blueprint?.narrative_markdown && (
        <article className="prose-blueprint">
          {renderMarkdown(blueprint.narrative_markdown)}
        </article>
      )}

      {/* Print-only footer */}
      <div className="hidden print:block mt-8 pt-4 border-t border-gray-300 text-xs text-gray-500">
        Generated by IMS 2.0 · Better Vision + WizOpt · Confidential — internal use only
      </div>
    </div>
  );
}
