# Landscape tablet coverage and New task width

The general page and popup layout probes omitted 1024x768. That size is now
in their shared viewport list. The first CI run measured 213 passing tests
and one real failure: the New task Create button ended at x=1056, beyond the
1024px screen. The saved trace/screenshot confirms the existing 1060px modal
was not shrinking inside its implicit grid track.

The owner explicitly approved a small width correction, with no field or
workflow changes. Explicit minmax(0, 1fr) columns on the overlay and modal let
the existing max-width clamp to the viewport. The desktop width, fields,
buttons, labels, priority rules and submission logic are unchanged. No POS
application source changed. Remove this popup's old known-broken entry so
all existing geometry assertions now protect it at every tested width.

An unrelated existing probe weakness surfaced during the category PR's CI:
cash-flow appeared clean while its screenshot still showed only Loading.
The route inventory now waits for its existing payables card before measuring;
no cash-flow screen or existing cash-flow exemption is changed.

Verification:

- Playwright discovers 214 layout-project tests in four files. Page and popup
  tests iterate the shared viewport list, without adding stateful runs.
- An isolated browser probe rendered the actual NewTaskModal component with
  no network access and the existing compiled CSS. It reproduced the old
  1060px width on small screens. With the two proposed column declarations,
  the existing auditLayout function found zero violations across all eight
  viewports. At 1024, the modal ends at 1004 and Create button at 980; desktop
  geometry at 1180/1440 is unchanged. This is bounded component evidence, not
  a substitute for the required seeded full-application CI run.
- Forced TypeScript checking and Vite production build pass (2,997 modules,
  existing unrelated CSS-comment warning). Fresh full browser CI is required
  before merge. The original failed head is b1147547; its run is evidence for
  the defect, not successful validation of the correction.
- Browser CI runs only against its isolated seeded stack. No production
  transaction, live data write or workflow action was performed.

External evidence: fixes-2026-10-18/tablet-ci-failure.log,
tablet-traces/layout/popup-layout-popup-tasks-new-task-layout/, and
task-modal-width-probe.json. Root owns exact-head review and CLEAN/CI gates.

Follow-up from browser run `39105516986` at `6ffbcf7e`: 213 layout tests
passed, but New task still overflowed its 320px inner width by 4px on the
360px phone. The original isolated probe blocked font requests and used
fallback fonts; the full application loaded Inter and JetBrains Mono.

The remaining fix is `flex-wrap: wrap` on the existing `.nt-foot`. Its
unchanged shortcut hints, Cancel and Create buttons can wrap when they do
not fit. No field, label, action, workflow, breakpoint or test exemption was
added. Desktop placement stays unchanged.

The follow-up offline probe embeds the exact font resources saved in the CI
trace, waits for fonts to load, and renders the real component. It reproduces
the same 324px/320px overflow, then verifies 320px/320px after the footer
correction. The existing `auditLayout` reports zero violations at all eight
sizes; 1180/1440 desktop geometry is identical. Before/after 360px screenshots
were visually checked. Forced TypeScript and Vite production build pass
(2,997 modules; existing unrelated CSS-comment warning). The isolated backend
import smoke passes with 1,314 routes. Fresh full browser CI is still required
before merge.

External follow-up evidence: `tablet-ci-39105516986-failure.log`,
`tablet-ci-39105516986-traces/`, `check-task-modal-footer.cjs`,
`task-modal-footer-probe.json`, and `task-modal-footer-{before,after}-360.png`.
The coordinator independently reviews this one-declaration follow-up because
the prior independent reviewer implemented it.
