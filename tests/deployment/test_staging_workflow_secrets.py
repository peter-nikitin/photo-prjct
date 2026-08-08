from __future__ import annotations

import os
import signal
import stat
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "deploy/run-staging-remote.sh"


def test_remote_helper_is_directly_executable_by_the_resolver() -> None:
    assert HELPER.stat().st_mode & stat.S_IXUSR


def _workflow(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    steps = [step for step in job["steps"] if step.get("name") == name]
    assert len(steps) == 1, f"expected one {name!r} step"
    return steps[0]


def _resolver_step(job: dict[str, Any], name: str, consumer: str) -> None:
    step = _step(job, name)
    command = step["run"]
    assert "scripts/run-with-environment-secrets.py" in command
    assert "--environment staging" in command
    assert f"--consumer {consumer}" in command
    assert "--identity github-oidc" in command
    assert "deploy/run-staging-remote.sh" in command
    assert "${{ secrets." not in command
    assert "GITHUB_OUTPUT" not in command


def test_every_migrated_staging_consumer_uses_environment_oidc_and_its_projection() -> None:
    deploy = _workflow("deploy.yml")
    monitor = _workflow("monitor-public-health.yml")
    promotion = _workflow("promote-production.yml")
    benchmark = _workflow("staging-face-embedding-benchmark.yml")

    expected = [
        (
            "stage-observability-release",
            deploy["jobs"]["stage-observability-release"],
            [
                ("Stage privileged observability source", "staging-remote-check"),
            ],
        ),
        (
            "deploy",
            deploy["jobs"]["deploy"],
            [
                ("Verify staged paused observability release", "staging-remote-check"),
                ("Run staging deployment", "staging-deploy"),
                ("Verify private upload storage contract", "staging-remote-check"),
                ("Verify selfie-search temporary storage contract", "staging-remote-check"),
                ("Verify selfie-feedback storage contract", "staging-deploy"),
            ],
        ),
        (
            "configure-monitoring-agent",
            deploy["jobs"]["configure-monitoring-agent"],
            [
                ("Configure staging Unified Agent", "staging-remote-check"),
            ],
        ),
        (
            "probe",
            monitor["jobs"]["probe"],
            [
                ("Probe public health and write metrics", "staging-public-monitor"),
            ],
        ),
        (
            "verify-staging",
            promotion["jobs"]["verify-staging"],
            [
                ("Confirm image was deployed successfully to staging", "staging-remote-check"),
            ],
        ),
        (
            "benchmark",
            benchmark["jobs"]["benchmark"],
            [
                ("Run bounded benchmark operation", "staging-remote-check"),
            ],
        ),
    ]

    for _name, job, steps in expected:
        assert job["environment"] == "staging"
        assert job["permissions"] == {"contents": "read", "id-token": "write"}
        for step_name, consumer in steps:
            _resolver_step(job, step_name, consumer)


def test_every_resolver_job_checks_out_without_persisting_credentials() -> None:
    jobs = [
        _workflow("deploy.yml")["jobs"]["stage-observability-release"],
        _workflow("deploy.yml")["jobs"]["deploy"],
        _workflow("deploy.yml")["jobs"]["configure-monitoring-agent"],
        _workflow("monitor-public-health.yml")["jobs"]["probe"],
        _workflow("promote-production.yml")["jobs"]["verify-staging"],
        _workflow("staging-face-embedding-benchmark.yml")["jobs"]["benchmark"],
    ]
    expected_options = [
        {
            "ref": "${{ github.sha }}",
            "fetch-depth": 1,
            "persist-credentials": False,
        },
        {
            "ref": "${{ needs.classify-staging-release.outputs.release_sha }}",
            "fetch-depth": 1,
            "persist-credentials": False,
        },
        {"persist-credentials": False},
        {"persist-credentials": False},
        {"persist-credentials": False},
        {"persist-credentials": False},
    ]
    for job, expected in zip(jobs, expected_options, strict=True):
        checkout = [step for step in job["steps"] if step.get("uses") == "actions/checkout@v4"]
        assert len(checkout) == 1
        assert checkout[0]["with"] == expected


def test_build_and_pull_request_jobs_do_not_receive_oidc_or_lockbox_access() -> None:
    deploy = _workflow("deploy.yml")
    ci = _workflow("ci.yml")

    assert deploy["jobs"]["build"]["permissions"] == {"contents": "read", "packages": "write"}
    assert "id-token" not in deploy["jobs"]["build"]["permissions"]
    assert "run-with-environment-secrets.py" not in str(deploy["jobs"]["build"])
    for job in ci["jobs"].values():
        assert "id-token" not in job.get("permissions", {})
        assert "run-with-environment-secrets.py" not in str(job)


def test_migrated_staging_jobs_have_no_github_secret_expressions_or_secret_outputs() -> None:
    workflows = {
        "deploy.yml": ("stage-observability-release", "deploy", "configure-monitoring-agent"),
        "monitor-public-health.yml": ("probe",),
        "promote-production.yml": ("verify-staging",),
        "staging-face-embedding-benchmark.yml": ("benchmark",),
    }
    for workflow_name, job_names in workflows.items():
        workflow = _workflow(workflow_name)
        for job_name in job_names:
            rendered = str(workflow["jobs"][job_name])
            assert "${{ secrets." not in rendered
            assert "GITHUB_OUTPUT" not in rendered

    build = _workflow("deploy.yml")["jobs"]["build"]
    assert "${{ secrets.GITHUB_TOKEN }}" in str(build)
    assert str(build).count("secrets.GITHUB_TOKEN") == 1


def test_staging_workflows_supply_remote_host_user_and_deployment_identity_from_variables() -> None:
    deploy = _workflow("deploy.yml")
    promotion = _workflow("promote-production.yml")

    deploy_step = _step(deploy["jobs"]["deploy"], "Run staging deployment")
    verify_step = _step(
        promotion["jobs"]["verify-staging"], "Confirm image was deployed successfully to staging"
    )
    for step in (deploy_step, verify_step):
        assert step["env"]["STAGING_VM_HOST"] == "${{ vars.VM_HOST }}"
        assert step["env"]["STAGING_VM_USER"] == "${{ vars.VM_USER }}"
        assert step["env"]["STAGING_SSH_KNOWN_HOSTS"] == "${{ vars.STAGING_SSH_KNOWN_HOSTS }}"
    assert deploy_step["env"]["DB_NAME"] == "${{ vars.DB_NAME }}"
    assert deploy_step["env"]["DB_USER"] == "${{ vars.DB_USER }}"
    assert deploy_step["env"]["ALLOWED_HOSTS"] == "${{ vars.ALLOWED_HOSTS }}"
    assert deploy_step["env"]["GHCR_USERNAME"] == "${{ vars.GHCR_USERNAME }}"


def _write_executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\nset -eu\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)


