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

## Corrected host installation and API freshness contract

PR #218 merged as `d8b755b0f5647ae8d385531037b3fb2018f0dc70`; CI and final local gates passed.
The reviewed host package `6c0f15b2142f5f93e4f811174a2c62c64da851eb`, source SHA256
`8c8250c8010c8c930a26ba8e2c5480c18d31ca46057ffcec344cdf189f0fa10e`, installed successfully
on both VMs. Commerce exporter returned HTTP200, alive=1, oldest-ready age=0 under its actual unit.
Public exporter and both agents were active. Cloud queries subsequently returned the expected
public/Linux/HTTP/Commerce series. Retained host rollback backups:

- Canonical: `/var/lib/findme-prometheus/backups/1790576803732274212-6c0f15b2142f5f93e4f811174a2c62c64da851eb`.
- Public: `/var/lib/findme-prometheus/backups/1790576811443339287-6c0f15b2142f5f93e4f811174a2c62c64da851eb`.

An owner-approved GitHub OIDC read-only run reached cloud query preflight; it failed on the public
instant selector. Five-minute public observations appeared in historical windows while the instant
selector periodically returned no points. Live `timestamp(selector)` returned evaluation time,
including an explicit historical query at an off-grid timestamp; it is not source-sample-age proof
in this backend. Instant range-vector queries returned `resultType=matrix` with actual source
points: public on the five-minute grid, Commerce/CPU on the one-minute grid.

Freshness preflight must inspect the latest actual matrix point for each series, reject missing,
nonfinite, stale and future samples, and preserve computed vector/rate checks. Public age bound is
600 seconds to match the existing ten-minute missing-observation rule; collection remains300s.
Other raw observation age bounds remain120s. Alert application, full dashboard read-back and new
notification acceptance remain pending. Native alerts and worker control remain active.

The live API also treats a selector with an empty `{}` filter followed by a range as an instant
vector: `findme_http_requests_total{}[120s]` returned evaluation-time values, whereas the same
selector without `{}` returned a matrix containing 22 series with source timestamps. The same
behavior was reproduced with the public metric. Generated selectors omit empty filters; labeled
selectors and the derived HTTP 5xx filter retain valid braces. No vector fallback is accepted for
raw freshness checks.

## Initial cloud activation

The corrected live preflight passed with actual source timestamps for every configured metric.
Operator-approved apply wrote Alertmanager routing and the exact eleven rules to
`mon0c97qv2s5uju1ark8`. All eleven evaluation snapshots subsequently had state `OK`, empty errors
and fresh evaluation timestamps; `ALERTS{project="findme-photo"}` was empty. The dashboard Get
returned fourteen widgets matching every owned desired field, etag `3`. Initial control backup:
`/private/tmp/findme-monitoring-backup-b78ef299-20260928`.

The first apply reported an SDK wait error after successful dashboard mutation: Monitoring Update
returns a synchronous operation, while the generic SDK waiter polls the global OperationService,
which rejected the Monitoring operation ID. Polling OperationService on the Monitoring endpoint
returned `UNIMPLEMENTED`. The control validates the completed Update response and preserves
mandatory fresh Dashboard Get/read-back in both apply and restore; incomplete or errored responses
fail closed. No application container or native timer was changed during cloud activation.

The bounded notification drill retained exactly eleven rules and changed only the first existing
rule's expression/summary. `PublicServiceUnavailable` with an explicit synthetic-test summary
was observed in `ALERTS` with `alertstate="firing"`; the public health response remained `ok`.
After about two minutes its condition was cleared, then the exact original Git rule content was
restored and read back. Native public observation alerts remained active throughout. The test
created no additional rule, workspace or VM. Operator receipt of firing/recovery emails and final
exact-main GitHub reconciliation remain acceptance observations.

## Exact-main GitHub acceptance and TLS window correction

PR #219 merged as `4870552382e9112e007812304016329754b11ab4` after full GREEN CI. The exact
merge's automatic application Deploy run `36390545954` completed cancelled; its Deploy job had
zero steps. GitHub Monitoring run `36390566055` used the protected environment and successfully
exchanged its OIDC identity, but preflight stopped at `expression calculation failed: tls` before
any mutation/backup. Its raw source freshness checks passed.

A five-minute `min_over_time` TLS window can be empty between five-minute polls plus delivery lag,
even while the latest actual observation meets the approved 600-second freshness bound. TLS
preflight and certificate alert both use ten minutes; the fourteen-day threshold, 300s collection
cadence and eleven-rule count do not change. Sparse-point rule fixtures protect the expiry
condition and healthy certificate behavior during that gap. No host reinstall is needed.

The operator confirmed receipt of the synthetic firing email. Current alert condition and ALERTS
subsequently cleared after exact original rule restoration. Recovery email receipt remains pending.
