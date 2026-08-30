// Approvals routes. Moved verbatim from App.tsx (route-registry split);
// paths, elements and role gates are unchanged.
import { lazy } from 'react';
import { Route } from 'react-router-dom';
import { ProtectedRoute } from '../components/layout/ProtectedRoute';

const ApprovalInboxPage = lazy(() => import('../pages/approvals/ApprovalInboxPage').then(m => ({ default: m.ApprovalInboxPage })));
const MyRequestsPage = lazy(() => import('../pages/approvals/MyRequestsPage').then(m => ({ default: m.MyRequestsPage })));
const PendingApprovalsPage = lazy(() => import('../pages/returns/PendingApprovalsPage').then(m => ({ default: m.PendingApprovalsPage })));

export const approvalRoutes = (
  <>
    {/* E4 Approvals — inbox (approvers) + my requests (any maker).
        Route gates mirror the backend rbac_policy: inbox/approve
        is the approver set; my-requests is any authenticated user. */}
    <Route
      path="approvals"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
          <ApprovalInboxPage />
        </ProtectedRoute>
      }
    />
    <Route
      path="approvals/mine"
      element={<ProtectedRoute><MyRequestsPage /></ProtectedRoute>}
    />
    {/* F27 refund approvals queue — the refund-only slice of the
        E4 inbox. Gate mirrors the backend approvals inbox roles
        (ACCOUNTANT is read-only; approve/reject is gated again
        server-side to the approver set). */}
    <Route
      path="returns/approvals"
      element={
        <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT']}>
          <PendingApprovalsPage />
        </ProtectedRoute>
      }
    />
  </>
);
