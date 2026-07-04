// ============================================================================
// IMS 2.0 - Purchase Status Chip (owner-approved 5-word vocabulary)
// ============================================================================
// Procurement-UX council ruling (Phase 1): one shared chip that translates the
// internal PO / GRN / purchase-invoice statuses into the FIVE words the owner
// signed off for the shop floor:
//
//   'Ordered'      -- PO exists but has not gone to the vendor yet
//   'Sent'         -- PO is with the vendor (sent / acknowledged)
//   'Box received' -- goods physically arrived (GRN recorded, incl. partial)
//   'On shelf'     -- stock accepted into inventory
//   'Bill settled' -- purchase invoice booked / paid
//
// Fail-soft by design (same lesson as pages/purchase/statusBadge.tsx): an
// unmapped/legacy status renders as a muted chip with the raw status text --
// it must NEVER throw and unmount the page. Phase 1 uses this in the PO list's
// Receiving column; broader rollout (GRN list, recon console) is Phase 2.

export type PurchaseStatusKind = 'po' | 'grn' | 'invoice';

interface VocabEntry {
  label: string;
  className: string;
}

const VOCAB: Record<string, VocabEntry> = {
  ordered: { label: 'Ordered', className: 'bg-gray-100 text-gray-700' },
  sent: { label: 'Sent', className: 'bg-indigo-50 text-indigo-700' },
  box_received: { label: 'Box received', className: 'bg-amber-50 text-amber-700' },
  on_shelf: { label: 'On shelf', className: 'bg-green-50 text-green-700' },
  bill_settled: { label: 'Bill settled', className: 'bg-teal-50 text-teal-700' },
};

// Internal status -> vocabulary key, per document kind.
const PO_MAP: Record<string, keyof typeof VOCAB> = {
  DRAFT: 'ordered',
  PENDING: 'ordered',
  APPROVED: 'ordered',
  SENT: 'sent',
  ACKNOWLEDGED: 'sent',
  ORDERED: 'sent',
  PARTIAL: 'box_received',
  PARTIALLY_RECEIVED: 'box_received',
  RECEIVED: 'on_shelf',
};

const GRN_MAP: Record<string, keyof typeof VOCAB> = {
  PENDING: 'box_received',
  ACCEPTED: 'on_shelf',
  PARTIALLY_ACCEPTED: 'on_shelf',
};

const INVOICE_MAP: Record<string, keyof typeof VOCAB> = {
  BOOKED: 'bill_settled',
  SETTLED: 'bill_settled',
  PAID: 'bill_settled',
};

/** Resolve a raw status to the owner vocabulary entry, or null when unmapped
 *  (e.g. CANCELLED / VOID -- those keep their raw label, muted). */
export function purchaseStatusVocab(
  status: string | null | undefined,
  kind: PurchaseStatusKind = 'po',
): VocabEntry | null {
  const key = String(status || '').toUpperCase();
  const map = kind === 'grn' ? GRN_MAP : kind === 'invoice' ? INVOICE_MAP : PO_MAP;
  const vocabKey = map[key];
  return vocabKey ? VOCAB[vocabKey] : null;
}

export function PurchaseStatusChip({
  status,
  kind = 'po',
}: {
  status: string | null | undefined;
  kind?: PurchaseStatusKind;
}) {
  const entry = purchaseStatusVocab(status, kind);
  if (entry) {
    return (
      <span
        className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${entry.className}`}
      >
        {entry.label}
      </span>
    );
  }
  // Unmapped/legacy status: render the raw text muted rather than throwing.
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-500">
      {String(status || 'Unknown')}
    </span>
  );
}

export default PurchaseStatusChip;
