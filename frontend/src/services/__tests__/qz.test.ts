// ============================================================================
// QZ Tray connection: one attempt at a time, and a dropped socket reconnects
// ============================================================================
// Audit R36: connectQz kept the promise of a SUCCESSFUL connection forever and
// returned it before looking at the socket. Once QZ Tray dropped (the helper
// restarted, the PC slept), every later label in that tab went to the HTML
// print window - the websocket was never tried again until a full page reload.
// The audit's simulation, replayed here over a stub QZ: connect, print, drop,
// print again.
//
// Audit L2-INTEGRATION-F03 (the client half): the page reaches QZ Tray only at
// the endpoints of the one policy in qzEndpoint.ts - the same list the deployed
// CSP allows (security-headers.test.ts).

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QZ_HOST, QZ_ORIGINS, QZ_SECURE_PORTS } from '../qzEndpoint';

const h = vi.hoisted(() => {
  const state = {
    active: false,
    connects: [] as unknown[],
    prints: [] as unknown[],
    connectFails: false,
    printFails: false,
    pending: null as null | (() => void), // set to hold a connect open
    holdConnect: false,
  };
  const qz = {
    security: {
      setCertificatePromise: () => {},
      setSignatureAlgorithm: () => {},
      setSignaturePromise: () => {},
    },
    websocket: {
      isActive: () => state.active,
      connect: (options: unknown) => {
        state.connects.push(options);
        if (state.connectFails) return Promise.reject(new Error('Unable to establish connection'));
        if (state.holdConnect) {
          return new Promise<void>((resolve) => {
            state.pending = () => {
              state.active = true;
              resolve();
            };
          });
        }
        state.active = true;
        return Promise.resolve();
      },
    },
    configs: { create: (printer: string) => ({ printer }) },
    // Like the real helper: a print over a socket that is not up is refused.
    print: (_config: unknown, data: unknown) => {
      state.prints.push(data);
      return state.printFails || !state.active
        ? Promise.reject(new Error('A connection to QZ has not been established yet'))
        : Promise.resolve();
    },
  };
  return { state, qz, html: [] as string[] };
});

vi.mock('qz-tray', () => ({ default: h.qz }));
vi.mock('../api/client', () => ({
  default: {
    get: async () => ({ status: 200, data: 'CERT' }),
    post: async () => ({ status: 200, data: 'SIGNATURE' }),
  },
  getSecureApiUrl: () => 'http://api.test',
}));
vi.mock('../printWindow', () => ({
  printHtmlFallback: (doc: string) => {
    h.html.push(doc);
    return { method: 'html', message: 'Opened label in a print window.' };
  },
}));

/** A fresh copy of the service: its connection state is module-level, and the
 *  audit's "reload the page" is exactly a fresh module. */
async function loadQzService() {
  vi.resetModules();
  return import('../qz');
}

beforeEach(() => {
  Object.assign(h.state, {
    active: false,
    connects: [],
    prints: [],
    connectFails: false,
    printFails: false,
    pending: null,
    holdConnect: false,
  });
  h.html.length = 0;
});

describe('connectQz', () => {
  it('shares ONE attempt between presses that overlap', async () => {
    const { connectQz } = await loadQzService();
    h.state.holdConnect = true;
    const first = connectQz();
    const second = connectQz();
    await vi.waitFor(() => expect(h.state.pending).not.toBeNull());
    h.state.pending!();
    expect(await Promise.all([first, second])).toEqual([true, true]);
    expect(h.state.connects).toHaveLength(1);
  });

  it('does not reconnect while the socket is still up', async () => {
    const { connectQz } = await loadQzService();
    expect(await connectQz()).toBe(true);
    expect(await connectQz()).toBe(true);
    expect(h.state.connects).toHaveLength(1);
  });

  it('reaches QZ Tray only at the endpoints of the one policy', async () => {
    const { connectQz } = await loadQzService();
    await connectQz();
    expect(h.state.connects).toEqual([
      { host: [QZ_HOST], port: { secure: [...QZ_SECURE_PORTS] } },
    ]);
    expect(QZ_ORIGINS).toEqual([
      'wss://localhost:8181',
      'wss://localhost:8282',
      'wss://localhost:8383',
      'wss://localhost:8484',
    ]);
  });
});

describe('printZpl after the QZ socket drops (R36)', () => {
  it('reconnects in the same page and prints through QZ again', async () => {
    const { printZpl } = await loadQzService();
    expect((await printZpl('Zebra', '^XA1^XZ', '<html>1</html>')).method).toBe('qz');
    expect(h.state.connects).toHaveLength(1);

    h.state.active = false; // QZ Tray dropped; it is reachable again

    expect((await printZpl('Zebra', '^XA2^XZ', '<html>2</html>')).method).toBe('qz');
    expect((await printZpl('Zebra', '^XA3^XZ', '<html>3</html>')).method).toBe('qz');
    expect(h.state.connects).toHaveLength(2); // one reconnect, then reused
    expect(h.html).toEqual([]); // no label fell to the print window
    expect(h.state.prints).toHaveLength(3); // each label sent to QZ once
  });

  it('falls back to the print window while QZ is down, and recovers when it is back', async () => {
    const { printZpl } = await loadQzService();
    await printZpl('Zebra', '^XA1^XZ', '<html>1</html>');

    h.state.active = false;
    h.state.connectFails = true; // still down
    expect((await printZpl('Zebra', '^XA2^XZ', '<html>2</html>')).method).toBe('html');
    expect(h.html).toEqual(['<html>2</html>']);

    h.state.connectFails = false; // back
    expect((await printZpl('Zebra', '^XA3^XZ', '<html>3</html>')).method).toBe('qz');
    expect(h.html).toEqual(['<html>2</html>']);
  });

  it('never sends a label to QZ twice: a failed print goes to the print window once', async () => {
    const { printZpl } = await loadQzService();
    await printZpl('Zebra', '^XA1^XZ', '<html>1</html>');

    h.state.printFails = true; // the socket died mid-print: outcome unknown
    const res = await printZpl('Zebra', '^XA2^XZ', '<html>2</html>');
    expect(res.method).toBe('html');
    expect(h.state.prints).toHaveLength(2); // job 2 went to QZ exactly once
    expect(h.html).toEqual(['<html>2</html>']);
  });

  it('still goes straight to the print window with no printer chosen', async () => {
    const { printZpl } = await loadQzService();
    expect((await printZpl(undefined, '^XA1^XZ', '<html>1</html>')).method).toBe('html');
    expect(h.state.connects).toHaveLength(0);
  });
});
