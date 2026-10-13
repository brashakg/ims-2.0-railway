// The Design Queue's Publish toast says in plain words what happened to the
// photo on the website -- never 'LIVE (noop)' or a raw Shopify id.
import { describe, it, expect } from 'vitest';
import { formatPhotoPublish } from '../OnlineStoreSyncBanner';
import type { PushResult } from '../../../services/api/onlineStore';

const label = 'Image "Aviator Gold"';
const live = (r: Partial<PushResult>): PushResult => ({
  mode: 'LIVE',
  entity: 'image',
  action: 'create',
  ok: true,
  shopify_id: 'gid://shopify/MediaImage/100',
  ...r,
});

describe('formatPhotoPublish', () => {
  it('says each LIVE outcome plainly, without the Shopify id', () => {
    expect(formatPhotoPublish(label, live({ action: 'create' }))).toBe(
      'Image "Aviator Gold": sent to the website.',
    );
    expect(formatPhotoPublish(label, live({ action: 'update' }))).toBe(
      'Image "Aviator Gold": the new photo is on the website and the old one was taken down.',
    );
    expect(formatPhotoPublish(label, live({ action: 'noop' }))).toBe(
      'Image "Aviator Gold": already on the website, so nothing was sent.',
    );
    expect(formatPhotoPublish(label, live({ action: 'dedupe' }))).toBe(
      'Image "Aviator Gold": two copies were on the website - IMS took the extra one down.',
    );
  });

  it('never says a photo Shopify is still processing is already on the website', () => {
    const msg = formatPhotoPublish(label, live({ action: 'processing' }));
    expect(msg).toBe('Image "Aviator Gold": sent to the website - Shopify is still processing it.');
    expect(msg).not.toContain('already');
  });

  it("shows the backend's own sentence when the press did not finish", () => {
    const error =
      'The new photo was sent, but IMS cannot see it finished on the website listing yet, ' +
      'so the old photo stays up. Press Publish again in a few minutes.';
    expect(formatPhotoPublish(label, live({ ok: false, action: 'update', error }))).toBe(
      `${label}: ${error}`,
    );
  });

  it('keeps the dry-run line for a SIMULATED press', () => {
    const msg = formatPhotoPublish(label, live({ mode: 'SIMULATED', shopify_id: null }));
    expect(msg).toContain('dry-run (SIMULATED)');
  });
});
