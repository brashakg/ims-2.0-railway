// `npm run lint:security` (R33): a small, blocking SAST pass using only ESLint
// core rules, so no extra dependency. Any hit fails the CI security job.
// Run with --no-inline-config: an eslint-disable comment cannot silence it;
// a reviewed exception goes in the file list at the bottom.
import globals from 'globals'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist', 'coverage', 'node_modules', 'src/**/__tests__/**', 'src/**/*.test.{ts,tsx}', 'src/test/**']),
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: { parser: tseslint.parser, globals: globals.browser },
    rules: {
      'no-eval': 'error',
      'no-implied-eval': 'error',
      'no-new-func': 'error',
      'no-script-url': 'error',
      'no-restricted-syntax': ['error',
        { selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']", message: 'Raw HTML injection: escape input and add the file to the reviewed list in eslint.security.config.js.' },
        { selector: "AssignmentExpression[left.property.name=/^(innerHTML|outerHTML)$/]", message: 'Raw HTML assignment.' },
        { selector: "CallExpression[callee.property.name='insertAdjacentHTML']", message: 'Raw HTML insertion.' },
        { selector: "CallExpression[callee.object.name='document'][callee.property.name=/^write(ln)?$/]", message: 'document.write.' },
      ],
    },
  },
  {
    // Reviewed: both HTML-escape the whole string before adding <strong>/<em>/<code>/<br>.
    files: ['src/pages/jarvis/JarvisChatPane.tsx', 'src/pages/reports/GrowthBlueprintPage.tsx'],
    rules: { 'no-restricted-syntax': 'off' },
  },
])
