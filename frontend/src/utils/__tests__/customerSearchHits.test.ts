import { describe, it, expect } from 'vitest';
import { buildCustomerSearchHits } from '../customerSearchHits';

const mahesh = {
  customer_id: 'C1',
  name: 'Mahesh Kumar Gupta',
  mobile: '9810000001',
  patients: [
    { patient_id: 'P1', name: 'Alka Gupta', mobile: '9810000002' },
    { patient_id: 'P2', name: 'Suresh Gupta', mobile: '9810000003' },
  ],
};
const alkaAccount = {
  customer_id: 'C2',
  name: 'Alka Gupta',
  mobile: '9899999999',
  patients: [],
};

describe('buildCustomerSearchHits', () => {
  it('surfaces a matching patient as its own labeled hit (not just the account)', () => {
    const hits = buildCustomerSearchHits([mahesh], 'Alka');
    // account "Mahesh" does not contain "alka" -> only the patient hit
    expect(hits.map((h) => `${h.kind}:${h.displayName}`)).toEqual(['patient:Alka Gupta']);
    expect(hits[0].accountName).toBe('Mahesh Kumar Gupta');
    expect(hits[0].patient?.patient_id).toBe('P1');
  });

  it('does NOT surface a sibling sharing only one token (all-tokens semantics)', () => {
    const hits = buildCustomerSearchHits([mahesh], 'Alka Gupta');
    const names = hits.map((h) => h.displayName);
    expect(names).toContain('Alka Gupta');
    expect(names).not.toContain('Suresh Gupta'); // only matched "gupta" -> excluded
  });

  it('emits account + every matching patient for a shared surname (single token)', () => {
    const hits = buildCustomerSearchHits([mahesh], 'Gupta');
    expect(hits.map((h) => h.kind)).toEqual(['account', 'patient', 'patient']);
    expect(hits.map((h) => h.displayName)).toEqual([
      'Mahesh Kumar Gupta',
      'Alka Gupta',
      'Suresh Gupta',
    ]);
  });

  it('matches a patient by phone', () => {
    const hits = buildCustomerSearchHits([mahesh], '9810000002');
    expect(hits).toHaveLength(1);
    expect(hits[0].kind).toBe('patient');
    expect(hits[0].displayName).toBe('Alka Gupta');
  });

  it('distinguishes a top-level customer from a same-named patient under another account', () => {
    const hits = buildCustomerSearchHits([alkaAccount, mahesh], 'Alka');
    const labels = hits.map((h) => `${h.kind}:${h.accountName}`);
    expect(labels).toContain('account:Alka Gupta'); // the standalone customer
    expect(labels).toContain('patient:Mahesh Kumar Gupta'); // Alka under Mahesh
  });

  it('produces collision-free keys', () => {
    const twins = {
      customer_id: 'C3',
      name: 'Ravi',
      patients: [
        { name: 'Baby Gupta', mobile: '1' }, // no patient_id -> falls back to name
        { name: 'Baby Gupta', mobile: '2' }, // same name -> must not collide
      ],
    };
    const hits = buildCustomerSearchHits([alkaAccount, mahesh, twins], 'Gupta');
    const keys = hits.map((h) => h.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it('never vanishes a backend-matched customer (account fallback)', () => {
    const weird = {
      customer_id: 'C9',
      name: 'Zzz',
      mobile: '5550000000',
      patients: [{ patient_id: 'PX', name: 'Yyy', mobile: '5551111111' }],
    };
    const hits = buildCustomerSearchHits([weird], 'nomatch');
    expect(hits).toHaveLength(1);
    expect(hits[0].kind).toBe('account');
  });

  it('empty query yields one account hit per customer, no patient rows', () => {
    const hits = buildCustomerSearchHits([mahesh], '');
    expect(hits).toHaveLength(1);
    expect(hits[0].kind).toBe('account');
  });
});
