# Canonical VM Commerce monitoring

The host systemd timer observes Commerce once every 60 seconds through `docker compose exec -T web`.
It reads the existing worker advisory lock and oldest ready work, then writes two custom DGAUGE
metrics using the VM service account metadata token. Empty ready work produces age zero.
A valid observation of a dead worker publishes liveness zero; command, database, JSON, timeout,
or metric transport failures print only a bounded generic error and never invent healthy data.
No order, photo, customer identifiers or credentials are included in the metrics or journal.

## Activation prerequisites

Activate only after explicit operational and current pricing approval. The deployed image must
include `commerce_worker_health --format json`, the canonical Commerce worker must intentionally
be enabled, Docker Compose and Python 3 must be installed, and the VM service account must have
`monitoring.editor` for the supplied folder. A deliberately disabled worker would publish zero;
keep this timer disabled until the worker is enabled. Docker socket access remains host root only.
Normal application deployment neither installs nor activates this timer and retains an installed timer.

The ordinary deployment payload does not include `scripts/`. Prepare a bounded bundle from the
reviewed committed SHA on the local machine, then upload it through the existing personal SSH key:

```sh
git archive --format=tar REVIEWED_SHA scripts/monitor_commerce.py scripts/monitor_public_health.py \
  deploy/monitoring/commerce-vm | gzip > /private/tmp/commerce-monitoring.tar.gz
shasum -a 256 /private/tmp/commerce-monitoring.tar.gz
scp /private/tmp/commerce-monitoring.tar.gz petrnikitin@111.88.151.64:/tmp/commerce-monitoring.tar.gz
```

On the canonical VM, verify the archive digest against the local output, extract into a fresh
root-owned directory and invoke that bundle installer. These are explicit operational steps:

```sh
sha256sum /tmp/commerce-monitoring.tar.gz
sudo install -d -m 0700 /root/commerce-monitoring-reviewed
sudo tar -xzf /tmp/commerce-monitoring.tar.gz -C /root/commerce-monitoring-reviewed
```

Replace the two values with the verified folder ID and
canonical deployment directory; the directory contains `.env` and both Compose files.

```sh
sudo /usr/bin/python3 /root/commerce-monitoring-reviewed/deploy/monitoring/commerce-vm/install.py \
  --folder-id FOLDER_ID --deploy-root /absolute/canonical/deploy/root
```

The installer copies the reviewed collector and existing public metric writer into
`/usr/local/lib/findme-commerce-monitoring`, creates root-owned configuration mode 0600 and scripts
mode 0644, installs the units, stops any previous collection, and performs one successful metric
write before enabling the timer. It restores prior files and enabled/active states on failure.
The first test observation can remain in Monitoring even if later timer activation fails.
The 30 second observation timeout plus two 10 second HTTP bounds fit the 55 second service bound.

## Verify

```sh
sudo systemctl is-enabled findme-commerce-monitoring.timer
sudo systemctl is-active findme-commerce-monitoring.timer
sudo systemctl list-timers findme-commerce-monitoring.timer
sudo systemctl status findme-commerce-monitoring.service
sudo journalctl -u findme-commerce-monitoring.service --since '10 minutes ago'
```

In the Yandex Monitoring query console, use the actual folder ID explicitly:

```text
{folderId="FOLDER_ID", service="custom", name="commerce_worker_alive", check="canonical-commerce"}
{folderId="FOLDER_ID", service="custom", name="commerce_oldest_ready_age_seconds", check="canonical-commerce"}
```

Require fresh points roughly every minute, liveness one for the enabled worker, and age zero for
an empty ready queue. Compare against the read-only management command for a non-empty queue.
Missing observation is **No data**, not proof that the worker died. Liveness monitoring treats
missing data as requiring attention; the age alert cannot diagnose queue age without an observation.
See `deploy/monitoring/alerts.md` for the alert selectors, thresholds and No data policy.

## Disable and recovery

```sh
sudo systemctl disable --now findme-commerce-monitoring.timer
sudo systemctl stop findme-commerce-monitoring.service
```

The service stop bounds an in-flight collector. Verify the timer is disabled/inactive and subsequent
metric points cease. Disable before investigating collector failures; examine the worker independently.
For a failed installation, verify the prior files and timer state were restored. If restoration itself
fails because systemd/filesystem operations are unavailable, keep the timer disabled and repair those
operations before retrying. To return to an earlier reviewed collector, run that checkout's installer
with the same verified folder ID and deployment directory. No application or queue data is changed.
