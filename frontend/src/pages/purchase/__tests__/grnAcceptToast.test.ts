// ============================================================================
// What the shop floor is told after an accept that still holds lines
// (audit C1; panel R1-91, R1-96)
// ============================================================================
// Only a line waiting to be catalogued goes on the shelf by itself once its
// product is finished; a line beyond the order waits for the store manager,
// and a line naming no product waits for nobody's save. The toast says what
// each held line waits for, and names no person the server may not have found
// (no catalogue manager -> the admins get the task).

import { describe, it, expect, vi } from 'vitest';
import { heldLinesSummary, reportGrnAccept } from '../grnAcceptToast';

const toast = () => ({ success: vi.fn(), warning: vi.fn() });

describe('reportGrnAccept on a held receipt', () => {
  it('a line held for the catalogue: it goes on the shelf by itself once finished', () => {
    const t = toast();
    reportGrnAccept(t, 'R-1', {
      grn_status: 'PARTIALLY_ACCEPTED',
      units_added: 0,
      unresolved_lines: [{ reason: 'incomplete_catalog' }],
    });
    expect(t.success).not.toHaveBeenCalled();
    const msg = String(t.warning.mock.calls[0][0]);
    expect(msg).toMatch(/1 line\(s\) waiting to be catalogued/);
    expect(msg).toMatch(/go on the shelf by themselves/);
    expect(msg).not.toMatch(/catalogue manager has a task/i);
  });

  it('lines beyond the order or naming no product never "go on the shelf by themselves"', () => {
    const t = toast();
    reportGrnAccept(t, 'R-2', {
      grn_status: 'PARTIALLY_ACCEPTED',
      units_added: 1,
      unresolved_lines: [{ reason: 'over_order' }, { reason: 'not_catalogued' }],
    });
    const msg = String(t.warning.mock.calls[0][0]);
    expect(msg).toMatch(/beyond the order, for the store manager/);
    expect(msg).toMatch(/for a product not in the catalogue/);
    expect(msg).not.toMatch(/by themselves/);
    expect(msg).not.toMatch(/waiting to be catalogued/);
  });

  it('heldLinesSummary counts each reason once', () => {
    expect(
      heldLinesSummary([{ reason: 'incomplete_catalog' }, { reason: 'over_order' }, { reason: 'not_catalogued' }]),
    ).toMatchObject({ catalogue: 1, over: 1 });
  });
});
