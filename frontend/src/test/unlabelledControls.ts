// The audit's settings census (lane1 settings-tablet-census.json) as a test
// helper: every visible input / select / textarea must be tied to a label
// (wrapping <label>, htmlFor/id) or carry an aria-label / aria-labelledby.
// Returns a short description of each control that is not.

export function unlabelledControls(root: ParentNode = document.body): string[] {
  const out: string[] = [];
  root.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>('input, select, textarea').forEach((el) => {
    if (el instanceof HTMLInputElement && (el.type === 'hidden' || el.type === 'file')) return;
    const named =
      (el.labels && el.labels.length > 0) ||
      !!el.getAttribute('aria-label')?.trim() ||
      !!el.getAttribute('aria-labelledby')?.trim();
    if (!named) {
      out.push(`${el.tagName.toLowerCase()}[type=${el.getAttribute('type') || ''}][placeholder=${el.getAttribute('placeholder') || ''}]`);
    }
  });
  return out;
}

/** Icon-only buttons with no accessible name (no text, aria-label or title). */
export function unnamedButtons(root: ParentNode = document.body): number {
  let n = 0;
  root.querySelectorAll('button').forEach((b) => {
    const named = !!b.textContent?.trim() || !!b.getAttribute('aria-label')?.trim() || !!b.getAttribute('title')?.trim();
    if (!named) n += 1;
  });
  return n;
}
