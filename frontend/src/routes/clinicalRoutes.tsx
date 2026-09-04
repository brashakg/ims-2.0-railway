// Clinical / eye-test routes.
//
// Wave 2 split: the ClinicalPage mega-page (5 sections in useState, zero
// bookmarkable addresses) is gone, replaced by a layout with one REAL page per
// section:
//   /clinical/queue · /clinical/completed · /clinical/prescriptions ·
//   /clinical/abuse-alerts (managers) · /clinical/conversion
//
// The TWO RIVAL PRESCRIPTION DOORS are consolidated: the standalone
// /prescriptions library page is DELETED (read-only, CL-blind, private
// formatter copy) and its address forwards to /clinical/prescriptions, which
// mounts the surviving door (ClinicPrescriptionHistory in panel+search mode —
// the only implementation that can create/edit and is CL-aware). Its "Mark
// redo" action was re-homed on /clinical/history's detail modal.
//
// /clinical/test was a placeholder page whose entire behaviour was "go to the
// queue" (with a 2-second auto-redirect); it is now an actual redirect.
//
// /clinical/test/:entryId is THE EYE EXAMINATION -- its own page, opened from
// a queue row (it used to be a modal over the queue). /clinical/test/amend/
// :testId is the same page reopening a completed exam from Rx history.
//
// Role gates come from ONE list, pages/clinical/clinicalRoles.ts. The six
// contradicting copies that used to live here and inside ClinicalPage are
// deleted, not synced.
//
// /clinical/history, /clinical/family-rx and /clinical/contact-lens keep their
// addresses AND their standalone full-page chrome (they were never tabs of the
// mega-page), so they sit outside the layout route.
import { lazy } from 'react';
import { Route, Navigate } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';
import { CLINICAL_MODULE_ROLES, CLINICAL_MANAGER_ROLES } from '../pages/clinical/clinicalRoles';

const ClinicalLayout = lazy(() => import('../pages/clinical/ClinicalLayout').then(m => ({ default: m.ClinicalLayout })));
const ClinicalQueuePage = lazy(() => import('../pages/clinical/ClinicalQueuePage').then(m => ({ default: m.ClinicalQueuePage })));
const ClinicalCompletedPage = lazy(() => import('../pages/clinical/ClinicalCompletedPage').then(m => ({ default: m.ClinicalCompletedPage })));
const ClinicalPrescriptionsPage = lazy(() => import('../pages/clinical/ClinicalPrescriptionsPage').then(m => ({ default: m.ClinicalPrescriptionsPage })));
const ClinicalAbusePage = lazy(() => import('../pages/clinical/ClinicalAbusePage').then(m => ({ default: m.ClinicalAbusePage })));
const ClinicalConversionPage = lazy(() => import('../pages/clinical/ConversionTab').then(m => ({ default: m.ConversionTab })));
const EyeExamPage = lazy(() => import('../pages/clinical/EyeExamPage').then(m => ({ default: m.EyeExamPage })));
const TestHistoryPage = lazy(() => import('../pages/clinical/TestHistoryPage').then(m => ({ default: m.TestHistoryPage })));
const FamilyRxPage = lazy(() => import('../pages/clinical/FamilyRxPage').then(m => ({ default: m.FamilyRxPage })));
const ContactLensFittingPage = lazy(() => import('../pages/clinical/ContactLensFittingPage').then(m => ({ default: m.ContactLensFittingPage })));

export const clinicalRoutes = (
  <>
    {/* Clinical / Eye Tests — layout + one page per section */}
    <Route
      path="clinical"
      element={
        <ProtectedRoute allowedRoles={CLINICAL_MODULE_ROLES}>
          <ClinicalLayout />
        </ProtectedRoute>
      }
    >
      {/* Bare /clinical — the queue is the module's home. */}
      <Route index element={<Navigate to="/clinical/queue" replace />} />
      <Route
        path="queue"
        element={
          <ProtectedRoute allowedRoles={CLINICAL_MODULE_ROLES}>
            <ClinicalQueuePage />
          </ProtectedRoute>
        }
      />
      <Route
        path="completed"
        element={
          <ProtectedRoute allowedRoles={CLINICAL_MODULE_ROLES}>
            <ClinicalCompletedPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="prescriptions"
        element={
          <ProtectedRoute allowedRoles={CLINICAL_MODULE_ROLES}>
            <ClinicalPrescriptionsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="abuse-alerts"
        element={
          <ProtectedRoute allowedRoles={CLINICAL_MANAGER_ROLES}>
            <ClinicalAbusePage />
          </ProtectedRoute>
        }
      />
      <Route
        path="conversion"
        element={
          <ProtectedRoute allowedRoles={CLINICAL_MODULE_ROLES}>
            <ClinicalConversionPage />
          </ProtectedRoute>
        }
      />
      {/* /clinical/test was NewEyeTestPage — a screen whose whole message was
          "add the patient to the queue first" plus a 2s auto-redirect. Now the
          redirect it always was. */}
      <Route path="test" element={<Navigate to="/clinical/queue" replace />} />
      {/* The eye examination: a page with an address, inside the module rail. */}
      <Route
        path="test/:entryId"
        element={
          <ProtectedRoute allowedRoles={CLINICAL_MODULE_ROLES}>
            <EyeExamPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="test/amend/:testId"
        element={
          <ProtectedRoute allowedRoles={CLINICAL_MODULE_ROLES}>
            <EyeExamPage />
          </ProtectedRoute>
        }
      />
    </Route>

    {/* Standalone clinical pages (own full-page chrome, addresses unchanged) */}
    <Route
      path="clinical/history"
      element={
        <ProtectedRoute allowedRoles={CLINICAL_MODULE_ROLES}>
          <TestHistoryPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="clinical/family-rx"
      element={
        <ProtectedRoute allowedRoles={CLINICAL_MODULE_ROLES}>
          <FamilyRxPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="clinical/contact-lens"
      element={
        <ProtectedRoute allowedRoles={CLINICAL_MODULE_ROLES}>
          <ContactLensFittingPage />
        </ProtectedRoute>
      }
    />

    {/* The old standalone Rx-library address forwards to the ONE surviving
        prescriptions door. Kept forever: bookmarks + the Hub linked it. */}
    <Route path="prescriptions" element={<Navigate to="/clinical/prescriptions" replace />} />
  </>
);
