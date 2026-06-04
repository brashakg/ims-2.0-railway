import { describe, it, expect } from 'vitest';
import { categoryLabel, describeForReceipt } from '../receiptFormat';

describe('categoryLabel', () => {
  it('maps canonical categories to customer-friendly labels', () => {
    expect(categoryLabel('FRAMES')).toBe('Spectacle Frame');
    expect(categoryLabel('SUNGLASSES')).toBe('Sunglass');
    expect(categoryLabel('OPTICAL_LENS')).toBe('Spectacle Lens');
    expect(categoryLabel('CONTACT_LENS')).toBe('Contact Lens');
    expect(categoryLabel('WATCHES')).toBe('Watch');
    expect(categoryLabel('SERVICE')).toBe('Service');
  });

  it('normalizes casing and separators (dash / space / lowercase) before matching', () => {
    expect(categoryLabel('spectacle-frame')).toBe('Spectacle Frame');
    expect(categoryLabel('contact lenses')).toBe('Contact Lens');
    expect(categoryLabel('Smart_Watch')).toBe('Watch');
  });

  it('Title-cases an unknown category instead of leaking UPPER_CASE to the customer', () => {
    expect(categoryLabel('GIFT_CARD')).toBe('Gift Card');
  });

  it('returns "Item" for null / undefined / empty', () => {
    expect(categoryLabel(null)).toBe('Item');
    expect(categoryLabel(undefined)).toBe('Item');
    expect(categoryLabel('')).toBe('Item');
  });
});

describe('describeForReceipt', () => {
  it('produces "Brand Category" when a brand is present', () => {
    expect(
      describeForReceipt({ brand: 'Ray-Ban', category: 'SUNGLASSES', name: 'Wayfarer RB2140' }),
    ).toBe('Ray-Ban Sunglass');
    expect(
      describeForReceipt({ brand: 'Zeiss', category: 'OPTICAL_LENS', name: 'DuraVision' }),
    ).toBe('Zeiss Spectacle Lens');
  });

  it('falls back to subbrand + category when brand is missing', () => {
    expect(
      describeForReceipt({ brand: '', subbrand: 'Aqualite', category: 'FRAMES' }),
    ).toBe('Aqualite Spectacle Frame');
  });

  it('falls back to the product name when neither brand nor subbrand exists', () => {
    expect(
      describeForReceipt({ category: 'SERVICE', name: 'Frame fitting & adjustment' }),
    ).toBe('Frame fitting & adjustment');
  });

  it('falls back to just the category label when nothing else is available', () => {
    expect(describeForReceipt({ category: 'CONTACT_LENS' })).toBe('Contact Lens');
    // edge: completely empty item -> "Item"
    expect(describeForReceipt({})).toBe('Item');
  });

  it('trims whitespace around brand / subbrand / name', () => {
    expect(
      describeForReceipt({ brand: '  Gucci  ', category: 'SUNGLASSES' }),
    ).toBe('Gucci Sunglass');
  });
});
