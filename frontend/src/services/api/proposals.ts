// ============================================================================
// IMS 2.0 - AI Change-Proposal API (SUPERADMIN only)
// ============================================================================
// The review loop from SYSTEM_INTENT section 8: list pending proposals,
// approve (auto-executes when reversible Tier-1, else advisory) or reject.
// All endpoints are SUPERADMIN-gated server-side (404 for everyone else).

import api from './client';

export type ProposalStatus =
  | 'PENDING'
  | 'APPROVED'
  | 'REJECTED'
  | 'EXECUTED'
  | 'FAILED';

export interface AIProposal {
  proposal_id: string;
  created_by_agent: string;
  type: string;
  title: string;
  rationale: string;
  payload: Record<string, unknown>;
  status: ProposalStatus;
  reversible: boolean;
  created_at: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  reject_reason: string | null;
  before_state: Record<string, unknown> | null;
  after_state: Record<string, unknown> | null;
  execution_error: string | null;
  audit_log_id: string | null;
}

export interface ProposalListResponse {
  proposals: AIProposal[];
  total: number;
  filter_status: string | null;
  filter_type?: string | null;
  reversible_types: string[];
}

export interface ApproveResponse {
  ok: boolean;
  executed: boolean;
  advisory: boolean;
  error?: string;
  execution?: Record<string, unknown>;
  proposal: AIProposal;
}

export interface RejectResponse {
  ok: boolean;
  proposal: AIProposal;
}

export const proposalsApi = {
  // `type` filters to one proposal kind (e.g. 'draft_po' for #7 reorder
  // suggestions). Server-side filter; omitted -> all types (unchanged).
  list: async (params?: { status?: ProposalStatus; type?: string; limit?: number }) => {
    const { data } = await api.get<ProposalListResponse>('/jarvis/proposals', {
      params,
    });
    return data;
  },

  get: async (proposalId: string) => {
    const { data } = await api.get<AIProposal>(`/jarvis/proposals/${proposalId}`);
    return data;
  },

  // Approve: server auto-executes when the type is reversible Tier-1,
  // otherwise records the approval as advisory.
  approve: async (proposalId: string) => {
    const { data } = await api.post<ApproveResponse>(
      `/jarvis/proposals/${proposalId}/approve`,
    );
    return data;
  },

  reject: async (proposalId: string, reason: string) => {
    const { data } = await api.post<RejectResponse>(
      `/jarvis/proposals/${proposalId}/reject`,
      { reason },
    );
    return data;
  },
};
