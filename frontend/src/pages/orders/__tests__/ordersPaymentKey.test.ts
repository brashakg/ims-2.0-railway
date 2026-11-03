// Money review 12 of #1264: the Orders screen's "Collect payment" sends ONE
// Idempotency-Key per open payment modal, so a re-press after a lost answer
// replays the same payment (the server dedupes it) instead of recording it
// twice. A SOURCE guard: the screen has no render harness yet.
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect } from 'vitest';

const HERE = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(HERE, '..', 'OrdersPage.tsx'), 'utf8');

describe('Orders screen payment key', () => {
  it('mints the key when the modal opens and sends it with the payment', () => {
    const open = src.slice(src.indexOf('const openPaymentModal'), src.indexOf('const handlePayment'));
    expect(open).toMatch(/paymentKeyRef\.current\s*=/);
    const pay = src.slice(src.indexOf('const handlePayment'), src.indexOf('const openDeliverModal'));
    expect(pay).toMatch(/orderApi\.addPayment\([\s\S]*?paymentKeyRef\.current/);
    expect(pay).not.toMatch(/paymentKeyRef\.current\s*=/); // a re-press never mints a new one
  });
});
