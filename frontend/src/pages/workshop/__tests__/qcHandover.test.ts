/**
 * Workshop QC-handover helper regressions (PR #971 adversarial fix round).
 *
 * Covers the two counter-facing failures the patient-safety panel found:
 *   - the QC remedy was offered only at READY, but the handover gate actually
 *     parks a held job at IN_PROGRESS (no lab station ever sets COMPLETED);
 *   - the Workshop-page create path read `prescription_id` from an order item
 *     the API serialises as `prescriptionId`, so every job created there sent a
 *     blank Rx link and the backend's Rx verification silently no-op'd.
 */
import { describe, it, expect } from 'vitest';

import {
  hasQcOnFile,
  awaitingHandoverQc,
  handoverBlockerMessage,
  isAwaitingSalesConfirmation,
  QC_ACTIONABLE_STATUSES,
  resolveItemPrescriptionId,
  backendMessage,
} from '../qcHandover';

describe('hasQcOnFile', () => {
  it('accepts a QC pass or an audited waiver', () => {
    expect(hasQcOnFile({ status: 'READY', qc_passed: true })).toBe(true);
    expect(hasQcOnFile({ status: 'READY', qc_waived: true })).toBe(true);
  });

  it('treats an absent record as not cleared', () => {
    expect(hasQcOnFile({ status: 'READY' })).toBe(false);
  });

  it('fails closed on a recorded failure and on truthy junk', () => {
    expect(hasQcOnFile({ status: 'READY', qc_passed: false })).toBe(false);
    // Mirrors the backend's strict `is True` — a legacy string must not pass.
    expect(hasQcOnFile({ status: 'READY', qc_passed: 'false' } as never)).toBe(false);
  });
});

describe('QC_ACTIONABLE_STATUSES', () => {
  it('includes IN_PROGRESS — the state a held job actually parks in', () => {
    expect(QC_ACTIONABLE_STATUSES).toContain('IN_PROGRESS');
  });

  it('still includes READY for a job already on the pickup shelf', () => {
    expect(QC_ACTIONABLE_STATUSES).toContain('READY');
  });

  it('excludes PENDING, which the backend refuses', () => {
    expect(QC_ACTIONABLE_STATUSES).not.toContain('PENDING');
  });
});

describe('awaitingHandoverQc', () => {
  it('warns for a job on the pickup shelf with no QC', () => {
    expect(awaitingHandoverQc({ status: 'READY' })).toBe(true);
  });

  it('warns for a HELD job that walked to the pickup station', () => {
    // The exact state the gate produces: DISPATCH->READY held, so status stayed
    // IN_PROGRESS, but the job physically advanced to PICKUP.
    expect(
      awaitingHandoverQc({ status: 'IN_PROGRESS', current_station: 'PICKUP' }),
    ).toBe(true);
    expect(
      awaitingHandoverQc({ status: 'IN_PROGRESS', current_station: 'DISPATCH' }),
    ).toBe(true);
  });

  it('stays quiet mid-bench, where QC is not due yet', () => {
    expect(
      awaitingHandoverQc({ status: 'IN_PROGRESS', current_station: 'EDGING' }),
    ).toBe(false);
  });

  it('warns for EVERY un-QCd PENDING job, station or not', () => {
    // THE LIVE PROD SHAPE. Both un-QC'd jobs in production are PENDING with
    // confirmed_by_sales=true and no current_station, and the backend gate
    // blocks them -- so the screen must warn. An earlier version only warned
    // when sales had NOT confirmed, i.e. exactly the shape the live rows are
    // NOT in, leaving both screens silent on every order in flight.
    expect(
      awaitingHandoverQc({
        status: 'PENDING',
        fitting_details: { confirmed_by_sales: true },
      }),
    ).toBe(true);
    expect(awaitingHandoverQc({ status: 'PENDING' })).toBe(true);
    expect(awaitingHandoverQc({ status: 'PENDING', fitting_details: {} })).toBe(true);
  });

  it('stays quiet for a PENDING job that already has QC on file', () => {
    expect(
      awaitingHandoverQc({ status: 'PENDING', qc_passed: true }),
    ).toBe(false);
  });

  it('stays quiet once QC is on file', () => {
    expect(awaitingHandoverQc({ status: 'READY', qc_passed: true })).toBe(false);
    expect(
      awaitingHandoverQc({ status: 'IN_PROGRESS', current_station: 'PICKUP', qc_waived: true }),
    ).toBe(false);
  });

  it('stays quiet for terminal jobs', () => {
    expect(awaitingHandoverQc({ status: 'DELIVERED', current_station: 'PICKUP' })).toBe(false);
    expect(awaitingHandoverQc({ status: 'CANCELLED', current_station: 'PICKUP' })).toBe(false);
  });
});

