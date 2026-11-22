// ============================================================================
// PR-1425 reviewer must-fix: the Workshop "Jobs by stage" board uses the same
// IST overdue rule as the job list (workshop/shared.isPastDueIst). promisedDate
// is now filled from expected_date ('YYYY-MM-DD'); the old instant comparison
// `new Date(promisedDate) < new Date()` marked a job due TODAY overdue from
// 05:30 IST (and from the evening before in a browser west of UTC).
// ============================================================================
process.env.TZ = 'America/New_York';

import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StageMonitorBoard } from '../StageMonitorBoard';

// 10:00 IST on 9 Nov 2026 = 04:30 UTC (= 23:30 on 8 Nov in New York).
const TEN_AM_IST = new Date('2026-11-09T04:30:00Z');

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(TEN_AM_IST);
});
afterEach(() => vi.useRealTimers());

const job = (id: string, promisedDate: string, status = 'IN_PROGRESS') => ({
  id, jobNumber: id, customerName: 'Asha', status, priority: 'NORMAL', promisedDate,
});

describe('StageMonitorBoard overdue rule (IST day, same as the job list)', () => {
  it('a job due today (IST) is not overdue', () => {
    render(<StageMonitorBoard jobs={[job('WS-TODAY', '2026-11-09')]} />);
    expect(screen.queryByText('Overdue')).toBeNull();
  });

  it('a job due today is not overdue at 20:00 New York the evening before (06:30 IST)', () => {
    vi.setSystemTime(new Date('2026-11-09T01:00:00Z'));
    render(<StageMonitorBoard jobs={[job('WS-TODAY-NY', '2026-11-09')]} />);
    expect(screen.queryByText('Overdue')).toBeNull();
  });

  it('a job due yesterday (IST) is overdue', () => {
    render(<StageMonitorBoard jobs={[job('WS-LATE', '2026-11-08')]} />);
    expect(screen.getByText('Overdue')).toBeTruthy();
  });

  it('a delivered, ready or cancelled job is never marked overdue (as in the job list)', () => {
    render(
      <StageMonitorBoard
        jobs={[
          job('WS-DLV', '2026-11-01', 'DELIVERED'),
          job('WS-RDY', '2026-11-01', 'READY'),
          job('WS-CXL', '2026-11-01', 'CANCELLED'),
        ]}
      />,
    );
    expect(screen.queryByText('Overdue')).toBeNull();
  });

  it('no promise date on file is not overdue', () => {
    render(<StageMonitorBoard jobs={[job('WS-NONE', '')]} />);
    expect(screen.queryByText('Overdue')).toBeNull();
  });
});

// The traveler (work-order) label prints the same promise date: it is the IST
// calendar day in every browser zone (the old local-zone formatter printed
// '13 Nov 2026' for '2026-11-14' in New York).
import { travelerHtml, travelerZpl } from '../labelTemplates';

describe('traveler label promise date', () => {
  const d = { job_id: 'J1', job_number: 'WS-1', promised_date: '2026-11-14' };
  it('HTML and ZPL print 14 Nov 2026 in a New York browser', () => {
    expect(travelerHtml(d)).toContain('14 Nov 2026');
    expect(travelerZpl(d)).toContain('Promised: 14 Nov 2026');
  });
  it('an unreadable value prints as-is, an empty one prints nothing', () => {
    expect(travelerZpl({ ...d, promised_date: 'soon' })).toContain('Promised: soon');
    expect(travelerZpl({ ...d, promised_date: '' })).toContain('Promised: ^FS');
  });
});
