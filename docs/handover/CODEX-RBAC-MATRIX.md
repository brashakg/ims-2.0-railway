# RBAC matrix parity handover

## Scope and result

Based on main `10c6c645ebe8354958da2e1e64d91ee2c36a9791`.
The access matrix was manually maintained and no longer represented the policy
used by the middleware. The document now generates all 1,305 method/path rows
from `api.services.rbac_policy.POLICY`: 45 PUBLIC, 393 AUTHENTICATED and 867
role-list entries, including 237 store-scoped and 47 self-enforced entries.

This change affects documentation, its generator and tests only. It does not
change role policy, authentication, shop permissions, routes, frontend behavior,
POS or any live data. No business decision or staff workflow changes are needed.

## Maintenance and safeguards

- Run `python scripts/generate_rbac_matrix.py` after a policy change, or add
  `--check` to fail on drift without writing. Importing the generator reads the
  policy only, without starting the API or connecting to a database.
- The generator replaces only the marked block. Business explanations for
  Accounts approval access, receiving roles and the self-only incentive endpoint
  remain outside it. Original foundation-survey notes are retained separately
  and explicitly labeled historical, rather than current permission claims.
- The introduction now describes the existing enforcing middleware and explains
  PUBLIC/AUTH, implicit SUPERADMIN access, scope metadata, self-enforced routes
  and additional handler/override checks. The table describes the registry, not
  an individual user's effective access to every resource.
- A test parses the Markdown independently and compares the complete key set,
  roles and both flags to POLICY. Mutation controls cover all five fields and
  duplicate rows. Other controls verify totals, stable generation, preservation
  of manual content, read-only checks and refusal of malformed section markers.
- Existing route-coverage tests separately compare policy keys with mounted API
  routes. Both tests run in the ordinary backend suite; no extra CI job is needed.

## Validation

All Python checks used the shared root virtual environment through the isolated
`fixes-2026-10-18/run_checks.py` runner: test environment, test-only JWT key,
unavailable localhost MongoDB port 27099 with short timeouts, dark dispatch and
Shopify writes disabled. No live service was used.

| Check | Result |
|---|---|
| New whole-document check against original matrix | Failed: legacy prose in a scope-flag cell was invalid; original table drift was also established in source comparison |
| `test_rbac_matrix_document.py` | 13 passed |
| Related policy/access/settings suites | 1,048 passed |
| Generator `--check` | Current, 1,305 rows |
| CI-pinned Pylint 3.0.3, errors/fatal on generator | Passed |
| Backend application import smoke | Passed, 1,314 routes |
| `git diff --check` | Passed |
| Frontend forced TypeScript/Vite build | Reused the successful PR1211 build for the exact unchanged frontend Git tree `d476e3596fee11672e9ac675cb54020a4edb0991` |

Related suites: `test_rbac_policy.py`, `test_rbac_access_matrix.py`,
`test_supplier_money_accounts_only.py`, `test_counter_roles_no_purchase_reads.py`
and `test_settings_one_rule_credit_default.py`. The existing settings document
test now compares role membership rather than a handwritten display order.

Local evidence is in `fixes-2026-10-18/rbac-matrix-{red,focused,related,check,pinned-lint,smoke}.log`.
The identical frontend tree is shared with commit
`304401bc82c932d25c3f8c5fdda325f78e7d402e`; its successful forced typecheck/build
is recorded in `task-reassignment-frontend.log`. Existing test warnings and the
existing CSS-comment build warning are unrelated to this documentation change.

## Review limits

This prevents registry/document drift; it does not establish that every policy
entry is the desired business rule, or replace handler-level access tests.
Historical concerns are preserved as history, not revalidated or silently fixed.
The draft PR requires independent review and current CI before merging. No merge
or deployment was performed for this handover.
