// ============================================================================
// IMS 2.0 - Approval Workflow System
// ============================================================================
// Multi-step approval workflows with role-based routing

import { useState } from 'react';
import React from 'react';
import { CheckCircle2, Clock, XCircle, AlertCircle } from 'lucide-react';
import clsx from 'clsx';

export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'on_hold';

export interface ApprovalStep {
  id: string;
  level: number;
  approverRole: string;
  approverName: string;
  status: ApprovalStatus;
  comments?: string;
  actionDate?: string;
  requiredAttempts: number;
  currentAttempts: number;
}

export interface ApprovalRequest {
  id: string;
  title: string;
  description: string;
  requestedBy: string;
  requestDate: string;
  amount?: number;
  steps: ApprovalStep[];
  currentLevel: number;
  type: 'purchase' | 'expense' | 'leave' | 'promotion' | 'budget' | 'custom';
  status: ApprovalStatus;
  priority: 'low' | 'normal' | 'high' | 'urgent';
  metadata?: Record<string, any>;
}

interface ApprovalWorkflowProps {
  request: ApprovalRequest;
  onApprove: (stepId: string, comments?: string) => Promise<void>;
  onReject: (stepId: string, reason: string) => Promise<void>;
  onResubmit: () => Promise<void>;
  canApprove: boolean;
  loading?: boolean;
}

const statusIcons = {
  pending: Clock,
  approved: CheckCircle2,
  rejected: XCircle,
  on_hold: AlertCircle,
};

const statusColors = {
  pending: 'text-yellow-600 dark:text-yellow-400',
  approved: 'text-green-600 dark:text-green-400',
  rejected: 'text-red-600 dark:text-red-400',
  on_hold: 'text-blue-600 dark:text-blue-400',
};

const statusBg = {
  pending: 'bg-yellow-50 dark:bg-yellow-900/20 border-yellow-200 dark:border-yellow-800',
  approved: 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800',
  rejected: 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800',
  on_hold: 'bg-blue-50 dark:bg-blue-900/20 border-blue-200 dark:border-blue-800',
};

