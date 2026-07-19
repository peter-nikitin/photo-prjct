# Node Runtime Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make FindMe Photo Node commands use Node 22 on the first attempt in local interactive, login, and Codex shells.

**Architecture:** The repository declares one Node 22 contract through `.nvmrc`, npm engines, CI, and the visual-test image, guarded by an executable repository test. The local machine loads NVM in login shells, selects Node 22 by default and per directory, then removes the obsolete Homebrew Node only after fresh-shell verification succeeds.

**Tech Stack:** NVM, Zsh, Homebrew, Node.js 22, npm, pytest, GitHub Actions, Docker

## Global Constraints

- The supported Node range is `>=22 <23`.
- `.nvmrc`, GitHub Actions, and `Dockerfile.visual-tests` must all select Node major version `22`.
- Shell edits must preserve unrelated configuration and remain idempotent.
- Homebrew Node may be removed only after a fresh login shell resolves a working NVM Node 22.
- Do not remove any Homebrew ICU formula.
- Repository changes stay on `fix-node-bootstrap` and are delivered through a pull request.
- CI remains authoritative for the full PostgreSQL and containerized visual suite.

---

### Task 1: Versioned Node 22 contract

**Files:**
- Create: `.nvmrc`
- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: `README.md`
- Test: `tests/test_repository_foundation.py`

**Interfaces:**
- Consumes: the existing CI `Set up Node.js` step and `Dockerfile.visual-tests` Node base image.
- Produces: `.nvmrc` value `22` and npm engine range `>=22 <23`, used by NVM, npm, contributors, and the repository contract test.

- [ ] **Step 1: Add the failing repository contract test**

Append this test to `tests/test_repository_foundation.py`:

```python
def test_local_node_version_matches_ci_and_visual_container() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    node_setup = _workflow_step(_load_workflow("ci.yml"), "quality", "Set up Node.js")
    dockerfile = (ROOT / "Dockerfile.visual-tests").read_text(encoding="utf-8")

    assert (ROOT / ".nvmrc").read_text(encoding="utf-8").strip() == "22"
    assert package["engines"]["node"] == ">=22 <23"
    assert node_setup["with"]["node-version"] == "22"
    assert "FROM node:22-bookworm-slim@sha256:" in dockerfile
```

- [ ] **Step 2: Run the new test and confirm the missing contract fails**

Run:

```bash
/Users/petrnikitin/Documents/Sites/photo-prjct/.venv/bin/pytest tests/test_repository_foundation.py::test_local_node_version_matches_ci_and_visual_container -q
```

Expected: FAIL because `.nvmrc` does not exist.

- [ ] **Step 3: Add the minimal repository version declarations**

Create `.nvmrc` with exactly:

```text
22
```

Add this top-level object to `package.json` after `"private": true`:

```json
"engines": {
  "node": ">=22 <23"
},
```

Regenerate only npm manifest metadata under Node 22:

```bash
zsh -lic 'cd /Users/petrnikitin/Documents/Sites/photo-prjct/.worktrees/fix-node-bootstrap && nvm use 22 >/dev/null && npm install --package-lock-only --ignore-scripts'
```

- [ ] **Step 4: Document first-time Node setup**

In `README.md`, extend the Local development requirements with Node 22 and NVM, then add this block before `Run the main version`:

````markdown
### Prepare Node.js

