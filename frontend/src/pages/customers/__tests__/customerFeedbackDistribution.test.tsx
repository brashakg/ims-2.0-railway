// L1-F51: the Score Distribution is drawn from the server's per-score counts
// (same population as the promoter/detractor summary), not from the latest
// twenty verbatim responses, so an older detractor is not dropped.
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('../../../context/AuthContext', () => ({ useAuth: () => ({ user: { activeStoreId: 'S1' } }) }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => ({ error: vi.fn(), success: vi.fn() }) }));
const getNpsDashboard = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api/marketing', () => ({ marketingApi: { getNpsDashboard } }));

import { CustomerFeedback } from '../CustomerFeedback';

describe('CustomerFeedback score distribution', () => {
  it('counts the older detractor that is not in the latest twenty', async () => {
    const dist: Record<string, number> = Object.fromEntries(Array.from({ length: 11 }, (_, i) => [String(i), 0]));
    dist['10'] = 20;
    dist['0'] = 1;
    getNpsDashboard.mockResolvedValue({
      avg_score: 9.5, promoters: 20, passives: 0, detractors: 1, response_rate: 100,
      total_surveys: 21, total_responses: 21, nps_score: 90.5, score_distribution: dist,
      responses: Array.from({ length: 20 }, (_, i) => ({ nps_id: `P${i}`, score: 10 })),
    });
    render(<CustomerFeedback />);
    await screen.findByText('Score Distribution');
    const counts = screen.getAllByText(/^\d+ responses$/);
    // distribution rows render 10 -> 0, after the summary card's total
    expect(counts.map((c) => c.textContent)).toContain('20 responses');
    expect(counts[counts.length - 1].textContent).toBe('1 responses');
  });
});
