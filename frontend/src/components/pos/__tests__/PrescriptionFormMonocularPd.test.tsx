// ============================================================================
// A per-eye PD is MONOCULAR. Validating it against the BINOCULAR range broke
// every edit of a prescription that had one.
// ============================================================================
// PrescriptionForm's PD boxes are per-eye ("PD (Pupillary Distance) - mm",
// placeholder 32.5) -- a monocular measurement, roughly half the binocular
// value. validateBeforeSubmit passed them to the canonical validator as `pd`,
// whose range is the BINOCULAR 40-80mm, so a correct 32.5 was refused with
// "Right eye (OD) PD must be between 40.00 and 80.00" and the Save button did
// nothing. The backend has always used the monocular range for this field
// (EyeData.validate_pd -> "pd_mono", 20-45mm) and is the source of truth.
//
// This is the form the clinic's "Edit" pencil opens, so the failure landed
// directly on the owner's edit complaint.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

const errors: string[] = [];
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({
    error: (m: string) => errors.push(m),
    success: () => {},
    warning: () => {},
    info: () => {},
  }),
}));

import { PrescriptionForm } from '../PrescriptionForm';

function renderForm(initialData?: Record<string, unknown>) {
  errors.length = 0;
  const onSubmit = vi.fn();
  render(
    <PrescriptionForm
      onSubmit={onSubmit}
      onCancel={() => {}}
      initialData={initialData as never}
      allowContactLens={false}
      submitLabel="Save changes"
    />,
  );
  return onSubmit;
}

const submit = () => fireEvent.click(screen.getByRole('button', { name: /Save changes/ }));

describe('per-eye (monocular) PD', () => {
  it('ACCEPTS 32.5mm on both eyes', async () => {
    const onSubmit = renderForm({ sph_od: -2.25, pd_od: 32.5, sph_os: -2, pd_os: 32 });
    submit();

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(errors).toEqual([]);
  });

  it('still REJECTS an impossible per-eye PD', async () => {
    // POSITIVE CONTROL for the other direction: widening the range must not
    // turn the check off. 9999mm is not a face.
    const onSubmit = renderForm({ sph_od: -2.25, pd_od: 9999 });
    submit();

    await waitFor(() => expect(errors.length).toBe(1));
    expect(errors[0]).toMatch(/PD/i);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('still REJECTS an out-of-range sphere', async () => {
    const onSubmit = renderForm({ sph_od: -9999 });
    submit();

    await waitFor(() => expect(errors.length).toBe(1));
    expect(errors[0]).toMatch(/SPH/i);
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
