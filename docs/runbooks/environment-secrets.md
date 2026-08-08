# Staging environment secrets operator runbook

Use this runbook only for the logical `staging` environment. It implements the repository boundary
from [ADR 0026](../adr/0026-use-lockbox-for-environment-secrets.md) while retaining the Docker
Compose deployment boundary from [ADR 0003](../adr/0003-docker-compose-yandex-cloud.md) and the
staging-only promotion boundary from [ADR 0005](../adr/0005-promote-images-through-staging.md).
The paired [inventory](environment-secrets-inventory.md) is the source for schema, consumer
projection, migration ownership, and stable non-secret resource IDs.

## Scope and non-negotiable safety rules

- The reviewed secret is `e6q85jjl76r45maigtfb` in folder `b1g2qttgfhb4gdunvlge`.
- The GitHub OIDC reader is the service account `ajeaekiue94ogksguh0h` through federation
  `ajeula3gd46omgf9jiko`, issuer `https://token.actions.githubusercontent.com`, audience
  `https://github.com/peter-nikitin`, and subject
  `repo:peter-nikitin/photo-prjct:environment:staging`.
- No Lockbox payload or version ID is recorded in this repository.
- No production Lockbox secret exists and this runbook must not create one. A later production
  environment needs its own secret, IAM boundary, approval, and ADR.
- The VM, Django process, workers, and Docker Compose runtime do not receive Lockbox IAM access and
  do not fetch Lockbox. The separate EJ-018 work is not claimed, selected, or recovered here.

Every command that can change IAM, Lockbox, a Lockbox version, a GitHub Secret, a credential, or
cost is a hard stop: present its exact command, resource ID, current and intended state, impact,
validation, rollback, and price effect (or that it is unknown), then obtain fresh operator approval
immediately before execution. Approval of this runbook, plan, or a previous command is not enough.

Payload values may enter only through a mode-0600 protected file opened in a non-logging local
editor or interactive protected input. They are never command arguments, standard output, standard
error, shell tracing, GitHub step output, artifacts, tickets, Git history, or repository `.env`.
Do not run `yc config list`, `yc lockbox payload get`, `set -x`, `env`, or `printenv` while handling
this procedure. Record only non-secret IDs, key names, timestamps, and sanitized stage codes.

## Setup

### Preflight

From the isolated worktree, establish only the non-secret control-plane facts. These commands are
read-only and must not be redirected to a ticket containing credentials:

```bash
yc version
yc config profile list
yc config get cloud-id
yc config get folder-id
yc resource-manager folder get --id b1g2qttgfhb4gdunvlge --format json
yc lockbox secret get --id e6q85jjl76r45maigtfb --format json
yc lockbox secret list-access-bindings --id e6q85jjl76r45maigtfb --format json
yc iam service-account get --id ajeaekiue94ogksguh0h --format json
yc iam workload-identity oidc federation get --id ajeula3gd46omgf9jiko --format json
yc iam workload-identity federated-credential list \
  --service-account-id ajeaekiue94ogksguh0h --format json
yc iam key list --service-account-id ajeaekiue94ogksguh0h --format json
yc iam api-key list --service-account-id ajeaekiue94ogksguh0h --format json
yc iam access-key list --service-account-id ajeaekiue94ogksguh0h --format json
yc resource-manager folder list-access-bindings --id b1g2qttgfhb4gdunvlge --format json
cloud_id=$(yc config get cloud-id)
yc resource-manager cloud list-access-bindings --id "$cloud_id" --format json
```

Confirm the exact folder, secret, service-account, federation, issuer, audience, and staging-only
subject listed above. Confirm `lockbox.payloadViewer` appears only on the secret for the approved
human reader(s) and CI service account. If any ID, subject, issuer, audience, or role differs, stop
for incident/design review; do not create a duplicate secret or broaden a folder role.

All three key lists are empty. Confirm the CI service account is absent from the folder and cloud
bindings; the only approved CI grant is the exact-secret `lockbox.payloadViewer` binding. Any key,
API/access key, inherited folder/cloud role, or other broad grant is an incident/design-review stop,
not a role to explain away or narrow during this procedure.

