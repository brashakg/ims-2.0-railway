// ============================================================================
// IMS 2.0 — Customer/Patient search-hit flattening
// ============================================================================
// The /customers search returns a PARENT customer doc whenever the query
// matches the account holder OR any nested family member (patients[].name /
// .mobile). A flat dropdown of those docs is confusing: searching a patient
// ("Alka") just shows the account holder ("Mahesh"), and a common surname mixes
// accounts and patients with no way to tell them apart before selecting.
//
// This re-derives, per result, whether the account holder and/or specific
// patients match the typed query, and emits one LABELED, individually-pickable
// hit for each — so POS and clinic dropdowns can clearly mark "Account holder"
// vs "Patient · under <holder>" and let the operator pick the exact person.
// Frontend-only; no API shape change.
//
// Match semantics MIRROR the backend (base_repository.search): a query matches
// an entity only when EVERY whitespace token appears somewhere across that
// entity's fields (OR-across-fields under an all-tokens requirement). Joining
// the fields before the substring test is the correct analogue — without it a
// sibling patient sharing just a surname token would be wrongly surfaced.
// ============================================================================

export interface CustomerSearchHit {
  /** 'account' = the account holder; 'patient' = a family member under it. */
  kind: 'account' | 'patient';
  /** The parent/account customer doc as returned by the API. */
  customer: any;
  /** The matched family member — present only when kind === 'patient'. */
  patient?: any;
  /** Account holder's display name (always the parent). */
  accountName: string;
  /** Primary label: the patient's name for patient hits, else the account name. */
  displayName: string;
  /** Best phone to show for this hit. */
  phone: string;
  /** Stable React key. */
  key: string;
}

const lc = (v: unknown): string => (v == null ? '' : String(v)).toLowerCase();
const custId = (c: any): string => c?.customer_id || c?._id || c?.id || '';
const patId = (p: any): string => p?.patient_id || p?.id || p?.name || '';

export function buildCustomerSearchHits(
  customers: any[],
  query: string,
): CustomerSearchHit[] {
  const tokens = lc(query).trim().split(/\s+/).filter(Boolean);
  const matchesAll = (...vals: unknown[]): boolean => {
    if (tokens.length === 0) return true;
    const joined = vals.map(lc).filter(Boolean).join(' ');
    return tokens.every((t) => joined.includes(t));
  };
  const matchesAny = (...vals: unknown[]): boolean => {
    if (tokens.length === 0) return true;
    const joined = vals.map(lc).filter(Boolean).join(' ');
    return tokens.some((t) => joined.includes(t));
  };

  const hits: CustomerSearchHit[] = [];
  for (const c of Array.isArray(customers) ? customers : []) {
    const accountName = c?.name || c?.customer_name || c?.full_name || 'Customer';
    const accountPhone = c?.phone || c?.mobile || '';
    const patients: any[] = Array.isArray(c?.patients) ? c.patients : [];
    const cid = custId(c) || accountName; // never empty -> stable, collision-safe keys

    const accountMatches = matchesAll(accountName, c?.phone, c?.mobile, c?.email);
    let matchingPatients = patients.filter((p) =>
      matchesAll(p?.name, p?.mobile, p?.phone),
    );
    // Fallback: the backend returned this customer but the tokens were split
    // across holder + patient (no single entity carries them all). Surface
    // any-token patient matches so a genuine backend hit never vanishes.
    if (!accountMatches && matchingPatients.length === 0) {
      matchingPatients = patients.filter((p) =>
        matchesAny(p?.name, p?.mobile, p?.phone),
      );
    }

    const pushAccount = () =>
      hits.push({
        kind: 'account',
        customer: c,
        accountName,
        displayName: accountName,
        phone: accountPhone,
        key: `acct:${cid}`,
      });

    if (accountMatches) pushAccount();
    matchingPatients.forEach((p, i) =>
      hits.push({
        kind: 'patient',
        customer: c,
        patient: p,
        accountName,
        displayName: p?.name || 'Patient',
        phone: p?.mobile || p?.phone || accountPhone,
        // index tiebreaker: two same-name pre-ID patients under one account
        // must not collide on key.
        key: `pat:${cid}:${patId(p)}:${i}`,
      }),
    );
    // Never silently vanish: backend matched but neither holder nor any patient
    // did (e.g. matched on a field we don't inspect).
    if (!accountMatches && matchingPatients.length === 0) pushAccount();
  }
  return hits;
}