def _deployment_values() -> dict[str, str]:
    return {
        "APP_IMAGE": "ghcr.io/peter-nikitin/photo-prjct:test-image",
        "WORKER_IMAGE": "ghcr.io/peter-nikitin/photo-prjct-worker:test-image",
        "DEPLOYMENT_TARGET": "staging",
        "DEBUG": "False",
        "ALLOWED_HOSTS": "staging.findme-photo.ru",
        "GUNICORN_WORKERS": "5",
        "GUNICORN_THREADS": "2",
        "GUNICORN_TIMEOUT": "180",
        "GUNICORN_MAX_REQUESTS": "1000",
        "GUNICORN_MAX_REQUESTS_JITTER": "100",
        "DB_NAME": "photo",
        "DB_USER": "photo",
        "PUBLIC_DOMAIN": "findme-photo.ru",
        "PUBLIC_DOMAIN_ALIAS": "www.findme-photo.ru",
        "MEDIA_STORAGE_BACKEND": "filesystem",
        "MEDIA_S3_ENDPOINT_URL": "https://storage.yandexcloud.net",
        "MEDIA_S3_REGION": "ru-central1",
        "MEDIA_S3_PUBLIC_BUCKET": "public",
        "PHOTO_UPLOAD_ENABLED": "False",
        "PRIVATE_MEDIA_S3_BUCKET": "private",
        "PRIVATE_MEDIA_ALLOWED_ORIGINS": "https://findme-photo.ru",
        "PHOTO_PROCESSING_ENABLED": "False",
        "PHOTO_PROCESSING_PREVIEW_ENABLED": "False",
        "PHOTO_PROCESSING_FACE_ENABLED": "False",
        "PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS": "120",
        "PHOTO_PROCESSING_MAX_REQUEST_BYTES": "131072",
        "PHOTO_WORKER_BUILD": "capture-metadata-v1",
        "PHOTO_WORKER_LEASE_SECONDS": "120",
        "PHOTO_WORKER_PROCESSOR_IDENTITIES": "1/capture_metadata/1",
        "PHOTO_WORKER_PROCESSOR_TYPES": (
            "selfie_query,face_embedding,capture_metadata,generate_preview"
        ),
        "PHOTO_WORKER_REPLICAS": "1",
        "SELFIE_SEARCH_MAX_UPLOAD_BYTES": "20971520",
        "SELFIE_SEARCH_MAX_PIXELS": "25000000",
        "SELFIE_SEARCH_DOWNLOAD_TTL_SECONDS": "120",
        "SELFIE_SEARCH_EMBEDDING_MODEL": "sface",
        "SELFIE_SEARCH_EMBEDDING_DIMENSIONS": "128",
        "SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD": "0.363",
        "SELFIE_SEARCH_TEMPORARY_PREFIX": "selfie-search/",
        "SELFIE_SEARCH_LIFECYCLE_MAX_AGE_HOURS": "24",
        "SELFIE_FEEDBACK_ENABLED": "False",
        "SELFIE_FEEDBACK_S3_BUCKET": "feedback",
        "SELFIE_FEEDBACK_S3_ENDPOINT_URL": "https://storage.yandexcloud.net",
        "SELFIE_FEEDBACK_S3_REGION": "ru-central1",
        "SELFIE_FEEDBACK_KMS_KEY_ID": "kms-id",
        "SELFIE_FEEDBACK_STORAGE_PREFLIGHT_CONFIRMED": "False",
        "GHCR_USERNAME": "peter-nikitin",
    }


