// ============================================================================
// IMS 2.0 - Date/time formatting (India Standard Time)
// ============================================================================
// The backend emits NAIVE timestamps (e.g. "2026-05-22T16:28:24.836000") that
// are actually UTC (Railway runs in UTC) but carry no timezone designator. A
// plain `new Date(s)` then parses them as the browser's LOCAL time, so order
// times etc. show ~5.5h off. These helpers (1) interpret naive timestamps as
// UTC and (2) format in Asia/Kolkata so the app always shows correct IST,
// regardless of the viewer's timezone.

export const IST_TZ = 'Asia/Kolkata';

/** Parse a backend timestamp into a correct Date.
 *  - Date / number: returned as-is.
 *  - String WITH a tz designator (Z or +/-HH:MM): parsed as-is.
 *  - Naive string (no tz): treated as UTC (the backend's wall clock). */
export function toDate(value: string | number | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === '') return null;
  if (value instanceof Date) return isNaN(value.getTime()) ? null : value;
  if (typeof value === 'number') {
    const d = new Date(value);
    return isNaN(d.getTime()) ? null : d;
  }
  let s = String(value).trim();
  if (!s) return null;
  // Already has timezone info? (ends with Z, or +HH:MM / -HH:MM / +HHMM)
  const hasTz = /[zZ]$/.test(s) || /[+-]\d{2}:?\d{2}$/.test(s);
  if (!hasTz) {
    // Normalize "YYYY-MM-DD HH:MM:SS" -> ISO, then mark as UTC.
    s = s.replace(' ', 'T') + 'Z';
  }
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

function fmt(value: Parameters<typeof toDate>[0], options: Intl.DateTimeFormatOptions, fallback = '—'): string {
  const d = toDate(value);
  if (!d) return fallback;
  try {
    return new Intl.DateTimeFormat('en-IN', { timeZone: IST_TZ, ...options }).format(d);
  } catch {
    return fallback;
  }
}

/** Date + time in IST, e.g. "22 May 2026, 9:58 pm". */
export function formatDateTimeIST(value: Parameters<typeof toDate>[0], fallback = '—'): string {
  return fmt(value, { day: '2-digit', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true }, fallback);
}

/** Date only in IST, e.g. "22 May 2026". */
export function formatDateIST(value: Parameters<typeof toDate>[0], fallback = '—'): string {
  return fmt(value, { day: '2-digit', month: 'short', year: 'numeric' }, fallback);
}

/** Time only in IST, e.g. "9:58 pm". */
export function formatTimeIST(value: Parameters<typeof toDate>[0], fallback = '—'): string {
  return fmt(value, { hour: 'numeric', minute: '2-digit', hour12: true }, fallback);
}

/** IST calendar date of a backend timestamp as "YYYY-MM-DD", or null if
 *  unparseable. Used by day-scoped reports (e.g. Day-End close) to pin rows
 *  to the exact IST business day, belt-and-braces on top of the server-side
 *  from_date/to_date window. */
export function istDayString(value: Parameters<typeof toDate>[0]): string | null {
  const d = toDate(value);
  if (!d) return null;
  try {
    // en-CA formats as YYYY-MM-DD.
    return new Intl.DateTimeFormat('en-CA', {
      timeZone: IST_TZ, year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(d);
  } catch {
    return null;
  }
}
