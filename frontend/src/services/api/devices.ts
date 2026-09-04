// ============================================================================
// IMS 2.0 - Approved-device login gate API (owner rulings 2026-09-02)
// ============================================================================
// Client for backend/api/routers/devices.py (/auth/devices/*). The gate is
// DARK until the backend env DEVICE_GATE_MODE is armed; every call here is
// safe to make while it is off.
//
// Two halves:
//   * devicesApi        - SUPERADMIN management (the phone approval screen)
//                         + the pre-auth enrolment/assertion endpoints.
//   * WebAuthn helpers  - the browser passkey ceremonies the LOGIN PAGE will
//                         wire once the gate is armed (enrolDevicePasskey /
//                         getDeviceAssertion). They live here so the login
//                         wiring is a single call, and so the base64 plumbing
//                         has exactly one implementation.
//
// Import directly from this module (not the barrel) per convention.

import api from './client';

// ----------------------------------------------------------------------------
// Types
// ----------------------------------------------------------------------------

export type DeviceStatus = 'PENDING' | 'APPROVED' | 'REVOKED';

export interface LoginDevice {
  device_id: string;
  credential_id: string;
  status: DeviceStatus | string;
  device_name: string;
  platform?: string | null;
  /** True when the passkey is sync-eligible (iCloud / Google Password
   *  Manager) and may follow the enrolling person to their other devices —
   *  shown as a warning chip on the approval screen. Null = unknown. */
  backup_eligible?: boolean | null;
  requested_by?: { user_id?: string; username?: string } | null;
  requested_at?: string | null;
  approved_by?: { user_id?: string; username?: string } | null;
  approved_at?: string | null;
  revoked_by?: { user_id?: string; username?: string } | null;
  revoked_at?: string | null;
}

export interface DeviceListResponse {
  /** Live gate mode: 'off' (dark), 'log' (dry run), 'enforce'. */
  mode: 'off' | 'log' | 'enforce' | string;
  devices: LoginDevice[];
}

export interface ChallengeOptions {
  challenge_id: string;
  challenge: string; // base64url
  rp_id: string;
  timeout_ms?: number;
  user_handle?: string; // enrolment only
  username?: string; // enrolment only
}

/** Shape of LoginRequest.device_assertion on POST /auth/login. */
export interface DeviceAssertionPayload {
  challenge_id: string;
  credential_id: string;
  client_data_json: string;
  authenticator_data: string;
  signature: string;
}

// ----------------------------------------------------------------------------
// API
// ----------------------------------------------------------------------------

export const devicesApi = {
  /** SUPERADMIN: all device rows (pending first) + live gate mode. */
  list: async (): Promise<DeviceListResponse> => {
    const { data } = await api.get<DeviceListResponse>('/auth/devices');
    return data;
  },

  /** SUPERADMIN: approve a pending (or re-approve a revoked) device. */
  approve: async (deviceId: string): Promise<LoginDevice> => {
    const { data } = await api.post<LoginDevice>(
      `/auth/devices/${deviceId}/approve`
    );
    return data;
  },

  /** SUPERADMIN: revoke an approved device / reject a pending request. */
  revoke: async (deviceId: string): Promise<LoginDevice> => {
    const { data } = await api.post<LoginDevice>(
      `/auth/devices/${deviceId}/revoke`
    );
    return data;
  },

  /** AUTHENTICATED pre-arming path: while the gate is off (dark) or in
   *  dry-run, staff still sign in normally — this mints a ticket so the
   *  CURRENT device can be registered and approved BEFORE enforce mode is
   *  armed. Without it, arming would block every till at once. */
  enrollTicket: async (): Promise<{ enroll_ticket: string }> => {
    const { data } = await api.post<{ enroll_ticket: string }>(
      '/auth/devices/enroll-ticket'
    );
    return data;
  },

  /** Pre-auth: challenge for the create() ceremony (needs the enrolment
   *  ticket a device-rejected login returns). */
  enrollOptions: async (enrollTicket: string): Promise<ChallengeOptions> => {
    const { data } = await api.post<ChallengeOptions>(
      '/auth/devices/enroll/options',
      { enroll_ticket: enrollTicket }
    );
    return data;
  },

  /** Pre-auth: submit the created credential as a PENDING device. */
  enroll: async (payload: {
    enroll_ticket: string;
    challenge_id: string;
    credential_id: string;
    client_data_json: string;
    public_key_spki: string;
    public_key_alg?: number | null;
    authenticator_data?: string | null;
    device_name: string;
    platform?: string;
  }): Promise<{ device_id: string; status: string; message: string }> => {
    const { data } = await api.post('/auth/devices/enroll', payload);
    return data;
  },

  /** Pre-auth: fresh single-use challenge for the get() ceremony. */
  assertionOptions: async (): Promise<ChallengeOptions> => {
    const { data } = await api.post<ChallengeOptions>(
      '/auth/devices/assertion-options'
    );
    return data;
  },
};

