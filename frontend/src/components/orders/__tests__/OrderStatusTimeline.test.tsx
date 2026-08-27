// ============================================================================
// IMS 2.0 - Order Status Timeline names the person, not the user id
// ============================================================================
// The timeline's "Changed by:" line rendered entry.changedBy straight from
// status_history -- a raw internal user id ("97d2a24c-..."). The backend now
// resolves changedByName beside it; this component must PREFER the name and,
// when the id names nobody (deleted QA logins exist in prod), fall back to
// printing the stored id VERBATIM -- never an invented name.

import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import { OrderStatusTimeline } from '../OrderStatusTimeline';
import type { StatusHistory } from '../../../types';

const RAW_ID = '97d2a24c-2b1f-4f7e-9c33-deadbeef0000';

function history(entry: Partial<StatusHistory>): StatusHistory[] {
  return [
    {
      status: 'CONFIRMED',
      timestamp: '2026-08-25T10:00:00',
      changedBy: RAW_ID,
      ...entry,
    } as StatusHistory,
  ];
}

describe('OrderStatusTimeline actor names', () => {
  it('shows the resolved name when the backend supplied one', () => {
    render(
      <OrderStatusTimeline
        statusHistory={history({ changedByName: 'Priya Nair' })}
        createdAt="2026-08-25T09:00:00"
        createdBy="System"
      />,
    );
    expect(screen.getByText('Changed by: Priya Nair')).toBeTruthy();
    expect(screen.queryByText(`Changed by: ${RAW_ID}`)).toBeNull();
  });

  it('prints the stored id verbatim when it resolves to nobody', () => {
    render(
      <OrderStatusTimeline
        statusHistory={history({})}
        createdAt="2026-08-25T09:00:00"
        createdBy="System"
      />,
    );
    // Traceability over cosmetics: the id must survive untouched.
    expect(screen.getByText(`Changed by: ${RAW_ID}`)).toBeTruthy();
  });
});
