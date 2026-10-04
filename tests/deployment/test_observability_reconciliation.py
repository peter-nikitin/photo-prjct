import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def classifier():
    spec = importlib.util.spec_from_file_location("release", ROOT / "deploy/classify-release.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.mark.parametrize(
    ("paths", "selected"),
    [
        (["deploy/monitoring/prometheus/rules.yml"], {"cloud_changed"}),
        (["deploy/monitoring/prometheus/exporter.py"], {"canonical_changed", "public_changed"}),
        (["deploy/selfie-observability/root-helper.sh"], {"canonical_changed"}),
        (["deploy/monitoring/probe-vm/install.py"], {"probe_changed"}),
        (
            ["deploy/image-origin/monitoring/unified-agent.yml.template"],
            {"image_monitoring_changed"},
        ),
        (["docs/architecture.md"], set()),
        (["src/backend/processing/views.py"], set()),
        (["src/worker/photo_worker/client.py"], set()),
    ],
)
def test_observability_selection(paths, selected):
    module = classifier()
    assert {key for key, value in module.classify_observability(paths).items() if value} == selected
    if selected:
        assert not any(module.classify(paths).values())


def workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


def test_cloud_automatically_applies_exact_push_sha_and_retains_backup_on_failure():
    package = workflow("monitoring.yml")
    assert package.get("on", package.get(True))["push"]["branches"] == ["main"]
    reconcile = package["jobs"]["reconcile"]
    assert "cloud_changed" in reconcile["if"]
    assert reconcile["environment"] == "monitoring"
    assert (
        reconcile["env"]["MONITORING_REVISION"]
        == "${{ github.event_name == 'push' && github.sha || inputs.revision }}"
    )
    assert (
        reconcile["env"]["MONITORING_ACTION"]
        == "${{ github.event_name == 'push' && 'apply' || inputs.action }}"
    )
    backup = next(
        step
        for step in reconcile["steps"]
        if step.get("with", {}).get("name", "").startswith("monitoring-backup-")
    )
    assert "always()" in backup["if"]
    assert "MONITORING_REVISION" in backup["with"]["name"]
    assert package["concurrency"]["queue"] == "max"


def test_canonical_host_is_separate_and_never_runs_mutable_checkout_as_root():
    jobs = workflow("deploy.yml")["jobs"]
    host = jobs["reconcile-observability-host"]
    assert "canonical_changed" in host["if"]
    assert host["needs"] == ["classify-release"]
    assert host["permissions"]["id-token"] == "write"
    run = next(step for step in host["steps"] if "run" in step)
    assert "reconcile-observability" in run["run"]
    assert run["env"]["RELEASE_SHA"] == "${{ github.sha }}"
    classify = next(
        step["run"] for step in jobs["classify-release"]["steps"] if step.get("id") == "classify"
    )
    assert 'pause_reason="Commit ${GITHUB_SHA} changes' not in classify


def test_public_host_automation_uses_existing_workflow_and_oidc_boundaries():
    public = workflow("deploy-public-probe.yml")
    assert public.get("on", public.get(True))["push"]["branches"] == ["main"]
    assert "public_changed" in public["jobs"]["reconcile-public-observability"]["if"]
    origin = workflow("deploy-image-origin.yml")
    assert origin.get("on", origin.get(True))["push"]["branches"] == ["main"]
    steps = origin["jobs"]["deploy-image-origin"]["steps"]
    deploy = next(step for step in steps if step.get("name") == "Deploy isolated image origin")
    assert "github.event_name == 'push' && 'true'" in deploy["env"]["IMAGE_ORIGIN_MONITORING_ONLY"]


