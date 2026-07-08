---
name: publish
description: >-
  Release the idevice Python package to PyPI: scan for and remove sensitive
  information, bump version in pyproject.toml, run checks, commit, and push to
  trigger CI publish. Use when the user asks to publish, release, ship, bump
  version and push, or cut a PyPI version.
---

# Publish idevice to PyPI

## Overview

- **Package:** `idevice` (hatchling build, `src/` layout)
- **Version source:** `pyproject.toml` → `[project].version` (only place to bump)
- **Tooling:** [uv](https://docs.astral.sh/uv/)
- **CI:** `.github/workflows/workflow.yml` — push to `main` runs test + lint, then builds and publishes to PyPI via trusted publishing (`environment: pypi`)

## Pre-publish checklist

Run from repo root **in this order**. Do not publish until every step passes.

### 1. Remove sensitive information

Before bumping the version or pushing, scan the repo and **remove or replace** anything that should not ship in a public PyPI package or open repository.

Search tracked files for:

| Category | Examples to remove or genericize |
|----------|----------------------------------|
| Secrets / credentials | API keys, tokens, passwords, private keys, `.env` contents |
| Connection strings | `mongodb://`, `postgres://`, `redis://…@…` with credentials |
| Real device identifiers | Physical UDIDs, adb serials, real bundle ids used internally |
| Internal host / network info | Non-placeholder private IPs, internal hostnames |
| Internal product identifiers | Company-specific package names in examples/docs when generic placeholders suffice |

Quick scan commands:

```bash
# Secrets and credential patterns
rg -i '(api[_-]?key|secret|password|token|credential|private[_-]?key|BEGIN (RSA |OPENSSH )?PRIVATE KEY)' \
  --glob '!uv.lock' --glob '!.git/**'

# Common token formats
rg '(AKIA[0-9A-Z]{16}|ghp_[a-zA-Z0-9]{36}|gho_[a-zA-Z0-9]{36}|sk-[a-zA-Z0-9]{20,})' \
  --glob '!uv.lock'

# Tracked env / key files
git ls-files '.env' '.env.*' '*.pem' '*.key' '*.p12'
```

Replace findings with safe placeholders, for example:

- UDID / serial → `00000000-0000000000000000`, `emulator-5554`
- Bundle / package id → `com.example.app`
- Private IP → `192.168.0.10`
- App exe name in docs → `MyApp.exe`

Ensure `.gitignore` excludes `.env` and `.env.*` so secrets are not committed accidentally.

**Fix, commit, and push sanitization changes before the release version bump** when code or docs contained sensitive or internal-only identifiers.

Do not publish if secrets or untracked credential files remain in the tree.

### 2. Lint and test

```bash
uv sync --extra dev
uv run ruff check src tests
uv run pytest
```

Do not publish if lint or tests fail. Fix first, then continue.

## Version bump

1. Read current version in `pyproject.toml`.
2. Choose semver increment:
   - **patch** (`0.1.5` → `0.1.6`): bug fixes, docs, tests
   - **minor** (`0.1.5` → `0.2.0`): backward-compatible features
   - **major** (`0.1.5` → `1.0.0`): breaking API changes
3. Update **only** `[project].version` in `pyproject.toml`.
4. Do not edit `uv.lock` unless dependencies changed.

## Commit and push

Only commit when the user explicitly requests a release/publish.

```bash
git status
git diff
git log -3 --oneline
```

Stage release-related files (typically `pyproject.toml` plus any intentional changes included in the release):

```bash
git add pyproject.toml [other release files…]
git commit -m "$(cat <<'EOF'
Brief summary of what ships in this release.

Explain why the version bump matters for consumers.
EOF
)"
git push -u origin HEAD
```

Commit message: 1–2 sentences, focus on **why**, match recent repo style.

## After push

1. Confirm GitHub Actions **CI** workflow started on `main`.
2. **test** job must pass (ruff + pytest).
3. **publish** job builds (`uv build`) and uploads to PyPI.

If publish fails (e.g. version already on PyPI), bump to a new unused version and push again.

## Optional: git tag

CI also runs on tags matching `v*`. After a successful publish:

```bash
git tag "v$(grep '^version = ' pyproject.toml | cut -d'"' -f2)"
git push origin --tags
```

Use tags for release tracking; PyPI publish is already triggered by `main` pushes.

## Do not

- Publish without scanning for and removing sensitive information first
- Commit secrets (`.env`, tokens, credentials)
- Force-push `main`
- Skip hooks unless the user explicitly asks
- Bump version without running tests when code changed
- Publish from uncommitted local-only changes the user did not intend to ship

## Quick reference

| Step | Command |
|------|---------|
| Scan for secrets / internal ids | See **§1. Remove sensitive information** above |
| Install dev deps | `uv sync --extra dev` |
| Lint | `uv run ruff check src tests` |
| Test | `uv run pytest` |
| Build locally (smoke) | `uv build` |
| Inspect wheel | `unzip -l dist/idevice-*.whl` |

Local `uv build` does **not** publish; CI handles PyPI upload after push.
