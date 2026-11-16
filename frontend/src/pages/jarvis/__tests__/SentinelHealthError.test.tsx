// INFRA-05: a failed SENTINEL health fetch must not read as "no checks on record".
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SentinelHealth } from '../SentinelHealth'
import type { JarvisPageState } from '../useJarvisPage'

function page(over: Partial<Record<string, unknown>>) {
  return {
    sentinelHealth: null,
    sentinelLoading: false,
    sentinelError: false,
    handleRunNow: vi.fn(),
    loadSentinelHealth: vi.fn(),
    ...over,
  } as unknown as JarvisPageState
}

describe('SentinelHealth card', () => {
  it('shows an error, not the empty state, when the fetch failed', () => {
    render(<SentinelHealth page={page({ sentinelError: true })} />)
    expect(screen.getByRole('alert').textContent).toMatch(/Could not load SENTINEL health/)
    expect(screen.queryByText(/No health checks on record yet/)).toBeNull()
  })

  it('keeps the empty state when the store really has no checks', () => {
    render(<SentinelHealth page={page({ sentinelHealth: { latest: null, history: [], alerts: [], history_count: 0 } })} />)
    expect(screen.getByText(/No health checks on record yet/)).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