def host_module():
    spec = importlib.util.spec_from_file_location(
        "host", ROOT / "deploy/observability/reconcile.py"
    )
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_reconciliation_rolls_back_even_when_reviewed_dependency_import_fails(
    tmp_path, monkeypatch
):
    module = host_module()
    monkeypatch.setattr(module, "STATE", tmp_path)
    monkeypatch.setattr(module, "configuration", lambda: {"role": "public"})
    monkeypatch.setattr(module.os, "geteuid", lambda: tmp_path.stat().st_uid)
    monkeypatch.setattr(module, "fetch_source", lambda *args: {"file": "hash"})
    monkeypatch.setattr(module, "snapshot", lambda *args: {"files": {}, "units": {}})
    from unittest.mock import Mock

    rollback = Mock()
    monkeypatch.setattr(module, "restore", rollback)

    def failed(*args):
        raise ImportError("missing reviewed dependency")

    monkeypatch.setattr(module, "apply", failed)
    tmp_path.chmod(0o700)
    # Test uses a simulated root state owner; no privileged operation is executed.
    original = module.Path.lstat

    def simulated_owner(path):
        details = original(path)
        from types import SimpleNamespace

        return SimpleNamespace(st_mode=details.st_mode, st_uid=0)

    monkeypatch.setattr(module.Path, "lstat", simulated_owner)
    with pytest.raises(ImportError):
        module.reconcile_transaction(
            "a" * 40, {"role": "public"}, tmp_path, tmp_path / "repository.git"
        )
    rollback.assert_called_once()
    assert not (tmp_path / "receipt.json").exists()


@pytest.mark.parametrize("revision", ["main", "A" * 40, "a" * 39, "a" * 40 + ";id"])
def test_privileged_helper_rejects_untrusted_revision_before_network(
    revision, tmp_path, monkeypatch
):
    module = host_module()
    from unittest.mock import Mock

    command = Mock()
    monkeypatch.setattr(module, "run", command)
    with pytest.raises(ValueError):
        module.fetch_source(revision, "canonical", tmp_path, tmp_path / "repo")
    command.assert_not_called()


def test_host_fetch_checks_main_ancestry_and_rejects_symlink_manifest(tmp_path, monkeypatch):
    module = host_module()
    from unittest.mock import Mock

    command = Mock(side_effect=["", "", "", "120000 blob " + "b" * 40 + "\t" + module.COMMON[0]])
    monkeypatch.setattr(module, "run", command)
    with pytest.raises(ValueError, match="non-regular"):
        module.fetch_source("a" * 40, "public", tmp_path, tmp_path / "repo")
    assert command.call_args_list[1].args[-2:] == (
        module.REPOSITORY,
        "+refs/heads/main:refs/heads/main",
    )
    assert command.call_args_list[2].args[-4:] == (
        "merge-base",
        "--is-ancestor",
        "a" * 40,
        "refs/heads/main",
    )


def test_combined_release_waits_for_host_reconciliation_before_runtime_package_verification():
    deploy = workflow("deploy.yml")["jobs"]["deploy"]
    assert "reconcile-observability-host" in deploy["needs"]
    assert "always()" in deploy["if"]
    assert "needs.build.result == 'success'" in deploy["if"]
    assert "needs.reconcile-observability-host.result == 'success'" not in deploy["if"]


def test_public_host_writes_share_one_queue_with_image_origin_repairs():
    origin = workflow("deploy-image-origin.yml")["jobs"]["deploy-image-origin"]["concurrency"]
    public = workflow("deploy-public-probe.yml")["jobs"]
    for name in ("deploy-public-probe", "reconcile-public-observability"):
        assert public[name]["concurrency"] == origin
    assert origin["cancel-in-progress"] is False
    assert origin["queue"] == "max"


def test_host_rollback_restores_saved_bytes_modes_and_prior_units(tmp_path, monkeypatch):
    module = host_module()
    target = tmp_path / "owned"
    target.write_text("new")
    backup = tmp_path / "backup"
    saved = backup / "files" / str(target).lstrip("/")
    saved.parent.mkdir(parents=True)
    saved.write_text("old")
    from unittest.mock import Mock

    command = Mock()
    monkeypatch.setattr(module, "run", command)
    module.restore(
        {
            "files": {str(target): 0o600},
            "units": {
                "collector.service": {"is-active": True, "is-enabled": False},
            },
        },
        backup,
    )
    assert target.read_text() == "old"
    assert target.stat().st_mode & 0o777 == 0o600
    assert [call.args for call in command.call_args_list] == [
        ("systemctl", "daemon-reload"),
        ("systemctl", "disable", "collector.service"),
        ("systemctl", "restart", "collector.service"),
    ]


