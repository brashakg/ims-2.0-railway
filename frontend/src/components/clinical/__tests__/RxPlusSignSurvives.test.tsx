// ============================================================================
// A typed "+4.00" must still be "+4.00" on the wire, and "+4.00" on screen
// ============================================================================
// Owner report 2026-08-24: "when putting +4.00 power, plus is removed and only
// 4.00 is shown."
//
// The sign was not lost in the input and not lost by the formatter. It was
// destroyed on the WRITE path, at the one moment a signed string was coerced
// to a number:
//
//   PrescriptionForm.handleRxChange : parseFloat("+4.00")            -> 4
//   ClinicalPage eye-test mapping   : parseValue("+4.00")            -> 4
//   sales.ts _rxStr                 : String(4)                      -> "4"
//
// After that no formatter downstream can restore the plus, because the plus is
// not missing, it never existed. So there are two halves to pin:
//   (1) the write path keeps the text, and
//   (2) the display surfaces RENDER the power rather than echoing the stored
//       string -- because every row written before this fix is unsigned.
//
// Nothing here stubs a formatter or a validator: the subject of each test is
// the real component or the real mapper.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import { finalRxPayload, examTabsPayload } from '../eyeTestPayload';
import type { FinalRxData } from '../eyeTestTypes';

// ---------------------------------------------------------------------------
// (1) WRITE PATH -- the eye examination
// ---------------------------------------------------------------------------
function finalRx(over: Partial<FinalRxData> = {}): FinalRxData {
  const eye = { sphere: '', cylinder: '', axis: '', add: '', pd: '', va: '', prism: '', base: '' };
  return {
    rightEye: { ...eye },
    leftEye: { ...eye },
    rightAdd: '', leftAdd: '', ipd: '', lensType: '', nextCheckup: '', remarks: '',
    ...over,
  } as FinalRxData;
}

describe('the eye-exam write path keeps the sign', () => {
  it('sends "+4.00", not the number 4 and not "4"', () => {
    const wire = finalRxPayload(
      finalRx({ rightEye: { ...finalRx().rightEye, sphere: '+4.00' } }),
    );

    expect(wire.rightEye.sphere).toBe('+4.00');
    // The exact failure the owner reported, asserted directly.
    expect(wire.rightEye.sphere).not.toBe('4');
    expect(wire.rightEye.sphere).not.toBe(4);
  });

  it('NEVER loses a minus', () => {
    const wire = finalRxPayload(
      finalRx({ leftEye: { ...finalRx().leftEye, sphere: '-4.00', cylinder: '-0.75' } }),
    );

    expect(wire.leftEye.sphere).toBe('-4.00');
    expect(wire.leftEye.cylinder).toBe('-0.75');
  });

  it('keeps a recorded plano as a power and a blank as null', () => {
    // The blank-vs-zero rule, which a "just add a plus" fix would break.
    const wire = finalRxPayload(
      finalRx({ rightEye: { ...finalRx().rightEye, sphere: '0.00' } }),
    );

    expect(wire.rightEye.sphere).toBe('0.00');
    expect(wire.rightEye.cylinder).toBeNull();
    expect(wire.leftEye.sphere).toBeNull();
  });

  it('keeps the sign on every exam tab, not just the final Rx', () => {
    const reading = { sphere: '+4.00', cylinder: '-0.75', axis: '90', add: '', pd: '', va: '' };
    const wire = examTabsPayload({
      lensometer: { rightEye: reading, leftEye: { ...reading, sphere: '-2.25' }, remarks: '' },
      autoRef: {
        rightEye: { ...reading, k1: '42.50', k1Axis: '', k2: '', k2Axis: '' },
        leftEye: { ...reading, k1: '', k1Axis: '', k2: '', k2Axis: '' },
        remarks: '',
      },
      subjectiveRx: { rightEye: reading, leftEye: reading, remarks: '' },
    });

    expect(wire.lensometer?.rightEye?.sphere).toBe('+4.00');
    expect(wire.lensometer?.leftEye?.sphere).toBe('-2.25');
    expect(wire.autoRef?.rightEye?.sphere).toBe('+4.00');
    expect(wire.subjectiveRx?.rightEye?.sphere).toBe('+4.00');
  });
});

// ---------------------------------------------------------------------------
// (2) DISPLAY -- the sales floor's handover card, asserted on rendered DOM
// ---------------------------------------------------------------------------
const listClinicalInbox = vi.fn();
vi.mock('../../../services/api/handoffs', () => ({
  handoffsApi: {
    listClinicalInbox: (...a: unknown[]) => listClinicalInbox(...a),
    acknowledgeClinical: vi.fn(),
    markServedClinical: vi.fn(),
  },
}));
vi.mock('../../../hooks/useNow', () => ({ useNow: () => new Date('2026-08-24T10:00:00Z') }));

import { ClinicalHandoverCard } from '../../handoffs/ClinicalHandoverCard';

function handover(right: Record<string, unknown>) {
  return {
    handoff_id: 'h1',
    patient_name: 'Asha Kumari',
    optometrist_name: 'Rao',
    created_at: '2026-08-24T09:55:00Z',
    acknowledged_by: null,
    mark_served: false,
    product_recommendations: [],
    rx_summary: { right_eye: right, left_eye: {} },
  };
}

async function renderHandover(right: Record<string, unknown>) {
  listClinicalInbox.mockResolvedValue({ handoffs: [handover(right)] });
  const { container } = render(<ClinicalHandoverCard />);
  await waitFor(() => expect(screen.getByText('Asha Kumari')).toBeTruthy());
  return (container.textContent || '');
}

describe('the clinical handover card RENDERS the power', () => {
  it('shows +4.00 for a power stored unsigned as "4"', async () => {
    // Every row written before this fix is unsigned, so echoing the stored
    // string is not good enough -- the card has to render it.
    const text = await renderHandover({ sph: '4' });
    expect(text).toContain('+4.00');
  });

  it('shows -4.00 for a minus power', async () => {
    const text = await renderHandover({ sph: '-4.00' });
    expect(text).toContain('-4.00');
    expect(text).not.toContain('+4.00');
  });

  it('shows a recorded plano rather than silently dropping it', async () => {
    const text = await renderHandover({ sph: '0', cyl: '-1.00', axis: 90 });
    expect(text).toContain('+0.00');
    expect(text).toContain('-1.00');
  });

  it('does NOT invent a power for an eye with nothing recorded', async () => {
    const text = await renderHandover({});
    expect(text).not.toContain('+0.00');
    expect(text).toContain('—');
  });
});
