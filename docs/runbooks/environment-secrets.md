# Canonical deployment secrets operator runbook

This runbook operates one Lockbox authority for the canonical deployment:
[`deploy/environment-secrets.json`](../../deploy/environment-secrets.json). It implements
[ADR 0028](../adr/0028-operate-one-canonical-deployment.md) and the Lockbox boundary in
[ADR 0026](../adr/0026-use-lockbox-for-environment-secrets.md). It does not create another
environment, make the runtime read Lockbox, or expose payload values.

## Scope and safety rules

- The reviewed secret is `e6q85jjl76r45maigtfb` in folder `b1g2qttgfhb4gdunvlge`.
- GitHub OIDC uses service account `ajeaekiue94ogksguh0h`, federation `ajeula3gd46omgf9jiko`,
  repository `peter-nikitin/photo-prjct`, and subject `repo:peter-nikitin/photo-prjct:ref:refs/heads/main`.
- Only the manifest's `local-web`, `deploy`, `remote-check`, and `public-monitor` projections are
  authorized. Repository variables provide reviewed non-secret configuration.
- Commerce order-link signing and Postbox SMTP credentials are `deploy` projection entries only.
  The application workflow supplies Commerce factory paths, sender address, public origin, support
  contact, and worker-enable state as repository variables; do not add provider credentials there.
- No GitHub Environment participates. Do not use GitHub Environment secret or variable commands.
- Do not run `yc config list`, `yc lockbox payload get`, `set -x`, `env`, or `printenv` while
  handling this procedure.

Every IAM, Lockbox, credential, or secret mutation is a hard stop: before it, show the exact
resource, current and intended state, impact, validation, rollback, and price effect, then obtain
fresh operator approval. Values may enter only through a protected mode-0600 local file and never
through command arguments, output, artifacts, tickets, Git history, or `.env`.

## Setup

### Preflight

From an isolated worktree, read only the control-plane facts:

```bash
yc config profile list
yc config get cloud-id
yc config get folder-id
yc resource-manager folder get --id b1g2qttgfhb4gdunvlge --format json
yc lockbox secret get --id e6q85jjl76r45maigtfb --format json
yc lockbox secret list-access-bindings --id e6q85jjl76r45maigtfb --format json
yc iam service-account get --id ajeaekiue94ogksguh0h --format json
yc iam workload-identity oidc federation get --id ajeula3gd46omgf9jiko --format json
yc iam workload-identity federated-credential list --service-account-id ajeaekiue94ogksguh0h --format json
yc iam key list --service-account-id ajeaekiue94ogksguh0h --format json
yc iam api-key list --service-account-id ajeaekiue94ogksguh0h --format json
yc iam access-key list --service-account-id ajeaekiue94ogksguh0h --format json
yc resource-manager folder list-access-bindings --id b1g2qttgfhb4gdunvlge --format json
cloud_id=$(yc config get cloud-id)
yc resource-manager cloud list-access-bindings --id "$cloud_id" --format json
```

Confirm the inventory's exact subject, main-branch workflow allowlist, and resource-level
`lockbox.viewer` plus `lockbox.payloadViewer` roles. Stop for review if they differ; do not create
a duplicate secret or broaden a folder role.

All three key lists are empty. Confirm the CI service account is absent from the folder and cloud
bindings; it may hold only the two exact-secret reader roles. Any static key, API/access key, or
broad inherited role is an incident/design-review stop, not a deviation to work around.

### Success evidence

Record only non-secret IDs, role names, and the time of the read-only check in protected change
evidence.

### Rollback

Read-only setup has no rollback. Missing infrastructure needs separately approved creation work.

### Non-disclosure

Do not attach raw CLI output, tokens, key material, or payload metadata to a ticket.

## Inventory

### Preflight

Compare the checked-in manifest and [inventory](environment-secrets-inventory.md). List only
repository secret and variable names:

```bash
gh secret list --repo peter-nikitin/photo-prjct --json name,updatedAt
gh variable list --repo peter-nikitin/photo-prjct --json name,updatedAt
```

