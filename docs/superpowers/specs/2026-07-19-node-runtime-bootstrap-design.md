# Node Runtime Bootstrap Design

**Date:** 2026-07-19

## Goal

Make FindMe Photo Node commands work on the first attempt in interactive terminals, login shells,
and Codex command sessions, while keeping local development aligned with CI and the visual-test
container on Node 22.

## Root cause

The machine currently has three conflicting runtime paths:

- `/usr/local/bin/node` points to Homebrew Node 18.11.0, which cannot start because its ICU 71
  dynamic library is no longer installed;
- interactive Zsh loads NVM through Oh My Zsh and selects the moving `node` default, currently
  Node 24;
- GitHub Actions and `Dockerfile.visual-tests` use Node 22.

The repository does not declare its expected local Node version, so shell type and startup path
determine whether commands fail, use Node 24, or use Node 22.

## Design

### Repository contract

- Add `.nvmrc` containing `22` so `nvm install` and `nvm use` select the current installed Node 22
  release without pinning contributors to one patch release.
- Add `engines.node` to `package.json` with `>=22 <23` so package tooling and editors can detect an
  incompatible runtime.
- Keep CI's `node-version: "22"` and the digest-pinned Node 22 visual-test image unchanged because
  they already express the same major-version contract.
- Add a short local Node setup section to `README.md`: install NVM, run `nvm install` and `nvm use`,
  then `npm ci`. Document `node --version` as the diagnostic command.
- Extend the repository foundation test to verify `.nvmrc`, `engines.node`, CI, and the visual-test
  Dockerfile stay aligned on Node 22.

### User shell bootstrap

- Load `~/.nvm/nvm.sh` from `~/.zprofile` so login and non-interactive login Zsh sessions see NVM
  before the broken Homebrew fallback.
- Set the NVM default alias to `22`, making commands outside a repository use Node 22 as well.
- Add an idempotent Zsh directory-change hook in `~/.zshrc` that silently runs `nvm use` when the
  current directory or one of its parents contains `.nvmrc`. It must not print during every `cd` and
  must leave directories without `.nvmrc` on the NVM default.
- Preserve unrelated shell configuration verbatim.

### Homebrew cleanup

After NVM works in fresh shells, uninstall the obsolete Homebrew `node` formula. Do not remove ICU
versions because other Homebrew packages may depend on them. Verify that `/usr/local/bin/node` is
gone or no longer selected and that `command -v node` resolves inside `~/.nvm`.

## Failure handling and safety

- Back up the two shell startup files before editing them.
- Make shell edits idempotent so repeated setup does not duplicate NVM initialization or hooks.
- Validate a fresh Zsh process before uninstalling Homebrew Node. If fresh-shell validation fails,
  retain Homebrew state and correct NVM initialization first.
- Repository changes live on the isolated `fix-node-bootstrap` branch and are delivered through a
  pull request. User shell and Homebrew changes remain local and are not committed.

## Verification

The change is complete when all of the following hold:

1. A fresh login Zsh outside the repository resolves `node` from NVM and reports Node 22.
2. A fresh login Zsh inside the repository recognizes `.nvmrc` and reports Node 22.
3. The ordinary command `npm ci` succeeds in the repository without a manual NVM preamble.
4. `npm run test:js` passes all JavaScript tests.
5. The focused repository-foundation Node-version contract test passes.
6. Homebrew no longer lists the obsolete `node` formula, and removing it does not affect NVM Node.
7. The worktree has no unintended changes before commit and push.

Containerized Playwright is not required for this runtime-contract change: its Node 22 image and
public command remain unchanged. CI remains authoritative for the full PostgreSQL and visual suite.
