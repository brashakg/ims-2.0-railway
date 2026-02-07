// ============================================================================
// IMS 2.0 - Lens Suggestion Panel for POS
// ============================================================================
// Displays AI-suggested lens options based on patient prescription data
// Uses lensAutoSuggest engine for Indian optical retail recommendations

import { useMemo } from 'react';
import {
  Sparkles,
  Eye,
  Shield,
  ChevronRight,
  Star,
  ArrowUpCircle,
  Circle,
  IndianRupee,
  Layers,
} from 'lucide-react';
import {
  suggestLenses,
  type PrescriptionInput,
  type LensSuggestion,
} from '../../utils/lensAutoSuggest';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface LensSuggestionPanelProps {
  prescriptionInput: PrescriptionInput;
  onSelect: (suggestion: LensSuggestion) => void;
}

// ---------------------------------------------------------------------------
// Priority badge config
// ---------------------------------------------------------------------------

const PRIORITY_CONFIG: Record<
  LensSuggestion['priority'],
  {
    label: string;
    badgeClass: string;
    cardBorder: string;
    cardBg: string;
    icon: typeof Star;
  }
> = {
  PRIMARY: {
    label: 'Recommended',
    badgeClass: 'bg-green-100 text-green-800 border border-green-300',
    cardBorder: 'border-green-400 ring-1 ring-green-200',
    cardBg: 'bg-white',
    icon: Star,
  },
  UPGRADE: {
    label: 'Premium Upgrade',
    badgeClass: 'bg-blue-100 text-blue-800 border border-blue-300',
    cardBorder: 'border-blue-300',
    cardBg: 'bg-white',
    icon: ArrowUpCircle,
  },
  OPTIONAL: {
    label: 'Optional',
    badgeClass: 'bg-gray-100 text-gray-600 border border-gray-300',
    cardBorder: 'border-gray-200',
    cardBg: 'bg-gray-50',
    icon: Circle,
  },
};

// ---------------------------------------------------------------------------
// Format helpers
// ---------------------------------------------------------------------------

function formatINR(value: number): string {
  return value.toLocaleString('en-IN');
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function CoatingChip({ name }: { name: string }) {
  const chipColors: Record<string, string> = {
    'Anti-Reflective': 'bg-emerald-100 text-emerald-700',
    'Blue Cut': 'bg-indigo-100 text-indigo-700',
    'Photochromic': 'bg-amber-100 text-amber-700',
    'Hard Coat': 'bg-slate-100 text-slate-600',
  };

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${chipColors[name] ?? 'bg-gray-100 text-gray-600'}`}
    >
      {name}
    </span>
  );
}

function SuggestionCard({
  suggestion,
  onSelect,
}: {
  suggestion: LensSuggestion;
  onSelect: (s: LensSuggestion) => void;
}) {
  const config = PRIORITY_CONFIG[suggestion.priority];
  const PriorityIcon = config.icon;

  return (
    <div
      className={`rounded-lg border-2 ${config.cardBorder} ${config.cardBg} p-4 transition-shadow hover:shadow-md`}
    >
      {/* Header row */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <Eye className="h-4 w-4 text-gray-500 flex-shrink-0" />
          <div>
            <h4 className="text-sm font-semibold text-gray-900">
              {suggestion.lensType}
            </h4>
            <p className="text-xs text-gray-500 flex items-center gap-1 mt-0.5">
              <Layers className="h-3 w-3" />
              {suggestion.material}
            </p>
          </div>
        </div>

        <span
          className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium ${config.badgeClass}`}
        >
          <PriorityIcon className="h-3 w-3" />
          {config.label}
        </span>
      </div>

      {/* Coatings */}
      <div className="flex flex-wrap gap-1.5 mb-3">
        {suggestion.coatings.map((coating) => (
          <CoatingChip key={coating} name={coating} />
        ))}
      </div>

      {/* Price range */}
      <div className="flex items-center gap-1 text-sm font-medium text-gray-900 mb-2">
        <IndianRupee className="h-3.5 w-3.5 text-gray-500" />
        <span>{formatINR(suggestion.priceRange.min)}</span>
        <span className="text-gray-400">-</span>
        <span>{formatINR(suggestion.priceRange.max)}</span>
      </div>

      {/* Reason */}
      <p className="text-xs text-gray-600 leading-relaxed mb-3">
        {suggestion.reason}
      </p>

      {/* Select button */}
      <button
        type="button"
        onClick={() => onSelect(suggestion)}
        className={`w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
          suggestion.priority === 'PRIMARY'
            ? 'bg-green-600 text-white hover:bg-green-700'
            : suggestion.priority === 'UPGRADE'
              ? 'bg-blue-600 text-white hover:bg-blue-700'
              : 'bg-gray-200 text-gray-700 hover:bg-gray-300'
        }`}
      >
        Select
        <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function LensSuggestionPanel({
  prescriptionInput,
  onSelect,
}: LensSuggestionPanelProps) {
  const suggestions = useMemo(
    () => suggestLenses(prescriptionInput),
    [prescriptionInput],
  );

  if (suggestions.length === 0) {
    return null;
  }

  const primarySuggestions = suggestions.filter((s) => s.priority === 'PRIMARY');
  const upgradeSuggestions = suggestions.filter((s) => s.priority === 'UPGRADE');
  const optionalSuggestions = suggestions.filter((s) => s.priority === 'OPTIONAL');

  return (
    <div className="space-y-4">
      {/* Section header */}
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1.5 text-amber-600">
          <Sparkles className="h-4 w-4" />
          <Shield className="h-4 w-4" />
        </div>
        <h3 className="text-sm font-semibold text-gray-900">
          Lens Recommendations
        </h3>
        <span className="text-xs text-gray-500">
          ({suggestions.length} suggestions)
        </span>
      </div>

      {/* Primary suggestions */}
      {primarySuggestions.length > 0 && (
        <div className="space-y-3">
          {primarySuggestions.map((s) => (
            <SuggestionCard key={s.id} suggestion={s} onSelect={onSelect} />
          ))}
        </div>
      )}

      {/* Upgrade suggestions */}
      {upgradeSuggestions.length > 0 && (
        <div className="space-y-3">
          <p className="text-xs font-medium text-blue-700 uppercase tracking-wide">
            Premium Upgrades
          </p>
          {upgradeSuggestions.map((s) => (
            <SuggestionCard key={s.id} suggestion={s} onSelect={onSelect} />
          ))}
        </div>
      )}

      {/* Optional suggestions */}
      {optionalSuggestions.length > 0 && (
        <div className="space-y-3">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">
            Other Options
          </p>
          {optionalSuggestions.map((s) => (
            <SuggestionCard key={s.id} suggestion={s} onSelect={onSelect} />
          ))}
        </div>
      )}
    </div>
  );
}