The repository uses Node 22 for JavaScript unit tests and local npm commands. With
[NVM](https://github.com/nvm-sh/nvm) installed, prepare the pinned major version once per checkout:

```bash
nvm install
nvm use
node --version
npm ci
```

`node --version` must report `v22.x.x`. NVM reads `.nvmrc`, matching GitHub Actions and the
containerized visual-test environment.
````

- [ ] **Step 5: Run focused repository verification**

Run:

```bash
/Users/petrnikitin/Documents/Sites/photo-prjct/.venv/bin/pytest tests/test_repository_foundation.py::test_local_node_version_matches_ci_and_visual_container -q
zsh -lic 'cd /Users/petrnikitin/Documents/Sites/photo-prjct/.worktrees/fix-node-bootstrap && nvm use >/dev/null && npm ci && npm run test:js'
git diff --check
```

Expected: the focused pytest passes, npm reports no audit failures, all 21 JavaScript tests pass,
and `git diff --check` prints nothing.

- [ ] **Step 6: Commit the repository contract**

```bash
git add .nvmrc package.json package-lock.json README.md tests/test_repository_foundation.py
git commit -m "fix: standardize local Node 22 runtime"
```

Expected: one commit containing only the version contract, documentation, and contract test.

### Task 2: Local NVM bootstrap and obsolete Homebrew cleanup

**Files:**
- Modify locally, do not commit: `/Users/petrnikitin/.zprofile`
- Modify locally, do not commit: `/Users/petrnikitin/.zshrc`
- Create locally, do not commit: timestamped backups beside both shell files

**Interfaces:**
- Consumes: NVM at `/Users/petrnikitin/.nvm/nvm.sh` and repository `.nvmrc` value `22`.
- Produces: `load-nvmrc`, a Zsh hook that selects `.nvmrc` inside projects and the NVM default elsewhere.

- [ ] **Step 1: Record the failing non-interactive baseline**

Run:

```bash
zsh -lc 'command -v node; node --version'
```

Expected before the fix: `/usr/local/bin/node` followed by a `dyld` error referencing missing ICU 71.

- [ ] **Step 2: Back up shell startup files**

Run with a single resolved timestamp:

```bash
node_bootstrap_stamp=$(date +%Y%m%d-%H%M%S)
cp -p /Users/petrnikitin/.zprofile "/Users/petrnikitin/.zprofile.before-node-bootstrap-$node_bootstrap_stamp"
cp -p /Users/petrnikitin/.zshrc "/Users/petrnikitin/.zshrc.before-node-bootstrap-$node_bootstrap_stamp"
```

Expected: two readable backups with the same timestamp.

- [ ] **Step 3: Load NVM from login Zsh**

Add these lines once to `/Users/petrnikitin/.zprofile` after its existing source lines:

```zsh
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
```

Use `apply_patch`, and confirm `rg -n 'NVM_DIR|nvm.sh' /Users/petrnikitin/.zprofile` shows one initialization block.

- [ ] **Step 4: Add idempotent per-directory version switching**

Add this block once to the user-configuration portion of `/Users/petrnikitin/.zshrc`, after Oh My
Zsh is sourced:

```zsh
autoload -U add-zsh-hook

load-nvmrc() {
  local nvmrc_path
  nvmrc_path="$(nvm_find_nvmrc)"

  if [ -n "$nvmrc_path" ]; then
    nvm use --silent
  elif [ "$(nvm version)" != "$(nvm version default)" ]; then
    nvm use --silent default
  fi
}

add-zsh-hook chpwd load-nvmrc
load-nvmrc
```

Use `apply_patch`, and confirm `rg -n 'load-nvmrc|add-zsh-hook' /Users/petrnikitin/.zshrc` shows one
function and one hook registration.

- [ ] **Step 5: Set and verify the NVM default before Homebrew removal**

Run:

```bash
zsh -lic 'nvm alias default 22 >/dev/null && nvm use default >/dev/null && command -v node && node --version && npm --version'
zsh -lc 'command -v node && node --version && npm --version'
zsh -lic 'cd /Users/petrnikitin/Documents/Sites/photo-prjct/.worktrees/fix-node-bootstrap && command -v node && node --version'
```

Expected: every Node path starts with `/Users/petrnikitin/.nvm/versions/node/`, every Node version
starts with `v22.`, and npm exits successfully.

- [ ] **Step 6: Remove only the obsolete Homebrew Node formula**

Run:

```bash
brew uninstall node
```

Do not run `brew autoremove` and do not uninstall any ICU formula. Expected: Homebrew removes Node
18.11.0 and its links without modifying `~/.nvm`.

- [ ] **Step 7: Verify first-attempt behavior after cleanup**

Run:

```bash
brew list --versions node
zsh -lc 'cd /Users/petrnikitin/Documents/Sites/photo-prjct/.worktrees/fix-node-bootstrap && command -v node && node --version && npm ci && npm run test:js'
zsh -lic 'cd /tmp && command -v node && node --version'
```

Expected: `brew list --versions node` produces no installed version; both shells resolve NVM Node
22; npm install succeeds; all 21 JavaScript tests pass.

- [ ] **Step 8: Push and open the implementation pull request**

Run:

```bash
git status --short --branch
git log --oneline origin/main..HEAD
git push -u origin fix-node-bootstrap
env -u GITHUB_TOKEN gh pr create --draft --base main --head fix-node-bootstrap \
  --title "Fix first-run Node setup" \
  --body "Standardizes local Node tooling on Node 22, documents NVM bootstrap, and adds an executable version-alignment contract. Local shell initialization and obsolete Homebrew Node cleanup were verified separately and are not committed."
```

Expected: the branch is pushed and one draft pull request targets `main`.
