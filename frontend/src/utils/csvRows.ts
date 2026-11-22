// RFC 4180 CSV reading for pasted / uploaded sheets (audit L3-26): quoted
// cells keep their commas, doubled quotes ("") are one quote, and a quoted
// cell may span lines. Splitting on every comma cut "AUDIT,CSV-QUOTED" in two
// and shifted every column after it.

/** Rows of trimmed cells; blank lines dropped. */
export function csvRows(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  const s = text.replace(/^\uFEFF/, '');
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (quoted) {
      if (ch === '"' && s[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') { row.push(cell.trim()); cell = ''; }
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && s[i + 1] === '\n') i++;
      row.push(cell.trim()); cell = '';
      if (row.some((c) => c !== '')) rows.push(row);
      row = [];
    } else cell += ch;
  }
  row.push(cell.trim());
  if (row.some((c) => c !== '')) rows.push(row);
  return rows;
}

/** A money cell as a number: '1,250.50', 'Rs 1250', '₹ 18' and '(18)' read;
 *  a blank cell is 0 (a nil cell, as a GSTR-2B sheet leaves it); anything
 *  else is null -- an unreadable amount is never a silent 0. */
export function csvMoney(cell: string | undefined): number | null {
  let v = (cell ?? '').replace(/₹|rs\.?|inr|,|\s/gi, '');
  if (v === '') return 0;
  const neg = /^\(.*\)$/.test(v);
  if (neg) v = v.slice(1, -1);
  if (!/^-?\d+(\.\d+)?$/.test(v)) return null;
  const n = Number(v);
  return neg ? -n : n;
}
