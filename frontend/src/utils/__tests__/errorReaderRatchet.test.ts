// Ratchet: the shared client rejects with an ApiError (`status`, `code`,
// `detail`) and never an axios `.response`, so a screen that reads
// `err.response.data.detail` or `err.response.status` reads undefined and
// shows its generic text (or never opens its recovery branch). Read errors
// through utils/errorHandler (getErrorMessage / getErrorDetail /
// getErrorStatus) instead.
//
// KNOWN_READERS is the deliberately deferred list (POS, till, refunds and
// supplier money paths need an owner-reviewed change each; see
// docs/handover/CODEX-20261024-E.md). It may only SHRINK: fix a reader, then
// lower or delete its row. A new file, or one more reader in a listed file,
// fails here by name.
import { describe, expect, it } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

const KNOWN_READERS: Record<string, number> = {
  // The one shared reader: it also accepts raw axios adapters (vendor portal).
  'utils/errorHandler.ts': 3,
  'components/clinical/PatientIntakeModal.tsx': 1,
  'components/orders/SuperadminOrderEditModal.tsx': 1,
  'components/pos/LoyaltyRedeemControl.tsx': 2,
  'pages/finance/expenses/DaySettlementSection.tsx': 1,
  'pages/orders/ReturnsPage.tsx': 4,
  'pages/pos/next/BillingSurface.tsx': 1,
  'pages/pos/next/CustomerPanel.tsx': 1,
  'pages/pos/next/DeliverySurface.tsx': 1,
  'pages/pos/next/GeneralCounterSurface.tsx': 1,
  'pages/purchase/SupplierCreditConfirmation.tsx': 1,
  'pages/purchase/VendorReturns.tsx': 1,
  'pages/reports/DayEndReport.tsx': 3,
  // Reads the Blob body of a PDF refusal (see the follow-up in the handover).
  'services/api/printDocuments.ts': 1,
};

const RAW_READER =
  /\.response\?\.(data|status)|\b(e|err|error|ex|e2|exc|cause|reason)\.response\.(data|status)|'response' in (e|err|error)\b/;

function readerCounts(): Record<string, number> {
  const out: Record<string, number> = {};
  for (const rel of readdirSync(SRC, { recursive: true }) as string[]) {
    const file = rel.split(path.sep).join('/');
    if (!/\.(ts|tsx)$/.test(file) || /__tests__|\.test\./.test(file) || file === 'services/api/client.ts') continue;
    const n = readFileSync(path.join(SRC, rel), 'utf8')
      .split('\n')
      .filter((line) => !/^\s*(\/\/|\*|\/\*)/.test(line) && RAW_READER.test(line)).length;
    if (n) out[file] = n;
  }
  return out;
}

describe('error readers go through utils/errorHandler', () => {
  const found = readerCounts();

  it('adds no raw err.response reader anywhere', () => {
    const grown = Object.entries(found)
      .filter(([file, n]) => n > (KNOWN_READERS[file] ?? 0))
      .map(([file, n]) => `${file}: ${n} (allowed ${KNOWN_READERS[file] ?? 0})`);
    expect(grown).toEqual([]);
  });

  it('keeps the allowlist honest: shrink a row once its reader is fixed', () => {
    const stale = Object.entries(KNOWN_READERS)
      .filter(([file, n]) => (found[file] ?? 0) < n)
      .map(([file, n]) => `${file}: ${found[file] ?? 0} (listed ${n})`);
    expect(stale).toEqual([]);
  });
});