### Success evidence

Keep a protected change record containing the listed non-secret IDs, the role name, and the time of
the read-only check. The expected state is one staging Lockbox secret and one dedicated CI account;
there is no service-account key and no runtime Lockbox reader.

### Rollback

Read-only preflight has no rollback. If the approved setup has not been created yet, do not attempt
ad-hoc creation from this runbook. The root operator must present current pricing and an exact
resource-create/IAM command sheet for fresh approval before Gate A creates any billable Lockbox or
identity resource.

### Non-disclosure

Do not request the payload as part of setup and do not paste JSON responses wholesale into a ticket.
The checks above intentionally inspect metadata, bindings, and identities only. Never place a human
account ID, federated-credential ID, or a CLI profile credential in this repository.

## Inventory

### Preflight

Read the manifest and paired inventory before changing a provider value. List GitHub names, not
values, in both source scopes, and inspect Lockbox metadata without calling the payload endpoint:

```bash
gh secret list --repo peter-nikitin/photo-prjct --json name,updatedAt
gh secret list --repo peter-nikitin/photo-prjct --env staging --json name,updatedAt
gh variable list --repo peter-nikitin/photo-prjct --env staging --json name,updatedAt
yc lockbox secret list-versions --id e6q85jjl76r45maigtfb --format json
```

Compare those names with the migration table and every manifest key and consumer in
`deploy/environment-secrets/staging.json`. Classify only the six visible configuration names as
GitHub Environment variables: `ALLOWED_HOSTS`, `DB_NAME`, `DB_USER`, `GHCR_USERNAME`, `VM_HOST`,
and `VM_USER`. All other migrated names are Lockbox entries.

### Success evidence

Record only the secret names, variable names, version IDs, version statuses, entry key names, and
timestamps. A complete Lockbox version has every reviewed manifest key exactly once; the output must
not include values.

### Rollback

Inventory changes nothing. A mismatch is a stop condition: correct the reviewed manifest and
inventory through repository review, or prepare a new complete version after approval. Do not use a
GitHub Secret as a permanent parallel store.

### Non-disclosure

`gh secret list` cannot read values and the explicit JSON field list excludes them. Do not use
`gh secret get`, `gh secret set --body`, a broad variable JSON field selection, or
`yc lockbox payload get`; a name-only inventory is sufficient.

## Payload version validation and population

### Preflight

Use an isolated worktree and a protected local path. First compare the candidate's key names and
types with the manifest without printing values. The candidate is a JSON array for the `yc lockbox`
CLI: text entries use `text_value`; the binary `VM_SSH_KEY` entry uses a base64 `binary_value`.
Create it only with a trusted non-logging editor; do not copy a payload through chat, shell history,
or a command argument. The editor must not create swap, backup, or cloud-synced copies; disable
those features or use a protected local editor.

```bash
candidate_payload=""
cleanup_candidate() {
  [ -n "$candidate_payload" ] || return 0
  [ ! -e "$candidate_payload" ] && {
    candidate_payload=""
    return 0
  }
  if ! rm -f -- "$candidate_payload"; then
    printf '%s\n' \
      "[environment-secrets] stage=candidate_cleanup status=error code=cleanup_failed retained_path=$candidate_payload" >&2
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
.venv/bin/python - "$candidate_payload" deploy/environment-secrets/staging.json <<'PY'
import base64
import json
import sys
from pathlib import Path

candidate = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
manifest = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
expected = {entry["key"]: entry["type"] for entry in manifest["entries"]}
if not isinstance(candidate, list):
    raise SystemExit("candidate must be a JSON array")
actual = {}
for entry in candidate:
    if not isinstance(entry, dict) or not isinstance(entry.get("key"), str):
        raise SystemExit("candidate entry is malformed")
    key = entry["key"]
    if key in actual or key not in expected:
        raise SystemExit("candidate key set is invalid")
    field = "text_value" if expected[key] == "text" else "binary_value"
    if set(entry) != {"key", field} or not isinstance(entry[field], str) or not entry[field]:
        raise SystemExit("candidate entry type is invalid")
    if field == "binary_value":
        try:
            base64.b64decode(entry[field], validate=True)
        except ValueError:
            raise SystemExit("candidate binary entry is invalid") from None
    actual[key] = field
if set(actual) != set(expected):
    raise SystemExit("candidate key set is incomplete")
print("candidate schema is complete")
PY
```

