// ============================================================================
// PATIENT SAFETY: the clinical queue's "Print Rx Card" button
// ============================================================================
// This is the button that hands a printed prescription to the patient, and its
// mapping carried the blank-vs-zero conflation three times over:
//
//     rightEye: { sphere: readEyePower(test, 'right', 'sphere') || 0, ... }
//     add: 0,
//     pd: 0,
//
// So a power the clinician never recorded printed as "0.00" -- a confident
// claim of plano -- and EVERY card ever printed from this button asserted "no
// reading addition", because `add` was hard-coded to zero rather than read.
//
// PrescriptionCardBlankVsZero.test.tsx pins how the CARD renders a value. This
// file pins what this call site FEEDS it, which is a separate failure: a card
// that renders null correctly is no help if the caller sends 0.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const H = vi.hoisted(() => ({ getTodayTests: vi.fn() }));

const MOCK_USER = { id: 'u1', name: 'Dr Rao', roles: ['OPTOMETRIST'], activeStoreId: 'BV-BOK-01' };
// Hoisted to a STABLE object: a fresh one per render changes the identity the
// page's effects depend on and re-triggers the load forever.
const MOCK_AUTH = {
  user: MOCK_USER,
  hasRole: (role: string | string[]) =>
    (Array.isArray(role) ? role : [role]).some((r) => MOCK_USER.roles.includes(r)),
  hasPermission: () => true,
};
vi.mock('../../../context/AuthContext', () => ({ useAuth: () => MOCK_AUTH }));

vi.mock('../../../services/api', () => ({
  clinicalApi: {
    getQueue: () => Promise.resolve({ queue: [] }),
    getTodayTests: H.getTodayTests,
    startTest: vi.fn(),
    completeTest: vi.fn(),
    removeFromQueue: vi.fn(),
    addToQueue: vi.fn(),
  },
  customerApi: { search: () => Promise.resolve([]), getCustomer: () => Promise.resolve(null) },
  prescriptionApi: { getPrescriptions: () => Promise.resolve({ prescriptions: [] }) },
}));

vi.mock('../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: () => Promise.resolve(null),
}));

// The page asks whether this is an online store via a react-query hook. Stubbed
// rather than wrapped in a QueryClientProvider: none of it bears on the powers.
vi.mock('../../../hooks/useIsOnlineStore', () => ({
  useIsOnlineStore: () => false,
  default: () => false,
}));

import { MemoryRouter } from 'react-router-dom';
import { ToastProvider } from '../../../context/ToastContext';
import { ClinicalPage } from '../ClinicalPage';

const SLOW = 20000;

// Distance-vision columns of the printed card: Eye SPH CYL AXIS ADD
const SPH = 1, CYL = 2, ADD = 4;

/** Load the page with ONE completed test and open its printed Rx card. */
async function printCardFor(right: Record<string, unknown>, left: Record<string, unknown>) {
  H.getTodayTests.mockResolvedValue({
    tests: [
      {
        id: 'T-1',
        patientName: 'Asha Kumari',
        customerPhone: '9000000001',
        customerId: 'c1',
        completedAt: '2026-08-01T10:00:00.000Z',
        rightEye: right,
        leftEye: left,
      },
    ],
  });

  const { container } = render(
    <MemoryRouter>
      <ToastProvider>
        <ClinicalPage />
      </ToastProvider>
    </MemoryRouter>,
  );

  // By ROLE: "Completed today" is also a stat-card label, so a bare text query
  // is ambiguous and would click the wrong node (or throw).
  fireEvent.click(await screen.findByRole('button', { name: /Completed today/ }, { timeout: SLOW }));
  fireEvent.click(await screen.findByTitle('Print Rx Card', {}, { timeout: SLOW }));
  await waitFor(() => expect(powerRows(container).length).toBeGreaterThan(1), { timeout: SLOW });
  const rows = powerRows(container);
  return { od: rows[0], os: rows[1], container };
}

function powerRows(container: HTMLElement): string[][] {
  const rows: string[][] = [];
  for (const table of Array.from(container.querySelectorAll('table'))) {
    const head = (table.querySelector('thead')?.textContent || '').toUpperCase();
    if (!(head.includes('SPH') && head.includes('CYL') && head.includes('ADD'))) continue;
    for (const tr of Array.from(table.querySelectorAll('tbody tr'))) {
      rows.push(Array.from(tr.querySelectorAll('td')).map((td) => (td.textContent || '').trim()));
    }
  }
  return rows;
}

beforeEach(() => {
  H.getTodayTests.mockReset();
});

