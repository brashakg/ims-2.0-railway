// Audit b21 review (R23 follow-up): the inbox and clinical-inbox reads now
// answer 503 on a database failure. The Hub must show that as "unavailable"
// with a Retry, never as the same blank it shows for zero handoffs.
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';

const listInbox = vi.fn();
const listClinicalInbox = vi.fn();
vi.mock('../../../services/api/handoffs', () => ({
  handoffsApi: {
    listInbox: (...a: unknown[]) => listInbox(...a),
    listClinicalInbox: (...a: unknown[]) => listClinicalInbox(...a),
    acknowledgeClinical: vi.fn(),
    markServedClinical: vi.fn(),
  },
}));
vi.mock('../../../hooks/useNow', () => ({ useNow: () => new Date('2026-11-16T10:00:00Z') }));

import HandoffInboxCard from '../HandoffInboxCard';
import { ClinicalHandoverCard } from '../ClinicalHandoverCard';

afterEach(() => { cleanup(); vi.clearAllMocks(); });
const down = () => Promise.reject(new Error('Handoffs are unavailable right now'));

describe('Hub handoff sections never turn a failed read into an empty section', () => {
  it('inbox: failure shows unavailable + Retry; Retry recovers', async () => {
    listInbox.mockImplementationOnce(down).mockResolvedValueOnce({ handoffs: [], total: 0 });
    render(<HandoffInboxCard />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/Handoffs are unavailable/);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(listInbox).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });

  it('inbox: a real empty read stays silent', async () => {
    listInbox.mockResolvedValueOnce({ handoffs: [], total: 0 });
    const { container } = render(<HandoffInboxCard />);
    await waitFor(() => expect(listInbox).toHaveBeenCalled());
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('clinical: failure shows unavailable + Retry, not nothing', async () => {
    listClinicalInbox.mockImplementationOnce(down).mockResolvedValueOnce({ handoffs: [] });
    render(<ClinicalHandoverCard />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/Clinical handovers are unavailable/);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(listClinicalInbox).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });
});