def test_combined_package_selects_independent_application_and_observability_paths():
    module = classifier()
    paths = [
        "src/backend/processing/views.py",
        "src/worker/photo_worker/client.py",
        "deploy/monitoring/prometheus/rules.yml",
    ]
    assert module.classify(paths) == {
        "web_changed": True,
        "worker_changed": True,
        "worker_base_changed": False,
    }
    assert module.classify_observability(paths)["cloud_changed"]


def test_host_foundation_rejects_wrong_vm_before_any_install(tmp_path, monkeypatch):
    import json
    from unittest.mock import Mock

    module = host_module()
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "role": "public",
                "instance_id": "instance123",
                "folder_id": "folder123",
                "workspace_id": "workspace123",
            }
        )
    )
    monkeypatch.setattr(module, "root_owned", lambda path: None)
    response = Mock()
    response.read.return_value = b"other-instance"
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(module, "urlopen", lambda *args, **kwargs: context)
    with pytest.raises(ValueError, match="VM identity mismatch"):
        module.configuration(config)


def test_reviewed_helper_is_exact_allowlisted_blob_and_part_of_both_host_backups():
    module = host_module()
    assert "deploy/observability/reconcile.py" in module.COMMON
    for role in ("canonical", "public"):
        assert Path("/usr/local/sbin/findme-observability-reconcile") in module.managed(role)[0]


def test_manual_cloud_recovery_checks_ancestry_and_checks_out_reviewed_sha():
    reconcile = workflow("monitoring.yml")["jobs"]["reconcile"]
    first = reconcile["steps"][0]
    assert first["with"]["ref"] == "${{ env.MONITORING_REVISION }}"
    bind = next(
        step["run"]
        for step in reconcile["steps"]
        if step.get("name") == "Bind to exact main revision"
    )
    assert 'git merge-base --is-ancestor "$MONITORING_REVISION" "$GITHUB_SHA"' in bind


def test_selfie_verify_failure_rolls_back_armed_transaction_before_outer_restore(monkeypatch):
    module = host_module()
    from unittest.mock import Mock

    command = Mock(side_effect=["", RuntimeError("verification failed"), ""])
    monkeypatch.setattr(module, "run", command)
    helper = Path("/usr/local/sbin/findme-selfie-observability")
    with pytest.raises(RuntimeError, match="verification failed"):
        module.install_selfie(helper)
    assert [call.args for call in command.call_args_list] == [
        (str(helper), "install"),
        (str(helper), "verify"),
        (str(helper), "rollback"),
    ]


def test_failed_selfie_rollback_is_visible_and_never_commits(monkeypatch):
    module = host_module()
    from unittest.mock import Mock

    command = Mock(
        side_effect=["", RuntimeError("verification failed"), RuntimeError("rollback failed")]
    )
    monkeypatch.setattr(module, "run", command)
    helper = Path("/usr/local/sbin/findme-selfie-observability")
    with pytest.raises(RuntimeError, match="rollback failed"):
        module.install_selfie(helper)
    assert all(call.args != (str(helper), "commit") for call in command.call_args_list)


def test_complete_observability_package_never_publishes_application_images():
    module = classifier()
    paths = [
        ".github/workflows/deploy.yml",
        "deploy/run-remote.sh",
        ".github/workflows/monitoring.yml",
        ".github/workflows/deploy-image-origin.yml",
        ".github/workflows/deploy-public-probe.yml",
        "deploy/classify-release.py",
        "deploy/observability/reconcile.py",
        "deploy/monitoring/prometheus/rules.yml",
        "deploy/monitoring/prometheus/dashboard.json",
    ]
    assert not any(module.classify(paths).values())
    assert module.classify_observability(paths)["canonical_changed"]
    selected = module.classify(paths + ["src/backend/processing/views.py", "Dockerfile.worker"])
    assert selected == {"web_changed": True, "worker_changed": True, "worker_base_changed": False}


