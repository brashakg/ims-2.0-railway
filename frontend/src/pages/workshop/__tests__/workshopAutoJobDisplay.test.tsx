// ============================================================================
// L2-WS-F02 / L2-UI-F01 - an auto-created workshop job on the board and on the
// printed job card
// ============================================================================
// The confirm safety net's job carries no priority, and a job whose order lost
// its customer has no name / phone / due date. Before the fix the Print Card
// crashed (`undefined.toUpperCase()`), the board showed blanks and the date
// read "Invalid Date". Now a missing detail reads "Not on file" and the card
// prints. The phone layout (L2-UI-F01) is pinned here only as its class
// contract; jsdom cannot measure overlap -- that needs a browser at 390 px.

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { WorkshopJobCardPrint } from '../../../components/print/WorkshopJobCardPrint';
import { WorkshopJobList } from '../WorkshopJobList';
import type { WorkshopPageState } from '../useWorkshopPage';
import type { Job } from '../shared';

const STORE = {
  storeName: 'Better Vision',
  address: 'Main Road',
  city: 'Bokaro',
  state: 'Jharkhand',
  pincode: '827001',
};

describe('WorkshopJobCardPrint with an auto-created job', () => {
  it('prints with no priority, order or due date on file instead of crashing', () => {
    render(
      <WorkshopJobCardPrint
        job={{
          jobNumber: 'WS-261104-45039BF8',
          orderNumber: '',
          customerName: '',
          customerPhone: '',
          frameBrand: '',
          frameModel: '',
          frameColor: '',
          lensType: '',
          priority: undefined as unknown as string,
          dueDate: null as unknown as string,
          status: 'Pending',
          createdDate: '2026-11-04',
        }}
        store={STORE}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getAllByText('NORMAL').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Not on file/).length).toBeGreaterThan(0);
  });
});

function pageWith(jobs: Partial<Job>[]): WorkshopPageState {
  const formatDate = (d: string | null | undefined) => {
    const v = d ? new Date(d) : null;
    return !v || Number.isNaN(v.getTime()) ? 'Not on file' : v.toISOString().slice(0, 10);
  };
  return {
    isLoading: false,
    filteredJobs: jobs as Job[],
    searchQuery: '',
    statusFilter: 'ACTIVE',
    priorityFilter: 'ALL',
    isOverdue: (d: string | null | undefined) => !!d && new Date(d) < new Date(),
    formatDate,
    setSelectedJob: vi.fn(),
  } as unknown as WorkshopPageState;
}

describe('WorkshopJobList card', () => {
  it('shows a missing customer / frame / date as Not on file, never blank or Invalid Date', () => {
    render(
      <WorkshopJobList
        page={pageWith([
          { id: 'J1', jobNumber: 'WS-261104-45039BF8', status: 'PENDING', priority: 'NORMAL',
            customerName: '', customerPhone: '', frameName: '', lensType: '',
            promisedDate: null as unknown as string },
        ])}
      />,
    );
    expect(screen.getAllByText('Not on file').length).toBeGreaterThanOrEqual(5);
    expect(screen.queryByText(/Invalid Date/)).toBeNull();
    expect(screen.queryByText('Overdue')).toBeNull();
  });

  it('stacks the date/actions under the job info on a phone and wraps the badges', () => {
    const { container } = render(
      <WorkshopJobList
        page={pageWith([
          { id: 'J1', jobNumber: 'WS-261104-45039BF8', status: 'READY', priority: 'NORMAL',
            customerName: 'Asha Rao', customerPhone: '9800000001', frameName: 'Ray-Ban RB2140',
            lensType: 'Progressive 1.67', promisedDate: '2026-11-09', lens_status: 'MOUNTED' },
        ])}
      />,
    );
    const number = screen.getByText('WS-261104-45039BF8');
    expect(number.parentElement!.className).toMatch(/\bflex-wrap\b/);
    const row = container.querySelector('.card > div')!;
    expect(row.className).toMatch(/\bflex-col\b/);
    expect(row.className).toMatch(/\bsm:flex-row\b/);
  });
});
