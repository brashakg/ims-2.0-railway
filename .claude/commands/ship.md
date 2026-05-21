---
description: Verify (pylint + tsc + related tests), then commit, push, and open a PR
argument-hint: [short description of the change]
---
Ship the current working changes as a small, reviewed PR. Do NOT merge unless I explicitly say so.

Steps:

1. Run `git status` and `git diff` to see exactly what changed.

2. Verify locally — only the checks relevant to the changed files:
   - If any `backend/` Python changed: from `backend/`, run
     `../.venv/Scripts/python.exe -m pylint api/ --disable=all --enable=E,F --extension-pkg-allow-list=pydantic --disable=no-name-in-module,no-member,import-error`
     It MUST report 10.00/10. Also run any directly-related tests in `backend/tests/`
     with `JWT_SECRET_KEY=test-secret-key MONGODB_URI= ../.venv/Scripts/python.exe -m pytest <files> -q`.
   - If any `frontend/` changed: from `frontend/`, run `npx tsc --noEmit` (must be clean).
     Run `npx vite build` only if imports/deps/config changed.

3. Only if every check passes:
   - If currently on `main`, create a new branch named `claude/<kebab-scope>`.
   - Stage ONLY the changed source files by name — never `dist/`, build output, or
     unrelated untracked files (e.g. local scripts/backups).
   - Commit via `git commit -F` with a heredoc message (subject + why), ending with the
     `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.

4. `git push -u origin <branch>` then `gh pr create` with a Summary + Test plan body.

5. Report the PR URL. Stop there — wait for my go-ahead to merge.

Change being shipped: $ARGUMENTS