@pytest.fixture
def remote_boundary(tmp_path: Path) -> Path:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    _write_executable(
        binary_dir / "scp",
        """
        printf '%s\\n' "$@" > "$SCP_ARGUMENTS"
        printf '%s\\n' call >> "$SCP_CALLS"
        recursive=0
        for argument in "$@"; do
          [ "$argument" != -r ] || recursive=1
        done
        [ "$recursive" = 1 ] || exit 46
        [ "${FAIL_SCP:-0}" != 1 ] || exit 47

        target=''
        for argument in "$@"; do
          target=$argument
        done
        release_sha=${RELEASE_SHA:-missing-release-sha}
        release_root="/opt/photo-prjct/privileged-observability-releases/$release_sha"
        root_target="$STAGING_VM_USER@$STAGING_VM_HOST:$release_root/"
        deploy_target="$STAGING_VM_USER@$STAGING_VM_HOST:$release_root/deploy/"
        case "$target" in
          "$root_target")
            destination="$REMOTE_RELEASES/$release_sha"
            expected_count=2
            target_kind=root
            ;;
          "$deploy_target")
            destination="$REMOTE_RELEASES/$release_sha/deploy"
            expected_count=2
            target_kind=deploy
            ;;
          *) exit 0 ;;
        esac
        [ -d "$destination" ] || exit 49

        source_count=0
        skip_next=0
        for argument in "$@"; do
          [ "$argument" != "$target" ] || break
          if [ "$skip_next" = 1 ]; then
            skip_next=0
            continue
          fi
          case "$argument" in
            -r) continue ;;
            -o|-i) skip_next=1; continue ;;
          esac
          source_count=$((source_count + 1))
          case "$target_kind:$argument" in
            root:staging-observability-release-sha)
              : > "$destination/staging-observability-release-sha"
              ;;
            root:staging-observability-source.sha256)
              : > "$destination/staging-observability-source.sha256"
              ;;
            deploy:deploy/bootstrap-selfie-observability.sh)
              : > "$destination/bootstrap-selfie-observability.sh"
              ;;
            deploy:deploy/selfie-observability)
              mkdir "$destination/selfie-observability"
              ;;
            *) exit 51 ;;
          esac
        done
        [ "$source_count" = "$expected_count" ] || exit 52
        """,
    )
    _write_executable(
        binary_dir / "ssh",
        """
        if [ "${BLOCK_SSH:-0}" = 1 ]; then
          printf '%s\\n' "$$" > "$SSH_CHILD_PID"
          relay_count=0
          record_signal() {
            signal_name=$1
            status=$2
            relay_count=$((relay_count + 1))
            printf '%s\\n' "$signal_name" >> "$SSH_SIGNAL"
            if [ "${RESIST_FIRST_SIGNAL:-0}" = 1 ] && [ "$relay_count" = 1 ]; then
              return
            fi
            exit "$status"
          }
          trap 'record_signal HUP 129' HUP
          trap 'record_signal INT 130' INT
          trap 'record_signal TERM 143' TERM
          printf ready > "$SSH_READY"
          while :; do sleep 0.05; done
        fi
        printf '%s\\n' "$@" > "$SSH_ARGUMENTS"
        cat > "$SSH_STDIN"
        [ "${FAIL_SSH:-0}" != 1 ] || exit 48
        remote_command=''
        for argument in "$@"; do
          remote_command=$argument
        done
        release_sha=${RELEASE_SHA:-missing-release-sha}
        release_root="/opt/photo-prjct/privileged-observability-releases/$release_sha"
        if [ "$remote_command" = "mkdir -p -- $release_root $release_root/deploy" ]; then
          mkdir -p "$REMOTE_RELEASES/$release_sha/deploy"
          exit 0
        fi
        [ -z "${SSH_STDOUT:-}" ] || printf '%s\n' "$SSH_STDOUT"
        """,
    )
    return binary_dir


