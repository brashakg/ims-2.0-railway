import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'

function Greet({ name }: { name: string }) {
  return <button type="button">Hi {name}</button>
}

describe('vitest + RTL + jsdom are wired up', () => {
  it('renders a component and queries the DOM by role', () => {
    render(<Greet name="QA" />)
    expect(screen.getByRole('button', { name: 'Hi QA' })).toBeInTheDocument()
  })

  it('runs plain assertions', () => {
    expect(1 + 1).toBe(2)
  })
})
