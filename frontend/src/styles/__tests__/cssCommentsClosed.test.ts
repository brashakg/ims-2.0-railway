// UI-02 (audit): at 1024x768 the catalogue's category filters measured ~20px
// tall and "All" ~17px wide -- bare text, no pill. Cause: a comment above
// .ims-chip said "bg-*/text-*/border-*", and the first "*/" ended the comment
// early. The rest of the sentence became the selector of the next rule, the
// browser dropped that invalid rule, and with it every .ims-chip padding,
// border and background app-wide (the :hover and --on rules survived, hence
// red text with no pill). A browser reads comments exactly like the regex
// below; anything it leaves holding "*/" is prose being parsed as CSS.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const src = join(__dirname, '..', '..');
const files = ['index.css', 'styles/mobile.css', 'pages/dashboard/HubPage.css'];
const live = (f: string) => readFileSync(join(src, f), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

describe('every CSS comment ends where its author meant', () => {
  it.each(files)('%s has no comment text left outside a comment', (f) => {
    const css = live(f);
    const at = css.indexOf('*/');
    expect(at, at < 0 ? '' : `stray "*/" near: ${css.slice(Math.max(0, at - 80), at + 20)}`).toBe(-1);
  });

  it('.ims-chip (the one filter pill) is a live rule of its own, with its padding', () => {
    const css = live('index.css');
    const m = css.match(/(^|})\s*\.ims-chip\s*\{([^}]*)\}/);
    expect(m).not.toBeNull();
    expect(m![2]).toMatch(/padding:\s*4px 12px/);
    expect(m![2]).toMatch(/border:\s*1px solid/);
  });
});
