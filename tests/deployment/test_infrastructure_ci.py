"""Offline execution of the resource release boundaries."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_helper(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "infra/ci" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_release_classifier():
    spec = importlib.util.spec_from_file_location(
        "release_classifier", ROOT / "deploy/classify-release.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_plan_summary_updates_its_own_named_comment() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/infrastructure.yml").read_text())
    plan = workflow["jobs"]["plan"]
    publisher = next(
        step
        for step in plan["steps"]
        if step.get("uses", "").startswith("marocchino/sticky-pull-request-comment@")
    )
    assert publisher["with"] == {
        "header": "findme-infrastructure-plan",
        "path": "/tmp/infra-review.txt",
    }
    assert plan["permissions"]["pull-requests"] == "write"
    assert all("gh pr comment" not in step.get("run", "") for step in plan["steps"])


def test_release_classifier_keeps_docs_app_and_os_paths_separate() -> None:
    classify = load_release_classifier().classify
    assert classify(["docs/architecture.md"]) == {
        "web_changed": False,
        "worker_changed": False,
        "worker_base_changed": False,
        "import_changed": False,
        "commerce_changed": False,
        "terraform_changed": False,
        "ansible_changed": False,
    }
    assert classify(["infra/ansible/site.yml"]) == {
        "web_changed": False,
        "worker_changed": False,
        "worker_base_changed": False,
        "import_changed": False,
        "commerce_changed": False,
        "terraform_changed": False,
        "ansible_changed": True,
    }
    for path in (
        "infra/ci/release.py",
        "infra/ci/cost_estimation.py",
        ".github/workflows/infrastructure.yml",
        "deploy/classify-release.py",
    ):
        selected = classify([path])
        assert selected["terraform_changed"] is True
        assert selected["ansible_changed"] is False
    docs = classify(["docs/architecture.md"])
    assert docs["terraform_changed"] is False
    assert docs["ansible_changed"] is False
    assert classify(["src/backend/config/views.py"]) == {
        "web_changed": True,
        "worker_changed": False,
        "worker_base_changed": False,
        "import_changed": False,
        "commerce_changed": True,
        "terraform_changed": False,
        "ansible_changed": False,
    }
    assert (
        classify(["infra/terraform/compute.tf", "src/worker/photo_worker/client.py"])[
            "terraform_changed"
        ]
        is True
    )
    assert (
        classify(["infra/terraform/compute.tf", "src/worker/photo_worker/client.py"])[
            "worker_changed"
        ]
        is True
    )


def test_plan_step_writes_json_where_summary_command_reads_it(tmp_path: Path) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/infrastructure.yml").read_text())
    step = next(
        s
        for s in workflow["jobs"]["plan"]["steps"]
        if s.get("name") == "Create saved resource plan"
    )
    (tmp_path / "infra/terraform").mkdir(parents=True)
    (tmp_path / "infra/ci").mkdir(parents=True)
    (tmp_path / "infra/ci/plan_summary.py").write_bytes(
        (ROOT / "infra/ci/plan_summary.py").read_bytes()
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python").symlink_to(sys.executable)
    terraform = bin_dir / "terraform"
    terraform.write_text(
        "#!/bin/sh\n"
        'case "$2" in\n'
        "  plan) : > infra/terraform/plan.tfplan ;;\n"
        '  show) printf \'{"resource_changes":[{"address":"yandex_compute_instance.app",'
        '"change":{"actions":["create"]}}]}\\n\' ;;\n'
        "esac\n"
    )
    terraform.chmod(0o755)
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / "infra/terraform/plan.json").read_text())["resource_changes"]
    assert (tmp_path / "resource-diff.txt").read_text().strip() == (
        "create: yandex_compute_instance.app"
    )


def test_cost_plan_normalizes_referenced_boot_disk_without_double_counting() -> None:
    cost_estimation = load_helper("cost_estimation")
    fixture = ROOT / "tests/fixtures/infrastructure/cost_plan.json"
    plan = json.loads(fixture.read_text())

    normalized = cost_estimation.normalize_plan(plan)
    resources = normalized["planned_values"]["root_module"]["resources"]
    by_address = {resource["address"]: resource for resource in resources}

    assert set(by_address) == {
        "yandex_compute_disk.postgres",
        "yandex_compute_instance.app",
    }
    assert by_address["yandex_compute_instance.app"]["values"]["boot_disk"][0][
        "initialize_params"
    ] == [{"size": 30, "type": "network-hdd"}]
    assert by_address["yandex_compute_instance.app"]["values"]["scheduling_policy"] == [{}]
    assert by_address["yandex_compute_disk.postgres"]["values"] == {
        "size": 10,
        "type": "network-hdd",
    }
    assert plan["planned_values"]["root_module"]["resources"][0]["address"] == (
        "yandex_compute_disk.boot"
    )
    prior_resources = normalized["prior_state"]["values"]["root_module"]["resources"]
    prior_by_address = {resource["address"]: resource for resource in prior_resources}
    assert set(prior_by_address) == set(by_address)
    assert prior_by_address["yandex_compute_instance.app"]["values"]["boot_disk"][0][
        "initialize_params"
    ] == [{"size": 30, "type": "network-hdd"}]
    assert prior_by_address["yandex_compute_disk.postgres"]["values"]["size"] == 10

    id_only_plan = json.loads(json.dumps(plan))
    planned_instance = next(
        resource
        for resource in id_only_plan["planned_values"]["root_module"]["resources"]
        if resource["type"] == "yandex_compute_instance"
    )
    planned_instance["values"]["boot_disk"][0]["disk_id"] = "disk-boot-id"
    next(
        resource
        for resource in id_only_plan["planned_values"]["root_module"]["resources"]
        if resource["address"] == "yandex_compute_disk.boot"
    )["values"]["id"] = "disk-boot-id"
    id_only_plan["configuration"]["root_module"]["resources"][0]["expressions"]["boot_disk"][0][
        "disk_id"
    ]["references"] = []
    id_only_normalized = cost_estimation.normalize_plan(id_only_plan)
    assert len(id_only_normalized["planned_values"]["root_module"]["resources"]) == 2


def test_cost_estimator_rejects_upstream_error_printed_with_success_exit_code(
    tmp_path: Path,
) -> None:
    cost_estimation = load_helper("cost_estimation")
    estimator_dir = tmp_path / "estimator"
    estimator_dir.mkdir()
    (estimator_dir / "app.py").write_text("print(\"Error: 'initialize_params'\")\n")
    plan = tmp_path / "plan.json"
    plan.write_text("{}")

    with pytest.raises(RuntimeError, match="upstream cost estimator reported an error"):
        cost_estimation.run_estimator(estimator_dir, plan)


def test_cost_estimator_rejects_success_without_complete_summary(tmp_path: Path) -> None:
    cost_estimation = load_helper("cost_estimation")
    estimator_dir = tmp_path / "estimator"
    estimator_dir.mkdir()
    (estimator_dir / "app.py").write_text('print("No resources found")\n')
    plan = tmp_path / "plan.json"
    plan.write_text("{}")

    with pytest.raises(RuntimeError, match="incomplete report"):
        cost_estimation.run_estimator(estimator_dir, plan)


def test_cost_estimator_formats_verified_totals_as_plain_monthly_bullets() -> None:
    cost_estimation = load_helper("cost_estimation")
    report = """
