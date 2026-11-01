// The red DO NOT SHELVE label: the ONE thermal unit label (unitLabel.ts) in
// its quarantine form. Every door that prints it -- Receive (a damaged piece)
// and the Quarantine queue -- comes through printQuarantineLabels, so both
// print the same label and record it the same way.

import { inventoryApi, type QuarantineLabel } from '../../services/api/inventory';
import type { PrintResult } from '../../services/printWindow';
import { labelProblem, printUnitLabels, type UnitLabelData } from './unitLabel';

/** POST /labels/quarantine's payload as the thermal label prints it. */
export function quarantineLabelData(label: QuarantineLabel): UnitLabelData {
  const day = label.quarantine_at ? String(label.quarantine_at).slice(0, 10) : '';
  return {
    barcode: label.barcode_value,
    brand: label.brand,
    model: label.name,
    doNotShelve: true,
    detail: [label.quarantine_reason, label.store_code, day].filter(Boolean).join(' · '),
    note: label.luxury_brand_line,
  };
}

/** Check every barcode, record each label (POST /labels/quarantine, which
 *  notes who printed it and returns the payload), then print them all once.
 *  A barcode no label can carry refuses the batch before anything is recorded. */
export async function printQuarantineLabels(
  units: { stock_id: string; barcode?: string }[],
): Promise<PrintResult> {
  const bad = units.find((u) => labelProblem(u.barcode || u.stock_id));
  if (bad) {
    const code = bad.barcode || bad.stock_id;
    return { method: 'failed', message: `${labelProblem(code)} (${code}): no label printed.` };
  }
  const labels = await Promise.all(units.map((u) => inventoryApi.printQuarantineLabel(u.stock_id)));
  return printUnitLabels(labels.map(quarantineLabelData));
}
