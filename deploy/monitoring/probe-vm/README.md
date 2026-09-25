# Host public probe

This package runs the canonical HTTPS check on existing image-origin VM
`epdf6696opq3ock91pih` (`10.129.0.21`). It writes Monitoring metrics using the
VM's existing metadata identity. It opens no port and does not touch containers.
The host requires Python 3 and systemd. The timer starts within one minute after
boot (immediately when started after that point) and repeats every five minutes.
A failed check is recorded as a failed oneshot invocation; the timer keeps running.

## Install an exact reviewed commit

After the operational approval recorded by the controller, dispatch on main:

```sh
gh workflow run deploy-public-probe.yml --ref main -f action=install -f deployment_sha=<40-character-reviewed-SHA>
```

The workflow checks out that exact SHA. `package.py` reads the four deployment
files from that commit's git objects, adds their SHA-256 manifest, and sends only
that archive through the existing pinned SSH bastion. The runtime receives no SSH
key, Lockbox file, or Monitoring API key. The installer checks the manifest and
Python syntax before changing systemd, serializes host installs, and installs
root-owned read-only package files under `/opt/findme-public-probe/releases/`.
Reinstalling the same package preserves `previous`. An activation failure restores
the old package and timer; a first-install failure leaves the timer disabled.

For an already transferred and extracted package, the equivalent host command is:

```sh
sudo -n python3 /path/to/extracted/install.py install --package /path/to/extracted
```

## Inspect, disable, or roll back

Use the same pinned transport for rollback or disable even when personal SSH access
is unavailable. `deployment_sha` selects the reviewed management code; rollback
restores the package recorded by the host's `previous` link:

```sh
gh workflow run deploy-public-probe.yml --ref main -f action=disable -f deployment_sha=<40-character-reviewed-SHA>
gh workflow run deploy-public-probe.yml --ref main -f action=rollback -f deployment_sha=<40-character-reviewed-SHA>
```

Alternatively, run on that same VM:

```sh
sudo systemctl status findme-public-probe.timer findme-public-probe.service
sudo journalctl -u findme-public-probe.service --since '-15 minutes' --no-pager
sudo -n python3 /opt/findme-public-probe/current/install.py disable
sudo -n python3 /opt/findme-public-probe/current/install.py rollback
```

Rollback requires a retained `previous` release and re-enables the timer. For the
first installation, disable is the rollback. Retained release directories are
small and are not deleted automatically. A successful installer result proves
that the timer is active, not that a metric was accepted or an email delivered;
verify those separately. Preserve the old GitHub schedule until two fresh VM
probe points and the live cutover gate have been confirmed.
