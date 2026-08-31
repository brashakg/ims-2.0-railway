// @vitest-environment node
//
// Pure filesystem scan -- no DOM. Declaring the node environment skips this
// repo's jsdom setup for this file (measured ~42s of the ~53s a cold single-file
// run costs), so the guard is close to free in the frontend lane.
// Tripwire: no component may branch on the VIEWPORT WIDTH at mount without
// subscribing to resize.
//
// WHY THIS FILE EXISTS
// ====================
// The e2e layout probe (e2e/tests/layout.spec.ts + e2e/fixtures/layout.ts)
// checks every screen at every width in VIEWPORTS. Loading the page afresh for
// each width is correct but costs a full document load per measurement, which
// is the entire runtime of the lane; the probe therefore RESIZES an
// already-open page (and re-mounts the route in place) rather than reloading.
//
// That trade is only sound while the app's layout follows the viewport. CSS
// media queries do -- they re-evaluate on resize, for free. JavaScript that
// reads `window.innerWidth` once during render or in a mount effect does NOT:
// the component keeps whatever it decided at mount. Resize such a component
// past its threshold and the probe measures a layout THE USER NEVER SEES --
// in both directions. It can miss a real break (the phone branch never
// rendered, so nothing overlaps) and it can invent one (the phone branch is
// still on screen at 1440, so things overlap that never would).
//
// That is a silent failure: the probe stays green, the screen stays broken.
// This guard is what stops the assumption rotting. When it goes red, either
// the component gained reactivity (good, delete the entry) or a NEW
// unreactive gate appeared and the probe's reload/re-mount boundaries are now
// wrong.
//
// WHAT THIS SCANS FOR
// ===================
// Every non-test .ts/.tsx under frontend/src, comments stripped. A file is a
// HIT when it READS the viewport width:
//
//     window.innerWidth        window.outerWidth
//     documentElement.clientWidth   document.body.clientWidth
//     screen.width             window.matchMedia(     visualViewport
//
// and shows NO evidence of subscribing to width changes:
//
//     addEventListener('resize', ...)   .onresize
//     new ResizeObserver(...)           mql.addListener(...)
//     matchMedia(...).addEventListener(...)
//
// HOW TO FIX A NEW HIT (do not just add it to ALLOWED)
// ====================================================
// In order of preference:
//   1. Delete the JS gate. Most of these are expressible as a Tailwind
//      responsive class or a CSS media query, which costs nothing and is
//      correct on resize by construction. This is almost always the answer.
//   2. If the value must live in JS state, make it follow the viewport:
//        const [narrow, setNarrow] = useState(() => window.innerWidth < N);
//        useEffect(() => {
//          const on = () => setNarrow(window.innerWidth < N);
//          window.addEventListener('resize', on);
//          return () => window.removeEventListener('resize', on);
//        }, []);
//      A `matchMedia` list with a 'change' listener is equally fine.
//   3. Only if neither is possible, add a reasoned ALLOWED entry -- AND make
//      sure the layout probe reloads (or re-mounts) across that width, or the
//      probe is now lying about this screen.
//
// WHAT THIS GUARD HONESTLY CANNOT DO
// ==================================
// 1. It is FILE-granular, not component- or line-granular. A file where one
//    component listens for resize and a second one silently gates on width
//    reads as reactive. Splitting hairs here would need an AST walk and a
//    render/effect-path analysis; the file-level rule catches the shape that
//    actually occurs (a single component per file) and is ~40 lines.
//    ponytail: file-granular; go per-symbol only if a real miss shows up.
// 2. It cannot see a width gate that never names a width -- a prop threaded
//    down from a parent's mount-time measurement, or a `getBoundingClientRect`
//    on an element. Those are found by RUNNING the probe at two widths with
//    and without a reload, not by grepping.
// 3. Comment stripping is regex-based, so a `//` inside a template literal can
//    swallow the rest of that line. The failure direction is a MISSED read,
//    which is why the width-set assertion below re-checks the one file we know
//    about on every run: if the scanner ever stops seeing DisplayLayoutPanel,
//    `ALLOWED has not gone stale` goes red immediately.
import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';

const SRC = join(__dirname, '..');

