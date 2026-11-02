import { describe, it, expect } from 'vitest';
import { neutralizeFormula, toCSV } from '../exportUtils';

describe('exportUtils - neutralizeFormula (BUG-139 CSV injection fix)', () => {
  describe('formula injection character detection and neutralization', () => {
    it('should prefix strings starting with = with a single quote', () => {
      const dangerous = '=HYPERLINK("http://evil.com","click")';
      expect(neutralizeFormula(dangerous)).toBe("'=HYPERLINK(\"http://evil.com\",\"click\")");
    });

    it('should prefix strings starting with + with a single quote', () => {
      const dangerous = '+2+5+cmd';
      expect(neutralizeFormula(dangerous)).toBe("'+2+5+cmd");
    });

    it('should prefix strings starting with - with a single quote', () => {
      const dangerous = '-2+5+cmd';
      expect(neutralizeFormula(dangerous)).toBe("'-2+5+cmd");
    });

    it('should prefix strings starting with @ with a single quote', () => {
      const dangerous = '@SUM(A1:A10)';
      expect(neutralizeFormula(dangerous)).toBe("'@SUM(A1:A10)");
    });

    it('should prefix strings starting with tab with a single quote', () => {
      const dangerous = '\t=cmd';
      expect(neutralizeFormula(dangerous)).toBe("'\t=cmd");
    });

    it('should prefix strings starting with carriage return with a single quote', () => {
      const dangerous = '\r=cmd';
      expect(neutralizeFormula(dangerous)).toBe("'\r=cmd");
    });

    it('should NOT modify regular customer names', () => {
      expect(neutralizeFormula('John Doe')).toBe('John Doe');
      expect(neutralizeFormula('BASANT KESHRI')).toBe('BASANT KESHRI');
      expect(neutralizeFormula('Test Employee')).toBe('Test Employee');
    });

    it('should NOT modify strings with dangerous chars NOT at the start', () => {
      expect(neutralizeFormula('Test-Product')).toBe('Test-Product');
      expect(neutralizeFormula('Value: -500')).toBe('Value: -500');
      expect(neutralizeFormula('Email@example.com')).toBe('Email@example.com');
    });

    it('should handle empty string gracefully', () => {
      expect(neutralizeFormula('')).toBe('');
    });

    it('should handle single character dangerous strings', () => {
      expect(neutralizeFormula('=')).toBe("'=");
      expect(neutralizeFormula('+')).toBe("'+");
      expect(neutralizeFormula('-')).toBe("'-");
      expect(neutralizeFormula('@')).toBe("'@");
    });
  });
});

describe('exportUtils - toCSV numbers', () => {
  it('writes a number bare, a negative one too (summable, no stray apostrophe)', () => {
    expect(toCSV([{ v: 'Advance Co', owed: -1500 }, { v: '-x', owed: 12.5 }])).toBe(
      ['"v","owed"', '"Advance Co",-1500', `"'-x",12.5`].join('\n'),
    );
  });
});

describe('exportUtils - downloadCSV names the file on the IST day (FE-8)', () => {
  async function nameAt(iso: string): Promise<string> {
    const { vi } = await import('vitest');
    const { downloadCSV } = await import('../exportUtils');
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(iso));
    let name = '';
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      name = this.download;
    });
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
    try {
      downloadCSV('a,b', 'sales');
    } finally {
      click.mockRestore();
      vi.useRealTimers();
    }
    return name;
  }

  it('night: 01:30 IST on 1 Jan is dated 1 Jan', async () => {
    expect(await nameAt('2026-12-31T20:00:00Z')).toBe('sales_2027-01-01.csv');
  });

  it('day control: dated 31 Dec', async () => {
    expect(await nameAt('2026-12-31T09:00:00Z')).toBe('sales_2026-12-31.csv');
  });
});
