"""Offline execution of the resource release boundaries."""

import base64
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


def test_release_classifier_keeps_docs_app_and_os_paths_separate() -> None:
    classify = load_helper("classify").classify
    assert classify(["docs/architecture.md"]) == {
        "terraform": False,
        "ansible": False,
        "runtime": False,
    }
    assert classify(["infra/ansible/site.yml"]) == {
        "terraform": False,
        "ansible": True,
        "runtime": False,
    }
    assert classify(["src/backend/config/views.py"]) == {
        "terraform": False,
        "ansible": False,
        "runtime": True,
    }
    assert classify(["infra/terraform/compute.tf", "src/worker/photo_worker/client.py"]) == {
        "terraform": True,
        "ansible": False,
        "runtime": True,
    }


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


def test_serial_host_key_is_bound_to_terraform_ip() -> None:
    known_hosts = load_helper("known_hosts")
    key = base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519" + b"x" * 32).decode()
    serial = f"boot output\r\nFINDME_HOST_ED25519 ssh-ed25519 {key}\r\n"
    assert known_hosts.known_host(serial, "192.0.2.44") == (f"192.0.2.44 ssh-ed25519 {key}\n")
    with pytest.raises(ValueError, match="missing or conflicting"):
        known_hosts.known_host(
            serial + f"FINDME_HOST_ED25519 ssh-ed25519 {key[:-2]}AA\n", "192.0.2.44"
        )


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


def test_backend_policy_cannot_delete_main_state_or_access_media_buckets() -> None:
    policy = json.loads((ROOT / "infra/bootstrap/state-bucket-policy.json.template").read_text())
    assert {statement["Principal"]["CanonicalUser"] for statement in policy["Statement"]} == {
        "BACKEND_SA_ID"
    }
    main_state = "arn:aws:s3:::findme-photo-tfstate/core/single-vm/terraform.tfstate"
    allowed = {
        (action, resource)
        for statement in policy["Statement"]
        for action in statement["Action"]
        for resource in statement["Resource"]
    }
    assert ("s3:GetObject", main_state) in allowed
    assert ("s3:PutObject", main_state) in allowed
    assert ("s3:DeleteObject", main_state) not in allowed
    assert all(resource.startswith("arn:aws:s3:::findme-photo-tfstate") for _, resource in allowed)


def test_resource_shape_is_reviewed_in_git_and_runtime_only_skips_backend() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/infrastructure.yml").read_text())
    source = (ROOT / "infra/terraform/project.tfvars").read_text()
    assert "boot_disk_gb     = 30" in source
    assert "postgres_disk_gb = 10" in source
    assert 'ssh_cidrs        = ["0.0.0.0/0"]' in source
    release = workflow["jobs"]["release"]
    assert "TF_VAR_boot_disk_gb" not in release["env"]
    assert "TF_VAR_postgres_disk_gb" not in release["env"]
    assert "TF_VAR_ssh_cidrs" not in release["env"]
    backend = next(
        step
        for step in release["steps"]
        if step.get("name") == "Open locked state and apply approved saved plan only"
    )
    assert "runtime" not in backend["if"]


def test_ansible_owns_repeatable_ssh_policy_with_reviewed_ci_key() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/infrastructure.yml").read_text())
    assert workflow["jobs"]["validate"]["name"] == "Infrastructure validation"
    configure = next(
        step
        for step in workflow["jobs"]["release"]["steps"]
        if step.get("name") == "Configure host OS; never start app or restore data"
    )
    assert configure["env"]["FINDME_SSH_PUBLIC_KEY"] == "${{ vars.INFRA_SSH_PUBLIC_KEY }}"
    assert "-e @/tmp/findme-ansible-vars.json" in configure["run"]

    tasks = yaml.safe_load((ROOT / "infra/ansible/roles/host/tasks/main.yml").read_text())
    copies = {
        task["ansible.builtin.copy"]["dest"]: task
        for task in tasks
        if "ansible.builtin.copy" in task
    }
    assert copies["/home/findme/.ssh/authorized_keys"]["ansible.builtin.copy"]["content"] == (
        "{{ findme_ssh_public_key | trim }}\n"
    )
    assert copies["/etc/sudoers.d/90-findme"]["ansible.builtin.copy"]["validate"] == (
        "/usr/sbin/visudo -cf %s"
    )
    ssh = copies["/etc/ssh/sshd_config.d/00-findme-recovery.conf"]
    assert all(
        line in ssh["ansible.builtin.copy"]["content"]
        for line in (
            "PermitRootLogin no",
            "PasswordAuthentication no",
            "KbdInteractiveAuthentication no",
            "PubkeyAuthentication yes",
            "AllowUsers findme",
        )
    )
    assert ssh["notify"] == "Validate and reload SSH"
    handlers = yaml.safe_load((ROOT / "infra/ansible/roles/host/handlers/main.yml").read_text())
    assert handlers[0]["ansible.builtin.command"] == "/usr/sbin/sshd -t -f /etc/ssh/sshd_config"
    assert handlers[1]["ansible.builtin.service"]["state"] == "reloaded"
