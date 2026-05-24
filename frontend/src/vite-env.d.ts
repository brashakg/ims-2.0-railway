/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL: string;
  readonly VITE_API_KEY: string;
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
