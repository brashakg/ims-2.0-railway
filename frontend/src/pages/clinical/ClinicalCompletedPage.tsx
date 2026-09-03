// ============================================================================
// IMS 2.0 - /clinical/completed
// ============================================================================
// Eye tests completed TODAY. Was the "completed" tab of the deleted
// ClinicalPage mega-page; behaviour is unchanged. Data comes from the shared
// clinicalQueries cache (the layout's stat strip reads the same key).

import { useState } from 'react';
import { CheckCircle, Eye, FileText, Loader2, User } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { SendToFloorDrawer } from '../../components/clinical/SendToFloorDrawer';
import { PrescriptionCard, type PrescriptionData } from '../../components/clinical/PrescriptionCard';
import { ClinicPrescriptionHistory } from '../../components/clinical/ClinicPrescriptionHistory';
import { readEyePower } from '../../utils/rxEye';
// PATIENT SAFETY: a blank power is not a recorded 0. See utils/rxPowerValue.
import { formatPowerOrDash, powerNumberOrNull } from '../../utils/rxPowerValue';
import { axisOrNull } from '../../utils/rxAxisEntry';
import { useClinicalStoreIdentity, useTodayTests } from './clinicalQueries';

export function ClinicalCompletedPage() {
  const { user } = useAuth();
  const toast = useToast();

  const { data: completedTests = [], isLoading } = useTodayTests(user?.activeStoreId);
  const { storeInfo, storeEntity } = useClinicalStoreIdentity(user?.activeStoreId);

  // Typed with the card's own props, not `any`: `any` here silently discarded
  // PrescriptionCard's nullable-power contract, so nothing stopped this call
  // site from going back to `readEyePower(...) || 0` and printing a fabricated
  // plano on a patient's card. See components/clinical/PrescriptionCard.
  const [printRxCard, setPrintRxCard] = useState<PrescriptionData | null>(null);

  // F50: clinical -> retail handover. `sendToFloorFor` opens the drawer for a
  // completed test; `sentTestIds` tracks which rows have already been sent so
  // the button flips to "Sent" (idempotency UX).
  const [sendToFloorFor, setSendToFloorFor] = useState<{ testId: string; patientName: string } | null>(null);
  const [sentTestIds, setSentTestIds] = useState<Set<string>>(new Set());

  const [rxHistoryFor, setRxHistoryFor] = useState<{
    customerId: string;
    customerName?: string;
    patientId?: string;
  } | null>(null);

  const formatTime = (dateStr: string) => {
    return new Date(dateStr).toLocaleTimeString('en-IN', {
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  // PATIENT SAFETY / CRASH. This is fed by `readEyePower`, which is typed `any`
  // and returns UNDEFINED when the field is absent -- and a previous body
  // tested only `value === null`, so an undefined sailed past the guard into
  // `.toFixed` and threw, taking the whole "Completed today" list down for any
  // eye test whose stored Rx simply omits a key (a Mongo doc without the field,
  // an import, a device feed). One absence predicate for every spelling of
  // absence, and a recorded 0 still renders "+0.00". See utils/rxPowerValue.
  const formatPower = formatPowerOrDash;

  return (
    <div className="card overflow-hidden">
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 animate-spin text-bv-red-600" />
        </div>
      ) : completedTests.length === 0 ? (
        <div className="text-center py-12 text-gray-500">
          <CheckCircle className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p>No tests completed today</p>
        </div>
      ) : (
        <div className="divide-y divide-gray-200">
          {completedTests.map(test => (
            <div key={test.id} className="p-4 hover:bg-gray-100 transition-colors">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                  <div className="w-10 h-10 bg-green-100 rounded-full flex items-center justify-center">
                    <User className="w-5 h-5 text-green-600" />
                  </div>
                  <div>
                    <p className="font-medium text-gray-900">{test.patientName}</p>
                    <p className="text-sm text-gray-500">
                      Completed at {formatTime(test.completedAt)}
                    </p>
                  </div>
                </div>

                {/* Quick Rx Preview */}
                <div className="flex items-center gap-6">
                  <div className="text-sm">
                    <p className="text-gray-500">R: {formatPower(readEyePower(test, 'right', 'sphere'))} / {formatPower(readEyePower(test, 'right', 'cylinder'))}</p>
                    <p className="text-gray-500">L: {formatPower(readEyePower(test, 'left', 'sphere'))} / {formatPower(readEyePower(test, 'left', 'cylinder'))}</p>
                  </div>
                  {/* CLI-1 fix: guard on a real customerId — test.id is the
                      eye-test record id, not the customer id. Without a
                      real customer id the Rx lookup returns 404. */}
                  <button
                    type="button"
                    onClick={() => {
                      if (!test.customerId) {
                        toast.info('No customer account linked to this test. Rx history is unavailable.');
                        return;
                      }
                      setRxHistoryFor({
                        customerId: test.customerId,
                        customerName: test.patientName,
                        patientId: test.customerId,
                      });
                    }}
                    className="p-2 text-gray-500 hover:text-teal-600 transition-colors"
                    title={test.customerId ? 'View / edit prescriptions' : 'No customer linked'}
                  >
                    <Eye className="w-5 h-5" />
                  </button>
                  <button
                    onClick={() => setPrintRxCard({
                      id: test.id,
                      patientName: test.patientName,
                      date: test.completedAt,
                      optometristName: user?.name || 'Optometrist',
                      // PATIENT SAFETY: `|| 0` here PRINTED a fabricated
                      // 0.00 on the card handed to the patient whenever a
                      // power had not been recorded -- and hard-coded
                      // `add: 0` / `pd: 0` claimed "no reading addition"
                      // for every card ever printed from this button.
                      // A power that was not recorded now prints as a dash;
                      // one recorded AS 0 still prints 0.00. Both eyes get
                      // identical treatment. See utils/rxPowerValue.
                      // The AXIS goes through axisOrNull, not the power
                      // parser: rxAxisEntry owns the axis (a meridian
                      // notated 1-180), rxPowerValue owns the dioptric
                      // powers, and each states in its header that it does
                      // not govern the other. Same answer today; one owner
                      // per concept is what keeps it that way.
                      rightEye: { sphere: powerNumberOrNull(readEyePower(test, 'right', 'sphere')), cylinder: powerNumberOrNull(readEyePower(test, 'right', 'cylinder')), axis: axisOrNull(readEyePower(test, 'right', 'axis')), add: powerNumberOrNull(readEyePower(test, 'right', 'add')) },
                      leftEye: { sphere: powerNumberOrNull(readEyePower(test, 'left', 'sphere')), cylinder: powerNumberOrNull(readEyePower(test, 'left', 'cylinder')), axis: axisOrNull(readEyePower(test, 'left', 'axis')), add: powerNumberOrNull(readEyePower(test, 'left', 'add')) },
                      pd: null,
                      visualAcuity: '',
                      notes: '',
                      storeName: storeInfo?.storeName || storeInfo?.storeCode || '',
                      storePhone: storeInfo?.phone || '',
                      storeLegalName: storeEntity?.legal_name || storeEntity?.name || '',
                      storeAddress: storeInfo ? [storeInfo.address, storeInfo.city, storeInfo.state, storeInfo.pincode].filter(Boolean).join(', ') : '',
                      storeGstin: storeInfo?.gstin || '',
                      storeLogoUrl: storeEntity?.invoice?.logo_url || (storeEntity as any)?.logo_url || '',
                    })}
                    className="p-2 text-gray-500 hover:text-green-500 transition-colors"
                    title="Print Rx Card"
                  >
                    <FileText className="w-5 h-5" />
                  </button>
                  {/* F50: send this completed Rx to the sales floor. Secondary
                      (neutral) action — not the red CTA. Flips to "Sent" after
                      a successful send. The backend gates on the per-store
                      feature flag (403 -> toast). */}
                  <button
                    type="button"
                    onClick={() => setSendToFloorFor({ testId: test.id, patientName: test.patientName })}
                    disabled={sentTestIds.has(test.id)}
                    className="text-xs border border-gray-300 text-gray-700 px-2.5 py-1 rounded hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    title="Send Rx to the sales floor"
                  >
                    {sentTestIds.has(test.id) ? 'Sent' : 'Send to Floor'}
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* F50: Send-to-Floor drawer */}
      {sendToFloorFor && (
        <SendToFloorDrawer
          testId={sendToFloorFor.testId}
          patientName={sendToFloorFor.patientName}
          onClose={() => setSendToFloorFor(null)}
          onSent={() => {
            setSentTestIds((prev) => {
              const next = new Set(prev);
              next.add(sendToFloorFor.testId);
              return next;
            });
          }}
        />
      )}

      {/* Prescription Card Print Modal */}
      {printRxCard && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg max-w-2xl w-full max-h-[90dvh] overflow-y-auto">
            <PrescriptionCard prescription={printRxCard} />
            <div className="flex justify-end p-4 border-t">
              <button
                onClick={() => setPrintRxCard(null)}
                className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Prescription history / edit / new — per customer, grouped by family */}
      {rxHistoryFor && (
        <ClinicPrescriptionHistory
          isOpen={!!rxHistoryFor}
          onClose={() => setRxHistoryFor(null)}
          customerId={rxHistoryFor.customerId}
          customerName={rxHistoryFor.customerName}
          defaultPatientId={rxHistoryFor.patientId}
        />
      )}
    </div>
  );
}

export default ClinicalCompletedPage;
