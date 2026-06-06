import { describe, it, expect } from 'vitest';
import { neutralizeFormula } from '../exportUtils';

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