**STOP — obtain fresh operator approval immediately before the following Lockbox mutation.** Present
the candidate key names, the exact secret ID, current version metadata, the intended downstream
credential overlap/revocation sequence, expected price effect, and rollback version ID. Never put
the candidate's contents in the approval request.

### Success evidence

The reviewed current secret has zero versions. **STOP — obtain fresh operator approval immediately
before this initial, billable Lockbox mutation.** Create its first complete version through standard
input. `--payload -` keeps the payload out of argv; leave CLI debugging disabled. This initial
command intentionally has no base version:

```bash
yc lockbox secret add-version --id e6q85jjl76r45maigtfb \
  --payload - --format json < "$candidate_payload"
yc lockbox secret get --id e6q85jjl76r45maigtfb --format json
yc lockbox secret list-versions --id e6q85jjl76r45maigtfb --format json
```

If a version appears before this mutation, stop: do not use the initial command. Follow the later
rotation procedure with a freshly derived current-version ID. If the provider rejects the initial
command, do not substitute a guessed version or create a second secret. Revalidate the provider
contract and request a new approved Gate A command sheet.

Verify that the new version is current and its metadata reports the exact manifest key set. Resolve
one expected consumer through its approved path and complete the normal staging deployment check.
Record only the resulting non-secret version ID, key names, statuses, timestamps, secret ID, and
sanitized resolver stage. The exit trap removes the protected candidate on normal exit, validation
failure, rejected approval, interrupt, termination, and `add-version` failure. If cleanup reports a
retained path, immediately contain that local path under the device policy; do not copy it to a
ticket, artifact, cloud folder, or another host.

### Rollback

Before GitHub Secret cleanup, restore the prior workflow revision while its original secrets still
exist, then reapply the Lockbox workflow after the drill. After cleanup, rollback selects an earlier
complete, validated Lockbox version or restores workflow code while retaining the Lockbox reader;
it never re-establishes GitHub Secrets as a standing fallback. A failed resolver before the existing
deployment-promotion boundary leaves the VM and services unchanged.

### Non-disclosure

Do not display, `cat`, upload, commit, attach, or retain `candidate_payload`. The validator reports
only a fixed schema result. Never use `yc lockbox payload get` for a validation shortcut, and never
copy a base64 binary value into an issue or command argument.

## Local use

### Preflight

From the isolated worktree, confirm a supported local Docker endpoint, the checked-in `.venv`, and
an authenticated authorized human `yc` identity. Do not add a repository or worktree `.env` file.
The only supported launcher is:

```bash
make staging-local
```

It invokes `scripts/run-with-environment-secrets.py --environment staging --consumer local-web
--identity yc -- scripts/staging-local.sh --resolved`. The launcher applies the manifest's mandatory
local overrides: `DEBUG=True`, local-only hosts and bind address, `DB_HOST=db`, local database
identity, and empty deployment target/VM host.

### Success evidence

Expect only `[environment-secrets]` stage markers and
`[staging-local] stage=launch status=ready`. Confirm the running Compose services are the local
`db` and `web` services and the process is staging-capable but connected to the local database.
Confirm the resolver's temporary file no longer exists after it returns; do not retain or inspect it.

### Rollback

Stop the local services without deleting data volumes:

```bash
docker compose stop web db
```

If the resolver fails, it must not start the child service. Do not use this launcher to clone
staging data, deploy, SSH to staging, upload media, rotate a credential, or manage storage.
`make db-clone-staging` remains a separately chosen workflow.

### Non-disclosure

Never add the resolved file to Compose as `.env`, print it, or pass payload fields directly to
Docker. `FINDME_ENV_FILE` is a private path, not a value; do not log its path after cleanup.