describe('the printed card reflects what the eye test actually recorded', () => {
  it('prints "-" for powers the test never recorded, on BOTH eyes', async () => {
    // THE REQUIREMENT. `readEyePower(...) || 0` printed "0.00" here, telling
    // the patient no correction is needed for an eye nobody measured.
    const { od, os } = await printCardFor(
      { cylinder: -1.25, axis: 90 },
      {},
    );

    expect(od[SPH]).toBe('-');
    expect(os[SPH]).toBe('-');
    expect(os[CYL]).toBe('-');
    expect(od[SPH]).not.toBe('0.00');
    expect(od[SPH]).not.toBe('+0.00');
    // ...and what WAS recorded still prints.
    expect(od[CYL]).toBe('-1.25');
  }, SLOW);

  it('prints a recorded plano as +0.00 rather than a dash, on BOTH eyes', async () => {
    // The other direction, through the same mapping.
    const { od, os } = await printCardFor(
      { sphere: 0, cylinder: 0 },
      { sphere: 0, cylinder: 0 },
    );

    expect(od[SPH]).toBe('+0.00');
    expect(os[SPH]).toBe('+0.00');
    expect(od[CYL]).toBe('+0.00');
    expect(os[CYL]).toBe('+0.00');
  }, SLOW);

  it('does not claim "no reading addition" when no ADD was recorded', async () => {
    // `add: 0` was HARD-CODED, so every card printed from this button asserted
    // a measured zero near-add regardless of what the exam found.
    const { od, os } = await printCardFor({ sphere: -2 }, { sphere: -2 });

    expect(od[ADD]).toBe('-');
    expect(os[ADD]).toBe('-');
    expect(od[ADD]).not.toBe('+0.00');
  }, SLOW);

  it('prints the ADD the test DID record', async () => {
    // The control for the hard-coded zero: the field must be read, not invented
    // -- a mapping that always sent null would pass the test above.
    const { od, os } = await printCardFor({ sphere: -2, add: 2 }, { sphere: -2, add: 2 });

    expect(od[ADD]).toBe('+2.00');
    expect(os[ADD]).toBe('+2.00');
  }, SLOW);

  it('does not print a fabricated PD of 0', async () => {
    // `pd: 0` was hard-coded too. A PD of 0mm is anatomically impossible, so
    // printing it is printing a measurement that cannot exist.
    const { container } = await printCardFor({ sphere: -2 }, { sphere: -2 });

    expect(container.textContent).not.toContain('0mm');
    expect(container.textContent).not.toContain('Invalid Date');
  }, SLOW);
});

describe('the Completed-today list survives an Rx with fields simply absent', () => {
  it('renders the row as dashes instead of crashing the tab', async () => {
    // A CRASH, not a cosmetic bug. The page's own formatPower guarded only
    // `value === null`, but readEyePower returns UNDEFINED for an absent field
    // (a Mongo doc that omits the key, an import, a device feed), so it reached
    // `.toFixed` and threw -- taking the whole "Completed today" tab down.
    H.getTodayTests.mockResolvedValue({
      tests: [
        {
          id: 'T-2',
          patientName: 'Ravi Kumar',
          customerPhone: '9000000002',
          customerId: 'c2',
          completedAt: '2026-08-01T10:00:00.000Z',
          rightEye: {},
          leftEye: {},
        },
      ],
    });

    render(
      <MemoryRouter>
        <ToastProvider>
          <ClinicalPage />
        </ToastProvider>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: /Completed today/ }, { timeout: SLOW }));

    // THE REQUIREMENT: the row is on screen at all.
    expect(await screen.findByText('Ravi Kumar', {}, { timeout: SLOW })).toBeInTheDocument();
    expect(screen.getByText('R: - / -')).toBeInTheDocument();
    expect(screen.getByText('L: - / -')).toBeInTheDocument();
  }, SLOW);

  it('still shows a recorded plano as +0.00 in that same row', async () => {
    // The control: dashes everywhere would pass the test above.
    H.getTodayTests.mockResolvedValue({
      tests: [
        {
          id: 'T-3',
          patientName: 'Meera Devi',
          customerPhone: '9000000003',
          customerId: 'c3',
          completedAt: '2026-08-01T10:00:00.000Z',
          rightEye: { sphere: 0, cylinder: 0 },
          leftEye: { sphere: 0, cylinder: 0 },
        },
      ],
    });

    render(
      <MemoryRouter>
        <ToastProvider>
          <ClinicalPage />
        </ToastProvider>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: /Completed today/ }, { timeout: SLOW }));

    expect(await screen.findByText('R: +0.00 / +0.00', {}, { timeout: SLOW })).toBeInTheDocument();
    expect(screen.getByText('L: +0.00 / +0.00')).toBeInTheDocument();
  }, SLOW);
});
