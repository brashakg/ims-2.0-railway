// Clinical / eye-test routes. Moved verbatim from App.tsx (route-registry
// split); paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

const ClinicalPage = lazy(() => import('../pages/clinical/ClinicalPage').then(m => ({ default: m.ClinicalPage })));
const NewEyeTestPage = lazy(() => import('../pages/clinical/NewEyeTestPage').then(m => ({ default: m.NewEyeTestPage })));
const TestHistoryPage = lazy(() => import('../pages/clinical/TestHistoryPage').then(m => ({ default: m.TestHistoryPage })));
const PrescriptionsPage = lazy(() => import('../pages/clinical/PrescriptionsPage').then(m => ({ default: m.PrescriptionsPage })));
const FamilyRxPage = lazy(() => import('../pages/clinical/FamilyRxPage').then(m => ({ default: m.FamilyRxPage })));
const ContactLensFittingPage = lazy(() => import('../pages/clinical/ContactLensFittingPage').then(m => ({ default: m.ContactLensFittingPage })));

export const clinicalRoutes = (
  <>
    {/* Clinical / Eye Tests */}
    <Route
      path="clinical"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST']}
        >
          <ClinicalPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="clinical/test"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST']}
        >
          <NewEyeTestPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="clinical/history"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST']}
        >
          <TestHistoryPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="prescriptions"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST']}
        >
          <PrescriptionsPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="clinical/family-rx"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST']}
        >
          <FamilyRxPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="clinical/contact-lens"
      element={
        <ProtectedRoute
          allowedRoles={['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST']}
        >
          <ContactLensFittingPage />
        </ProtectedRoute>
      }
    />
  </>
);