export function ApprovalWorkflow({
  request,
  onApprove,
  onReject,
  onResubmit,
  canApprove,
  loading = false,
}: ApprovalWorkflowProps) {
  const [selectedStep, setSelectedStep] = useState<string | null>(null);
  const [approveComments, setApproveComments] = useState('');
  const [rejectReason, setRejectReason] = useState('');
  const [showApproveModal, setShowApproveModal] = useState(false);
  const [showRejectModal, setShowRejectModal] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);

  const currentStep = request.steps[request.currentLevel];
  const isRejected = request.status === 'rejected';

  const handleApprove = async (stepId: string) => {
    setActionLoading(true);
    try {
      await Promise.resolve(onApprove(stepId, approveComments));
      setShowApproveModal(false);
      setApproveComments('');
      setSelectedStep(null);
    } finally {
      setActionLoading(false);
    }
  };

  const handleReject = async (stepId: string) => {
    if (!rejectReason.trim()) {
      alert('Please provide a rejection reason');
      return;
    }

    setActionLoading(true);
    try {
      await Promise.resolve(onReject(stepId, rejectReason));
      setShowRejectModal(false);
      setRejectReason('');
      setSelectedStep(null);
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <div className="bg-white dark:bg-gray-900 rounded-lg border border-gray-200 dark:border-gray-800">
      {/* Header */}
      <div className={clsx('p-6 border-b border-gray-200 dark:border-gray-800', statusBg[request.status])}>
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1">
            <h2 className="text-lg font-bold text-gray-900 dark:text-white">
              {request.title}
            </h2>
            <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
              {request.description}
            </p>
            {request.amount && (
              <p className="text-lg font-semibold text-gray-900 dark:text-white mt-2">
                ₹{request.amount.toLocaleString()}
              </p>
            )}
          </div>
          <div className="text-right">
            <div className="flex items-center gap-2 mb-2">
              {React.createElement(statusIcons[request.status], {
                className: clsx('w-6 h-6', statusColors[request.status]),
              })}
              <span className="capitalize font-semibold text-sm">
                {request.status.replace('_', ' ')}
              </span>
            </div>
            <span className={clsx('text-xs px-2 py-1 rounded', {
              'bg-red-100 text-red-700 dark:bg-red-900/20 dark:text-red-400': request.priority === 'urgent',
              'bg-orange-100 text-orange-700 dark:bg-orange-900/20 dark:text-orange-400': request.priority === 'high',
              'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/20 dark:text-yellow-400': request.priority === 'normal',
              'bg-blue-100 text-blue-700 dark:bg-blue-900/20 dark:text-blue-400': request.priority === 'low',
            })}>
              {request.priority.toUpperCase()}
            </span>
          </div>
        </div>
      </div>

      {/* Timeline */}
      <div className="p-6">
        <h3 className="font-bold text-gray-900 dark:text-white mb-6">Approval Workflow</h3>
        <div className="space-y-6">
          {request.steps.map((step, index) => (
            <div key={step.id} className="relative">
              {index < request.steps.length - 1 && (
                <div className="absolute left-6 top-12 bottom-0 w-0.5 bg-gray-200 dark:bg-gray-700" />
              )}

              <button
                onClick={() => setSelectedStep(selectedStep === step.id ? null : step.id)}
                className={clsx(
                  'w-full text-left p-4 rounded-lg border-2 transition-colors',
                  step.status === 'pending' && 'border-yellow-200 dark:border-yellow-800 bg-yellow-50 dark:bg-yellow-900/10',
                  step.status === 'approved' && 'border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-900/10',
                  step.status === 'rejected' && 'border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/10',
                  step.status === 'on_hold' && 'border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-900/10'
                )}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    {React.createElement(statusIcons[step.status], {
                      className: clsx('w-5 h-5 relative z-10', statusColors[step.status]),
                    })}
                    <div>
                      <p className="font-semibold text-gray-900 dark:text-white">
                        Level {step.level}: {step.approverRole}
                      </p>
                      <p className="text-xs text-gray-600 dark:text-gray-400">
                        {step.approverName}
                      </p>
                    </div>
                  </div>
                  {step.actionDate && (
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                      {new Date(step.actionDate).toLocaleDateString()}
                    </p>
                  )}
                </div>
              </button>

              {/* Expanded Step Details */}
              {selectedStep === step.id && (
                <div className="mt-3 p-4 bg-gray-50 dark:bg-gray-800 rounded-lg space-y-4">
                  {step.comments && (
                    <div>
                      <p className="text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">
                        Comments
                      </p>
                      <p className="text-sm text-gray-700 dark:text-gray-300">
                        {step.comments}
                      </p>
                    </div>
                  )}

                  {step.status === 'pending' && canApprove && request.currentLevel === index && (
                    <div className="flex gap-2">
                      <button
                        onClick={() => setShowApproveModal(true)}
                        className="flex-1 flex items-center justify-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors font-medium disabled:opacity-50"
                        disabled={actionLoading}
                      >
                        <CheckCircle2 className="w-4 h-4" />
                        Approve
                      </button>
                      <button
                        onClick={() => setShowRejectModal(true)}
                        className="flex-1 flex items-center justify-center gap-2 px-4 py-2 border border-red-300 dark:border-red-700 text-red-600 dark:text-red-400 rounded-lg hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors font-medium disabled:opacity-50"
                        disabled={actionLoading}
                      >
                        <XCircle className="w-4 h-4" />
                        Reject
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Actions Footer */}
      {isRejected && (
        <div className="border-t border-gray-200 dark:border-gray-800 p-6 bg-red-50 dark:bg-red-900/10">
          <p className="text-sm text-red-700 dark:text-red-300 mb-4">
            This request has been rejected. You can resubmit it after making the necessary changes.
          </p>
          <button
            onClick={onResubmit}
            disabled={loading}
            className="px-4 py-2 bg-orange-600 text-white rounded-lg hover:bg-orange-700 transition-colors font-medium disabled:opacity-50"
          >
            Resubmit Request
          </button>
        </div>
      )}

      {/* Modals */}
      {showApproveModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onClick={() => setShowApproveModal(false)}>
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-sm w-full" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4">
              Approve Request?
            </h2>
            <textarea
              value={approveComments}
              onChange={e => setApproveComments(e.target.value)}
              placeholder="Add comments (optional)"
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white mb-4"
              rows={3}
            />
            <div className="flex gap-2">
              <button onClick={() => setShowApproveModal(false)} className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
                Cancel
              </button>
              <button onClick={() => handleApprove(currentStep.id)} disabled={actionLoading} className="flex-1 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50">
                {actionLoading ? 'Approving...' : 'Approve'}
              </button>
            </div>
          </div>
        </div>
      )}

      {showRejectModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" onClick={() => setShowRejectModal(false)}>
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-sm w-full" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-4">
              Reject Request?
            </h2>
            <textarea
              value={rejectReason}
              onChange={e => setRejectReason(e.target.value)}
              placeholder="Please provide a reason for rejection"
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white mb-4"
              rows={3}
            />
            <div className="flex gap-2">
              <button onClick={() => setShowRejectModal(false)} className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
                Cancel
              </button>
              <button onClick={() => handleReject(currentStep.id)} disabled={actionLoading || !rejectReason.trim()} className="flex-1 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50">
                {actionLoading ? 'Rejecting...' : 'Reject'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