### Success evidence

The manifest is the complete key/projection schema and repository variables are non-secret.

### Rollback

Do not change source scopes as a diagnostic action.

### Non-disclosure

Name-only listings are the maximum GitHub evidence allowed here.

## Payload version validation and population

### Preflight

Use an isolated worktree and a protected local path. The candidate is a JSON array for `yc lockbox`:
text entries use `text_value`; binary `VM_SSH_KEY` uses base64 `binary_value`. Validate the complete
manifest key set before mutation and never put a payload in argv or output. The editor must not
create swap, backup, or cloud-synced copies.

```bash
candidate_payload=""
cleanup_candidate() {
  [ -n "$candidate_payload" ] || return 0
  [ ! -e "$candidate_payload" ] && { candidate_payload=""; return 0; }
  if ! rm -f -- "$candidate_payload"; then
    printf '%s\n' "[environment-secrets] stage=candidate_cleanup status=error code=cleanup_failed retained_path=$candidate_payload" >&2
    return 1
  fi
  candidate_payload=""
}
finish_candidate() {
  status=$?
  trap - EXIT HUP INT TERM
  cleanup_candidate || exit 1
  exit "$status"
}
set -e
umask 077
candidate_payload=$(mktemp "${TMPDIR:-/tmp}/findme-lockbox-payload.XXXXXX.json") || exit 1
trap finish_candidate EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
chmod 600 "$candidate_payload"
"${EDITOR:?set EDITOR to a non-logging local editor}" "$candidate_payload"
.venv/bin/python - "$candidate_payload" deploy/environment-secrets.json <<'PY'
import base64, json, sys
from pathlib import Path

candidate = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
manifest = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
expected = {entry["key"]: entry["type"] for entry in manifest["entries"]}
if not isinstance(candidate, list): raise SystemExit("candidate must be a JSON array")
actual = {}
for entry in candidate:
    if not isinstance(entry, dict) or not isinstance(entry.get("key"), str): raise SystemExit("candidate entry is malformed")
    key = entry["key"]; field = "text_value" if expected.get(key) == "text" else "binary_value"
    if key in actual or key not in expected or set(entry) != {"key", field} or not isinstance(entry[field], str) or not entry[field]: raise SystemExit("candidate key set is invalid")
    if field == "binary_value": base64.b64decode(entry[field], validate=True)
    actual[key] = field
if set(actual) != set(expected): raise SystemExit("candidate key set is incomplete")
print("candidate schema is complete")
PY
```

**STOP — obtain fresh operator approval** immediately before an initial version or rotation. Show
only key names, secret ID, current metadata, downstream credential overlap/revocation, price effect,
and rollback version ID.

### Success evidence

For an empty secret, create the first version through standard input; never guess a base version.
It must contain every required manifest key. The Commerce signing secret and Postbox API-key
entries may be omitted only for a dark deploy with `COMMERCE_WORKER_ENABLED=False`; the enabled
deployment path rejects their absence before any mutation. Verify exact metadata and one permitted
consumer's sanitized resolver result:

```bash
yc lockbox secret add-version --id e6q85jjl76r45maigtfb \
  --payload - --format json < "$candidate_payload"
yc lockbox secret get --id e6q85jjl76r45maigtfb --format json
yc lockbox secret list-versions --id e6q85jjl76r45maigtfb --format json
```

The exit trap removes the candidate after success, failed validation, rejected approval, interrupt,
termination, or failed `add-version`.

### Rollback

Before retiring old sources, restore only reviewed workflow code for a drill. Afterwards, select an
earlier complete validated Lockbox version or reviewed code retaining the Lockbox reader; do not
recreate GitHub secrets as a standing fallback.

### Non-disclosure

Never display, `cat`, upload, commit, attach, or retain `candidate_payload`, and never use
`yc lockbox payload get` as a validation shortcut.

## Rotation

### Preflight