=== TERRAFORM COST ESTIMATION ===
CURRENT INFRASTRUCTURE:
  Hourly: 0 RUB
  Monthly: 0 RUB
PLANNED INFRASTRUCTURE:
  Hourly: 1825.0 RUB
  Monthly: 1357800.0 RUB
DIFFERENCE:
  Hourly: +1825.0 RUB
  Monthly: +1357800.0 RUB
=== CURRENT USAGE DETAILS ===
╔═══════╗
=== PLANNED USAGE DETAILS ===
╔═══════╗
"""

    summary = cost_estimation.format_summary(report)

    assert summary == (
        "- Сейчас: 0 RUB/мес. (0 RUB/ч)\n"
        "- По плану: 1 357 800 RUB/мес. (1 825 RUB/ч)\n"
        "- Разница: +1 357 800 RUB/мес. (+1 825 RUB/ч)\n"
        "- Расчётная база: 744 ч/мес.\n"
    )
    assert summary.endswith("\n")
    assert "|" not in summary
    with pytest.raises(RuntimeError, match="missing or invalid cost totals"):
        cost_estimation.format_summary(report.replace("Monthly: 0 RUB", "Monthly: unknown RUB"))

    negative_report = report.replace("Hourly: +1825.0 RUB", "Hourly: -0.5 RUB").replace(
        "Monthly: +1357800.0 RUB", "Monthly: -372 RUB"
    )
    negative_summary = cost_estimation.format_summary(negative_report)
    assert "- Разница: -372 RUB/мес. (-0.5 RUB/ч)" in negative_summary


def _approval_fixture():
    head = "a" * 40
    pr = {
        "number": 412,
        "base": {"ref": "main"},
        "head": {"sha": head, "repo": {"full_name": "peter-nikitin/photo-prjct"}},
    }
    comment = {
        "body": "/infra approve 98765",
        "created_at": "2026-10-28T05:01:00Z",
    }
    run = {
        "id": 98765,
        "event": "pull_request",
        "conclusion": "success",
        "path": ".github/workflows/infrastructure.yml",
        "updated_at": "2026-10-28T05:00:00Z",
        "head_sha": head,
        "repository": {"full_name": "peter-nikitin/photo-prjct"},
        "pull_requests": [],
    }
    artifacts = [
        {
            "id": 76543,
            "name": "terraform-plan-412",
            "expired": False,
            "created_at": "2026-10-28T05:00:00Z",
        }
    ]
    return pr, comment, run, artifacts


def test_only_current_authorized_plan_run_can_be_applied() -> None:
    release = load_helper("release")
    pr, comment, run, artifacts = _approval_fixture()
    assert release.choose_plan(pr, [comment], run, artifacts, True) == {
        "pr_number": 412,
        "run_id": 98765,
        "artifact_id": 76543,
        "head_sha": "a" * 40,
    }
    with pytest.raises(ValueError, match="write-authorized"):
        release.choose_plan(pr, [comment], run, artifacts, False)
    pr["head"]["sha"] = "b" * 40
    with pytest.raises(ValueError, match="current PR head"):
        release.choose_plan(pr, [comment], run, artifacts, True)
    pr["head"]["sha"] = "a" * 40
    # GitHub may expose the current mutable PR head in an older run relation.
    run["head_sha"] = "b" * 40
    run["pull_requests"] = [{"number": 412, "head": {"sha": "a" * 40}}]
    with pytest.raises(ValueError, match="current PR head"):
        release.choose_plan(pr, [comment], run, artifacts, True)
    run["head_sha"] = "a" * 40
    run["updated_at"] = "2026-10-28T05:02:00Z"
    with pytest.raises(ValueError, match="write-authorized"):
        release.choose_plan(pr, [comment], run, artifacts, True)


def test_github_merged_pr_run_with_empty_relation_keeps_immutable_head_binding() -> None:
    # Observed on merged PR 296: run 37271242782 retained head_sha, but
    # pull_requests became empty after merge.
    release = load_helper("release")
    pr, comment, run, artifacts = _approval_fixture()
    run["id"] = 37271242782
    comment["body"] = "/infra approve 37271242782"
    assert run["pull_requests"] == []
    assert release.choose_plan(pr, [comment], run, artifacts, True)["run_id"] == 37271242782


def test_github_old_run_mutable_pr_relation_cannot_approve_new_head() -> None:
    # Observed on PR 298: old run 37271378595 kept its original head_sha,
    # while its nested PR relation reported the current later head.
    release = load_helper("release")
    pr, comment, run, artifacts = _approval_fixture()
    run["id"] = 37271378595
    comment["body"] = "/infra approve 37271378595"
    run["head_sha"] = "b" * 40
    run["pull_requests"] = [{"number": 412, "head": {"sha": pr["head"]["sha"]}}]
    with pytest.raises(ValueError, match="current PR head"):
        release.choose_plan(pr, [comment], run, artifacts, True)


def test_infrastructure_identity_loads_real_helper_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = load_helper("identity")
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    with pytest.raises(KeyError, match="GITHUB_REPOSITORY"):
        identity.main()


def test_oidc_token_is_masked_before_runner_environment_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = load_helper("identity")
    events: list[str] = []
    real_open = open

    def record_mask(message: str, *, flush: bool) -> None:
        assert flush is True
        events.append(message)

    def checked_open(*args, **kwargs):
        assert events == ["::add-mask::synthetic-token"]
        return real_open(*args, **kwargs)

    monkeypatch.setattr(identity, "print", record_mask, raising=False)
    monkeypatch.setattr(identity, "open", checked_open, raising=False)
    output = tmp_path / "runner-env"
    identity.publish_token("synthetic-token", str(output))
    assert output.read_text() == "YC_TOKEN=synthetic-token\n"


def test_ansible_receives_both_public_keys_using_the_configured_ssh_user(tmp_path):
    workflow = yaml.safe_load((ROOT / ".github/workflows/infrastructure.yml").read_text())
    configure = next(
        step
        for step in workflow["jobs"]["release"]["steps"]
        if "ansible-playbook" in step.get("run", "")
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    python = fake_bin / "python"
    python.write_text(
        f"#!{sys.executable}\n"
        + """
