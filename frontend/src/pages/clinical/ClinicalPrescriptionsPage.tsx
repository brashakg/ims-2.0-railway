// ============================================================================
// IMS 2.0 - /clinical/prescriptions — THE one prescriptions door
// ============================================================================
// Wave 2 consolidation of the TWO RIVAL PRESCRIPTION DOORS:
//
//   1. The standalone /prescriptions page (PrescriptionsPage.tsx, DELETED) —
//      a read-only store-wide Rx library list. It could not create or edit an
//      Rx, read the spectacle fields on contact-lens Rx rows (rendering a card
//      of dashes), kept a private fourth copy of the power formatter, and
//      carried a second, client-rendered print path next to the canonical
//      server A5 card.
//   2. The ClinicalPage "Prescriptions" tab — ClinicPrescriptionHistory in
//      panel+search mode: customer search -> family-grouped history with
//      view / EDIT / NEW / A5 print, CL-aware, exam-backed edits reopening the
//      full seven-tab screen.
//
// Door 2 is the SURVIVOR (it is the superset and the only one that writes
// through the validated create/update APIs); this page is its permanent
// address. The old /prescriptions address forwards here (clinicalRoutes).
// The deleted page's one unique feature, "Mark redo", was re-homed on the
// Test History detail modal (TestHistoryPage.tsx) so clinicalApi.recordRedo —
// which feeds clinical abuse detection — keeps a caller.
//
// Customer-first (search a NAMED customer, then their family's full history)
// also matches the 30-day data-horizon ruling better than a browsable
// store-wide library ever did.

import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { ClinicPrescriptionHistory } from '../../components/clinical/ClinicPrescriptionHistory';

export function ClinicalPrescriptionsPage() {
  const { user } = useAuth();
  const navigate = useNavigate();

  return (
    <ClinicPrescriptionHistory
      isOpen
      mode="panel"
      searchable
      storeId={user?.activeStoreId}
      onClose={() => navigate('/clinical/queue')}
    />
  );
}

export default ClinicalPrescriptionsPage;