## CI OIDC preflight

### Preflight

The GitHub job must have `environment: staging`, `contents: read`, and `id-token: write`. Its OIDC
claim must match the issuer, audience, repository, and subject in the inventory. The approved
resolver call for each projection is:

```text
python scripts/run-with-environment-secrets.py --environment staging --consumer local-web --identity github-oidc -- sh -c 'test -r "$FINDME_ENV_FILE"'
python scripts/run-with-environment-secrets.py --environment staging --consumer staging-deploy --identity github-oidc -- sh -c 'test -r "$FINDME_ENV_FILE"'
python scripts/run-with-environment-secrets.py --environment staging --consumer staging-remote-check --identity github-oidc -- sh -c 'test -r "$FINDME_ENV_FILE"'
python scripts/run-with-environment-secrets.py --environment staging --consumer staging-public-monitor --identity github-oidc -- sh -c 'test -r "$FINDME_ENV_FILE"'
```

The current workflows do not provide one universal non-mutating preflight job for all four
projections. Do not dispatch a deployment, storage probe, monitoring configuration, or benchmark as
a substitute. Gate B is blocked pending a separate reviewed repository task. That task must add and
review a manually dispatched no-op workflow revision containing exactly these resolver calls and no
payload output before it is dispatched.

The manifest allowed_workflows is not enforced by the current resolver. It remains a reviewed
inventory/intent record until that separate task also implements and tests workflow-ref enforcement;
do not claim it is a current authorization boundary.

### Success evidence

For each consumer, record only the GitHub run URL/ID, workflow path, expected OIDC subject, service
account ID, secret ID, version ID emitted by the resolver, consumer name, and sanitized success
marker. No token, JWT, IAM token, payload, or environment file may be an output or artifact.

### Rollback

A no-op preflight failure changes no VM state. Stop the cutover, retain the existing GitHub Secrets
for the planned migration window, and correct trust or manifest configuration through review. If
the provider cannot enforce the exact staging subject, remove or disable the federated credential
only after fresh operator approval and return for design revision.

### Non-disclosure

Use `test -r "$FINDME_ENV_FILE"` only; never use `cat`, `env`, `printenv`, an action output, or a
diagnostic artifact. OIDC and IAM tokens remain in memory and must not be echoed or passed as
workflow parameters.

## Rotation

### Preflight

Identify the inventory owner and provider-specific overlap/revocation rule for the affected
downstream credential. Build and validate a **complete** candidate: every manifest key must appear,
including unchanged values and the binary `VM_SSH_KEY`. Obtain the currently active non-secret
version ID from metadata and plan one staging deployment after activation. Payload rotation is not
identity revocation.

**STOP — obtain fresh operator approval immediately before creating a Lockbox version, changing a
downstream credential, or scheduling version destruction.** The approval must state the exact
secret `e6q85jjl76r45maigtfb`, the prior non-secret version ID, intended credential overlap, price
effect or unknown price effect, success check, and rollback point.

### Success evidence

Derive the actual current version ID from metadata immediately before the approved mutation; never
invent, reuse from an old record, or leave a shell variable unset. Then create the new complete
version through the protected candidate file:

```bash
previous_version_id=$(yc lockbox secret get --id e6q85jjl76r45maigtfb --format json | \
  .venv/bin/python -c '
import json
import sys
from pathlib import Path

secret = json.load(sys.stdin)
manifest = json.loads(
    Path("deploy/environment-secrets/staging.json").read_text(encoding="utf-8")
)
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

Verify the resulting current version metadata has the exact manifest key set, then verify the
resolver reads that new current version for the affected consumer. Complete one normal staging
deployment and its existing preflights. Running application processes retain prior values until the
next normal deployment/restart; record this fact without recording values.

### Rollback

During the bounded rollback period, do not claim an older version is current merely because it still
exists. Select an earlier complete validated version by its non-secret ID, then follow the exact
rollback mutation in the global “Rollback” procedure and redeploy normally. Do not combine entries
from two versions. Only after a separate new approved command, schedule an old version's destruction
with the non-secret ID and an explicit retention period:

```bash
yc lockbox secret schedule-version-destruction --id e6q85jjl76r45maigtfb \
  --version-id "$old_version_id" --pending-period 168h --format json
