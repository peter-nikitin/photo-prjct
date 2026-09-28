# Monitoring activation evidence

## Access foundation — verified 2026-09-28

Explicit operator approval covered access changes and subsequent merge of PR #217.
Profile `default`, cloud `b1gmcsmr51o5kvp86l55`, folder `b1g2qttgfhb4gdunvlge`.

- Service account `aje3t70qka1dtc09k5ic`, name `findme-monitoring-ci`; folder role `monitoring.editor`.
- Existing federation `ajeula3gd46omgf9jiko` reused without changes.
- Credential `ajeprb31m2nhu6pbj2gn`, subject `repo:peter-nikitin/photo-prjct:environment:monitoring`.
- GitHub environment `monitoring`, ID `22896981301`: reviewer `peter-nikitin`, self approval allowed,
  branch policy ID `61254397` limited to `main`.
- No long-lived keys, Lockbox rights, VM restart or metric writes in this step.

The CLI's credential create command advertised a name selector but did not resolve it; the first
attempt returned `service_account_id: Field is required` and created nothing. Retrying with the
verified service-account ID succeeded. The returned credential was read back.

## Remaining live gates

Both live Unified Agent configurations were read and preserved as the rendering inputs. Canonical
uses `/etc/yc/unified_agent/config.yml` and `unified_agent.service` (26.09.10/21106904); image origin
uses `/etc/yandex/unified_agent/config.yml` and `unified-agent.service` (26.09.01/21010966).
The additive rendered configuration passed the installed agent's `check-config` on each VM;
neither active configuration nor service state was changed. Host installation and rollback select
these exact paths and units by role.

Personal SSH failed on image origin. An explicitly approved temporary OS Login export did not
provide access because OS Login is not enabled on that VM; its local credentials were removed.
The existing pinned deployment route (`deploy` on canonical, then `yc-user` on private image origin)
worked using the existing deployment credential, without IAM, metadata or SSH user changes.

Repository offline validation had missed an SDK registry limitation: Monitoring protobufs are
present but `SDK.client(DashboardServiceStub)` raises `Unknown service`. The corrected construction
passed an actual read-only Dashboard Get: dashboard `fbeketud0mdaupj43of6`, expected folder,
14 widgets, etag `2`. The workspace rules API returned an empty file list; `up` returned no series.

Ingestion on the two VMs, fresh workspace series, OIDC check/apply, rule evaluation and notification
acceptance are not yet verified. Existing native observation alerts remain active. Retire duplicates
only after new firing and recovery notifications are confirmed; native worker control remains active.

## First activation attempt and rollback

The operator approved the reviewed live activation on 2026-09-28. Both bounded installers passed,
including identity/hash/config checks and active units. Public exporter returned HTTP 200 with
success=1 and a finite TLS observation. Canonical exporter returned HTTP 503: its root service had
an empty capability set and could not read the deployment-owned mode-0600 `.env` required by the
existing Docker Compose health observation. The same collector succeeded under ordinary root.
A read-only reproduction with `setpriv --bounding-set=-all --no-new-privs` failed with permission
denied; retaining only `dac_read_search` passed. No application or collector behavior was changed.

Both installations were rolled back using their recorded transaction backups before alert apply.
Canonical config returned to SHA256 `2afc83b8ab1ae382bae28d950ab22dd2e198a466668524c44144c264462108bd`;
the agent remained active and the exporter unit was absent. The canonical privileged invocation
uses the existing personal `petrnikitin` SSH route; the `deploy` account's sudo policy rejected the
installer before any configuration mutation. Image origin uses its existing deployment jump route.

The canonical exporter needs only `CAP_DAC_READ_SEARCH` within the existing root/Docker observation
boundary. Public exporter retains an empty capability set. File modes, IAM, SSH metadata and
application containers do not change. Fresh ingestion, rule evaluation and notification acceptance
remain live gates after the corrected installation.
