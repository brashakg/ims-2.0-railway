// ============================================================================
// IMS 2.0 - Integration status API (read-only, SUPERADMIN)
// ============================================================================
// Thin client for GET /api/v1/jarvis/integrations/status. The backend reports
// KEY presence only (never secret values) plus the current DISPATCH_MODE.
// Import this module DIRECTLY (not via the services/api barrel) - newly added
// services don't reliably resolve through the barrel re-export.

import api from './client';

export interface IntegrationEnvKey {
  key: string;
  present: boolean;
}

export interface IntegrationCollectionState {
  exists: boolean;
  enabled: boolean;
  present_keys: string[];
  missing_required: string[];
}

export type IntegrationState =
  | 'live'
  | 'active'
  | 'test_only'
  | 'simulated'
  | 'dormant'
  | 'export_only'
  | 'not_wired';

export interface IntegrationStatusItem {
  id: string;
  label: string;
  powers: string;
  source: 'env' | 'env_or_collection' | 'collection' | 'export_only' | 'not_wired';
  dispatch_gated: boolean;
  configured: boolean;
  state: IntegrationState;
  env_keys: IntegrationEnvKey[];
  collection: IntegrationCollectionState | null;
  notes: string;
}

// MSG91 + Coexistence messaging preflight: honest per-item readiness rows,
// each carrying the owner's named next step. Names/ids/counts only - the
// backend never puts a credential value or a phone number in here.
export interface MessagingPreflightRow {
  id: string;
  label: string;
  ok: boolean;
  detail: string;
  next_step: string;
}

export interface MessagingPreflight {
  generated_at?: string;
  rows: MessagingPreflightRow[];
  ok: boolean;
}

export interface IntegrationStatusReport {
  generated_at: string;
  dispatch_mode: string;
  test_phone_set: boolean;
  summary: { total: number; configured: number; live: number };
  integrations: IntegrationStatusItem[];
  // Optional: absent on older backends; the card renders it only when present.
  messaging_preflight?: MessagingPreflight;
}

export async function getIntegrationStatus(): Promise<IntegrationStatusReport> {
  const { data } = await api.get<IntegrationStatusReport>('/jarvis/integrations/status');
  return data;
}

// ---------------------------------------------------------------------------
// Live Claude model list (for the Anthropic integration's model dropdown)
// ---------------------------------------------------------------------------
// Backed by GET /settings/integrations/anthropic/models, which lists the
// currently-available Claude models from the Anthropic Models API and
// FAIL-SOFTs to a curated current fallback list. ADMIN/SUPERADMIN only.

export interface AnthropicModel {
  id: string;
  display_name: string;
}

export interface AnthropicModelsResponse {
  models: AnthropicModel[];
  source: 'live' | 'cache' | 'fallback';
}

export async function getAnthropicModels(): Promise<AnthropicModelsResponse> {
  const { data } = await api.get<AnthropicModelsResponse>(
    '/settings/integrations/anthropic/models',
  );
  return data;
}

export const integrationsApi = { getIntegrationStatus, getAnthropicModels };
