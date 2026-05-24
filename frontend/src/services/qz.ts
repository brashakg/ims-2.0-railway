// ============================================================================
// IMS 2.0 - QZ Tray printing service (silent raw ZPL) + HTML fallback
// ============================================================================
// Silent raw thermal printing goes through QZ Tray (a small desktop helper
// the user installs). QZ requires every print request to be signed; we sign
// server-side so the private key never reaches the browser.
//
// FAIL-SOFT CONTRACT: if QZ Tray is not installed/connectable, OR the signing
// endpoint returns empty (no cert/key configured on the server yet), we fall
// back to opening the label HTML in a print window (window.print()). The
// workshop is therefore usable BEFORE QZ + a cert are configured.
//
// NOTE: services are imported DIRECTLY (not via the services/api barrel) -- a
// barrel re-export of a newly-added service has repeatedly failed to resolve
// for consumers (TS2614) in this codebase.

import apiClient, { getSecureApiUrl } from './api/client';

// qz-tray has no bundled types; treat the default export as `any`. The dep is
// declared in package.json and installed by the parent (no node_modules here).
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let qzModule: any = null;
let connectPromise: Promise<boolean> | null = null;
let securityConfigured = false;

/** Result of an attempted print: did it go via QZ, or fall back to HTML? */
export type PrintMethod = 'qz' | 'html' | 'failed';
export interface PrintResult {
  method: PrintMethod;
  message: string;
}

/** Lazy-load the qz-tray module (only when first needed). */
async function loadQz(): Promise<any | null> {
  if (qzModule) return qzModule;
  try {
    // Dynamic import so a missing dep / SSR never breaks module load.
    const mod = await import('qz-tray');
    qzModule = (mod as any).default || mod;
    return qzModule;
  } catch {
    return null;
  }
}

/**
 * Fetch the QZ public certificate from the backend. Returns null when the
 * server returns 204 (no cert configured) -> signals "fall back to HTML".
 */
async function fetchCert(): Promise<string | null> {
  try {
    const resp = await apiClient.get('/print/qz/cert', { responseType: 'text' });
    if (resp.status === 204) return null;
    const data = resp.data;
    if (!data || typeof data !== 'string' || data.trim() === '') return null;
    return data;
  } catch {
    return null;
  }
}

/**
 * Ask the backend to sign a QZ request string with the server-held private
 * key. Returns null on 204 / empty / error -> caller resolves the QZ promise
 * with a rejection so QZ knows signing is unavailable.
 */
async function signRequest(toSign: string): Promise<string | null> {
  try {
    const resp = await apiClient.post(
      '/print/qz/sign',
      { request: toSign },
      { responseType: 'text' },
    );
    if (resp.status === 204) return null;
    const data = resp.data;
    if (!data || typeof data !== 'string' || data.trim() === '') return null;
    return data;
  } catch {
    return null;
  }
}

/**
 * Wire qz.security to our cert + sign endpoints. If no cert is configured we
 * leave security UNset and return false so callers fall back to HTML.
 */
async function configureSecurity(qz: any): Promise<boolean> {
  if (securityConfigured) return true;

  const cert = await fetchCert();
  if (!cert) return false; // No cert -> HTML fallback.

  qz.security.setCertificatePromise((resolve: (v: string) => void) => {
    resolve(cert);
  });

  qz.security.setSignatureAlgorithm?.('SHA256');
  qz.security.setSignaturePromise((toSign: string) => {
    return (resolve: (v: string) => void, reject: (e: unknown) => void) => {
      signRequest(toSign).then((sig) => {
        if (sig) resolve(sig);
        else reject(new Error('QZ signing unavailable'));
      });
    };
  });

  securityConfigured = true;
  return true;
}

/**
 * Attempt to connect to QZ Tray. Memoised so concurrent print calls share a
 * single connection attempt. Returns false (never throws) when QZ is not
 * reachable or not configured for signing.
 */
export async function connectQz(): Promise<boolean> {
  if (connectPromise) return connectPromise;

  connectPromise = (async () => {
    const qz = await loadQz();
    if (!qz) return false;

    try {
      const ok = await configureSecurity(qz);
      if (!ok) return false;

      if (qz.websocket.isActive && qz.websocket.isActive()) return true;
      await qz.websocket.connect();
      return true;
    } catch {
      return false;
    }
  })();

  // Don't cache a failed attempt forever -- allow a later retry.
  const result = await connectPromise;
  if (!result) connectPromise = null;
  return result;
}

/** Is QZ available right now (connected)? Best-effort, never throws. */
export async function isQzAvailable(): Promise<boolean> {
  const qz = await loadQz();
  if (!qz) return false;
  try {
    return !!(qz.websocket.isActive && qz.websocket.isActive());
  } catch {
    return false;
  }
}

/**
 * Open a print window for an HTML label document and trigger the browser
 * print dialog. This is the universal fallback. Returns a PrintResult.
 */
export function printHtmlFallback(htmlDocument: string): PrintResult {
  try {
    const win = window.open('', '_blank', 'width=420,height=620');
    if (!win) {
      return {
        method: 'failed',
        message: 'Could not open a print window (popup blocked?).',
      };
    }
    win.document.open();
    win.document.write(htmlDocument);
    win.document.close();
    // Give the barcode SVG a tick to render before invoking print.
    win.onload = () => {
      win.focus();
      win.print();
    };
    // Safety: some browsers don't fire onload for document.write.
    setTimeout(() => {
      try {
        win.focus();
        win.print();
      } catch {
        /* ignore */
      }
    }, 400);
    return { method: 'html', message: 'Opened label in a print window.' };
  } catch {
    return { method: 'failed', message: 'HTML print failed.' };
  }
}

/**
 * Print raw ZPL to a named printer via QZ Tray, falling back to the HTML
 * document if QZ is unavailable / signing not configured / the raw print
 * fails. NEVER throws -- always returns a PrintResult.
 *
 * @param printerName  the configured label printer name (from settings)
 * @param zpl          the raw ZPL string
 * @param htmlFallback a full printable HTML document (from wrapLabelDocument)
 */
export async function printZpl(
  printerName: string | undefined,
  zpl: string,
  htmlFallback: string,
): Promise<PrintResult> {
  // No printer name configured -> straight to HTML so we never silently no-op.
  if (!printerName) {
    return printHtmlFallback(htmlFallback);
  }

  const connected = await connectQz();
  if (!connected) {
    return printHtmlFallback(htmlFallback);
  }

  try {
    const qz = await loadQz();
    const config = qz.configs.create(printerName);
    await qz.print(config, [{ type: 'raw', format: 'plain', data: zpl }]);
    return { method: 'qz', message: `Sent to ${printerName} via QZ Tray.` };
  } catch {
    // Any QZ runtime error -> fall back rather than failing the workshop.
    return printHtmlFallback(htmlFallback);
  }
}

// The backend base URL is exported for any caller that needs to show where
// cert/sign are served from (diagnostics). Re-exported from the client so we
// keep a single source of truth.
export { getSecureApiUrl };
