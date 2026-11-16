// ============================================================================
// IMS 2.0 - where the page may reach QZ Tray (THE local printer endpoint policy)
// ============================================================================
// QZ Tray is a helper running on the SAME computer as the browser. The deployed
// page is https, so qz-tray only ever tries its secure websocket ports. This is
// the one list of those endpoints:
//   * qz.ts connects to exactly these (never QZ's second default host,
//     localhost.qz.io - a public DNS name that merely points at 127.0.0.1);
//   * vercel.json's Content-Security-Policy `connect-src` allows exactly these
//     (audit L2-INTEGRATION-F03: it allowed none, so the browser blocked every
//     QZ connection before it started and labels always fell to the print
//     window). src/__tests__/security-headers.test.ts holds the two together;
//     e2e/tests/qz-csp.spec.ts checks the header in a real browser.
//
// No imports on purpose: the e2e spec loads this file outside Vite.

/** QZ Tray's own host name for "this computer". */
export const QZ_HOST = 'localhost';

/** QZ Tray's secure websocket ports, in the order it tries them. */
export const QZ_SECURE_PORTS = [8181, 8282, 8383, 8484] as const;

/** The websocket origins the deployed page may open - and no others. */
export const QZ_ORIGINS: readonly string[] = QZ_SECURE_PORTS.map(
  (port) => `wss://${QZ_HOST}:${port}`,
);
