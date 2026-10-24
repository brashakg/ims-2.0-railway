import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { InventorySection } from '../InventorySection';
import type { QuickAddForm } from '../useQuickAddForm';

describe('photo count wording', () => {
  it.each([0, 3])('says how many photos were added (%i), separately from the website requirement', (count) => {
    const form = {
      images: Array.from({ length: count }, (_, i) => `https://example.com/${i}.jpg`),
      openSections: { inventory: true }, sectionIssues: { inventory: 0 },
      imageInputRef: { current: null }, editingImages: new Set(), toggleSection: vi.fn(),
    } as unknown as QuickAddForm;
    render(<InventorySection form={form} />);
    expect(screen.getByText(`Photos: ${count} added (website needs at least 1 usable photo)`)).toBeInTheDocument();
    expect(screen.getByText('Upload Images')).toBeInTheDocument();
  });
});