```

If destruction was scheduled before rollback completes, **STOP for separate fresh approval** before
cancelling only that schedule; do not delete or replace another version while containing the error:

```bash
yc lockbox secret cancel-version-destruction --id e6q85jjl76r45maigtfb \
  --version-id "$old_version_id" --format json
```

### Non-disclosure

Keep both candidate and previous payload values out of comparison output. Compare key names,
version IDs, statuses, timestamps, and resolver markers only. Never paste the changed credential,
base64 private key, or provider response body into a rotation record.

## Revocation

### Preflight

Determine whether the subject is a human reader or GitHub CI. Read the exact resource binding and,
for CI, identify the federated-credential ID from the dedicated service account list by matching the
reviewed federation `ajeula3gd46omgf9jiko` and staging subject. Do not guess the credential ID.

**STOP — obtain fresh operator approval immediately before changing IAM, deleting a federated
credential, or suspending the service account.** State the subject, exact resource, current and
intended access, availability impact, validation identity, and rollback command.

### Success evidence

For a human reader, remove only that resource-level binding; for CI, delete the matching federated
credential or suspend the dedicated service account only when the approved response requires it:

```bash
yc lockbox secret remove-access-binding --id e6q85jjl76r45maigtfb \
  --user-account-id "$revoked_user_account_id" --role lockbox.payloadViewer --format json
yc iam workload-identity federated-credential delete \
  --id "$federated_credential_id" --format json
```

Verify separately that the revoked identity fails to resolve a projection without displaying a
payload and an intended identity still resolves its permitted projection. Payload rotation is not
identity revocation and does not prove this result.

### Rollback

Restoring access is another IAM mutation. **STOP for fresh operator approval** before adding back
the exact resource-level `lockbox.payloadViewer` binding or recreating the exact federated
credential. Re-run the two-identity verification and record IDs/statuses only.

### Non-disclosure

Never test revocation by calling `yc lockbox payload get` or capturing a resolver environment file.
The permitted observation is a sanitized resolver failure/success stage, not a value, token, or
private key.

## Rollback

### Preflight

Classify the state before acting. Before GitHub Secret cleanup, the rollback target is the prior
workflow revision while its retained staging secrets still exist. After cleanup, the target is an
earlier complete validated Lockbox version or workflow code that retains the Lockbox reader. Confirm
the last known healthy image and the existing `deploy/apply-deployment.sh` promotion boundary.

For a post-cleanup Lockbox rollback, use `yc lockbox secret list-versions` metadata to select a
complete old version and assign its non-secret ID to `rollback_version_id`; verify it is non-empty.
Do not select a version from payload output, and do not treat a retained old version as the current
version.

**STOP — obtain fresh operator approval immediately before a deployment, Lockbox-version mutation,
or GitHub Secret mutation.** A resolver failure before the remote promotion boundary is not a reason
to SSH around the deployment workflow.

### Success evidence

After the approval, create a new current Lockbox version based exactly on the selected complete old
version. The empty JSON change array is supplied on standard input; it neither prints nor accepts a
payload value from a command argument:

```bash
test -n "$rollback_version_id"
printf "[]" | yc lockbox secret add-version --id e6q85jjl76r45maigtfb \
  --base-version-id "$rollback_version_id" --payload - --format json
yc lockbox secret get --id e6q85jjl76r45maigtfb --format json
yc lockbox secret list-versions --id e6q85jjl76r45maigtfb --format json
```

Verify the resulting current version has the exact key set from the manifest, resolve the expected
consumer without printing its environment file, and use the approved normal staging deployment
path. Then record the deployed image marker, health check result, selected non-secret version ID,
and secret-free workflow markers. Reapply the Lockbox workflow after a pre-cleanup drill and repeat
the successful deployment verification.

### Rollback

Restore the selected state described in preflight. Do not add a dual-read resolver, restore GitHub
Secrets as a standing second authority, or mix payload entries from versions. If a version was
scheduled for destruction by mistake, **STOP for separate fresh approval** before cancelling only
that schedule:

```bash
yc lockbox secret cancel-version-destruction --id e6q85jjl76r45maigtfb \
  --version-id "$old_version_id" --format json
