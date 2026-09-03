// ============================================================================
// THE PAGE MUST NOT INVENT PROCEDURES
// ============================================================================
// When the SOP list came back empty, TaskManagementPage rendered four
// hard-coded SOPs styled exactly like real ones - including "End of Day Cash
// Reconciliation", which told staff to verify a Rs 5,000 opening float and
// retain Rs 5,000 in the safe overnight. In four live shops nobody could tell
// that from policy the owner wrote.
//
// Owner ruling 2026-09-03: delete them, show an honest empty state, invent no
// replacements. Asserted here against the real page with an empty server list.

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../../services/api', () => ({
  tasksApi: {
    getSopTemplates: vi.fn(async () => ({ templates: [], total: 0 })),
    seedDefaultSops: vi.fn(),
  },
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'USR-1', activeStoreId: 'BV-PUN-01', roles: ['STORE_MANAGER'] } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../../components/tasks/SopEditorModal', () => ({
  SopEditorModal: () => null,
}));

import { tasksApi } from '../../../services/api';
import { TasksSopPage } from '../TasksSopPage';

const seedDefaultSops = tasksApi.seedDefaultSops as unknown as ReturnType<typeof vi.fn>;

function renderSops() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TasksSopPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  document.body.innerHTML = '';
});

describe('SOP library with nothing configured', () => {
  it('says so honestly instead of rendering invented procedures', async () => {
    renderSops();
    expect(await screen.findByText(/No SOPs have been written yet/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Write the first SOP/i })).toBeInTheDocument();
  });

  it('shows none of the four fabricated SOPs, and no invented cash figure', async () => {
    const { container } = renderSops();
    await screen.findByText(/No SOPs have been written yet/i);

    const text = container.textContent || '';
    for (const fiction of [
      'Store Opening Procedure',
      'End of Day Cash Reconciliation',
      'Customer Order Processing',
      'Inventory Receiving',
      '5,000',
      'starting float',
    ]) {
      expect(text).not.toContain(fiction);
    }
  });

  it('does not offer to plant a pre-written starter set', async () => {
    renderSops();
    await screen.findByText(/No SOPs have been written yet/i);
    expect(screen.queryByText(/starter checklist/i)).not.toBeInTheDocument();
    expect(seedDefaultSops).not.toHaveBeenCalled();
  });
});
