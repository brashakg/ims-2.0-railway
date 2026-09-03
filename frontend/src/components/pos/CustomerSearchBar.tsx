// ============================================================================
// IMS 2.0 - POS customer search (shared)
// ============================================================================
// Owner rule (audit MCQ round 1): a customer is ALWAYS required on every bill —
// there is no anonymous sale on any counter. This is the door that satisfies
// it on the new one-surface POS.
//
// The SELECT half carries a real business rule (BILL-TO-MEMBER P1: an order
// bills a MEMBER, never the bare account), so it lives here ONCE rather than
// being retyped next to a second search box. The classic POS keeps its own
// richer result cards; both call selectCustomerHit, so the member rule cannot
// drift between the two surfaces.

import { AutoSearch } from '../common/AutoSearch';
import { customerApi } from '../../services/api/customers';
import { buildCustomerSearchHits, type CustomerSearchHit } from '../../utils/customerSearchHits';
import { choosePrimaryPatient, toPosPatient } from '../../utils/patientFromCustomer';
import { buildCustomerCreatePayload, type CustomerFormData } from '../../utils/customerPayload';

/** Apply a search hit to the POS store, honouring BILL-TO-MEMBER P1:
 *  - a 'patient' hit IS a member -> bill to that member;
 *  - an 'account' hit -> default-select the account's Primary member, so
 *    single-member accounts (the ~90% case) are one click and multi-member
 *    accounts still land somewhere sane and can be switched. */
export function selectCustomerHit(store: any, hit: CustomerSearchHit) {
  const c: any = hit.customer;
  store.setCustomer({
    ...c,
    id: c.customer_id || c._id || c.id,
    name: c.name || c.customer_name || c.full_name || 'Customer',
    phone: c.phone || c.mobile || '',
  });
  // setCustomer resets patient to null — set the billed member AFTER.
  const cid = c.customer_id || c._id || c.id || '';
  if (hit.kind === 'patient' && hit.patient) {
    store.setPatient(toPosPatient(hit.patient, cid));
  } else {
    const primary = choosePrimaryPatient(c?.patients);
    if (primary) store.setPatient(toPosPatient(primary, cid));
  }
}

/** Create a brand-new customer and put them straight on the bill.
 *
 * The CREATE half of the same rule, and it lives here for the same reason the
 * select half does: it carries BILL-TO-MEMBER P1 (an order bills a MEMBER, so
 * the server-returned Primary is auto-selected), and it must build the payload
 * through the ONE shared builder so a customer created at the till is
 * byte-identical to one created on the Customers page or at Clinical intake -
 * including dob, anniversary and consents, which an earlier hand-rolled POS
 * payload silently dropped.
 *
 * Called by BOTH tills. A first-time customer could not be billed on the new
 * POS at all before this: the assistant had to leave the counter, register them
 * on the Customers screen, and come back.
 */
export async function createAndSelectCustomer(
  store: any,
  customerData: CustomerFormData,
): Promise<void> {
  const payload = buildCustomerCreatePayload(customerData);
  const r: any = await customerApi.createCustomer(payload as any);
  const custId = r?.customer_id || r?.id || `new-${Date.now()}`;
  // Carry the server-returned patients[] (incl. the auto-seeded Primary, with
  // real patient_ids + is_primary) so the member picker can render the family.
  const returnedPatients = Array.isArray(r?.patients) ? r.patients : [];
  store.setCustomer({
    id: custId,
    name: customerData.fullName,
    phone: customerData.mobileNumber,
    email: customerData.email,
    customerType: customerData.customerType,
    patients: returnedPatients,
  } as any);
  const primary = choosePrimaryPatient(returnedPatients);
  if (primary) {
    store.setPatient(toPosPatient(primary, custId) as any);
  }
}


export function CustomerSearchBar({ store }: { store: any }) {
  return (
    <AutoSearch<CustomerSearchHit>
      fetchResults={async (q, sid) => {
        try {
          const res = await customerApi.getCustomers({ search: q, storeId: sid, limit: 8 });
          const customers = (res as any)?.customers || (res as any) || [];
          return buildCustomerSearchHits(customers, q);
        } catch {
          return [];
        }
      }}
      maxResults={10}
      renderItem={(hit) => {
        // Compact row for the narrow left column: the account/member tree is
        // still legible (members indent under their account) without the
        // classic surface's full chip set.
        const isMember = hit.kind === 'patient';
        const c: any = hit.customer || {};
        const phone = c.phone || c.mobile || '';
        return (
          <div className={'flex items-center gap-2 py-1 ' + (isMember ? 'pl-5' : '')}>
            <span className="w-7 h-7 rounded-full bg-gray-100 text-gray-700 text-xs font-semibold flex items-center justify-center shrink-0">
              {(hit.displayName || '?').charAt(0).toUpperCase()}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm text-gray-900 truncate">{hit.displayName}</span>
              <span className="block text-[11px] text-gray-500 truncate">
                {isMember ? 'Family member' : phone || 'No phone'}
              </span>
            </span>
          </div>
        );
      }}
      onSelect={(hit) => selectCustomerHit(store, hit)}
      getKey={(hit) => hit.key}
      placeholder="Search customer by phone or name…"
      clearOnSelect
      emptyMessage="No customers found"
    />
  );
}

export default CustomerSearchBar;
