// ============================================================================
// Quick Add - the small presentational pieces the blocks share.
// MOVED verbatim out of QuickAddPage.tsx (Wave 3 file diet). They were
// already module-level there for the remount reason spelled out below;
// living in their own file changes nothing about that.
// ============================================================================

import { ChevronDown, Lock } from 'lucide-react';
import clsx from 'clsx';
import type { SectionId } from './shared';

// Collapsible form section. MODULE-LEVEL on purpose: it was previously defined
// INSIDE QuickAddPage, which gave it a new component identity on every render —
// React remounted the whole section subtree per keystroke, so every input lost
// focus after one character. Hoisted + fed state via props, the identity is
// stable and typing keeps focus.
export function Section({
  id, title, icon, subtitle, open, issues = 0, onToggle, children,
}: {
  id: SectionId;
  title: string;
  icon: React.ReactNode;
  subtitle?: string;
  open: boolean;
  /** Validator errors inside this section -- shown on the header so a closed
   *  (unmounted) section cannot hide them. */
  issues?: number;
  onToggle: (id: SectionId) => void;
  children: React.ReactNode;
}) {
  return (
    <div className="card !p-0 overflow-hidden">
      <button
        type="button"
        onClick={() => onToggle(id)}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-gray-50 transition-colors"
        aria-expanded={open ? "true" : "false"}
      >
        <span className="flex items-center gap-3 min-w-0">
          <span className="text-bv">{icon}</span>
          <span className="min-w-0">
            <span className="block text-[15px] font-semibold text-gray-900">{title}</span>
            {subtitle && <span className="block text-xs text-gray-500">{subtitle}</span>}
          </span>
        </span>
        <span className="flex items-center gap-2 shrink-0">
          {issues > 0 && (
            <span
              className="inline-flex items-center rounded-full bg-red-50 px-2 py-0.5 text-[11px] font-medium text-red-700"
              data-testid={`qa-section-issues-${id}`}
            >
              {issues} to fix
            </span>
          )}
          <ChevronDown
            className={clsx('w-5 h-5 text-gray-400 transition-transform', open && 'rotate-180')}
          />
        </span>
      </button>
      {open && <div className="px-4 pb-4 pt-1 border-t border-gray-100">{children}</div>}
    </div>
  );
}

// Label chips with their meaning in WORDS. The shop iPad has no hover, so a
// tooltip-only explanation left a locked field looking broken.
export function LockChip({ text }: { text: string }) {
  return (
    <span className="ml-2 inline-flex items-center gap-1 px-1.5 py-px rounded bg-gray-100 text-gray-600 text-[10px] font-medium align-middle">
      <Lock className="w-2.5 h-2.5" /> {text}
    </span>
  );
}
export function ConfirmChip() {
  return (
    <span className="ml-2 inline-flex items-center px-1.5 py-px rounded bg-amber-100 text-amber-700 text-[10px] font-medium align-middle">
      copied — confirm or edit
    </span>
  );
}

// Review rail row.
export function ReviewRow({ label, value }: { label: string; value: string }) {
  return (
    <dl className="flex items-center justify-between gap-3">
      <dt className="text-gray-500 shrink-0">{label}</dt>
      <dd className="font-medium text-gray-900 text-right truncate">{value}</dd>
    </dl>
  );
}