// ----------------------------------------------------------------------------
// WebAuthn browser ceremonies (for the login-page wiring, once armed)
// ----------------------------------------------------------------------------

/** localStorage key holding this browser's enrolled credential id (NOT a
 *  secret — the private key never leaves the platform authenticator). */
export const DEVICE_CREDENTIAL_KEY = 'ims_device_credential_id';

const b64ToBytes = (value: string): Uint8Array => {
  const pad = value.replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(pad + '='.repeat((4 - (pad.length % 4)) % 4));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
};

const bytesToB64 = (buf: ArrayBuffer): string => {
  let out = '';
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i++) out += String.fromCharCode(bytes[i]);
  return btoa(out);
};

const bytesToB64Url = (buf: ArrayBuffer): string =>
  bytesToB64(buf).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

/** Create a platform passkey for THIS device and submit it for approval.
 *  Returns the pending device id. Throws if the browser lacks WebAuthn or
 *  the user cancels the OS prompt. */
export async function enrolDevicePasskey(
  enrollTicket: string,
  deviceName: string
): Promise<{ device_id: string; status: string; message: string }> {
  const opts = await devicesApi.enrollOptions(enrollTicket);
  const credential = (await navigator.credentials.create({
    publicKey: {
      challenge: b64ToBytes(opts.challenge) as BufferSource,
      rp: { id: opts.rp_id || undefined, name: 'IMS 2.0' },
      user: {
        id: b64ToBytes(opts.user_handle || opts.challenge_id) as BufferSource,
        name: deviceName,
        displayName: deviceName,
      },
      pubKeyCredParams: [
        { type: 'public-key', alg: -7 }, // ES256
        { type: 'public-key', alg: -257 }, // RS256 (Windows Hello)
      ],
      authenticatorSelection: {
        authenticatorAttachment: 'platform',
        residentKey: 'preferred',
        userVerification: 'required',
      },
      attestation: 'none',
      timeout: opts.timeout_ms || 60000,
    },
  })) as PublicKeyCredential | null;
  if (!credential) throw new Error('Device registration was cancelled');
  const response = credential.response as AuthenticatorAttestationResponse;
  const spki = response.getPublicKey?.();
  if (!spki) throw new Error('This browser cannot register a device passkey');
  const result = await devicesApi.enroll({
    enroll_ticket: enrollTicket,
    challenge_id: opts.challenge_id,
    credential_id: credential.id,
    client_data_json: bytesToB64(response.clientDataJSON),
    public_key_spki: bytesToB64(spki),
    public_key_alg: response.getPublicKeyAlgorithm?.() ?? null,
    authenticator_data: response.getAuthenticatorData
      ? bytesToB64(response.getAuthenticatorData())
      : null,
    device_name: deviceName,
    platform: navigator.userAgent,
  });
  try {
    localStorage.setItem(DEVICE_CREDENTIAL_KEY, credential.id);
  } catch {
    /* private mode - re-enrolment will be offered */
  }
  return result;
}

/** Sign a fresh server challenge with this device's passkey; the result goes
 *  on LoginRequest.device_assertion. Returns null when this browser has no
 *  enrolled credential recorded (caller then shows the enrol flow). */
export async function getDeviceAssertion(): Promise<DeviceAssertionPayload | null> {
  let credentialId: string | null = null;
  try {
    credentialId = localStorage.getItem(DEVICE_CREDENTIAL_KEY);
  } catch {
    credentialId = null;
  }
  if (!credentialId) return null;
  const opts = await devicesApi.assertionOptions();
  const credential = (await navigator.credentials.get({
    publicKey: {
      challenge: b64ToBytes(opts.challenge) as BufferSource,
      rpId: opts.rp_id || undefined,
      allowCredentials: [
        { type: 'public-key', id: b64ToBytes(credentialId) as BufferSource },
      ],
      userVerification: 'preferred',
      timeout: opts.timeout_ms || 60000,
    },
  })) as PublicKeyCredential | null;
  if (!credential) return null;
  const response = credential.response as AuthenticatorAssertionResponse;
  return {
    challenge_id: opts.challenge_id,
    credential_id: bytesToB64Url(credential.rawId),
    client_data_json: bytesToB64(response.clientDataJSON),
    authenticator_data: bytesToB64(response.authenticatorData),
    signature: bytesToB64(response.signature),
  };
}
