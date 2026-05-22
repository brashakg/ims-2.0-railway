// Best-effort install of the repo's pre-commit git hooks. Runs as the npm
// `prepare` lifecycle step (i.e. on every `npm install`).
//
// MUST be cross-platform and MUST NEVER fail `npm install`. The previous
// `prepare` was a bash one-liner (`if command -v pre-commit ...`) which crashes
// on Windows cmd ("'-v' was unexpected at this time") and aborts the install.
// This node shim works on any platform and swallows all errors.
const { execSync } = require('child_process');
const path = require('path');

try {
  // .pre-commit-config.yaml lives at the repo root (one level above frontend/).
  execSync('pre-commit install', {
    cwd: path.resolve(__dirname, '..', '..'),
    stdio: 'ignore',
  });
} catch {
  // pre-commit isn't installed (or isn't available on this platform). That's
  // fine — hook installation is optional; skip silently so `npm install`
  // always succeeds.
}
