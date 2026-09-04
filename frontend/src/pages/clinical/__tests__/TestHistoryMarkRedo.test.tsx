// ============================================================================
// "Mark redo" survives the door consolidation
// ============================================================================
// The standalone /prescriptions page was DELETED in the Wave 2 split, and it
// was the ONLY caller of clinicalApi.recordRedo — a live backend feature whose
// records feed clinical abuse detection (backend/api/services/clinical_abuse).
// The action was re-homed on the Test History detail modal, where the Rx id is
// already at hand (test.prescriptionId). This file pins that the door is still
// open AND still guarded:
//
//   * the recorded redo targets the PRESCRIPTION id, not the eye-test id
//   * the reason is required and trimmed
//   * a cancelled prompt records nothing
//   * a test with no linked Rx records nothing (and never even prompts)

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const H = vi.hoisted(() => ({
  getTests: vi.fn(),
  recordRedo: vi.fn(),
}));

const MOCK_USER = { id: 'u1', name: 'Dr Rao', roles: ['OPTOMETRIST'], activeStoreId: 'BV-BOK-01' };
const MOCK_AUTH = {
  user: MOCK_USER,
  hasRole: (role: string | string[]) =>
    (Array.isArray(role) ? role : [role]).some((r) => MOCK_USER.roles.includes(r)),
  hasPermission: () => true,
};
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => MOCK_AUTH }));

vi.mock('../../../services/api', () => ({
  clinicalApi: {
    getTests: H.getTests,
    getSoapNote: () => Promise.resolve({ soapNote: null }),
    getPrescriptionPrintHtml: vi.fn(() => Promise.resolve('<html></html>')),
    recordRedo: H.recordRedo,
  },
}));

import { MemoryRouter } from 'react-router-dom';
import { ToastProvider } from '../../../context/ToastContext';
import { TestHistoryPage } from '../TestHistoryPage';

const SLOW = 20000;

function testRow(overrides: Record<string, unknown> = {}) {
  return {
    id: 'TEST-77',
    patientName: 'Asha Kumari',
    customerPhone: '9000000001',
    completedAt: '2026-08-01T10:00:00.000Z',
    prescriptionId: 'RX-9',
    rightEye: { sphere: -1, cylinder: null, axis: null, add: null },
    leftEye: { sphere: -1, cylinder: null, axis: null, add: null },
    ...overrides,
  };
}

async function openDetailAndFindRedo(row: Record<string, unknown>) {
  H.getTests.mockResolvedValue({ tests: [row] });
  render(
    <MemoryRouter>
      <ToastProvider>
        <TestHistoryPage />
      </ToastProvider>
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByText('Asha Kumari', {}, { timeout: SLOW }));
  return await screen.findByRole('button', { name: /Mark redo/ }, { timeout: SLOW });
}

beforeEach(() => {
  H.getTests.mockReset();
  H.recordRedo.mockReset();
  H.recordRedo.mockResolvedValue({ message: 'Redo recorded' });
  vi.restoreAllMocks();
});

describe('Mark redo on the Test History detail modal', () => {
  it('records a redo against the PRESCRIPTION id with the trimmed reason', async () => {
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue('  wrong axis  ');
    const redoBtn = await openDetailAndFindRedo(testRow());

    fireEvent.click(redoBtn);

    await waitFor(() => expect(H.recordRedo).toHaveBeenCalledTimes(1), { timeout: SLOW });
    // The Rx id, NOT the eye-test id — a redo against TEST-77 would 404.
    expect(H.recordRedo).toHaveBeenCalledWith('RX-9', 'wrong axis');
    expect(promptSpy).toHaveBeenCalled();
  }, SLOW);

  it('records nothing when the prompt is cancelled', async () => {
    vi.spyOn(window, 'prompt').mockReturnValue(null);
    const redoBtn = await openDetailAndFindRedo(testRow());

    fireEvent.click(redoBtn);

    // Give any wrongly-fired async call a beat to land before asserting.
    await new Promise((r) => setTimeout(r, 50));
    expect(H.recordRedo).not.toHaveBeenCalled();
  }, SLOW);

  it('records nothing for a blank reason', async () => {
    vi.spyOn(window, 'prompt').mockReturnValue('   ');
    const redoBtn = await openDetailAndFindRedo(testRow());

    fireEvent.click(redoBtn);

    await new Promise((r) => setTimeout(r, 50));
    expect(H.recordRedo).not.toHaveBeenCalled();
  }, SLOW);

  it('never prompts, never records, when the test has no linked prescription', async () => {
    const promptSpy = vi.spyOn(window, 'prompt').mockReturnValue('should never be read');
    const redoBtn = await openDetailAndFindRedo(testRow({ prescriptionId: undefined }));

    fireEvent.click(redoBtn);

    await new Promise((r) => setTimeout(r, 50));
    expect(promptSpy).not.toHaveBeenCalled();
    expect(H.recordRedo).not.toHaveBeenCalled();
  }, SLOW);
});
