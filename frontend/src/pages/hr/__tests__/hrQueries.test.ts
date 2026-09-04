// Wave 2 HR split — the envelope unwrap must stay behaviour-identical.
//
// HRPage read `attendanceData?.records || attendanceData || []` and then
// guarded with Array.isArray. A previous attempt at this split shortened that
// to `data?.records ?? []`, which drops the BARE-ARRAY response shape: the
// roster would render empty with no error. These cases fail if that happens
// again.
import { describe, it, expect } from 'vitest';
import { unwrapHrList } from '../hrQueries';

describe('unwrapHrList', () => {
  it('unwraps the enveloped shape', () => {
    expect(unwrapHrList({ records: [{ id: 'a' }] }, 'records')).toEqual([{ id: 'a' }]);
    expect(unwrapHrList({ leaves: [{ id: 'l' }] }, 'leaves')).toEqual([{ id: 'l' }]);
  });

  // The case the last attempt dropped.
  it('accepts a BARE ARRAY response', () => {
    expect(unwrapHrList([{ id: 'a' }, { id: 'b' }], 'records')).toEqual([{ id: 'a' }, { id: 'b' }]);
  });

  it('yields [] for every non-list shape, never undefined', () => {
    expect(unwrapHrList({ records: [] }, 'records')).toEqual([]);
    expect(unwrapHrList({}, 'records')).toEqual([]);
    expect(unwrapHrList(null, 'records')).toEqual([]);
    expect(unwrapHrList(undefined, 'records')).toEqual([]);
    // An object under the key is not a list — the old Array.isArray guard.
    expect(unwrapHrList({ records: { nope: 1 } }, 'records')).toEqual([]);
  });
});
