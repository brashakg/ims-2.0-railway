// L1-F10 (audit): the Jarvis hero asked for a white title on an ink panel, but
// the light theme's global `main h1 { color: #111827 !important }` won, so the
// title was near-black on near-black; and "Deploy agent" had no handler.
// jsdom does not load index.css, so this pins the pairing the hero asks for
// (dark text on a light surface -- what the global rule also enforces) and
// that its action button does something.

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { JarvisHero } from '../JarvisHero';
import type { JarvisPageState } from '../useJarvisPage';

const page = {
  enabledCount: 6, totalActs24h: 12, awaitingApproval: 1,
  loadInsights: vi.fn(), loadRecommendations: vi.fn(), loadAgents: vi.fn(), loadActivity: vi.fn(), loadProposals: vi.fn(),
} as unknown as JarvisPageState;

describe('Jarvis hero (L1-F10)', () => {
  it('asks for dark text on a light surface, never white on ink', () => {
    const { container } = render(<JarvisHero page={page} />);
    const section = container.querySelector('section')!;
    const h1 = screen.getByRole('heading', { level: 1 });
    expect(section.style.background).toBe('var(--surface)');
    expect(h1.style.color).toBe('var(--ink)');
    expect(container.innerHTML).not.toMatch(/#fff\b|rgb\(255, 255, 255\)/i);
  });

  it('"Manage agents" takes the superadmin to the agent switches', () => {
    const grid = document.createElement('div');
    grid.id = 'jarvis-agents';
    grid.scrollIntoView = vi.fn();
    document.body.appendChild(grid);
    try {
      render(<JarvisHero page={page} />);
      expect(screen.queryByRole('button', { name: /Deploy agent/ })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: /Manage agents/ }));
      expect(grid.scrollIntoView).toHaveBeenCalledTimes(1);
    } finally {
      grid.remove();
    }
  });
});
