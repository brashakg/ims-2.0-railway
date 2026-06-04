/// <reference types="vitest/config" />
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
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    css: false,
    restoreMocks: true,
    clearMocks: true,
  },
})
