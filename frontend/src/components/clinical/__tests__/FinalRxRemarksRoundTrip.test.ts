// ===========================================================================
// The optometrist's Final Rx Remarks were thrown away, and the chief complaint
// was printed on the patient's prescription in their place
// ===========================================================================
// Three failures in one loop, and the third is what made it invisible:
//
//   1. FinalRxTab renders a "Remarks" textarea (FinalRxTab.tsx:316) that the
//      optometrist types clinical advice into.
//   2. finalRxPayload never included `remarks`, and eyeTestWriteBody set
//      `notes: data.chiefComplaint` - so the remarks were never sent, and the
//      CHIEF COMPLAINT went in their place. The backend stores that as the
//      prescription's `remarks` (clinical.py: `"remarks": data.notes`), and
//      the Rx card prints it. A patient's printed prescription could carry
//      "blurred distance vision" where the optometrist's advice belonged.
//   3. eyeTestHydrate read `p.notes ?? p.remarks`, so reopening the exam
//      refilled the Remarks box from `notes` - showing the chief complaint
//      back. The box was never EMPTY, so nothing looked lost.
//
// The chief complaint is not lost by the fix: it travels on its own
// `chiefComplaint` field and is stored as `chief_complaint` on the test record
// (clinical.py: _exam_header_fields).

import { describe, it, expect } from 'vitest';
import { eyeTestWriteBody } from '../eyeTestPayload';
import { hydrateFinalRx } from '../eyeTestHydrate';

// A sentinel that could not plausibly appear by accident.
const REMARKS = 'ZZ_REVIEW_AXIS_IN_SIX_MONTHS';
const COMPLAINT = 'ZZ_BLURRED_DISTANCE';

function body() {
  return eyeTestWriteBody({
    chiefComplaint: COMPLAINT,
    finalRx: {
      rightEye: { sphere: '-1.00' },
      leftEye: { sphere: '-1.25' },
      remarks: REMARKS,
    },
  } as never);
}

describe('Final Rx remarks survive a save', () => {
  it('sends the remarks the optometrist actually typed', () => {
    const wire = body() as Record<string, unknown>;
    expect(JSON.stringify(wire)).toContain(REMARKS);
  });

  it('does not send the chief complaint as the prescription remarks', () => {
    // The backend stores `notes` as the prescription's `remarks` and prints it
    // on the Rx card, so this is the assertion that keeps a patient's
    // complaint off their own prescription.
    const wire = body() as Record<string, unknown>;
    expect(wire.notes).toBe(REMARKS);
    expect(wire.notes).not.toBe(COMPLAINT);
  });

  it('still sends the chief complaint on its own field', () => {
    // The negative control: a fix that simply dropped the complaint would pass
    // both assertions above while losing a different field.
    const wire = body() as Record<string, unknown>;
    expect(wire.chiefComplaint).toBe(COMPLAINT);
  });

  it('reads the remarks back from remarks, not from notes', () => {
    const stored = { remarks: REMARKS, notes: COMPLAINT };
    expect(hydrateFinalRx(stored as never).remarks).toBe(REMARKS);
  });

  it('falls back to notes for exams saved before the fix', () => {
    // Rows written by the old code have the text only in `notes`. They must
    // still display something rather than going blank.
    const legacy = { notes: 'ZZ_OLD_ROW_TEXT' };
    expect(hydrateFinalRx(legacy as never).remarks).toBe('ZZ_OLD_ROW_TEXT');
  });
});
