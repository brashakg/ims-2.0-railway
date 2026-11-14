// @vitest-environment-options { "url": "https://app.ims.test/" }
// ============================================================================
// The REAL qz-tray library dials only the endpoints the deployed CSP allows
// ============================================================================
// Audit L2-INTEGRATION-F03. qz.test.ts runs qz.ts over a stub; this runs the
// installed qz-tray itself, on an https page like the deployed one, with the
// connection options qz.ts passes - and records every address it tries when
// no helper answers. Each must be in QZ_ORIGINS (the list vercel.json's
// connect-src allows): one address outside it would be a CSP violation in the
// shop's browser. With QZ's own defaults it also tries localhost.qz.io, which
// the policy does not allow - the control below shows the probe can see that.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { QZ_HOST, QZ_ORIGINS, QZ_SECURE_PORTS } from '../qzEndpoint';

const dialled: string[] = [];

/** A websocket nobody answers: records the address, then errors. */
class DeadSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  readyState = DeadSocket.CONNECTING;
  onerror: ((e: unknown) => void) | null = null;
  constructor(public url: string) {
    dialled.push(url);
    setTimeout(() => {
      this.readyState = DeadSocket.CLOSED;
      this.onerror?.({ type: 'error' });
    }, 0);
  }
  close() {}
  send() {}
}

async function realQz() {
  vi.resetModules();
  vi.stubGlobal('WebSocket', DeadSocket);
  dialled.length = 0;
  const mod = await vi.importActual<any>('qz-tray');
  return mod.default || mod;
}

afterEach(() => vi.unstubAllGlobals());

describe('qz-tray on an https page', () => {
  it('with the options qz.ts passes, tries exactly the allowed endpoints', async () => {
    expect(window.location.protocol).toBe('https:');
    const qz = await realQz();
    await expect(
      qz.websocket.connect({ host: [QZ_HOST], port: { secure: [...QZ_SECURE_PORTS] } }),
    ).rejects.toThrow();
    expect(dialled).toEqual([...QZ_ORIGINS]);
  });

  it('control: with its own defaults it would also try a host the policy does not allow', async () => {
    const qz = await realQz();
    await expect(qz.websocket.connect()).rejects.toThrow();
    expect(dialled.filter((url) => !QZ_ORIGINS.includes(url))).toContain(
      'wss://localhost.qz.io:8181',
    );
  });
});
