// ============================================================================
// Codex PR-1425 round 1 - the bench sees the sale's fitting instructions, and
// the promise date is the same IST day in every browser zone
// ============================================================================
// Runs with the browser zone set to America/New_York: a date-only promise
// date '2026-11-14' parses as UTC midnight = 13 Nov 19:00 there, which the old
// local-zone formatters showed as "13 Nov" / "13-Nov-2026".
process.env.TZ = 'America/New_York';

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { WorkshopJobCardPrint } from '../../../components/print/WorkshopJobCardPrint';
import { WorkshopJobDetail } from '../WorkshopJobDetail';
import { formatJobDate, jobCardPrintData } from '../shared';
import type { Job } from '../shared';
import type { WorkshopPageState } from '../useWorkshopPage';

vi.mock('../VendorCaptureBlock', () => ({ VendorCaptureBlock: () => null }));

const NOTES = 'CLAUDE1425 Paid Frame: Frame note must reach bench; CLAUDE1425 Paid Pair: Lens note must reach bench';

const JOB = {
  id: 'J1', jobNumber: 'WS-261114-42315E0A', orderNumber: 'BV-1425', customerId: 'C1',
  customerName: 'Asha Rao', customerPhone: '9800001425', frameName: 'Audit Frame', lensType: 'Progressive 1.60',
  status: 'PENDING', priority: 'NORMAL', expectedDate: '2026-11-14', promisedDate: '2026-11-14',
  createdAt: '2026-11-13T20:00:00', fittingInstructions: NOTES, notes: 'Order special note',
} as unknown as Job;

const STORE = { storeName: 'Better Vision', address: 'Main Road', city: 'Bokaro', state: 'Jharkhand', pincode: '827001' };

describe('browser zone is America/New_York', () => {
  it('sanity: the local zone really is west of UTC', () => {
    expect(new Date('2026-11-14').getDate()).toBe(13);
  });
});

describe('job detail drawer (ordinary bench view)', () => {
  it('shows the fitting instructions and the IST promise day', () => {
    const page = {
      selectedJob: JOB, setSelectedJob: vi.fn(), isOverdue: () => false, formatDate: formatJobDate,
      loadJobs: vi.fn(), lensBusy: false, handleLensAdvance: vi.fn(), canConfirmFitting: false,
      setFittingJob: vi.fn(), handleStatusChange: vi.fn(), canRunQc: false, setQcModalJob: vi.fn(),
      openReworkModal: vi.fn(), qcBusy: false, pickupName: '', setPickupName: vi.fn(), notifyBusy: false,
      handleNotifyReady: vi.fn(), setLabelSpec: vi.fn(), setPrintJob: vi.fn(),
    } as unknown as WorkshopPageState;
    render(<WorkshopJobDetail page={page} />);
    expect(screen.getByText(NOTES)).toBeTruthy();
    expect(screen.getByText('Order special note')).toBeTruthy();
    expect(screen.queryByText('13 Nov')).toBeNull();
    // created 2026-11-13T20:00 UTC (naive) = 14 Nov 01:30 IST
    expect(screen.getAllByText('14 Nov').length).toBe(2);
  });

  it('formats a missing date as Not on file', () => {
    expect(formatJobDate(null)).toBe('Not on file');
    expect(formatJobDate('garbage')).toBe('Not on file');
  });
});

describe('printed job card', () => {
  it('carries the fitting instructions and prints the IST due day', () => {
    const { container } = render(
      <WorkshopJobCardPrint job={jobCardPrintData(JOB)} store={STORE} onClose={vi.fn()} />,
    );
    const text = container.textContent || '';
    expect(text).toContain(NOTES);
    expect(text).toContain('Order special note');
    expect(text).toContain('14-Nov-2026');
    expect(text).not.toContain('13-Nov-2026');
  });

  it('a raw date-only due date also prints as that calendar day', () => {
    const { container } = render(
      <WorkshopJobCardPrint job={{ ...jobCardPrintData(JOB), dueDate: '2026-11-14', createdDate: '2026-11-14' }} store={STORE} onClose={vi.fn()} />,
    );
    expect(container.textContent).toContain('14-Nov-2026');
    expect(container.textContent).not.toContain('13-Nov-2026');
  });
});