import os, subprocess, sys
if sys.argv[1] == "scripts/run-with-environment-secrets.py":
    assert sys.argv[sys.argv.index("--consumer") + 1] == "infrastructure-ansible"
    command = sys.argv[sys.argv.index("--") + 1:]
    raise SystemExit(subprocess.run(command, env=os.environ).returncode)
os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
"""
    )
    ssh = fake_bin / "ssh"
    ssh.write_text(
        f"#!{sys.executable}\n"
        + """
import os, pathlib, sys
assert "configured-deployer@192.0.2.10" in sys.argv
assert pathlib.Path(os.environ["VM_SSH_KEY_FILE"]).is_file()
pathlib.Path(os.environ["SSH_MARKER"]).touch()
"""
    )
    ansible = fake_bin / "ansible-playbook"
    ansible.write_text(
        f"#!{sys.executable}\n"
        + """
import os, pathlib, sys
assert pathlib.Path(os.environ["SSH_MARKER"]).exists()
assert sys.argv[sys.argv.index("-u") + 1] == "configured-deployer"
assert sys.argv[sys.argv.index("--private-key") + 1] == os.environ["VM_SSH_KEY_FILE"]
variables = pathlib.Path(sys.argv[sys.argv.index("-e") + 1].removeprefix("@"))
pathlib.Path(os.environ["ANSIBLE_CAPTURE"]).write_bytes(variables.read_bytes())
"""
    )
    for executable in (python, ssh, ansible):
        executable.chmod(0o755)
    (tmp_path / "findme-host").write_text("192.0.2.10\n")
    key = tmp_path / "deploy-key"
    key.write_text("synthetic-test-key")
    key.chmod(0o600)
    captured = tmp_path / "captured.json"
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FINDME_DEPLOY_SSH_PUBLIC_KEY": "ssh-ed25519 technical-public-test",
        "FINDME_ADMIN_SSH_PUBLIC_KEY": "ssh-ed25519 admin-public-test",
        "VM_SSH_KEY_FILE": str(key),
        "VM_USER": "configured-deployer",
        "SSH_MARKER": str(tmp_path / "ssh-checked"),
        "ANSIBLE_CAPTURE": str(captured),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
    }
    script = configure["run"].replace("/tmp/findme-", str(tmp_path / "findme-"))
    result = subprocess.run(
        ["bash", "-c", script], cwd=ROOT, env=environment, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(captured.read_text()) == {
        "findme_deploy_ssh_public_key": environment["FINDME_DEPLOY_SSH_PUBLIC_KEY"],
        "findme_admin_ssh_public_key": environment["FINDME_ADMIN_SSH_PUBLIC_KEY"],
    }


def test_cost_cli_keeps_app_and_disks_and_marks_partial_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cost_estimation = load_helper("cost_estimation")
    plan = json.loads((ROOT / "tests/fixtures/infrastructure/cost_plan.json").read_text())
    for resources in (
        plan["prior_state"]["values"]["root_module"]["resources"],
        plan["planned_values"]["root_module"]["resources"],
    ):
        resources.extend(
            [
                {
                    "address": "yandex_compute_instance.legacy_holder",
                    "type": "yandex_compute_instance",
                },
                {
                    "address": "yandex_lb_network_load_balancer.edge",
                    "type": "yandex_lb_network_load_balancer",
                },
            ]
        )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    received = []

    def fake_estimator(_directory: Path, path: Path) -> str:
        received.append(json.loads(path.read_text()))
        return """CURRENT INFRASTRUCTURE:
