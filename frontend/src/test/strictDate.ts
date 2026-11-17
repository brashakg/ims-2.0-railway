// A browser whose Date reads ONLY the format the JavaScript standard defines
// (YYYY-MM-DD, optionally THH:mm[:ss[.sss]] and Z or +-HH:mm). Chrome and Node
// also accept leftovers such as "2026-10-25Z"; Safari on iPhone and iPad does
// not, and this suite runs on Node. Run date code inside this to prove it never
// leans on the lenient parser.
import { vi } from 'vitest';

const STANDARD = /^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?)?$/;

export function withStrictDate<T>(run: () => T): T {
  const RealDate = Date;
  class StrictDate extends RealDate {
    constructor(...args: unknown[]) {
      const first = args[0];
      if (args.length === 1 && typeof first === 'string' && !STANDARD.test(first)) super(NaN);
      else super(...(args as []));
    }
  }
  vi.stubGlobal('Date', StrictDate);
  try {
    return run();
  } finally {
    vi.unstubAllGlobals();
  }
}