def _remote_environment(tmp_path: Path, remote_boundary: Path) -> tuple[dict[str, str], str]:
    key = tmp_path / "staging-key"
    key.write_text("private-key-sentinel", encoding="utf-8")
    key.chmod(0o600)
    environment_file = tmp_path / "environment.env"
    sentinel = "secret-sentinel-not-in-command-or-output"
    environment_file.write_text(
        f'VM_SSH_KEY_FILE="{key}"\nSECRET_KEY="{sentinel}"\n',
        encoding="utf-8",
    )
    environment_file.chmod(0o600)
    environment = {
        **os.environ,
        **_deployment_values(),
        "PATH": f"{remote_boundary}{os.pathsep}{os.environ['PATH']}",
        "TMPDIR": str(tmp_path),
        "FINDME_ENV_FILE": str(environment_file),
        "STAGING_VM_HOST": "staging.example.test",
        "STAGING_VM_USER": "deployer",
        "STAGING_SSH_KNOWN_HOSTS": "staging.example.test ssh-ed25519 known-host-sentinel",
        "SCP_ARGUMENTS": str(tmp_path / "scp-arguments"),
        "SCP_CALLS": str(tmp_path / "scp-calls"),
        "SSH_ARGUMENTS": str(tmp_path / "ssh-arguments"),
        "SSH_STDIN": str(tmp_path / "ssh-stdin"),
        "SSH_CHILD_PID": str(tmp_path / "ssh-child-pid"),
        "SSH_SIGNAL": str(tmp_path / "ssh-signal"),
        "SSH_READY": str(tmp_path / "ssh-ready"),
        "REMOTE_RELEASES": str(tmp_path / "remote-releases"),
    }
    return environment, sentinel


