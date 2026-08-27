// ============================================================================
// IMS 2.0 - Workshop productivity scorecard names the technician
// ============================================================================
// The Technician column rendered t.technician_id -- a raw internal user id.
// The report endpoint now stamps technician_id_name beside it; the card must
// PREFER the name and print an unresolved id VERBATIM (never an invented
// name), keeping 'Unassigned' for the no-technician bucket.

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

// NOTE: repeated as a literal inside the vi.mock factory below -- vi.mock is
// hoisted above this const and must not reference it.
const RAW_ID = 'qa-deadbeef';

vi.mock('../../../../services/api', () => ({
  reportsApi: {
    getWorkshopProductivity: vi.fn().mockResolvedValue({
      from_date: '2026-08-01',
      to_date: '2026-08-27',
      store_id: 'BV-01',
      technicians: [
        {
          technician_id: 'tech-1',
          technician_id_name: 'Ramesh Kumar',
          jobs_completed: 8,
          avg_turnaround_days: 1.5,
          qc_fail_rate: 0,
          qc_jobs: 8,
          on_time_rate: 1,
          remake_jobs: 0,
          utilization: 1,
        },
        {
          technician_id: 'qa-deadbeef',
          jobs_completed: 2,
          avg_turnaround_days: 2,
          qc_fail_rate: null,
          qc_jobs: 0,
          on_time_rate: null,
          remake_jobs: 0,
          utilization: 0.25,
        },
      ],
      totals: {
        jobs_completed: 10,
        avg_turnaround_days: 1.6,
        qc_fail_rate: 0,
        on_time_rate: 1,
        remake_rate: 0,
        technicians_active: 2,
      },
    }),
  },
}));

import { WorkshopProductivityCard } from '../WorkshopProductivityCard';

describe('WorkshopProductivityCard technician names', () => {
  it('shows the resolved name, and an unresolved id verbatim', async () => {
    render(<WorkshopProductivityCard storeId="BV-01" />);

    // Resolved technician: the PERSON, not the id.
    expect(await screen.findByText('Ramesh Kumar')).toBeTruthy();
    expect(screen.queryByText('tech-1')).toBeNull();

    // Deleted account: the stored id must survive untouched.
    expect(screen.getByText(RAW_ID)).toBeTruthy();
  });
});
