// E2E-only preview config.
//
// `vite preview` serves the built SPA. This config adds a /api proxy so the
// suite is SAME-ORIGIN (http://localhost:4173 -> backend on :8000), avoiding
// CORS entirely and mirroring how the app is reverse-proxied in production.
// Used only by the E2E CI lane (the app build uses vite.config.ts).
import { mergeConfig, defineConfig } from 'vite';
import base from './vite.config';

const backend = process.env.E2E_BACKEND_URL ?? 'http://localhost:8000';

export default mergeConfig(
  base,
  defineConfig({
    preview: {
      port: 4173,
      strictPort: true,
      host: true,
      proxy: {
        '/api': {
          target: backend,
          changeOrigin: true,
        },
      },
    },
  })
);
