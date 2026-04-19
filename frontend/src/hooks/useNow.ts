// App-level ticking clock for live countdowns (escalation timers, token waits).
// Single interval per mode so we don't spin up one per component.
//
// Usage:
//   const now = useNow();           // 1s tick — for fine counters like "escalates in 4m 12s"
//   const now = useNow(15_000);     // 15s tick — for coarse "last action 3m ago" style
//
// Returns a Date stamp. Derived state (overdue, remaining) should be computed from this,
// not stored — don't duplicate truth.

import { useEffect, useState } from 'react';

export function useNow(intervalMs: number = 1000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
