import { describe, it, expect } from 'vitest';
import {
  calculateRFMScore,
  getSegmentSummary,
  getAllSegmentConfigs,
  type CustomerRFMData,
  type RFMScore,
} from '../rfmSegmentation';

function customer(overrides: Partial<CustomerRFMData>): CustomerRFMData {
  return {
    customerId: 'C1',
    customerName: 'Test',
    phone: '9810000000',
    lastPurchaseDate: '2026-01-01',
    totalOrders: 1,
    totalSpend: 1000,
    daysSinceLastPurchase: 30,
    ...overrides,
  };
}

describe('calculateRFMScore — component scores', () => {
  it('scores recency/frequency/monetary on the documented thresholds', () => {
    const s = calculateRFMScore(
      customer({ daysSinceLastPurchase: 30, totalOrders: 5, totalSpend: 60000 }),
    );
    expect(s.recency).toBe(5); // <=90 days
    expect(s.frequency).toBe(5); // 5+ orders
    expect(s.monetary).toBe(5); // >=50k
    expect(s.total).toBe(15);
  });

  it('uses the lower buckets at the boundaries', () => {
    const s = calculateRFMScore(
      customer({ daysSinceLastPurchase: 800, totalOrders: 1, totalSpend: 1000 }),
    );
    expect(s.recency).toBe(1); // >730 days
    expect(s.frequency).toBe(1); // 1 order
    expect(s.monetary).toBe(1); // <5000
    expect(s.total).toBe(3);
  });
});

describe('calculateRFMScore — segment classification', () => {
  it('classifies a recent, frequent, high-spend buyer as CHAMPION', () => {
    const s = calculateRFMScore(
      customer({ daysSinceLastPurchase: 10, totalOrders: 6, totalSpend: 80000 }),
    );
    expect(s.segment).toBe('CHAMPION');
    expect(s.label).toBe('Champion');
    expect(s.action).toMatch(/[Rr]eward/);
  });

  it('classifies a brand-new single-purchase customer as NEW_CUSTOMER', () => {
    const s = calculateRFMScore(
      customer({ daysSinceLastPurchase: 20, totalOrders: 1, totalSpend: 4000 }),
    );
    // r=5, f=1 -> NEW_CUSTOMER
    expect(s.segment).toBe('NEW_CUSTOMER');
  });

  it('classifies an inactive low-recency single buyer as LOST', () => {
    const s = calculateRFMScore(
      customer({ daysSinceLastPurchase: 900, totalOrders: 1, totalSpend: 2000 }),
    );
    // r=1, f=1 -> falls through to LOST
    expect(s.segment).toBe('LOST');
  });

  it('attaches display config (color + bgColor) for the chosen segment', () => {
    const s = calculateRFMScore(
      customer({ daysSinceLastPurchase: 10, totalOrders: 6, totalSpend: 80000 }),
    );
    expect(s.color).toMatch(/^text-/);
    expect(s.bgColor).toMatch(/^bg-/);
  });
});

describe('getSegmentSummary', () => {
  it('counts, percentages, and sorts segments by descending count', () => {
    const scores = [
      calculateRFMScore(customer({ daysSinceLastPurchase: 10, totalOrders: 6, totalSpend: 80000 })), // CHAMPION
      calculateRFMScore(customer({ daysSinceLastPurchase: 10, totalOrders: 6, totalSpend: 80000 })), // CHAMPION
      calculateRFMScore(customer({ daysSinceLastPurchase: 900, totalOrders: 1, totalSpend: 2000 })), // LOST
    ];
    const summary = getSegmentSummary(scores);
    expect(summary[0].segment).toBe('CHAMPION');
    expect(summary[0].count).toBe(2);
    expect(summary[0].percentage).toBe(67); // round(2/3*100)
    const lost = summary.find((x) => x.segment === 'LOST')!;
    expect(lost.count).toBe(1);
    expect(lost.percentage).toBe(33);
  });

  it('handles an empty list without dividing by zero', () => {
    expect(getSegmentSummary([] as RFMScore[])).toEqual([]);
  });
});

describe('getAllSegmentConfigs', () => {
  it('exposes a config for every known segment', () => {
    const cfg = getAllSegmentConfigs();
    expect(Object.keys(cfg)).toContain('CHAMPION');
    expect(Object.keys(cfg)).toContain('LOST');
    expect(cfg.CHAMPION.label).toBe('Champion');
  });
});
