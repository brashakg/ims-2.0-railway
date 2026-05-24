// ============================================================================
// IMS 2.0 - Online-store (e-commerce/BVI) SSO handoff
// ============================================================================
// Asks the backend to mint a short-lived RS256 exchange token and returns the
// URL to open (…/sso?token=…). Import directly (not via the api barrel).

import api from './client';

export const ecommerceSsoApi = {
  getUrl: async () => {
    const res = await api.get('/auth/ecommerce-sso');
    return res.data as { url: string; expires_in: number };
  },
};
