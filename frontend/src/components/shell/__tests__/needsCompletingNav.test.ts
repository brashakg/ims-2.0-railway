// PR #1264 follow-up: Accounts finish refunds / give-backs waiting for a
// record on Returns > Needs completing, but no menu row led there. The panel
// itself (Accounts see it, a cashier does not) is covered by
// components/returns/__tests__/NeedsCompletingPanel.test.tsx.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { filterVisibleGroups } from '../navConfig';
import type { UserRole } from '../../../types';

const here = path.dirname(fileURLToPath(import.meta.url));
const items = (role: UserRole) =>
  filterVisibleGroups([role], role, () => true).flatMap((g) => g.items);

describe('Needs completing menu row', () => {
  it('shows for ACCOUNTANT and opens /returns, whose route admits ACCOUNTANT', () => {
    const row = items('ACCOUNTANT').find((i) => i.id === 'needs-completing');
    expect(row?.to).toBe('/returns');
    const src = readFileSync(path.resolve(here, '../../../routes/orderRoutes.tsx'), 'utf8');
    const gate = /path="returns"[\s\S]*?allowedRoles=\{\[([^\]]*)\]/.exec(src)![1];
    expect(gate).toContain("'ACCOUNTANT'");
  });

  it('is not shown to counter roles (they keep the Returns desk row)', () => {
    for (const r of ['CASHIER', 'SALES_STAFF', 'STORE_MANAGER'] as UserRole[]) {
      expect(items(r).some((i) => i.id === 'needs-completing')).toBe(false);
    }
  });
});
