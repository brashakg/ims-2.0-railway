// ============================================================================
// A queue row opens the examination PAGE -- never a popup over the queue
// ============================================================================
// Owner (2026-09-04), with a screenshot of the exam opening as a modal over
// /clinical/queue: "why is this screen still a pop up".
//
// Drives the REAL queue page inside a router whose /clinical/test/:entryId
// route is a sentinel that prints the entry id it was opened with. A revert
// to the modal keeps the queue page mounted (no navigation), so the sentinel
// never appears and the assertions below fail.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter, Routes, Route, useParams } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const H = vi.hoisted(() => ({ getQueue: vi.fn(), startTest: vi.fn() }));

vi.mock('../../../services/api', () => ({
  clinicalApi: {
    getQueue: (...a: unknown[]) => H.getQueue(...a),
    startTest: (...a: unknown[]) => H.startTest(...a),
    removeFromQueue: vi.fn(),
  },
  prescriptionApi: { getFamilyRx: vi.fn() },
  customerApi: { getCustomers: vi.fn() },
}));

const MOCK_USER = { id: 'u1', name: 'Dr Rao', roles: ['OPTOMETRIST'], activeStoreId: 'BV-BOK-01' };
const MOCK_AUTH = {
  user: MOCK_USER,
  hasRole: (role: string | string[]) =>
    (Array.isArray(role) ? role : [role]).some((r) => MOCK_USER.roles.includes(r)),
  hasPermission: () => true,
};
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => MOCK_AUTH }));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: () => {}, error: () => {}, warning: () => {}, info: () => {} }),
}));
vi.mock('../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: () => Promise.resolve(null),
}));

import { ClinicalQueuePage } from '../ClinicalQueuePage';

const SLOW = 20000;

const WAITING = {
  id: 'q-1', tokenNumber: 'A-01', patientName: 'Asha Kumari', customerPhone: '9000000001',
  age: 44, status: 'WAITING', waitTime: 3, createdAt: new Date().toISOString(), customerId: 'c1',
};
const IN_PROGRESS = {
  id: 'q-2', tokenNumber: 'A-02', patientName: 'Rahul Sharma', customerPhone: '9000000002',
  age: 34, status: 'IN_PROGRESS', waitTime: 12, createdAt: new Date().toISOString(),
  testId: 't-2', customerId: 'c2',
};

function ExamSentinel() {
  const { entryId } = useParams();
  return <div>EXAM-PAGE-SENTINEL for {entryId}</div>;
}

function renderQueue() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/clinical/queue']}>
        <Routes>
          <Route path="/clinical/queue" element={<ClinicalQueuePage />} />
          <Route path="/clinical/test/:entryId" element={<ExamSentinel />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const anyPopup = () => document.querySelector('.scrim, .modal-overlay, [role="dialog"]');

beforeEach(() => {
  H.getQueue.mockReset();
  H.startTest.mockReset();
  H.getQueue.mockResolvedValue({ queue: [WAITING, IN_PROGRESS] });
  H.startTest.mockResolvedValue({ testId: 't-1' });
});

describe('the queue opens the examination as a page', () => {
  it('Continue on an in-progress row navigates to /clinical/test/<entry id>; no popup opens', async () => {
    renderQueue();
    fireEvent.click(await screen.findByRole('button', { name: /Continue/ }, { timeout: SLOW }));

    expect(await screen.findByText('EXAM-PAGE-SENTINEL for q-2', {}, { timeout: SLOW })).toBeInTheDocument();
    expect(anyPopup()).toBeNull();
    // The queue itself is gone from the screen -- a page, not an overlay on it.
    expect(screen.queryByRole('button', { name: /Continue/ })).toBeNull();
  }, SLOW);

  it('Start on a waiting row starts the test, then navigates to the same address', async () => {
    renderQueue();
    fireEvent.click(await screen.findByRole('button', { name: /Start/ }, { timeout: SLOW }));

    await waitFor(() => expect(H.startTest).toHaveBeenCalledWith('q-1'));
    expect(await screen.findByText('EXAM-PAGE-SENTINEL for q-1', {}, { timeout: SLOW })).toBeInTheDocument();
    expect(anyPopup()).toBeNull();
  }, SLOW);

  it('a failed start stays on the queue rather than opening an exam with no test', async () => {
    H.startTest.mockRejectedValue(new Error('boom'));
    renderQueue();
    fireEvent.click(await screen.findByRole('button', { name: /Start/ }, { timeout: SLOW }));

    await waitFor(() => expect(H.startTest).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/EXAM-PAGE-SENTINEL/)).toBeNull();
    expect(screen.getByRole('button', { name: /Start/ })).toBeInTheDocument();
  }, SLOW);

  it('mounts no exam form at all while the queue is on screen', async () => {
    renderQueue();
    await screen.findByRole('button', { name: /Continue/ }, { timeout: SLOW });
    expect(anyPopup()).toBeNull();
    expect(screen.queryByRole('button', { name: /Save Prescription|Complete test|Save & pause/ })).toBeNull();
    expect(screen.queryByLabelText('Right SPH')).toBeNull();
  }, SLOW);
});
