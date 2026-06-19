// ============================================================================
// IMS 2.0 - POS checkout-flow preference (condensed vs classic)
// ============================================================================
// The POS supports two checkout flows over the SAME canonical step machine
// (customer / prescription / products / review / payment / complete in
// posStore). This hook only chooses how those canonical steps are GROUPED and
// presented — it never changes the underlying order-create pipeline, money
// math, Rx validation, or tender logic.
//
//   - "condensed" (DEFAULT): 3 visible steps — Customer · Products & Rx ·
//     Pay & Review — merging Prescription into Products and Review into
//     Payment. Faster checkout at the counter.
//   - "classic": the original 6-step wizard, fully preserved.
//
// The choice is persisted to localStorage (ims_pos_workflow) and read on mount
// so a terminal keeps the cashier's preference across sessions. Simple +
// reversible: a single inline toggle in the POS shell flips it.

import { useCallback, useEffect, useState } from 'react';

export type POSWorkflow = 'condensed' | 'classic';

export const POS_WORKFLOW_KEY = 'ims_pos_workflow';

const DEFAULT_WORKFLOW: POSWorkflow = 'condensed';

function readWorkflow(): POSWorkflow {
  if (typeof localStorage === 'undefined') return DEFAULT_WORKFLOW;
  try {
    const v = localStorage.getItem(POS_WORKFLOW_KEY);
    return v === 'classic' ? 'classic' : 'condensed';
  } catch {
    return DEFAULT_WORKFLOW;
  }
}

/**
 * Returns the persisted POS checkout-flow preference plus a setter that
 * writes through to localStorage. Default = condensed (3-step).
 */
export function usePOSWorkflow(): [POSWorkflow, (w: POSWorkflow) => void] {
  const [workflow, setWorkflowState] = useState<POSWorkflow>(readWorkflow);

  // Re-read once on mount in case the very first render ran before
  // localStorage was available (SSR-safe / hydration parity).
  useEffect(() => {
    const stored = readWorkflow();
    setWorkflowState((prev) => (prev === stored ? prev : stored));
  }, []);

  const setWorkflow = useCallback((w: POSWorkflow) => {
    setWorkflowState(w);
    try {
      localStorage.setItem(POS_WORKFLOW_KEY, w);
    } catch {
      /* storage may be blocked — keep the in-memory choice */
    }
  }, []);

  return [workflow, setWorkflow];
}
