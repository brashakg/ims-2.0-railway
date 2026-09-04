// The review queue moved from /catalog?segment=review to /catalog/review.
// Links already in the wild (bookmarks, the old "Back to queue") must still
// land the reviewer on the SAME page + item, so /catalog forwards them.
import { describe, it, expect } from 'vitest';
import { legacyReviewRedirect } from '../reviewQueue';

describe('legacyReviewRedirect', () => {
  it('leaves a plain /catalog visit alone', () => {
    expect(legacyReviewRedirect('')).toBeNull();
    expect(legacyReviewRedirect('?focus=P1')).toBeNull();
    expect(legacyReviewRedirect('?segment=catalog')).toBeNull();
  });

  it('forwards the bare legacy address', () => {
    expect(legacyReviewRedirect('?segment=review')).toBe('/catalog/review');
  });

  it('keeps page and focus so the reviewer lands where they left', () => {
    expect(legacyReviewRedirect('?segment=review&page=3&focus=abc%20123')).toBe(
      '/catalog/review?page=3&focus=abc+123',
    );
  });

  it('keeps any other filter it was carrying', () => {
    const to = legacyReviewRedirect('?segment=review&brand=Ray-Ban&page=2');
    expect(to?.startsWith('/catalog/review?')).toBe(true);
    const sp = new URLSearchParams(to!.split('?')[1]);
    expect(sp.get('brand')).toBe('Ray-Ban');
    expect(sp.get('page')).toBe('2');
    expect(sp.get('segment')).toBeNull();
  });
});