def test_old_entrypoint_uses_new_revision_inventory_and_logic(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    old = host_module()
    tmp_path.chmod(0o700)
    monkeypatch.setattr(old, "STATE", tmp_path)
    monkeypatch.setattr(old, "configuration", lambda: {"role": "public"})
    original_lstat = old.Path.lstat
    monkeypatch.setattr(
        old.Path,
        "lstat",
        lambda path: SimpleNamespace(
            st_uid=0,
            st_mode=original_lstat(path).st_mode,
        ),
    )
    content = (ROOT / "deploy/observability/reconcile.py").read_text()
    content = content.replace(
        'STATE = Path("/var/lib/findme-observability-reconcile")',
        f"STATE = Path({str(tmp_path)!r})",
    )
    content += """
COMMON += ("new-reviewed-file.py",)
def fetch_source(revision, role, source, repository):
    (source / "new-reviewed-file.py").write_text("new-source")
    return {name: "reviewed" for name in COMMON}
def managed(role):
    return [Path("/new-owned-file")], ["new-owned.service"]
def snapshot(files, units, backup):
    inventory = {"files": list(map(str, files)), "units": units}
    (backup / "new-inventory.json").write_text(json.dumps(inventory))
    return {"files": {}, "units": {}}
def apply(source, config, revision):
    assert (source / "new-reviewed-file.py").read_text() == "new-source"
    with (STATE / "lock").open("a") as contender:
        try:
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise AssertionError("bootstrap lock was bypassed")
    (STATE / "new-behavior").write_text(revision)
"""

    def trusted_helper(revision, source, repository):
        target = source / "deploy/observability/reconcile.py"
        target.parent.mkdir(parents=True)
        target.write_text(content)
        return target

    monkeypatch.setattr(old, "fetch_helper", trusted_helper, raising=False)
    monkeypatch.setattr(old, "fetch_source", lambda *args: {})
    monkeypatch.setattr(old, "snapshot", lambda *args: {})
    monkeypatch.setattr(old, "apply", lambda *args: pytest.fail("old orchestration executed"))
    old.reconcile("a" * 40)
    assert (tmp_path / "new-behavior").read_text() == "a" * 40
    receipt = json.loads((tmp_path / "receipt.json").read_text())
    assert "new-reviewed-file.py" in receipt["manifest"]
    inventory = json.loads((Path(receipt["backup"]) / "new-inventory.json").read_text())
    assert inventory == {"files": ["/new-owned-file"], "units": ["new-owned.service"]}


def wait_module():
    spec = importlib.util.spec_from_file_location(
        "host_wait", ROOT / "deploy/observability/wait-host-releases.py"
    )
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def trusted_run(workflow_name):
    return {
        "id": 42,
        "head_sha": "a" * 40,
        "head_branch": "main",
        "event": "push",
        "path": ".github/workflows/" + workflow_name,
        "repository": {"full_name": "peter-nikitin/photo-prjct"},
        "status": "completed",
    }


def test_cloud_wait_requires_same_sha_host_job_success_not_application_run_success():
    module = wait_module()
    calls = []

    def api(path):
        calls.append(path)
        if "/workflows/" in path:
            run = trusted_run("deploy.yml")
            run["conclusion"] = "failure"  # Unrelated application job failure is independent.
            return {"workflow_runs": [run]}
        return {
            "jobs": [
                {
                    "name": "Reconcile canonical observability",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        }

    module.wait_for_hosts("a" * 40, {"canonical_changed": True}, api=api)
    assert "head_sha=" + "a" * 40 in calls[0]
    assert "event=push" in calls[0]


def test_cloud_wait_host_failure_is_visible():
    module = wait_module()

    def api(path):
        if "/workflows/" in path:
            return {"workflow_runs": [trusted_run("deploy.yml")]}
        return {
            "jobs": [
                {
                    "name": "Reconcile canonical observability",
                    "status": "completed",
                    "conclusion": "failure",
                }
            ]
        }

    with pytest.raises(module.HostReleaseError, match="failure"):
        module.wait_for_hosts("a" * 40, {"canonical_changed": True}, api=api)


def test_cloud_wait_without_host_changes_never_contacts_actions():
    module = wait_module()
    module.wait_for_hosts("a" * 40, {}, api=lambda path: pytest.fail("unneeded API request"))


def test_cloud_wait_missing_run_or_other_sha_times_out_without_false_success():
    module = wait_module()
    elapsed = [0]

    def api(path):
        run = trusted_run("deploy.yml")
        run["head_sha"] = "b" * 40
        return {"workflow_runs": [run]}

    def sleep(seconds):
        elapsed[0] += seconds

    with pytest.raises(module.HostReleaseError, match="timed out"):
        module.wait_for_hosts(
            "a" * 40,
            {"canonical_changed": True},
            api=api,
            timeout=20,
            clock=lambda: elapsed[0],
            sleep=sleep,
        )


def test_cloud_workflow_waits_for_selected_host_jobs_before_any_apply():
    package = workflow("monitoring.yml")
    outputs = package["jobs"]["classify"]["outputs"]
    for component in ("canonical", "public", "probe", "image_monitoring"):
        assert component + "_changed" in outputs
    steps = package["jobs"]["reconcile"]["steps"]
    wait = next(
        step for step in steps if step.get("name") == "Wait for same-SHA host reconciliation"
    )
    assert wait["if"] == "github.event_name == 'push'"
    assert wait["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "wait-host-releases.py" in wait["run"]
    assert steps.index(wait) < next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Check or apply exact reviewed configuration"
    )


def test_fresh_samples_retry_until_new_host_metrics_arrive():
    module = wait_module()
    attempts = []

    class NotFresh(Exception):
        pass

    def check():
        attempts.append(True)
        if len(attempts) < 3:
            raise NotFresh("new metric absent")

    module.wait_for_samples(check, NotFresh, timeout=20, sleep=lambda seconds: None)
    assert len(attempts) == 3


def test_missing_fresh_samples_fail_with_bounded_visible_timeout():
    module = wait_module()
    elapsed = [0]

    class NotFresh(Exception):
        pass

    def check():
        raise NotFresh("new metric absent")

    def sleep(seconds):
        elapsed[0] += seconds

    with pytest.raises(module.HostReleaseError, match="fresh samples"):
        module.wait_for_samples(check, NotFresh, timeout=20, clock=lambda: elapsed[0], sleep=sleep)
    assert elapsed[0] == 20


def test_cloud_workflow_fresh_sample_barrier_is_after_tools_before_apply():
    steps = workflow("monitoring.yml")["jobs"]["reconcile"]["steps"]
    names = [step.get("name") for step in steps]
    sample = next(
        step
        for step in steps
        if step.get("name") == "Wait for fresh samples after host reconciliation"
    )
    assert names.index("Prepare isolated tools and foundation inputs") < steps.index(sample)
    assert steps.index(sample) < names.index("Check or apply exact reviewed configuration")
    assert "github.event_name == 'push'" in sample["if"]
    assert "samples" in sample["run"]
    assert "--oidc-config /tmp/monitoring-oidc.json" in sample["run"]


@pytest.mark.parametrize("suffix,success", [("@main", True), ("@feature", False)])
def test_documented_workflow_run_path_accepts_only_main_suffix(suffix, success):
    module = wait_module()
    elapsed = [0]

    def api(path):
        if "/workflows/" in path:
            run = trusted_run("deploy.yml")
            run["path"] += suffix
            return {"workflow_runs": [run]}
        return {
            "jobs": [
                {
                    "name": "Reconcile canonical observability",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        }

    def sleep(seconds):
        elapsed[0] += seconds

    if success:
        module.wait_for_hosts(
            "a" * 40,
            {"canonical_changed": True},
            api=api,
            timeout=10,
            clock=lambda: elapsed[0],
            sleep=sleep,
        )
    else:
        with pytest.raises(module.HostReleaseError, match="timed out"):
            module.wait_for_hosts(
                "a" * 40,
                {"canonical_changed": True},
                api=api,
                timeout=10,
                clock=lambda: elapsed[0],
                sleep=sleep,
            )


def bootstrap_module():
    spec = importlib.util.spec_from_file_location(
        "public_bootstrap", ROOT / "deploy/observability/bootstrap.py"
    )
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_public_bootstrap_installs_fixed_foundation_and_preserves_existing(tmp_path, monkeypatch):
    module = bootstrap_module()
    helper = b"#!/usr/bin/python3\nreviewed helper\n"
    import hashlib

    digest = hashlib.sha256(helper).hexdigest()
    monkeypatch.setattr(module, "authenticate", lambda *args: helper)
    monkeypatch.setattr(module, "verify_identity", lambda *args: None)
    monkeypatch.setattr(module, "safe", lambda *args: None)
    monkeypatch.setattr(module, "validate_sudoers", lambda *args: None)
    assert module.bootstrap("a" * 40, digest, tmp_path) == "installed"
    config = tmp_path / "etc/findme-observability-reconcile.json"
    assert __import__("json").loads(config.read_text()) == module.CONFIGURATION
    assert (tmp_path / "usr/local/sbin/findme-observability-reconcile").read_bytes() == helper
    assert module.bootstrap("a" * 40, digest, tmp_path) == "existing"
    config.write_text("{}")
    with pytest.raises(ValueError, match="foundation mismatch"):
        module.bootstrap("a" * 40, digest, tmp_path)


def test_public_bootstrap_rejects_partial_foundation_and_bad_source(tmp_path, monkeypatch):
    module = bootstrap_module()
    monkeypatch.setattr(module, "verify_identity", lambda *args: None)
    monkeypatch.setattr(module, "safe", lambda *args: None)
    monkeypatch.setattr(module, "authenticate", lambda *args: b"bad source")
    with pytest.raises(ValueError, match="source checksum"):
        module.bootstrap("a" * 40, "0" * 64, tmp_path)
    target = tmp_path / "etc/findme-observability-reconcile.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}")
    import hashlib

    with pytest.raises(ValueError, match="partial foundation"):
        module.bootstrap("a" * 40, hashlib.sha256(b"bad source").hexdigest(), tmp_path)


def test_public_bootstrap_rolls_back_write_failure(tmp_path, monkeypatch):
    module = bootstrap_module()
    helper = b"reviewed"
    import hashlib

    monkeypatch.setattr(module, "authenticate", lambda *args: helper)
    monkeypatch.setattr(module, "verify_identity", lambda *args: None)
    monkeypatch.setattr(module, "safe", lambda *args: None)
    monkeypatch.setattr(module, "validate_sudoers", lambda *args: None)
    original = module.install
    calls = []

    def failing(target, content, mode):
        calls.append(target)
        if len(calls) == 3:
            raise OSError("write failed")
        original(target, content, mode)

    monkeypatch.setattr(module, "install", failing)
    with pytest.raises(OSError, match="write failed"):
        module.bootstrap("a" * 40, hashlib.sha256(helper).hexdigest(), tmp_path)
    for name in module.TARGETS:
        assert not (tmp_path / name).exists()


def test_bootstrap_authenticates_fixed_git_source_and_existing_helper(tmp_path, monkeypatch):
    module = bootstrap_module()
    calls = []
    revision = "a" * 40

    def command(*args):
        calls.append(args)
        if "ls-tree" in args:
            return f"100644 blob {'b' * 40}\t{module.HELPER}\n".encode()
        if "show" in args:
            return b"reviewed"
        if "rev-list" in args:
            return (revision + "\n").encode()
        return b""

    monkeypatch.setattr(module, "command", command)
    assert module.authenticate(revision, tmp_path, b"reviewed") == b"reviewed"
    assert any(module.REPOSITORY in call and "fetch" in call for call in calls)
    assert any("--is-ancestor" in call for call in calls)
    with pytest.raises(ValueError, match="authenticated main source"):
        module.authenticate(revision, tmp_path, b"modified")


def test_bootstrap_rejects_symlink_or_unsafe_root_file(tmp_path):
    module = bootstrap_module()
    path = tmp_path / "file"
    path.write_text("source")
    path.chmod(0o666)
    with pytest.raises(ValueError, match="unsafe foundation"):
        module.safe(path, 0o755)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="unsafe foundation"):
        module.safe(link, 0o755)


def test_canonical_bootstrap_uses_fixed_identity_and_deploy_user(tmp_path, monkeypatch):
    import hashlib

    module = bootstrap_module()
    roles = []
    monkeypatch.setattr(module, "verify_identity", roles.append)
    monkeypatch.setattr(module, "authenticate", lambda *args: b"reviewed")
    monkeypatch.setattr(module, "safe", lambda *args: None)
    monkeypatch.setattr(module, "validate_sudoers", lambda *args: None)
    module.bootstrap("a" * 40, hashlib.sha256(b"reviewed").hexdigest(), tmp_path, "canonical")
    assert roles == ["canonical"]
    assert __import__("json").loads((tmp_path / module.TARGETS[1]).read_text()) == module.CANONICAL
    assert (tmp_path / module.TARGETS[2]).read_text().startswith("deploy ALL=(root)")