Build a complete candidate including unchanged entries and `VM_SSH_KEY`. Payload rotation is not
identity revocation. **STOP — obtain fresh approval** before a new version, downstream credential
change, or destruction schedule.

### Success evidence

Derive the actual current version ID from metadata immediately before mutation, then add exactly one
complete candidate version:

```bash
previous_version_id=$(yc lockbox secret get --id e6q85jjl76r45maigtfb --format json | \
  .venv/bin/python -c '
import json
import sys
from pathlib import Path

secret = json.load(sys.stdin)
manifest = json.loads(Path("deploy/environment-secrets.json").read_text(encoding="utf-8"))
expected_keys = {entry["key"] for entry in manifest["entries"]}
current = secret.get("current_version")
if not isinstance(current, dict):
    raise SystemExit(2)
version_id = current.get("id")
payload_keys = current.get("payload_entry_keys")
if (
    not isinstance(version_id, str)
    or not version_id
    or secret.get("id") != "e6q85jjl76r45maigtfb"
    or current.get("secret_id") != "e6q85jjl76r45maigtfb"
    or current.get("status") != "ACTIVE"
    or not isinstance(payload_keys, list)
    or any(not isinstance(key, str) for key in payload_keys)
    or len(payload_keys) != len(set(payload_keys))
    or set(payload_keys) != expected_keys
):
    raise SystemExit(2)
print(version_id)
')
test -n "$previous_version_id"
yc lockbox secret add-version --id e6q85jjl76r45maigtfb \
  --base-version-id "$previous_version_id" --payload - --format json < "$candidate_payload"
yc lockbox secret get --id e6q85jjl76r45maigtfb --format json
yc lockbox secret list-versions --id e6q85jjl76r45maigtfb --format json
```

Verify the new current version's required manifest key set, any present optional Commerce entries,
and the affected consumer without showing its environment file. Running services keep prior values
until a reviewed canonical deployment.

### Rollback

Select an earlier complete validated version and follow the exact global rollback below. Only after
a separate fresh approval, retire an old version with a bounded seven-day retention period:

```bash
yc lockbox secret schedule-version-destruction --id e6q85jjl76r45maigtfb \
  --version-id "$old_version_id" --pending-period 168h --format json
```

If destruction was scheduled before rollback completes, cancel only that schedule after fresh approval:

```bash
yc lockbox secret cancel-version-destruction --id e6q85jjl76r45maigtfb \
  --version-id "$old_version_id" --format json
```

### Non-disclosure

Compare only key names, IDs, statuses, timestamps, and sanitized resolver markers; never copy a
credential, base64 private key, or provider response body.

## Local use

### Preflight

Use only `make local-web` from an isolated worktree. It invokes the `local-web` projection with an
authorized human `yc` identity and applies manifest local overrides.

### Success evidence

Expect sanitized `[environment-secrets]` markers and `[local-web] stage=launch status=ready`.

### Rollback

Stop local services with `docker compose stop web db`; do not use this launcher to deploy or SSH.

### Non-disclosure

`FINDME_ENV_FILE` is a private path, not a value. Do not log it after cleanup.

## CI OIDC preflight

### Preflight

Dispatch the reviewed main workflow without application mutation:

```bash
gh workflow run deploy.yml --ref main -f preflight=true
```

The resolver enforces issuer, audience, repository, `refs/heads/main`, and exact `workflow_ref`
allowlist before exchange. It validates all four consumers: `local-web`, `deploy`, `remote-check`,
and `public-monitor`.

### Success evidence

Record the run URL/ID, workflow path, expected OIDC subject, service-account and secret IDs,
non-secret version ID, consumer name, and sanitized stage result.

### Rollback

A failed no-op preflight changes no VM state. Correct trust or manifest configuration through
review; never broaden the OIDC identity as a workaround.

### Non-disclosure

OIDC/IAM tokens remain in memory and must not be echoed or emitted as outputs.

## Revocation

### Preflight