describe('handoverBlockerMessage', () => {
  it('names SALES CONFIRMATION for an unconfirmed PENDING job', () => {
    // Telling staff to "run QC" on a PENDING job would name a step the API
    // refuses -- QC deliberately rejects PENDING.
    const msg = handoverBlockerMessage({ status: 'PENDING' });
    expect(msg).toContain('fitting');
    expect(msg).not.toContain('QC');
  });

  it('names START THE JOB for a confirmed PENDING job (the live prod shape)', () => {
    // Mirrors the backend's _handover_block_detail: sales have confirmed, so
    // the real next step is starting the job, not confirming the fitting.
    const msg = handoverBlockerMessage({
      status: 'PENDING',
      fitting_details: { confirmed_by_sales: true },
    });
    expect(msg).toContain('not been started');
  });

  it('names QC for a job on the pickup shelf', () => {
    expect(handoverBlockerMessage({ status: 'READY' })).toContain('QC');
  });

  it('names QC for a held job at the pickup station', () => {
    expect(
      handoverBlockerMessage({ status: 'IN_PROGRESS', current_station: 'PICKUP' }),
    ).toContain('QC');
  });
});

describe('isAwaitingSalesConfirmation', () => {
  it('is true only for an unconfirmed PENDING job', () => {
    expect(isAwaitingSalesConfirmation({ status: 'PENDING' })).toBe(true);
    expect(
      isAwaitingSalesConfirmation({
        status: 'PENDING',
        fitting_details: { confirmed_by_sales: true },
      }),
    ).toBe(false);
    expect(isAwaitingSalesConfirmation({ status: 'IN_PROGRESS' })).toBe(false);
  });
});

describe('resolveItemPrescriptionId', () => {
  it('reads the camelCase field the API actually serialises', () => {
    // orders.item_to_frontend renames prescription_id -> prescriptionId.
    expect(resolveItemPrescriptionId({ prescriptionId: 'RX-9' })).toBe('RX-9');
  });

  it('still reads a raw snake_case doc', () => {
    expect(resolveItemPrescriptionId({ prescription_id: 'RX-7' })).toBe('RX-7');
  });

  it('prefers the camelCase value when both are present', () => {
    expect(
      resolveItemPrescriptionId({ prescriptionId: 'RX-9', prescription_id: 'RX-7' }),
    ).toBe('RX-9');
  });

  it('returns empty string for a missing item so behaviour is unchanged', () => {
    expect(resolveItemPrescriptionId(undefined)).toBe('');
    expect(resolveItemPrescriptionId({})).toBe('');
  });
});

describe('backendMessage', () => {
  it('renders the gate sentence the counter needs to read', () => {
    const err = {
      response: {
        data: {
          detail:
            'Lens QC has not been recorded for workshop job WS-1. Ask workshop staff...',
        },
      },
    };
    expect(backendMessage(err, 'generic')).toContain('Lens QC has not been recorded');
  });

  it('unwraps an object detail (the DC hardlock shape)', () => {
    const err = { response: { data: { detail: { code: 'DC_HARDLOCK', message: 'No DC logged.' } } } };
    expect(backendMessage(err, 'generic')).toBe('No DC logged.');
  });

  it('falls back when there is no detail at all', () => {
    expect(backendMessage(new Error('boom'), 'Failed to update job status')).toBe(
      'Failed to update job status',
    );
    expect(backendMessage({ response: { data: { detail: '' } } }, 'generic')).toBe('generic');
  });
});
