// Disable INP / dev-tool overlays in production.
// Externalized from index.html so it complies with the strict CSP
// (script-src 'self'; no 'unsafe-inline'). Behaviour is identical to the
// former inline boot script.
if (typeof window !== 'undefined') {
  window.__VITE_DISABLE_DEV_TOOLS__ = true;
  window.__DISABLE_INP_OVERLAY__ = true;
}
