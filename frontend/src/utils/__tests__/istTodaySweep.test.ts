// IST clock sweep lane B, FE-8: these screens take "today" for an input
// default / min / max / file name. `new Date().toISOString()` is the UTC day,
// a day behind between 00:00 and 05:30 IST; they read istDayString instead.
// (ActivityLogPage, Reports and downloadCSV carry frozen-clock tests.)
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const FILES = [
  'pages/walkouts/FollowUpPanel.tsx',
  'components/clinical/useEyeExamDraft.ts',
  'components/clinical/FinalRxTab.tsx',
  'components/customers/CustomerIdentityFields.tsx',
  'pages/workshop/CreateJobModal.tsx',
  'pages/workshop/useWorkshopPage.ts',
  'components/inventory/SerialNumberModal.tsx',
  'pages/reports/GrowthBlueprintPage.tsx',
  'pages/reports/sections/TaxCodeAuditCard.tsx',
  'pages/admin/ActivityLogPage.tsx',
  'pages/reports/reportsQueries.ts',
  'utils/exportUtils.ts',
];
// "now" (optionally +/- N ms) straight to a UTC calendar day.
const UTC_TODAY = /new Date\((?:Date\.now\(\)[^)]*)?\)\.toISOString\(\)\.(?:slice\(0, (?:10|7)\)|split\('T'\)\[0\])/;

describe('screens read today on the IST day', () => {
  it.each(FILES)('%s has no UTC-day "today"', (rel) => {
    expect(readFileSync(path.join(SRC, rel), 'utf8')).not.toMatch(UTC_TODAY);
  });
});
