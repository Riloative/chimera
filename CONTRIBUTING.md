# Contributing to Chimera

Thanks for helping! Chimera is a small, privacy-first project: please keep
changes focused, tested and honest about what they do.

## Development setup

Requirements: Python 3.11+, Node.js 22, and (for end-to-end tests) Chromium via
Playwright. No model or GPU is needed: tests use a mock llama-server and a
mock web.

```bash
./scripts/install.sh              # backend venv + frontend dependencies + build
./scripts/dev.sh                  # backend on :8700 and Vite on :5173 with reload
```

Optional, for the headless-browser tests with real Chromium:

```bash
python3 -m venv browser/.venv
browser/.venv/bin/pip install -r browser/requirements.txt
browser/.venv/bin/playwright install chromium
```

Containers built from your checkout:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

## Tests

```bash
./scripts/test.sh                 # everything below
backend/.venv/bin/python -m pytest backend/tests
cd frontend && npx tsc -b && npx eslint . && npx vitest run
cd frontend && npx playwright test
python3 scripts/check-version.py
```

CI runs the same commands on every pull request (`.github/workflows/ci.yml`),
plus the Docker builds, shellcheck and actionlint.

## Conventions

- **Commits:** imperative subject line (≤ 72 characters), a blank line, then
  what and why. One logical change per commit.
- **Pull requests:** fill in the template; describe user-visible changes in
  `CHANGELOG.md` under *Unreleased* (with upgrade notes for migrations, new
  environment variables or breaking changes).
- **No dead controls:** every button or setting must work end to end.
- **Privacy:** no telemetry, no CDNs, no external requests the user didn't ask
  for. Never log message content, passwords, tokens or full URLs.
- **Security-sensitive changes** (auth, fetching, the browser, file handling)
  need tests for the failure cases, not only the happy path.
- **Database changes** go through a new migration in `backend/chimera/db.py`
  (append to `MIGRATIONS`; never edit an existing one).
- **Strings:** every user-visible string goes through i18n, in English and
  Lithuanian (see below). The backend returns stable error codes, never
  sentences meant for display.

## Adding or improving a language

1. Copy `frontend/src/i18n/locales/en/` to `locales/<code>/` and translate the
   JSON files (keep the keys, placeholders like `{{count}}` and plural
   suffixes `_one`, `_few`, `_many`, `_other` as your language needs).
2. Register it in `frontend/src/i18n/resources.ts` and the language list.
3. `npx vitest run` checks key parity, placeholders and plural forms.
4. Add terms to `docs/i18n-glossary.md`.

Details: [docs/I18N.md](docs/I18N.md).

## Reporting bugs and security issues

Bugs: open an issue with the bug template. Security issues: **privately**, see
[SECURITY.md](SECURITY.md).

By contributing you agree that your contributions are licensed under the MIT
License (see LICENSE) and that you follow the [Code of Conduct](CODE_OF_CONDUCT.md).
