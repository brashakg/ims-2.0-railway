// Review round 7: a typed-in product the server could not write was dropped
// from the order (or left for checking) while the form only said "saved".
import { describe, it, expect } from 'vitest';
import { typedInNotAddedMessage } from '../typedInNotAdded';

describe('typedInNotAddedMessage', () => {
  it('says nothing when every typed-in line was added', () => {
    expect(typedInNotAddedMessage(undefined)).toBeNull();
    expect(typedInNotAddedMessage([])).toBeNull();
  });

  it('names each line and why', () => {
    expect(
      typedInNotAddedMessage([
        {
          product_id: 'x',
          product_name: 'Vogue VO5286 Eyeglasses - W44',
          reason: 'could not be added to the catalogue and was taken off this order - add it again',
        },
        { product_id: 'y', product_name: null, reason: 'the order changed - check it' },
      ]),
    ).toBe(
      'Vogue VO5286 Eyeglasses - W44 could not be added to the catalogue and was taken off ' +
        'this order - add it again. A typed-in item the order changed - check it.',
    );
  });
});
