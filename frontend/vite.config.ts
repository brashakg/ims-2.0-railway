/// <reference types="vitest" />
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// ---------------------------------------------------------------------------
// Build identity for the "new version available — refresh" banner.
// Computed once at config-load time so the value baked into the bundle
// (__APP_BUILD_ID__) is byte-identical to the one emitted to dist/version.json.
// Prefer a deploy-stable git short SHA from the build env; fall back to a
// build-time ISO timestamp (evaluated here, in Node, at config load — safe even
// where Date.now() is restricted at runtime). Vercel exposes
// VERCEL_GIT_COMMIT_SHA; we also accept a plain GIT_SHA override.
// ---------------------------------------------------------------------------
const RAW_SHA =
  process.env.VERCEL_GIT_COMMIT_SHA ||
  process.env.GIT_SHA ||
  process.env.RAILWAY_GIT_COMMIT_SHA ||
  ''
const APP_BUILD_ID = (RAW_SHA ? RAW_SHA.slice(0, 8) : new Date().toISOString())

// Tiny dependency-free plugin: write dist/version.json at the end of the build.
// version.json is at the site root (NOT under /assets/), so vercel.json's
// catch-all no-cache rule keeps it always-fresh — exactly what the update check
// needs to detect a new deploy.
function emitVersionJson(buildId: string): Plugin {
  return {
    name: 'emit-version-json',
    apply: 'build',
    generateBundle() {
      this.emitFile({
        type: 'asset',
        fileName: 'version.json',
        source: JSON.stringify({ build: buildId }) + '\n',
      })
    },
  }
}

export default defineConfig({
  define: {
    __APP_BUILD_ID__: JSON.stringify(APP_BUILD_ID),
  },
  plugins: [react(), tailwindcss(), emitVersionJson(APP_BUILD_ID)],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: [],
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      '@components': path.resolve(__dirname, './src/components'),
      '@pages': path.resolve(__dirname, './src/pages'),
      '@hooks': path.resolve(__dirname, './src/hooks'),
      '@services': path.resolve(__dirname, './src/services'),
      '@context': path.resolve(__dirname, './src/context'),
      '@types': path.resolve(__dirname, './src/types'),
      '@utils': path.resolve(__dirname, './src/utils'),
    },
  },
  server: {
    port: 3000,
    middlewareMode: false,
    // Disable Vite's dev tools and overlays in development
    hmr: {
      protocol: 'ws',
      host: 'localhost',
      port: 5173,
    },
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    minify: 'terser',
    terserOptions: {
      compress: {
        drop_console: true,
        drop_debugger: true,
      },
    },
    rollupOptions: {
      output: {
        // Vite 8's Rolldown-powered bundler only accepts a function here
        // (the old Rollup object-map form throws "manualChunks is not a
        // function"); same vendor split, expressed by matching the id.
        manualChunks(id) {
          if (/node_modules\/(react|react-dom|react-router-dom)\//.test(id)) {
            return 'vendor-react'
          }
          if (/node_modules\/(lucide-react|clsx)\//.test(id)) {
            return 'vendor-ui'
          }
          if (/node_modules\/(date-fns|axios|zustand)\//.test(id)) {
            return 'vendor-utils'
          }
        },
      },
    },
  },
})
