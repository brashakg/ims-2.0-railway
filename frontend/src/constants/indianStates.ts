// ============================================================================
// GST state codes — ONE frontend copy, generated from the backend's canonical
// INDIAN_STATE_CODES (backend/api/services/org_validation.py)
// ============================================================================
// Two hand-typed copies of this map is how a Jharkhand customer came to be
// stamped as Maharashtra: the POS customer form carried 15 of the 38 states,
// was missing code 20 (Jharkhand) where 5 of the 6 shops are, and defaulted
// anything unknown to 'Maharashtra'. A wrong state turns a local sale into an
// inter-state one for every tax surface downstream.
//
// If this ever needs to change, change org_validation.py and regenerate.

export const GST_STATE_BY_CODE: Record<string, string> = {
  '01': 'Jammu and Kashmir',   '02': 'Himachal Pradesh',
  '03': 'Punjab',   '04': 'Chandigarh',
  '05': 'Uttarakhand',   '06': 'Haryana',
  '07': 'Delhi',   '08': 'Rajasthan',
  '09': 'Uttar Pradesh',   '10': 'Bihar',
  '11': 'Sikkim',   '12': 'Arunachal Pradesh',
  '13': 'Nagaland',   '14': 'Manipur',
  '15': 'Mizoram',   '16': 'Tripura',
  '17': 'Meghalaya',   '18': 'Assam',
  '19': 'West Bengal',   '20': 'Jharkhand',
  '21': 'Odisha',   '22': 'Chhattisgarh',
  '23': 'Madhya Pradesh',   '24': 'Gujarat',
  '26': 'Dadra and Nagar Haveli and Daman and Diu',   '27': 'Maharashtra',
  '29': 'Karnataka',   '30': 'Goa',
  '31': 'Lakshadweep',   '32': 'Kerala',
  '33': 'Tamil Nadu',   '34': 'Puducherry',
  '35': 'Andaman and Nicobar Islands',   '36': 'Telangana',
  '37': 'Andhra Pradesh',   '38': 'Ladakh',
  '97': 'Other Territory',   '99': 'Centre Jurisdiction',
};

/** State NAME from a GSTIN (its first two digits are the state code).
 *  Returns '' when the GSTIN is absent or its code is not a real state - never
 *  a guess. The backend resolves place of supply the same way, GSTIN first
 *  (orders._customer_state_code), so the printed invoice and the tax the server
 *  records cannot disagree about which state the customer is in. */
export function stateFromGstin(gstin?: string | null): string {
  const g = String(gstin || '').trim();
  if (g.length < 2) return '';
  return GST_STATE_BY_CODE[g.slice(0, 2)] || '';
}