Hourly: 1 RUB
Monthly: 744 RUB
PLANNED INFRASTRUCTURE:
Hourly: 1 RUB
Monthly: 744 RUB
DIFFERENCE:
Hourly: 0 RUB
Monthly: 0 RUB
=== CURRENT USAGE DETAILS ===
app yandex_compute_instance postgres yandex_compute_disk
=== PLANNED USAGE DETAILS ===
app yandex_compute_instance postgres yandex_compute_disk
"""

    monkeypatch.setattr(cost_estimation, "run_estimator", fake_estimator)
    monkeypatch.setattr(
        sys, "argv", ["cost_estimation.py", str(plan_path), "--estimator-dir", str(tmp_path)]
    )
    assert cost_estimation.main() == 0
    assert "Неполная оценка" in capsys.readouterr().out
    assert len(received) == 1
    for resources in (
        received[0]["prior_state"]["values"]["root_module"]["resources"],
        received[0]["planned_values"]["root_module"]["resources"],
    ):
        by_address = {resource["address"]: resource for resource in resources}
        assert "yandex_compute_instance.legacy_holder" not in by_address
        assert "yandex_compute_disk.boot" not in by_address
        assert by_address["yandex_compute_instance.app"]["values"]["boot_disk"][0][
            "initialize_params"
        ] == [{"size": 30, "type": "network-hdd"}]
        postgres = by_address["yandex_compute_disk.postgres"]["values"]
        assert postgres["size"] == 10 and postgres["type"] == "network-hdd"