/** Reading the viewport's width. */
const READS_WIDTH =
  /\binnerWidth\b|\bouterWidth\b|documentElement\.clientWidth|body\.clientWidth|\bscreen\.width\b|\bmatchMedia\s*\(|\bvisualViewport\b/;

/** Subscribing to width changes. Deliberately matches the CALL SHAPE, not the
 *  bare word "resize": a false positive here would be a false NEGATIVE for the
 *  guard, which is the dangerous direction. */
const SUBSCRIBES =
  /addEventListener\(\s*['"`]resize['"`]|\.onresize\b|new\s+ResizeObserver|\.addListener\s*\(|matchMedia\s*\([^)]*\)\s*\.\s*addEventListener/;

/** The thresholds a file compares the width against, either way round, plus
 *  any media-query breakpoint it builds in JS. */
const WIDTH_THRESHOLDS = [
  /(?:innerWidth|outerWidth|clientWidth|screen\.width)\s*[<>]=?\s*(\d+)/g,
  /(\d+)\s*[<>]=?\s*(?:window\.)?(?:innerWidth|outerWidth|clientWidth)/g,
  /(?:max|min)-width:\s*(\d+)px/g,
];

// ---------------------------------------------------------------------------
// THE ALLOW-LIST. Keyed by path relative to frontend/src. `widths` is every
// threshold the file gates on -- recorded, not just counted, so that adding a
// SECOND gate inside an already-blessed file still turns this red. The layout
// probe's reload boundaries must match this set exactly.
// ---------------------------------------------------------------------------
const ALLOWED: Record<string, { widths: number[]; reason: string }> = {
  'components/inventory/DisplayLayoutPanel.tsx': {
    widths: [1024],
    reason:
      'Opens the detail pane as a DRAWER below 1024 and as a side-by-side ' +
      'panel above it -- a structural difference, not a style one, so it is ' +
      'not expressible as a media query without duplicating the pane. The ' +
      'layout probe therefore treats 1024 as a boundary it must cross by ' +
      'loading/re-mounting rather than by resizing. If this width changes, ' +
      'the probe changes with it.',
  },
};

function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:"'`\\])\/\/[^\n]*/g, '$1 ');
}

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (!['__tests__', '__mocks__', 'node_modules'].includes(entry.name)) {
        sourceFiles(full, out);
      }
    } else if (/\.tsx?$/.test(entry.name) && !/\.(test|spec)\.tsx?$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

/** Files that read the viewport width and never subscribe to it changing. */
function scan(): Map<string, number[]> {
  const hits = new Map<string, number[]>();
  for (const file of sourceFiles(SRC)) {
    const code = stripComments(readFileSync(file, 'utf8'));
    if (!READS_WIDTH.test(code) || SUBSCRIBES.test(code)) continue;
    const widths = new Set<number>();
    for (const pattern of WIDTH_THRESHOLDS) {
      for (const m of code.matchAll(pattern)) widths.add(Number(m[1]));
    }
    hits.set(relative(SRC, file).replace(/\\/g, '/'), [...widths].sort((a, b) => a - b));
  }
  return hits;
}

// Scanned ONCE, at module load, not per test. Three separate walks of ~480
// files each raced the rest of the (fully parallel) frontend suite for IO and
// intermittently blew vitest's 5s per-test timeout on a loaded machine --
// caught by running the full suite three times, not once. Collection is not
// bounded by testTimeout, so doing the work here also takes the guard off that
// clock entirely.
const HITS = scan();

describe('mount-time viewport-width gates', () => {
  it('finds no unreactive width gate outside the reasoned allow-list', () => {
    const unexpected = [...HITS.keys()]
      .filter((f) => !(f in ALLOWED))
      .map((f) => `  ${f}`);

    expect(
      unexpected,
      'A component branches on window width at MOUNT and never listens for ' +
        'resize.\n\n' +
        'The e2e layout probe measures every screen at seven widths by ' +
        'RESIZING an open page instead of reloading it seven times -- that is ' +
        'the whole reason the gate is fast enough to block a merge. A gate ' +
        'that only runs at mount does not follow that resize, so the probe ' +
        'measures a screen the user never sees: it can miss a real break, and ' +
        'it can report one that does not exist.\n\n' +
        'Fix it, do not allow-list it: move the branch to a CSS media query ' +
        "or a Tailwind responsive class, or add a 'resize' listener that " +
        'updates the state. Only if neither is possible, add an ALLOWED entry ' +
        'in this file WITH its widths and a reason -- and make the layout ' +
        'probe reload across that width, or the probe is now lying.\n\n' +
        unexpected.join('\n'),
    ).toEqual([]);
  });

  it('sees the exact gate widths each allow-listed file records', () => {
    const drifted = Object.entries(ALLOWED)
      .filter(([file]) => HITS.has(file))
      .map(([file, entry]) => ({ file, recorded: entry.widths, actual: HITS.get(file)! }))
      .filter((d) => d.recorded.join(',') !== d.actual.join(','))
      .map((d) => `  ${d.file}: recorded [${d.recorded}] but source gates on [${d.actual}]`);

    expect(
      drifted,
      'An allow-listed file changed which widths it gates on. The layout ' +
        'probe reloads across exactly these widths, so it is now crossing the ' +
        'wrong boundary and measuring stale mounts. Update both this entry ' +
        "and the probe's gate list.\n\n" +
        drifted.join('\n'),
    ).toEqual([]);
  });

  it('has not gone stale: every ALLOWED entry still matches a live hit', () => {
    const stale = Object.keys(ALLOWED)
      .filter((f) => !HITS.has(f))
      .map((f) => `  ${f}`);

    // This is also the scanner's own canary. If a regex here ever stops
    // matching, every real hit disappears and this test -- not a silent green
    // run -- is what says so.
    expect(
      stale,
      'These ALLOWED entries no longer match any unreactive width gate. ' +
        'Either the file was fixed (delete the entry, and let the layout ' +
        'probe stop reloading across that width) or the SCANNER above is ' +
        'broken and is now finding nothing at all.\n\n' + stale.join('\n'),
    ).toEqual([]);
  });
});