```

Emergency GitHub Secret recreation needs a separately approved incident decision and removal after
recovery.

### Non-disclosure

Do not use a rollback to export a payload from either store. Keep deployment logs, runner process
diagnostics, retained files, and Git diffs free of payload values, token fragments, or shell tracing.

## Lost device or account recovery

### Preflight

Treat a lost device or account as an identity incident, not merely a secret-value incident. Contact
a surviving cloud organization administrator who can verify the affected human identity and the
separately maintained break-glass ownership record. This runbook deliberately does not name or
store those break-glass identities.

### Success evidence

The surviving cloud organization administrator restores the authorized human's access only after
the lost identity has been revoked from the exact Lockbox resource and a replacement identity is
verified with the sanitized resolver path. Record the revoked and restored identity IDs, approver,
timestamps, and success/failure codes, never values.

### Rollback

If replacement access is wrong or incomplete, remove the new resource-level binding after fresh
operator approval and continue recovery through the surviving administrator. Do not grant a
folder-wide role, create a service-account key, or use a copied payload as a recovery channel.

### Non-disclosure

Payload rotation is not identity revocation. Rotating a payload cannot make a lost identity unable
to read the new value while it still has payload-reader access. This procedure depends on separately
maintained break-glass ownership and does not claim EJ-018 runtime credential recovery.

## GitHub secret cleanup

### Preflight

Complete and evidence the local launch, CI OIDC preflight, one Lockbox staging deployment, and the
pre-cleanup rollback drill first. List only names and compare them to the inventory migration table:

```bash
gh secret list --repo peter-nikitin/photo-prjct --json name,updatedAt
gh secret list --repo peter-nikitin/photo-prjct --env staging --json name,updatedAt
gh variable list --repo peter-nikitin/photo-prjct --env staging --json name,updatedAt
```

Before deletion approval, use the name-only Environment-variable listing above to prove all required
destinations actually exist in `staging`. The exact required `staging` Environment variable-name set
is:

- `ALLOWED_HOSTS`
- `DB_NAME`
- `DB_USER`
- `GHCR_USERNAME`
- `VM_HOST`
- `VM_USER`

All six required Environment variables must be present before approval. If any is absent, stop: a
future tracked-default change first needs a reviewed inventory and workflow revision. Confirm
`GITHUB_TOKEN` remains GitHub issued and is not an Environment Secret.

**STOP — obtain fresh operator approval immediately before deleting any GitHub Secret.** The approval
must enumerate the exact source scope and secret names, confirm successful cutover and rollback
drill, and state the after-cleanup Lockbox-version rollback path.

### Success evidence

After approval, delete each migrated secret from the exact source scope, then repeat both name-only
secret listings and the Environment-variable listing. The 15 former repository secrets are:

```bash
gh secret delete ALLOWED_HOSTS --repo peter-nikitin/photo-prjct
gh secret delete DB_NAME --repo peter-nikitin/photo-prjct
gh secret delete DB_PASSWORD --repo peter-nikitin/photo-prjct
gh secret delete DB_USER --repo peter-nikitin/photo-prjct
gh secret delete GHCR_READ_TOKEN --repo peter-nikitin/photo-prjct
gh secret delete GHCR_USERNAME --repo peter-nikitin/photo-prjct
gh secret delete LETSENCRYPT_EMAIL --repo peter-nikitin/photo-prjct
gh secret delete MEDIA_S3_ACCESS_KEY_ID --repo peter-nikitin/photo-prjct
gh secret delete MEDIA_S3_SECRET_ACCESS_KEY --repo peter-nikitin/photo-prjct
gh secret delete PRIVATE_MEDIA_S3_ACCESS_KEY_ID --repo peter-nikitin/photo-prjct
gh secret delete PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY --repo peter-nikitin/photo-prjct
gh secret delete SECRET_KEY --repo peter-nikitin/photo-prjct
gh secret delete VM_HOST --repo peter-nikitin/photo-prjct
gh secret delete VM_SSH_KEY --repo peter-nikitin/photo-prjct
gh secret delete VM_USER --repo peter-nikitin/photo-prjct
```

The four former `staging` Environment secrets are:

```bash
gh secret delete PHOTO_PROCESSING_WORKER_TOKEN --repo peter-nikitin/photo-prjct --env staging
gh secret delete SELFIE_FEEDBACK_S3_ACCESS_KEY_ID --repo peter-nikitin/photo-prjct --env staging
gh secret delete SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY --repo peter-nikitin/photo-prjct --env staging
gh secret delete YANDEX_MONITORING_API_KEY --repo peter-nikitin/photo-prjct --env staging
```

Run all three name-only commands again. Require all 15 repository source names and all four staging
Environment source names to be absent. Require the six classified Environment variables
`ALLOWED_HOSTS`, `DB_NAME`, `DB_USER`, `GHCR_USERNAME`, `VM_HOST`, and `VM_USER` to remain. Record
only removed names and timestamps; do not request old values.

### Rollback

After deletion, use an earlier complete validated Lockbox version or restore workflow code with the
Lockbox reader. Do not repopulate GitHub Secrets as a standing fallback. Emergency recreation is an
incident-only decision requiring fresh approval and must be deleted again after recovery.

### Non-disclosure

Do not use `gh secret set --body`, `--env-file`, copied values, or a workflow artifact to recreate
or inspect a secret. GitHub's name-only list is the cleanup proof; `GITHUB_TOKEN` is job-scoped and
must never be written to Lockbox.

## Incident and non-disclosure

### Preflight

Stop the affected launch or deployment at the first sanitized resolver error. Preserve only the
stage, code, non-secret secret/version IDs, timestamp, consumer, workflow run URL/ID, and whether
the existing deployment-promotion boundary was reached. Determine whether the incident concerns
identity, provider availability, schema, temporary-file cleanup, downstream credential, or a
suspected disclosure.

### Success evidence

The incident record states containment owner, approved action, affected non-secret resource IDs,
sanitized validation result, and recovery decision. For a suspected disclosure, the accountable
inventory owner coordinates provider rotation and, if an identity is affected, resource-level IAM
or federation revocation. An already deployed service may keep running while Lockbox, IAM, or OIDC
is unavailable.

### Rollback

Use the applicable approved version or workflow rollback above. A cleanup failure is an error even
after child success: contain the reported private path locally, do not upload it, and request an
approved recovery action. Never restart, SSH into, or mutate the VM merely to probe Lockbox.

### Non-disclosure

Do not attach raw CLI output, payloads, IAM/OIDC tokens, SSH keys, database passwords, Object
Storage keys, GHCR tokens, monitoring API keys, environment dumps, artifacts, screenshots, or
temporary files to an incident. Redact credential-like content before sharing and limit the incident
to the sanctioned evidence fields above.

## References

- [Environment secrets inventory](environment-secrets-inventory.md)
- [Environment-scoped Lockbox secrets design](../superpowers/specs/2026-08-07-environment-scoped-lockbox-secrets-design.md)
- [Implementation plan](../plans/2026-08-07-environment-scoped-lockbox-secrets.md)
- [ADR 0003](../adr/0003-docker-compose-yandex-cloud.md),
  [ADR 0005](../adr/0005-promote-images-through-staging.md), and
  [ADR 0026](../adr/0026-use-lockbox-for-environment-secrets.md)
- [Yandex Lockbox version management](https://yandex.cloud/en/docs/lockbox/operations/secret-version-manage)
- [Yandex workload identity federation](https://yandex.cloud/en/docs/iam/operations/wlif/setup-wlif)
- [GitHub CLI environment secrets](https://cli.github.com/manual/gh_secret_delete)
