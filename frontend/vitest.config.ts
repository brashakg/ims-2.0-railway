import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

// Vitest config for the IMS 2.0 frontend. jsdom + React Testing Library.
// Tests live in src/**/*.{test,spec}.{ts,tsx} and src/__tests__/** -- the app
// tsconfig already EXCLUDES those, so `npm run build` (tsc -b) never sees them.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  test: {
    environment: 'jsdom',
    // 5s (the default) is not a correctness bound, it is a load bound: under
    // parallel agents or a busy CI runner a dozen unrelated suites failed at
    // 5.1-6.5s and all 1,246 passed alone at 20s. A genuine hang still fails.
    testTimeout: 15000,
    hookTimeout: 15000,
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    css: false,
    restoreMocks: true,
    clearMocks: true,
    coverage: {
      // lcov is what the CI codecov upload reads (coverage/lcov.info);
      // without it the default reporters produce no lcov file at all.
      reporter: ['text', 'lcov'],
    },
  },
})