def _run_helper(
    arguments: list[str], environment: dict[str, str], *, helper: Path = HELPER
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", helper, *arguments],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def _kill_test_process_group(process: subprocess.Popen[str]) -> tuple[str, str]:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        return process.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate(timeout=1)


def _wait_for_transport(process: subprocess.Popen[str], ready: Path) -> None:
    # Startup is outside Task 1's five-second post-signal shutdown contract. This ceiling only
    # prevents a broken test double from leaking a process group; ready/early-exit are the gates.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if ready.exists():
            return
        if process.poll() is not None:
            stdout, stderr = _kill_test_process_group(process)
            pytest.fail(
                "helper exited before fake transport readiness: "
                f"returncode={process.returncode} stdout={stdout!r} stderr={stderr!r}"
            )
        time.sleep(0.02)
    stdout, stderr = _kill_test_process_group(process)
    pytest.fail(
        "fake transport never reached its first-instruction readiness checkpoint: "
        f"returncode={process.returncode} stdout={stdout!r} stderr={stderr!r}"
    )


def test_deploy_helper_uses_private_files_and_ssh_stdin_without_disclosing_values(
    tmp_path: Path, remote_boundary: Path
) -> None:
    environment, sentinel = _remote_environment(tmp_path, remote_boundary)

    result = _run_helper(["deploy"], environment)

    assert result.returncode == 0, result.stderr
    scp_arguments = Path(environment["SCP_ARGUMENTS"]).read_text(encoding="utf-8")
    ssh_arguments = Path(environment["SSH_ARGUMENTS"]).read_text(encoding="utf-8")
    ssh_stdin = Path(environment["SSH_STDIN"]).read_text(encoding="utf-8")
    assert "docker-compose.prod.yml" in scp_arguments
    assert "-r" in scp_arguments.splitlines()
    assert "deploy" in scp_arguments
    assert "StrictHostKeyChecking=yes" in ssh_arguments
    assert "UserKnownHostsFile=" in ssh_arguments
    assert "/dev/null" not in ssh_arguments
    assert str(tmp_path / "staging-key") in ssh_arguments
    assert sentinel in ssh_stdin
    assert "VM_SSH_KEY_FILE" not in ssh_stdin
    for output in (result.stdout, result.stderr, scp_arguments, ssh_arguments):
        assert sentinel not in output
    assert result.stdout == "[staging-remote] stage=deploy status=ok\n"
    assert not list(tmp_path.glob("findme-staging-remote.*"))


def test_deploy_helper_stops_before_ssh_when_copy_fails_and_cleans_private_files(
    tmp_path: Path, remote_boundary: Path
) -> None:
    environment, sentinel = _remote_environment(tmp_path, remote_boundary)
    environment["FAIL_SCP"] = "1"

    result = _run_helper(["deploy"], environment)

    assert result.returncode == 2
    assert not Path(environment["SSH_ARGUMENTS"]).exists()
    assert result.stderr == "[staging-remote] stage=copy status=error code=copy_failed\n"
    assert sentinel not in result.stdout
    assert sentinel not in result.stderr
    assert not list(tmp_path.glob("findme-staging-remote.*"))


def test_deploy_helper_preserves_the_existing_staging_apply_boundary() -> None:
    source = HELPER.read_text(encoding="utf-8")

    assert "DEPLOY_ROOT=/opt/photo-prjct" in source
    assert "COMPOSE_PROJECT_NAME=photo-prjct-staging" in source
    assert "exec sh /opt/photo-prjct/deploy/apply-deployment.sh" in source


def test_helper_falls_back_to_gnu_stat_when_stat_f_is_not_a_file_mode(
    tmp_path: Path, remote_boundary: Path
) -> None:
    _write_executable(
        remote_boundary / "stat",
        """
        if [ "$1" = -f ]; then
          printf '%s\\n' unsupported-filesystem-format
        else
          [ "$1" = -c ] || exit 89
          printf '%s\\n' 600
        fi
        """,
    )
    environment, _sentinel = _remote_environment(tmp_path, remote_boundary)

    result = _run_helper(["deploy"], environment)

    assert result.returncode == 0, result.stderr


def test_monitoring_configuration_copies_its_tracked_inputs_before_remote_execution(
    tmp_path: Path, remote_boundary: Path
) -> None:
    environment, sentinel = _remote_environment(tmp_path, remote_boundary)
    environment["YANDEX_CLOUD_FOLDER_ID"] = "folder-id"

    result = _run_helper(["configure-monitoring"], environment)

    assert result.returncode == 0, result.stderr
    scp_arguments = Path(environment["SCP_ARGUMENTS"]).read_text(encoding="utf-8")
    assert "deploy" in scp_arguments.splitlines()
    assert "deployer@staging.example.test:/opt/photo-prjct/" in scp_arguments.splitlines()
    assert sentinel not in result.stdout
    assert sentinel not in result.stderr


def test_paused_observability_stage_creates_and_populates_the_exact_release_tree_over_private_ssh(
    tmp_path: Path, remote_boundary: Path
) -> None:
    environment, sentinel = _remote_environment(tmp_path, remote_boundary)
    release_sha = "a" * 40
    environment.update(
        RELEASE_SHA=release_sha,
        OBSERVABILITY_SOURCE_MANIFEST_SHA256="b" * 64,
    )

    result = _run_helper(["stage-paused-observability-release"], environment)

    assert result.returncode == 0, result.stderr
    release_root = Path(environment["REMOTE_RELEASES"]) / release_sha
    assert (release_root / "staging-observability-release-sha").is_file()
    assert (release_root / "staging-observability-source.sha256").is_file()
    assert (release_root / "deploy/bootstrap-selfie-observability.sh").is_file()
    assert (release_root / "deploy/selfie-observability").is_dir()
    assert not (release_root / "bootstrap-selfie-observability.sh").exists()
    assert not (release_root / "selfie-observability").exists()
    scp_arguments = Path(environment["SCP_ARGUMENTS"]).read_text(encoding="utf-8").splitlines()
    ssh_arguments = Path(environment["SSH_ARGUMENTS"]).read_text(encoding="utf-8")
    assert scp_arguments[-3:] == [
        "deploy/bootstrap-selfie-observability.sh",
        "deploy/selfie-observability",
        (
            "deployer@staging.example.test:"
            f"/opt/photo-prjct/privileged-observability-releases/{release_sha}/deploy/"
        ),
    ]
    assert Path(environment["SCP_CALLS"]).read_text(encoding="utf-8").splitlines() == [
        "call",
        "call",
    ]
    assert "-r" in scp_arguments
    assert "StrictHostKeyChecking=yes" in scp_arguments
    assert any(argument.startswith("UserKnownHostsFile=") for argument in scp_arguments)
    assert "StrictHostKeyChecking=yes" in ssh_arguments
    assert "UserKnownHostsFile=" in ssh_arguments
    assert (
        f"mkdir -p -- /opt/photo-prjct/privileged-observability-releases/{release_sha} "
        f"/opt/photo-prjct/privileged-observability-releases/{release_sha}/deploy"
    ) in ssh_arguments
    assert Path(environment["SSH_STDIN"]).read_text(encoding="utf-8") == ""
    assert result.stdout == "[staging-remote] stage=stage-paused-observability-release status=ok\n"
    assert sentinel not in result.stdout + result.stderr + "\n".join(scp_arguments) + ssh_arguments
    assert not list(tmp_path.glob("findme-staging-remote.*"))


@pytest.mark.parametrize(
    ("name", "value", "error"),
    [
        ("RELEASE_SHA", "A" * 40, "invalid_release_sha"),
        ("OBSERVABILITY_SOURCE_MANIFEST_SHA256", "not-a-sha256", "invalid_manifest_sha256"),
    ],
)
def test_paused_observability_operations_reject_invalid_release_identifiers_before_transport(
    tmp_path: Path,
    remote_boundary: Path,
    name: str,
    value: str,
    error: str,
) -> None:
    environment, sentinel = _remote_environment(tmp_path, remote_boundary)
    environment.update(
        RELEASE_SHA="a" * 40,
        OBSERVABILITY_SOURCE_MANIFEST_SHA256="b" * 64,
    )
    environment[name] = value

    result = _run_helper(["stage-paused-observability-release"], environment)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == f"[staging-remote] stage=observability status=error code={error}\n"
    assert not Path(environment["SCP_ARGUMENTS"]).exists()
    assert not Path(environment["SSH_ARGUMENTS"]).exists()
    assert sentinel not in result.stdout + result.stderr
    assert not list(tmp_path.glob("findme-staging-remote.*"))


def test_paused_observability_stage_stops_on_copy_failure_without_disclosure(
    tmp_path: Path, remote_boundary: Path
) -> None:
    environment, sentinel = _remote_environment(tmp_path, remote_boundary)
    environment.update(
        RELEASE_SHA="a" * 40,
        OBSERVABILITY_SOURCE_MANIFEST_SHA256="b" * 64,
        FAIL_SCP="1",
    )

    result = _run_helper(["stage-paused-observability-release"], environment)

    assert result.returncode == 2
    assert result.stderr == "[staging-remote] stage=copy status=error code=copy_failed\n"
    assert Path(environment["SSH_ARGUMENTS"]).exists()
    assert Path(environment["SCP_CALLS"]).read_text(encoding="utf-8").splitlines() == ["call"]
    assert sentinel not in result.stdout + result.stderr
    assert not list(tmp_path.glob("findme-staging-remote.*"))


def test_paused_observability_stage_stops_before_copy_when_directory_creation_fails(
    tmp_path: Path, remote_boundary: Path
) -> None:
    environment, sentinel = _remote_environment(tmp_path, remote_boundary)
    environment.update(
        RELEASE_SHA="a" * 40,
        OBSERVABILITY_SOURCE_MANIFEST_SHA256="b" * 64,
        FAIL_SSH="1",
    )

    result = _run_helper(["stage-paused-observability-release"], environment)

    assert result.returncode == 2
    assert result.stderr == "[staging-remote] stage=remote status=error code=remote_failed\n"
    assert Path(environment["SSH_ARGUMENTS"]).exists()
    assert not Path(environment["SCP_ARGUMENTS"]).exists()
    assert sentinel not in result.stdout + result.stderr
    assert not list(tmp_path.glob("findme-staging-remote.*"))


def test_paused_observability_verification_forwards_only_safe_identifiers_to_ssh(
    tmp_path: Path, remote_boundary: Path
) -> None:
    environment, sentinel = _remote_environment(tmp_path, remote_boundary)
    release_sha = "a" * 40
    source_manifest_sha256 = "b" * 64
    environment.update(
        RELEASE_SHA=release_sha,
        OBSERVABILITY_SOURCE_MANIFEST_SHA256=source_manifest_sha256,
    )

    result = _run_helper(["verify-paused-observability-release"], environment)

    assert result.returncode == 0, result.stderr
    ssh_arguments = Path(environment["SSH_ARGUMENTS"]).read_text(encoding="utf-8")
    ssh_stdin = Path(environment["SSH_STDIN"]).read_text(encoding="utf-8")
    assert not Path(environment["SCP_ARGUMENTS"]).exists()
    assert "StrictHostKeyChecking=yes" in ssh_arguments
    assert "UserKnownHostsFile=" in ssh_arguments
    assert f'RELEASE_SHA="{release_sha}"' in ssh_stdin
    assert f'OBSERVABILITY_SOURCE_MANIFEST_SHA256="{source_manifest_sha256}"' in ssh_stdin
    assert sentinel not in result.stdout + result.stderr + ssh_arguments
    assert result.stdout == "[staging-remote] stage=verify-paused-observability-release status=ok\n"
    assert not list(tmp_path.glob("findme-staging-remote.*"))


def test_paused_observability_verification_reports_ssh_failure_without_disclosure(
    tmp_path: Path, remote_boundary: Path
) -> None:
    environment, sentinel = _remote_environment(tmp_path, remote_boundary)
    environment.update(
        RELEASE_SHA="a" * 40,
        OBSERVABILITY_SOURCE_MANIFEST_SHA256="b" * 64,
        FAIL_SSH="1",
    )

    result = _run_helper(["verify-paused-observability-release"], environment)

    assert result.returncode == 2
    assert result.stderr == "[staging-remote] stage=remote status=error code=remote_failed\n"
    assert sentinel not in result.stdout + result.stderr
    assert not list(tmp_path.glob("findme-staging-remote.*"))


def test_benchmark_operation_preserves_inputs_inside_the_remote_environment(
    tmp_path: Path, remote_boundary: Path
) -> None:
    environment, sentinel = _remote_environment(tmp_path, remote_boundary)
    environment.update(
        BENCHMARK_OPERATION="baseline",
        BENCHMARK_EVENT_SLUG="summer-event",
        BENCHMARK_SOURCE_RUN_UUID="",
        SSH_STDOUT="BENCHMARK_RUN_ID=00000000-0000-0000-0000-000000000000",
    )

    result = _run_helper(["face-embedding-benchmark"], environment)

    assert result.returncode == 0, result.stderr
    remote_environment = Path(environment["SSH_STDIN"]).read_text(encoding="utf-8")
    assert 'BENCHMARK_OPERATION="baseline"' in remote_environment
    assert 'BENCHMARK_EVENT_SLUG="summer-event"' in remote_environment
    assert 'BENCHMARK_SOURCE_RUN_UUID=""' in remote_environment
    assert result.stdout == (
        "BENCHMARK_RUN_ID=00000000-0000-0000-0000-000000000000\n"
        "[staging-remote] stage=face-embedding-benchmark status=ok\n"
    )
    assert sentinel not in result.stdout + result.stderr


def test_remote_failure_is_sanitized_and_cleans_private_files(
    tmp_path: Path, remote_boundary: Path
) -> None:
    environment, sentinel = _remote_environment(tmp_path, remote_boundary)
    environment["FAIL_SSH"] = "1"

    result = _run_helper(["verify-staging-image"], environment)

    assert result.returncode == 2
    assert result.stderr == "[staging-remote] stage=remote status=error code=remote_failed\n"
    assert sentinel not in result.stdout + result.stderr
    assert not list(tmp_path.glob("findme-staging-remote.*"))


@pytest.mark.parametrize("signal_number", [signal.SIGHUP, signal.SIGINT, signal.SIGTERM])
def test_signals_reach_active_transport_and_cleanup_private_files(
    signal_number: signal.Signals, tmp_path: Path, remote_boundary: Path
) -> None:
    environment, sentinel = _remote_environment(tmp_path, remote_boundary)
    environment["BLOCK_SSH"] = "1"
    process = subprocess.Popen(
        [HELPER, "verify-staging-image"],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    ready = Path(environment["SSH_READY"])
    _wait_for_transport(process, ready)
    child_pid = int(Path(environment["SSH_CHILD_PID"]).read_text(encoding="utf-8"))

    os.kill(process.pid, signal_number)
    try:
        stdout, stderr = process.communicate(timeout=4)
    except subprocess.TimeoutExpired:
        _kill_test_process_group(process)
        pytest.fail("helper did not finish signal cleanup before the resolver deadline")

    assert process.returncode == 128 + signal_number
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
    assert Path(environment["SSH_SIGNAL"]).read_text(encoding="utf-8").splitlines() == [
        signal_number.name[3:]
    ]
    assert (stdout, stderr) == ("", "")
    assert sentinel not in stdout + stderr
    assert not list(tmp_path.glob("findme-staging-remote.*"))


def test_resistant_transport_is_forced_down_before_the_resolver_deadline(
    tmp_path: Path, remote_boundary: Path
) -> None:
    environment, sentinel = _remote_environment(tmp_path, remote_boundary)
    environment.update(BLOCK_SSH="1", RESIST_FIRST_SIGNAL="1")
    process = subprocess.Popen(
        [HELPER, "verify-staging-image"],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    ready = Path(environment["SSH_READY"])
    _wait_for_transport(process, ready)
    child_pid = int(Path(environment["SSH_CHILD_PID"]).read_text(encoding="utf-8"))

    os.kill(process.pid, signal.SIGHUP)
    try:
        stdout, stderr = process.communicate(timeout=4)
    except subprocess.TimeoutExpired:
        _kill_test_process_group(process)
        pytest.fail("resistant transport outlived the helper shutdown deadline")

    assert process.returncode == 128 + signal.SIGHUP
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
    assert Path(environment["SSH_SIGNAL"]).read_text(encoding="utf-8").splitlines() == [
        "HUP",
        "TERM",
    ]
    assert (stdout, stderr) == ("", "")
    assert sentinel not in stdout + stderr
    assert not list(tmp_path.glob("findme-staging-remote.*"))


def test_public_monitor_keeps_api_key_out_of_argv_output_and_remote_transport(
    tmp_path: Path, remote_boundary: Path
) -> None:
    checkout = tmp_path / "checkout"
    (checkout / "deploy").mkdir(parents=True)
    (checkout / "scripts").mkdir()
    helper = checkout / "deploy/run-staging-remote.sh"
    helper.write_bytes(HELPER.read_bytes())
    helper.chmod(0o755)
    record = tmp_path / "monitor-record"
    (checkout / "scripts/monitor_public_health.py").write_text(
        textwrap.dedent(
            f"""
            import sys
            from dataclasses import dataclass

            @dataclass(frozen=True)
            class ProbeConfig:
                target: str
                folder_id: str
                environment: str
                check_name: str
                api_key: str

            def run_probe(config):
                assert config.api_key == "monitor-api-key-sentinel"
                assert config.api_key not in sys.argv
                with open({str(record)!r}, "w", encoding="utf-8") as stream:
                    stream.write("|".join(sys.argv))
                return 0
            """
        ),
        encoding="utf-8",
    )
    environment, _ = _remote_environment(tmp_path, remote_boundary)
    Path(environment["FINDME_ENV_FILE"]).write_text(
        'YANDEX_MONITORING_API_KEY="monitor-api-key-sentinel"\n', encoding="utf-8"
    )
    environment.update(
        MONITOR_TARGET="https://findme-photo.ru/health/",
        MONITOR_ENVIRONMENT="staging",
        MONITOR_CHECK="canonical-health",
        YANDEX_CLOUD_FOLDER_ID="folder-id",
    )

    result = _run_helper(["public-monitor"], environment, helper=helper)

    assert result.returncode == 0, result.stderr
    assert record.exists()
    assert "monitor-api-key-sentinel" not in record.read_text(encoding="utf-8")
    assert "monitor-api-key-sentinel" not in result.stdout + result.stderr
    assert not Path(environment["SSH_ARGUMENTS"]).exists()
    assert not Path(environment["SCP_ARGUMENTS"]).exists()


def test_remote_helper_rejects_non_private_ssh_key_before_any_remote_command(
    tmp_path: Path, remote_boundary: Path
) -> None:
    environment, sentinel = _remote_environment(tmp_path, remote_boundary)
    key = tmp_path / "staging-key"
    key.chmod(0o644)
    assert stat.S_IMODE(key.stat().st_mode) == 0o644

    result = _run_helper(["verify-staging-image"], environment)

    assert result.returncode == 2
    assert not Path(environment["SCP_ARGUMENTS"]).exists()
    assert not Path(environment["SSH_ARGUMENTS"]).exists()
    assert result.stderr == "[staging-remote] stage=key status=error code=key_not_private\n"
    assert sentinel not in result.stdout
    assert sentinel not in result.stderr