Determine whether the affected reader is human or CI. Read the exact secret binding and, for CI,
identify the federated credential from the dedicated service account by matching federation
`ajeula3gd46omgf9jiko`, repository, and `refs/heads/main`; never guess an ID. **STOP — obtain fresh
operator approval** before changing IAM, deleting a federated credential, or suspending an account.

### Success evidence

For a human, remove only the exact resource-level binding. For CI, delete only the matching
federated credential when the approved response requires it:

```bash
yc lockbox secret remove-access-binding --id e6q85jjl76r45maigtfb \
  --user-account-id "$revoked_user_account_id" --role lockbox.payloadViewer --format json
yc iam workload-identity federated-credential delete --id "$federated_credential_id" --format json
```

Verify a sanitized resolver failure for the revoked identity and a sanitized success for an intended
identity. Payload rotation does not prove revocation.

### Rollback

Restoring access is a separate IAM mutation and needs fresh approval for the exact resource-level
binding or exact federated credential, followed by the two-identity verification.

### Non-disclosure

Never test revocation with `yc lockbox payload get` or by retaining a resolver environment file.

## Rollback

### Preflight

Select an earlier complete version from `yc lockbox secret list-versions` metadata and set its
non-secret ID as `rollback_version_id`. Do not treat a retained old version as current. **STOP —
obtain fresh approval** before a version mutation or canonical deployment.

### Success evidence

Create a new current version based exactly on the selected complete version; the empty change array
is standard input and never contains a payload value:

```bash
test -n "$rollback_version_id"
printf "[]" | yc lockbox secret add-version --id e6q85jjl76r45maigtfb \
  --base-version-id "$rollback_version_id" --payload - --format json
yc lockbox secret get --id e6q85jjl76r45maigtfb --format json
yc lockbox secret list-versions --id e6q85jjl76r45maigtfb --format json
```

Verify the resulting current version has the exact key set from the manifest and one permitted
consumer without displaying its resolved file, then
use the reviewed canonical deployment workflow.

### Rollback

Do not introduce dual-read behavior, mix entries from versions, or restore GitHub secrets as a
second authority. A mistakenly scheduled destruction needs separate approval before cancellation.

### Non-disclosure

Do not export a payload during rollback; keep diagnostics and Git history free of values, token
fragments, and shell tracing.

## Lost device or account recovery

### Preflight

Treat a lost device/account as an identity incident. Contact a surviving cloud organization
administrator who can verify the affected reader and the separately maintained break-glass
ownership record. This runbook never names or stores break-glass identities.

### Success evidence

The surviving administrator restores a human reader only after revoking the lost identity from the
exact Lockbox resource and verifying a replacement through the sanitized resolver. Record only IDs,
approver, timestamps, and stage codes.

### Rollback

If replacement access is wrong, remove the new exact binding after fresh approval and continue with
the surviving administrator. Never grant folder-wide access, create a service-account key, or use a
copied payload as a recovery channel.

### Non-disclosure

Payload rotation is not identity revocation and cannot invalidate a reader that retains payload
access. This relies on separately maintained break-glass ownership and does not claim runtime
credential recovery.

## Incident and non-disclosure

### Preflight

Stop at the first sanitized resolver error and preserve only stage, code, non-secret IDs, time,
consumer, and workflow URL/ID.

### Success evidence

The incident record states containment owner, approved action, and recovery decision without
credential data.

### Rollback

Use the approved Lockbox-version or deployment rollback path. A candidate cleanup failure remains
an error after child success: contain the reported private path locally, do not upload it, and
request approved recovery. Never restart, SSH to, or mutate the VM merely to probe Lockbox.

### Non-disclosure

Do not share raw CLI output, payloads, tokens, SSH keys, database passwords, Object Storage keys,
or environment dumps.

## References

- [Environment secrets inventory](environment-secrets-inventory.md)
- [Canonical deployment runbook](deployment.md)
- [ADR 0026](../adr/0026-use-lockbox-for-environment-secrets.md) and
  [ADR 0028](../adr/0028-operate-one-canonical-deployment.md)
