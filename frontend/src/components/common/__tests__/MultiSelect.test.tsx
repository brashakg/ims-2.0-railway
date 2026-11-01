// MultiSelect: the ONE tick-box multi-select every Catalog option filter uses
// (owner 2026-11-01). Its value is a comma-separated string.

import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MultiSelect, type MultiOption } from '../MultiSelect';

const PEOPLE: MultiOption[] = [
  { value: 'u-mansi', label: 'Mansi Kumari', count: 319 },
  { value: 'u-durga', label: 'Durga Devi', count: 41 },
  { value: 'u-ak', label: 'Akshay Ovhal', count: 2 },
];

function Harness({ initial = '', options = PEOPLE }: { initial?: string; options?: MultiOption[] }) {
  const [v, setV] = useState(initial);
  return (
    <>
      <MultiSelect title="Added by" anyLabel="Anyone" value={v} options={options} onChange={setV} />
      <output data-testid="value">{v}</output>
    </>
  );
}

const value = () => screen.getByTestId('value').textContent;
const box = (name: string) => screen.getByRole('checkbox', { name }) as HTMLInputElement;

describe('MultiSelect', () => {
  it('ticks several, unticks one, and Clear empties it; the button says what is ticked', () => {
    render(<Harness />);
    const button = screen.getByRole('button', { name: 'Added by' });
    expect(button.textContent).toBe('Anyone');
    fireEvent.click(button);
    expect(button.getAttribute('aria-expanded')).toBe('true');
    fireEvent.click(box('Mansi Kumari (319)'));
    expect(value()).toBe('u-mansi');
    expect(button.textContent).toBe('Mansi Kumari');
    fireEvent.click(box('Durga Devi (41)'));
    expect(value()).toBe('u-mansi,u-durga');
    expect(button.textContent).toBe('2 selected');
    fireEvent.click(box('Mansi Kumari (319)'));
    expect(value()).toBe('u-durga');
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(value()).toBe('');
    expect(box('Durga Devi (41)').checked).toBe(false);
  });

  it('a ticked value the options no longer list (a shared link) stays and can be unticked', () => {
    render(<Harness initial="u-gone,u-ak" />);
    fireEvent.click(screen.getByRole('button', { name: 'Added by' }));
    expect(box('u-gone').checked).toBe(true);
    expect(box('Akshay Ovhal (2)').checked).toBe(true);
    fireEvent.click(box('u-gone'));
    expect(value()).toBe('u-ak');
  });

  it('a long list gets a search box; a short one does not', () => {
    const many = Array.from({ length: 12 }, (_, i) => ({ value: `c${i}`, label: `Colour ${i}` }));
    const { unmount } = render(<Harness options={many} />);
    fireEvent.click(screen.getByRole('button', { name: 'Added by' }));
    fireEvent.change(screen.getByLabelText('Search Added by'), { target: { value: 'colour 1' } });
    expect(
      screen.getAllByRole('checkbox').map((c) => (c as HTMLInputElement).labels?.[0].textContent)
    ).toEqual(['Colour 1', 'Colour 10', 'Colour 11']);
    unmount();
    render(<Harness />);
    expect(screen.queryByLabelText('Search Added by')).toBeNull();
  });

  it('Escape and Done close it and hand focus back to the button', () => {
    render(<Harness />);
    const button = screen.getByRole('button', { name: 'Added by' });
    fireEvent.click(button);
    box('Durga Devi (41)').focus();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(button);
    fireEvent.click(button);
    const done = screen.getByRole('button', { name: 'Done' });
    done.focus();
    fireEvent.click(done);
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(document.activeElement).toBe(button);
  });

  it('a click elsewhere closes it; a click inside does not', () => {
    render(<Harness />);
    const button = screen.getByRole('button', { name: 'Added by' });
    fireEvent.click(button);
    fireEvent.mouseDown(box('Durga Devi (41)'));
    expect(button.getAttribute('aria-expanded')).toBe('true');
    fireEvent.mouseDown(document.body);
    expect(button.getAttribute('aria-expanded')).toBe('false');
  });

  it('tabbing out closes it, so two panels never stack', () => {
    render(
      <>
        <Harness />
        <button type="button">Next</button>
      </>
    );
    const button = screen.getByRole('button', { name: 'Added by' });
    fireEvent.click(button);
    fireEvent.blur(box('Durga Devi (41)'), { relatedTarget: box('Mansi Kumari (319)') });
    expect(button.getAttribute('aria-expanded')).toBe('true');
    fireEvent.blur(box('Durga Devi (41)'), { relatedTarget: screen.getByRole('button', { name: 'Next' }) });
    expect(button.getAttribute('aria-expanded')).toBe('false');
  });

  it('near the right edge of a phone the panel opens leftwards, so the page never scrolls sideways', () => {
    const was = window.innerWidth;
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 375 });
    try {
      render(<Harness />);
      const button = screen.getByRole('button', { name: 'Added by' });
      const panel = document.querySelector('[aria-label="Added by options"]') as HTMLElement;
      const at = (left: number) =>
        vi.spyOn(button, 'getBoundingClientRect').mockReturnValue({ left, right: left + 160 } as DOMRect);
      at(184);
      fireEvent.click(button);
      expect(panel.className.split(' ')).toContain('right-0');
      expect(panel.className.split(' ')).not.toContain('left-0');
      fireEvent.click(button);
      at(16);
      fireEvent.click(button);
      expect(panel.className.split(' ')).toContain('left-0');
    } finally {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: was });
    }
  });
});
