// F8: a unit code TYPED at the till must be looked up as a barcode, not sent to
// product search (which never matches a unit code). That includes labels printed
// before the 2026-09-28 format change ('BV--91FA3858'), which carry hyphens.

import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import { BarcodeScanner } from '../BarcodeScanner';

function typeAndEnter(text: string) {
  const onScan = vi.fn();
  const onManualSearch = vi.fn();
  render(<BarcodeScanner onScan={onScan} onManualSearch={onManualSearch} autoFocus={false} />);
  const input = screen.getByRole('textbox');
  fireEvent.change(input, { target: { value: text } });
  fireEvent.keyDown(input, { key: 'Enter' });
  return { onScan, onManualSearch };
}

describe('BarcodeScanner: typed unit codes are scans', () => {
  it.each(['BV0000000001', 'bv0000000001', 'BV--91FA3858', 'bv--91fa3858', 'BC-0A1B2C3D4E5F'])(
    '%s goes to the barcode lookup',
    (code) => {
      const { onScan, onManualSearch } = typeAndEnter(code);
      expect(onScan).toHaveBeenCalledWith(code);
      expect(onManualSearch).not.toHaveBeenCalled();
    },
  );

  it('a product name still goes to product search', () => {
    const { onScan, onManualSearch } = typeAndEnter('ray ban aviator');
    expect(onManualSearch).toHaveBeenCalledWith('ray ban aviator');
    expect(onScan).not.toHaveBeenCalled();
  });
});
