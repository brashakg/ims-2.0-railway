// ============================================================================
// IMS 2.0 - Prescription-to-Order Flow Component
// ============================================================================
// Bridges the gap between eye test prescriptions and sales orders.
// When an optometrist completes a prescription, this component guides the
// flow into a product sale with intelligent lens suggestions.

import { useState, useMemo, useCallback } from 'react';
import {
  Eye,
  ShoppingCart,
  CheckCircle,
  X,
  User,
  Calendar,
  Stethoscope,
  Monitor,
  Car,
  Glasses,
  Star,
  ArrowUpCircle,
  Circle,
  IndianRupee,
  Layers,
  Sparkles,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import {
  suggestLenses,
  type PrescriptionInput,
  type LensSuggestion,
} from '../../utils/lensAutoSuggest';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface EyeData {
  sphere: number | null;
  cylinder: number | null;
  axis: number | null;
  add: number | null;
}

interface PrescriptionToOrderProps {
  prescription: {
    id: string;
    patientName: string;
    rightEye: EyeData;
    leftEye: EyeData;
    testDate: string;
    optometristName?: string;
    pd?: number;
  };
  onStartOrder?: (rxId: string, suggestions: LensSuggestion[]) => void;
  onClose?: () => void;
}

// ---------------------------------------------------------------------------
// Lifestyle options
// ---------------------------------------------------------------------------

type LifestyleOption = 'OFFICE' | 'OUTDOOR' | 'GENERAL';

interface LifestyleChoice {
  key: LifestyleOption;
  label: string;
  description: string;
  icon: typeof Monitor;
}

const LIFESTYLE_OPTIONS: LifestyleChoice[] = [
  {
    key: 'OFFICE',
    label: 'Computer / Office',
    description: 'Primarily screen work, reading, desk tasks',
    icon: Monitor,
  },
  {
    key: 'OUTDOOR',
    label: 'Driving / Outdoor',
    description: 'Driving, sports, outdoor activities',
    icon: Car,
  },
  {
    key: 'GENERAL',
    label: 'General / Mixed',
    description: 'Balanced indoor and outdoor usage',
    icon: Glasses,
  },
];

// ---------------------------------------------------------------------------
// Priority badge configuration
// ---------------------------------------------------------------------------

const PRIORITY_CONFIG: Record<
  LensSuggestion['priority'],
  {
    label: string;
    sectionLabel: string;
    badgeClass: string;
    cardBorder: string;
    cardBg: string;
    icon: typeof Star;
  }
> = {
  PRIMARY: {
    label: 'Recommended',
    sectionLabel: 'Recommended',
    badgeClass: 'bg-green-100 text-green-800 border border-green-300',
    cardBorder: 'border-green-400 ring-1 ring-green-200',
    cardBg: 'bg-white',
    icon: Star,
  },
  UPGRADE: {
    label: 'Premium Upgrade',
    sectionLabel: 'Premium Upgrades',
    badgeClass: 'bg-blue-100 text-blue-800 border border-blue-300',
    cardBorder: 'border-blue-300',
    cardBg: 'bg-white',
    icon: ArrowUpCircle,
  },
  OPTIONAL: {
    label: 'Optional',
    sectionLabel: 'Other Options',
    badgeClass: 'bg-gray-100 text-gray-600 border border-gray-300',
    cardBorder: 'border-gray-200',
    cardBg: 'bg-gray-50',
    icon: Circle,
  },
};

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

/** Format a dioptre value with explicit sign: +1.50 or -2.25. Null renders as "-". */
function formatPower(value: number | null): string {
  if (value === null) return '-';
  const fixed = Math.abs(value).toFixed(2);
  return value >= 0 ? `+${fixed}` : `-${fixed}`;
}

/** Format axis as integer degrees, or "-" when null. */
function formatAxis(value: number | null): string {
  if (value === null) return '-';
  return `${Math.round(value)}\u00B0`;
}

/** Format INR currency with Indian locale grouping. */
function formatINR(value: number): string {
  return value.toLocaleString('en-IN');
}

/** Format a date string to Indian readable format. */
function formatDate(dateString: string): string {
  return new Date(dateString).toLocaleDateString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
}

/**
 * Classify prescription power based on max absolute sphere across both eyes.
 *   |SPH| <= 2.00  -> Low power
 *   |SPH| <= 5.00  -> Medium power
 *   |SPH| > 5.00   -> High power
 */
function classifyPower(
  rightSph: number | null,
  leftSph: number | null,
): { label: string; colorClass: string } {
  const maxSph = Math.max(
    rightSph !== null ? Math.abs(rightSph) : 0,
    leftSph !== null ? Math.abs(leftSph) : 0,
  );

  if (maxSph <= 2) {
    return { label: 'Low power', colorClass: 'bg-green-100 text-green-700' };
  }
  if (maxSph <= 5) {
    return { label: 'Medium power', colorClass: 'bg-amber-100 text-amber-700' };
  }
  return { label: 'High power', colorClass: 'bg-red-100 text-red-700' };
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Coating chip with contextual colouring. */
function CoatingChip({ name }: { name: string }) {
  const chipColors: Record<string, string> = {
    'Anti-Reflective': 'bg-emerald-100 text-emerald-700',
    'Blue Cut': 'bg-indigo-100 text-indigo-700',
    Photochromic: 'bg-amber-100 text-amber-700',
    'Hard Coat': 'bg-slate-100 text-slate-600',
  };

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
        chipColors[name] ?? 'bg-gray-100 text-gray-600'
      }`}
    >
      {name}
    </span>
  );
}

/** Individual suggestion card. */
function SuggestionCard({
  suggestion,
  isSelected,
  onToggle,
}: {
  suggestion: LensSuggestion;
  isSelected: boolean;
  onToggle: (s: LensSuggestion) => void;
}) {
  const config = PRIORITY_CONFIG[suggestion.priority];
  const PriorityIcon = config.icon;

  return (
    <button
      type="button"
      onClick={() => onToggle(suggestion)}
      className={`w-full text-left rounded-lg border-2 p-4 transition-all ${
        isSelected
          ? 'border-indigo-500 ring-2 ring-indigo-200 bg-indigo-50/40'
          : `${config.cardBorder} ${config.cardBg} hover:shadow-md`
      }`}
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

        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium ${config.badgeClass}`}
          >
            <PriorityIcon className="h-3 w-3" />
            {config.label}
          </span>
          <div
            className={`h-5 w-5 rounded-full border-2 flex items-center justify-center flex-shrink-0 ${
              isSelected
                ? 'border-indigo-600 bg-indigo-600'
                : 'border-gray-300 bg-white'
            }`}
          >
            {isSelected && <CheckCircle className="h-3.5 w-3.5 text-white" />}
          </div>
        </div>
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
      <p className="text-xs text-gray-600 leading-relaxed">{suggestion.reason}</p>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function PrescriptionToOrder({
  prescription,
  onStartOrder,
  onClose,
}: PrescriptionToOrderProps) {
  const [selectedLifestyle, setSelectedLifestyle] = useState<LifestyleOption | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [suggestionsExpanded, setSuggestionsExpanded] = useState(true);

  // Build PrescriptionInput for the suggestion engine.
  const rxInput = useMemo<PrescriptionInput>(() => {
    const lifestyle: PrescriptionInput['lifestyle'] =
      selectedLifestyle === 'OFFICE'
        ? 'OFFICE'
        : selectedLifestyle === 'OUTDOOR'
          ? 'OUTDOOR'
          : 'GENERAL';

    return {
      rightSphere: prescription.rightEye.sphere,
      rightCylinder: prescription.rightEye.cylinder,
      rightAxis: prescription.rightEye.axis,
      rightAdd: prescription.rightEye.add,
      leftSphere: prescription.leftEye.sphere,
      leftCylinder: prescription.leftEye.cylinder,
      leftAxis: prescription.leftEye.axis,
      leftAdd: prescription.leftEye.add,
      lifestyle,
    };
  }, [prescription, selectedLifestyle]);

  // Auto-generate lens suggestions whenever the input changes.
  const suggestions = useMemo(() => suggestLenses(rxInput), [rxInput]);

  // Group suggestions by priority.
  const grouped = useMemo(() => {
    const primary = suggestions.filter((s) => s.priority === 'PRIMARY');
    const upgrade = suggestions.filter((s) => s.priority === 'UPGRADE');
    const optional = suggestions.filter((s) => s.priority === 'OPTIONAL');
    return { primary, upgrade, optional };
  }, [suggestions]);

  // Power classification.
  const powerClass = useMemo(
    () => classifyPower(prescription.rightEye.sphere, prescription.leftEye.sphere),
    [prescription.rightEye.sphere, prescription.leftEye.sphere],
  );

  // Toggle a suggestion selection.
  const handleToggle = useCallback((s: LensSuggestion) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(s.id)) {
        next.delete(s.id);
      } else {
        next.add(s.id);
      }
      return next;
    });
  }, []);

  // Create order handler.
  const handleCreateOrder = useCallback(() => {
    if (!onStartOrder) return;
    const selected = suggestions.filter((s) => selectedIds.has(s.id));
    onStartOrder(prescription.id, selected);
  }, [onStartOrder, suggestions, selectedIds, prescription.id]);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="bg-white rounded-xl shadow-lg border border-gray-200 max-w-2xl w-full mx-auto overflow-hidden">
      {/* ------------------------------------------------------------------ */}
      {/* Header                                                             */}
      {/* ------------------------------------------------------------------ */}
      <div className="bg-gradient-to-r from-indigo-600 to-violet-600 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3 text-white">
          <Eye className="h-5 w-5" />
          <h2 className="text-lg font-semibold">Prescription to Order</h2>
        </div>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="text-white/70 hover:text-white transition-colors"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        )}
      </div>

      <div className="p-6 space-y-6 max-h-[80vh] overflow-y-auto">
        {/* ---------------------------------------------------------------- */}
        {/* Prescription summary card                                        */}
        {/* ---------------------------------------------------------------- */}
        <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 space-y-4">
          {/* Patient info row */}
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
            <span className="flex items-center gap-1.5 font-medium text-gray-900">
              <User className="h-4 w-4 text-gray-500" />
              {prescription.patientName}
            </span>
            <span className="flex items-center gap-1.5 text-gray-600">
              <Calendar className="h-4 w-4 text-gray-400" />
              {formatDate(prescription.testDate)}
            </span>
            {prescription.optometristName && (
              <span className="flex items-center gap-1.5 text-gray-600">
                <Stethoscope className="h-4 w-4 text-gray-400" />
                {prescription.optometristName}
              </span>
            )}
            <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium ${powerClass.colorClass}`}>
              {powerClass.label}
            </span>
          </div>

          {/* Eye values table */}
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-gray-500 uppercase">
                  <th className="text-left py-1.5 pr-3 font-medium">Eye</th>
                  <th className="text-center py-1.5 px-3 font-medium">SPH</th>
                  <th className="text-center py-1.5 px-3 font-medium">CYL</th>
                  <th className="text-center py-1.5 px-3 font-medium">AXIS</th>
                  <th className="text-center py-1.5 px-3 font-medium">ADD</th>
                  <th className="text-center py-1.5 pl-3 font-medium">PD</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                <tr>
                  <td className="py-2 pr-3 font-medium text-blue-700">
                    Right (OD)
                  </td>
                  <td className="py-2 px-3 text-center font-semibold">
                    {formatPower(prescription.rightEye.sphere)}
                  </td>
                  <td className="py-2 px-3 text-center font-semibold">
                    {formatPower(prescription.rightEye.cylinder)}
                  </td>
                  <td className="py-2 px-3 text-center font-semibold">
                    {formatAxis(prescription.rightEye.axis)}
                  </td>
                  <td className="py-2 px-3 text-center font-semibold">
                    {formatPower(prescription.rightEye.add)}
                  </td>
                  <td className="py-2 pl-3 text-center font-semibold">
                    {prescription.pd != null ? prescription.pd : '-'}
                  </td>
                </tr>
                <tr>
                  <td className="py-2 pr-3 font-medium text-green-700">
                    Left (OS)
                  </td>
                  <td className="py-2 px-3 text-center font-semibold">
                    {formatPower(prescription.leftEye.sphere)}
                  </td>
                  <td className="py-2 px-3 text-center font-semibold">
                    {formatPower(prescription.leftEye.cylinder)}
                  </td>
                  <td className="py-2 px-3 text-center font-semibold">
                    {formatAxis(prescription.leftEye.axis)}
                  </td>
                  <td className="py-2 px-3 text-center font-semibold">
                    {formatPower(prescription.leftEye.add)}
                  </td>
                  <td className="py-2 pl-3 text-center font-semibold">
                    {/* PD displayed once in right row; left is same value */}
                    -
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        {/* ---------------------------------------------------------------- */}
        {/* Lifestyle questionnaire                                          */}
        {/* ---------------------------------------------------------------- */}
        <div className="space-y-3">
          <h3 className="text-sm font-semibold text-gray-900">
            What do you primarily use your glasses for?
          </h3>
          <div className="grid grid-cols-3 gap-3">
            {LIFESTYLE_OPTIONS.map((opt) => {
              const Icon = opt.icon;
              const isActive = selectedLifestyle === opt.key;
              return (
                <button
                  key={opt.key}
                  type="button"
                  onClick={() => setSelectedLifestyle(opt.key)}
                  className={`rounded-lg border-2 p-3 text-left transition-all ${
                    isActive
                      ? 'border-indigo-500 bg-indigo-50 ring-1 ring-indigo-200'
                      : 'border-gray-200 bg-white hover:border-gray-300 hover:bg-gray-50'
                  }`}
                >
                  <Icon
                    className={`h-5 w-5 mb-1.5 ${
                      isActive ? 'text-indigo-600' : 'text-gray-400'
                    }`}
                  />
                  <p
                    className={`text-sm font-medium ${
                      isActive ? 'text-indigo-900' : 'text-gray-800'
                    }`}
                  >
                    {opt.label}
                  </p>
                  <p className="text-xs text-gray-500 mt-0.5 leading-snug">
                    {opt.description}
                  </p>
                </button>
              );
            })}
          </div>
        </div>

        {/* ---------------------------------------------------------------- */}
        {/* Lens suggestions                                                 */}
        {/* ---------------------------------------------------------------- */}
        <div className="space-y-3">
          <button
            type="button"
            onClick={() => setSuggestionsExpanded((prev) => !prev)}
            className="flex items-center gap-2 w-full"
          >
            <Sparkles className="h-4 w-4 text-amber-500" />
            <h3 className="text-sm font-semibold text-gray-900">
              Lens Suggestions
            </h3>
            <span className="text-xs text-gray-500">
              ({suggestions.length} options)
            </span>
            {suggestionsExpanded ? (
              <ChevronUp className="h-4 w-4 text-gray-400 ml-auto" />
            ) : (
              <ChevronDown className="h-4 w-4 text-gray-400 ml-auto" />
            )}
          </button>

          {suggestionsExpanded && (
            <div className="space-y-4">
              {/* Primary */}
              {grouped.primary.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-green-700 uppercase tracking-wide">
                    {PRIORITY_CONFIG.PRIMARY.sectionLabel}
                  </p>
                  {grouped.primary.map((s) => (
                    <SuggestionCard
                      key={s.id}
                      suggestion={s}
                      isSelected={selectedIds.has(s.id)}
                      onToggle={handleToggle}
                    />
                  ))}
                </div>
              )}

              {/* Upgrade */}
              {grouped.upgrade.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-blue-700 uppercase tracking-wide">
                    {PRIORITY_CONFIG.UPGRADE.sectionLabel}
                  </p>
                  {grouped.upgrade.map((s) => (
                    <SuggestionCard
                      key={s.id}
                      suggestion={s}
                      isSelected={selectedIds.has(s.id)}
                      onToggle={handleToggle}
                    />
                  ))}
                </div>
              )}

              {/* Optional */}
              {grouped.optional.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">
                    {PRIORITY_CONFIG.OPTIONAL.sectionLabel}
                  </p>
                  {grouped.optional.map((s) => (
                    <SuggestionCard
                      key={s.id}
                      suggestion={s}
                      isSelected={selectedIds.has(s.id)}
                      onToggle={handleToggle}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* ---------------------------------------------------------------- */}
        {/* Create Order action                                              */}
        {/* ---------------------------------------------------------------- */}
        <div className="sticky bottom-0 bg-white pt-3 border-t border-gray-200">
          <button
            type="button"
            onClick={handleCreateOrder}
            disabled={!onStartOrder}
            className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-lg bg-indigo-600 text-white font-medium text-sm hover:bg-indigo-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <ShoppingCart className="h-4 w-4" />
            Create Order
            {selectedIds.size > 0 && (
              <span className="ml-1 bg-white/20 px-2 py-0.5 rounded-full text-xs">
                {selectedIds.size} lens{selectedIds.size !== 1 ? 'es' : ''} selected
              </span>
            )}
          </button>
          {selectedIds.size === 0 && (
            <p className="text-xs text-gray-500 text-center mt-2">
              Select one or more lens suggestions above, or proceed without pre-selected lenses
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export default PrescriptionToOrder;
