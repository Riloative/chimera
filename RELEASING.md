# Releasing Chimera

Releases are cut by pushing a version tag. Everything else (tests, images,
scanning, signing, the GitHub Release) is done by
`.github/workflows/release.yml`. Nobody builds or pushes images by hand.

## One-time repository setup

1. **Actions permissions** (Settings → Actions → General): allow GitHub
   Actions and the actions used by the workflows (or "Allow all actions"; they
   are pinned to commit SHAs). *Workflow permissions* can stay at "Read
   repository contents": each job asks for exactly what it needs
   (`packages: write`, `id-token: write`, `contents: write`).
2. **Private vulnerability reporting** (Settings → Code security): enable it,
   and Dependabot alerts and security updates.
3. **Branch protection for `main`** (Settings → Rules → Rulesets → New branch
   ruleset): require a pull request, require the status checks *Version,
   workflows, scripts, compose*, *Backend tests (incl. real Chromium)*,
   *Frontend typecheck, lint, unit and i18n tests*, *End-to-end (Playwright)*,
   *Docker build (chimera)* and *Docker build (chimera-browser)*, block force
   pushes and deletion.
4. **Tag protection** (a tag ruleset for `v*`): only maintainers may create
   tags; block deletion and updates of existing tags.
5. **After the first release:** the packages `chimera` and `chimera-browser`
   appear under your profile's Packages. For each: Package settings → *Change
   visibility* → **Public** (so users can pull without logging in), and
   *Manage Actions access* → this repository with *Write* (already the case
   when the package was created by this repository's workflow).

## Cutting a release

1. **Pick the version** (Semantic Versioning): patch for fixes, minor for
   backwards-compatible features, major for anything that needs manual steps
   from users. Pre-releases: `1.1.0-rc.1`.
2. **Bump it in the single source of truth**, `backend/pyproject.toml`
   (`version = "1.1.0"`), then make the rest follow:
   - `frontend/package.json` and `frontend/package-lock.json` (`"version"`, two
     places in the lock file), e.g. `cd frontend && npm version 1.1.0 --no-git-tag-version`;
   - the default image tag in `docker-compose.yml` (both
     `${CHIMERA_VERSION:-1.1.0}`);
   - `CHANGELOG.md`: rename *Unreleased* to `## [1.1.0] - YYYY-MM-DD` and start
     a new empty *Unreleased*. Every release needs **Upgrade notes**
     (migrations, new or changed environment variables, breaking changes).

   `python3 scripts/check-version.py` must say "consistent".
3. **Pull request** with these changes; wait for CI to pass; merge.
4. **Tag the merge commit** on `main` and push the tag:

   ```bash
   git switch main && git pull
   python3 scripts/check-version.py --tag v1.1.0
   git tag -a v1.1.0 -m "Chimera 1.1.0"
   git push origin v1.1.0
   ```

5. **Watch the workflow** (Actions → Release). It runs the full CI, checks the
   tag against the version, builds linux/amd64 and linux/arm64 images, scans
   them (fails on fixable critical vulnerabilities), pushes them to
   `ghcr.io/riloative/chimera` and `ghcr.io/riloative/chimera-browser` with
   SBOM and provenance, signs them with cosign, and creates the GitHub Release
   with the CHANGELOG section as notes and the assets (`docker-compose.yml`,
   `.env.example`, `searxng-settings.yml`, `install-docker.sh`, `update.sh`,
   the systemd units, `SHA256SUMS`).
6. **Verify** the published result:

   ```bash
   cosign verify ghcr.io/riloative/chimera:1.1.0 \
     --certificate-identity-regexp '^https://github\.com/Riloative/chimera/\.github/workflows/release\.yml@refs/tags/v' \
     --certificate-oidc-issuer https://token.actions.githubusercontent.com
   docker buildx imagetools inspect ghcr.io/riloative/chimera:1.1.0     # both platforms
   ```

   and try `install-docker.sh --version 1.1.0` and `update.sh` from the
   previous release on a scratch VM.
7. **Edit the release notes** on GitHub if needed (the workflow publishes
   them directly; stable releases are marked *Latest*, pre-releases are not).

## Tags and channels

| Release | Image tags |
| --- | --- |
| `v1.1.0` | `1.1.0`, `1.1`, `1`, `latest` |
| `v1.1.0-rc.1` | `1.1.0-rc.1` only (never `latest`, `1.1` or `1`) |

`update.sh` and the in-app update check ignore pre-releases unless asked
(`update.sh --pre`).

## If something goes wrong

- **CI fails in the release run:** nothing was published. Fix on `main`, then
  move to a new patch version (don't reuse or move tags).
- **The scan fails:** update the base image (Dependabot usually has a PR) or
  the affected dependency, release a new patch version.
- **Images were published but the release step failed:** re-run the failed
  job from the Actions page (it creates the release from the same tag).
- **A broken release reached users:** publish a fixed patch release quickly.
  Users of `update.sh` were protected by the health check and rollback; tell
  others in the release notes how to pin back (`CHIMERA_VERSION`).

## From 1.0.0-rc.1 to 1.0.0

1. Tag `v1.0.0-rc.1` (already prepared) to exercise the pipeline end to end.
2. Fix anything it shows, then bump to `1.0.0` as above: in `CHANGELOG.md`
   replace the `[1.0.0] - Unreleased` heading with the date and remove the
   `[1.0.0-rc.1]` placeholder section (or keep it as history).
3. Tag `v1.0.0`.
