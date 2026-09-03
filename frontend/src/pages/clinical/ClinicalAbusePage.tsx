// ============================================================================
// IMS 2.0 - /clinical/abuse-alerts (managers only)
// ============================================================================
// Was the "abuse-alerts" tab of the deleted ClinicalPage mega-page. The
// AbuseDetection component self-fetches, scoped to the active store. Role gate
// lives in clinicalRoutes via clinicalRoles.CLINICAL_MANAGER_ROLES — an
// OPTOMETRIST can be a subject of this screen, so they never see it.

import { useAuth } from '../../context/AuthContext';
import { AbuseDetection } from '../../components/clinical/AbuseDetection';

export function ClinicalAbusePage() {
  const { user } = useAuth();
  return <AbuseDetection storeId={user?.activeStoreId} />;
}

export default ClinicalAbusePage;
