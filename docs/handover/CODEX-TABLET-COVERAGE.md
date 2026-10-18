# Landscape tablet coverage

The shared geometry-test viewport list omitted 1024x768 even though the
photo-badge-specific test covered it. Add that exact size to the shared list
consumed by both the general page and popup layout probes. All previous sizes,
assertions and recorded exceptions remain unchanged.

No application, POS, style or staff workflow source changes. The geometry
tests open pages and non-destructive popups against a seeded local CI stack.
They are never run against production.

Local verification: Playwright discovers 214 layout-project tests in four
files. General page and popup tests iterate the shared viewport list inside
each test, so this extends their measurement coverage without multiplying
unrelated stateful tests. Full browser geometry execution requires the seeded
stack and will run in required CI; discovery alone is not layout success.

The frontend source tree is d476e3596fee11672e9ac675cb54020a4edb0991, identical
to the category-guard checkout's successful forced TypeScript/Vite build in this
session. No new broad test exclusions were added. If the new size exposes a
layout defect, report and review its evidence before changing any screen or
test expectation.
