/// <reference types="vite/client" />

// Build identity injected at build time via vite `define` (see vite.config.ts).
// Used by the "new version available" update check to compare the running build
// against the freshly-fetched dist/version.json.
declare const __APP_BUILD_ID__: string;

interface ImportMetaEnv {
  readonly VITE_API_URL: string;
  readonly VITE_API_KEY: string;
  /** POS: when "true", auto-attach the customer's Rx at the Prescription step
   *  IFF exactly one valid (non-expired) Rx exists and none is attached yet.
   *  Default OFF — staff pick the Rx explicitly unless the owner opts in. */
  readonly VITE_POS_AUTO_ATTACH_SINGLE_RX?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

// qz-tray (silent raw thermal printing). The package ships partial types but
// we use it dynamically and treat it as `any`; this ambient declaration keeps
// `import('qz-tray')` type-checking cleanly regardless of the bundled types.
declare module 'qz-tray' {
  const qz: any;
  export default qz;
}
