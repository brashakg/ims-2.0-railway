// ============================================================================
// The prescription form must SUBMIT "+4.00", not the number 4
// ============================================================================
// This is the form the clinic's Edit pencil and the POS counter both open, and
// it held every power as a number:
//
//   handleRxChange: [field]: parseFloat(value)     // "+4.00" -> 4
//   sales.ts _rxStr: String(v)                     // 4       -> "4"
//
// so the explicit plus was destroyed between the keystroke and the request.
// The input itself was never at fault -- RxPowerInput normalises to "+4.00" and
// re-derives it on reload, which is why the box LOOKS right while the stored
// value is bare.
//
// Driven through the REAL component: type into the real box, click the real
// button, read the payload the real onSubmit receives.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ error: () => {}, success: () => {}, warning: () => {}, info: () => {} }),
}));

import { PrescriptionForm } from '../PrescriptionForm';

function renderForm(initialData?: Record<string, unknown>) {
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

function type(label: string, value: string) {
  const el = screen.getByLabelText(label);
  fireEvent.change(el, { target: { value } });
  fireEvent.blur(el);
}

const save = () => fireEvent.click(screen.getByRole('button', { name: /Save changes/ }));

describe('a typed power keeps its sign all the way to onSubmit', () => {
  it('submits "+4.00" for a typed +4.00', async () => {
    const onSubmit = renderForm();
    type('Right eye sphere', '+4.00');
    save();

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const payload = onSubmit.mock.calls[0][0];
    expect(payload.sph_od).toBe('+4.00');
    // The reported failure, asserted directly.
    expect(payload.sph_od).not.toBe(4);
    expect(payload.sph_od).not.toBe('4');
  });

  it('submits "+4.00" even when the user typed a bare 4', async () => {
    // The input normalises on blur, and the normalised text is what is kept.
    const onSubmit = renderForm();
    type('Right eye sphere', '4');
    save();

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].sph_od).toBe('+4.00');
  });

  it('NEVER loses a minus', async () => {
    const onSubmit = renderForm();
    type('Right eye sphere', '-4.00');
    type('Right eye cylinder', '-0.75');
    // A cylinder without an axis is un-grindable, and the form refuses it
    // (AXIS_REQUIRED_FOR_CYL). Enter a clinically complete eye so this test
    // measures the SIGN, not the axis rule -- weakening that rule to make a
    // sign test pass would trade one clinical defect for a worse one.
    type('Right eye axis', '90');
    save();

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const payload = onSubmit.mock.calls[0][0];
    expect(payload.sph_od).toBe('-4.00');
    expect(payload.cyl_od).toBe('-0.75');
  });

  it('keeps a recorded plano and leaves an untouched box absent', async () => {
    const onSubmit = renderForm();
    type('Right eye sphere', '0');
    save();

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const payload = onSubmit.mock.calls[0][0];
    expect(payload.sph_od).toBe('0.00');
    expect(payload.cyl_od).toBeUndefined();
    expect(payload.sph_os).toBeUndefined();
  });

  it('submits exactly what it was handed for an untouched box', async () => {
    // This form is faithful to its initialData: it does not re-format a value
    // the clinician never touched. Whoever hydrates it therefore has to hand it
    // SIGNED text -- see ClinicPrescriptionHistoryEdit.test.tsx, which pins the
    // clinic's Edit pencil doing exactly that. Asserted here so the division of
    // responsibility is explicit rather than assumed.
    const onSubmit = renderForm({ sph_od: '+4.00', cyl_od: '-0.75', axis_od: '90' });
    save();

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0][0].sph_od).toBe('+4.00');
  });
});

describe('the box itself shows the sign', () => {
  it('renders a stored unsigned 4 as +4.00', () => {
    renderForm({ sph_od: 4 });
    expect((screen.getByLabelText('Right eye sphere') as HTMLInputElement).value).toBe('+4.00');
  });

  it('renders a stored -0.75 cylinder as -0.75', () => {
    renderForm({ cyl_od: -0.75 });
    expect((screen.getByLabelText('Right eye cylinder') as HTMLInputElement).value).toBe('-0.75');
  });

  it('leaves an unrecorded power as an EMPTY box, never +0.00', () => {
    renderForm({});
    expect((screen.getByLabelText('Right eye sphere') as HTMLInputElement).value).toBe('');
  });
});
