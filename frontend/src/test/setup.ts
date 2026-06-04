// Vitest global setup: register @testing-library/jest-dom matchers
// (toBeInTheDocument, toHaveTextContent, ...) and auto-cleanup the DOM
// after each test.
import '@testing-library/jest-dom/vitest'
import { afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

// Jest-compat shim: a few legacy tests were authored against the jest API
// (jest.fn / jest.spyOn / jest.clearAllMocks) before the project had a runner.
// vitest's `vi` is API-compatible, so alias it. (jest.mock() hoisting is NOT
// covered here -- those calls are migrated to vi.mock() in the test files.)
;(globalThis as unknown as { jest: typeof vi }).jest = vi

afterEach(() => {
  cleanup()
})
