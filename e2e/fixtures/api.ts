/**
 * Backend API helper for stored-value assertions.
 *
 * The UI specs verify what the user sees; this helper verifies what the server
 * actually stored — the real source of truth (camelCase order fields, payment
 * status, etc.). It talks straight to the local backend (never prod).
 */
import { APIRequestContext, request as pwRequest } from '@playwright/test';
import { API_URL, CREDENTIALS, type GstMode } from './constants';

/** Decode a JWT payload (no verification — test-side inspection only). */
export function decodeJwt(token: string): Record<string, any> {
  const payload = token.split('.')[1];
  if (!payload) return {};
  const b64 = payload.replace(/-/g, '+').replace(/_/g, '/');
  const json = Buffer.from(b64, 'base64').toString('utf-8');
  return JSON.parse(json);
}

export class ApiClient {
  private constructor(
    private ctx: APIRequestContext,
    public token: string
  ) {}

  /** Log in against the local backend and return an authenticated client. */
  static async login(
    username = CREDENTIALS.username,
    password = CREDENTIALS.password
  ): Promise<ApiClient> {
    const ctx = await pwRequest.newContext({ baseURL: API_URL });
    const res = await ctx.post('/api/v1/auth/login', {
      data: { username, password },
    });
    if (!res.ok()) {
      throw new Error(
        `API login failed (${res.status()}): ${await res.text()}`
      );
    }
    const body = await res.json();
    const token = body.token ?? body.access_token;
    if (!token) {
      throw new Error(`API login returned no token: ${JSON.stringify(body)}`);
    }
    return new ApiClient(ctx, token);
  }

  private authHeaders() {
    return { Authorization: `Bearer ${this.token}` };
  }

  get activeStoreId(): string | undefined {
    return decodeJwt(this.token).active_store_id;
  }

  async getJson(path: string): Promise<any> {
    const res = await this.ctx.get(path, { headers: this.authHeaders() });
    if (!res.ok()) {
      throw new Error(`GET ${path} -> ${res.status()}: ${await res.text()}`);
    }
    return res.json();
  }

  /** Raw GET — caller inspects the status (used by the 200-guard specs). */
  async rawGet(path: string) {
    return this.ctx.get(path, { headers: this.authHeaders() });
  }

  /**
   * Fetch the persisted order. The POS success screen surfaces the
   * order_NUMBER (ORD-...), but GET /orders/{id} resolves by order_id (a UUID),
   * so a number 404s there. Resolve a number via /orders/search (matches
   * order_number) and fall back to the direct id lookup otherwise.
   */
  async getOrder(orderIdOrNumber: string): Promise<any> {
    if (/^ORD-/i.test(orderIdOrNumber)) {
      const { orders } = await this.getJson(
        `/api/v1/orders/search?q=${encodeURIComponent(orderIdOrNumber)}`
      );
      const match = (orders ?? []).find(
        (o: any) => o.orderNumber === orderIdOrNumber || o.order_number === orderIdOrNumber
      );
      if (!match) {
        throw new Error(
          `Order ${orderIdOrNumber} not found via /orders/search (got ${
            (orders ?? []).length
          } result(s))`
        );
      }
      return match;
    }
    return this.getJson(`/api/v1/orders/${orderIdOrNumber}`);
  }

  async switchStore(storeId: string): Promise<{ token: string; activeStoreId: string }> {
    const res = await this.ctx.post(`/api/v1/auth/switch-store/${storeId}`, {
      headers: this.authHeaders(),
    });
    if (!res.ok()) {
      throw new Error(`switch-store ${storeId} -> ${res.status()}: ${await res.text()}`);
    }
    const body = await res.json();
    this.token = body.access_token;
    return { token: body.access_token, activeStoreId: body.active_store_id };
  }

  async dispose() {
    await this.ctx.dispose();
  }
}

/**
 * Read the active GST pricing mode from the backend.
 *
 * The brief notes a possible runtime flag `GST_PRICING_MODE`. As of the merged
 * code there is no such field exposed on /health or /api/v1/config, so this
 * probes both, accepts several field name shapes, and otherwise defaults to
 * 'inclusive' (PR #331 made inclusive the live behavior). Specs read this so
 * they stay green whichever mode is active.
 */
export async function gstMode(): Promise<GstMode> {
  const ctx = await pwRequest.newContext({ baseURL: API_URL });
  try {
    for (const path of ['/health', '/api/v1/config']) {
      const res = await ctx.get(path);
      if (!res.ok()) continue;
      let body: any;
      try {
        body = await res.json();
      } catch {
        continue;
      }
      const raw =
        body.gst_pricing_mode ??
        body.gstPricingMode ??
        body.pricing_mode ??
        body.gst_mode ??
        body?.config?.gst_pricing_mode;
      if (typeof raw === 'string') {
        const norm = raw.toLowerCase();
        if (norm === 'exclusive') return 'exclusive';
        if (norm === 'inclusive') return 'inclusive';
      }
    }
  } finally {
    await ctx.dispose();
  }
  return 'inclusive';
}
