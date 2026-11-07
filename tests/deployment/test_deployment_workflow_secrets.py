from __future__ import annotations

import os
import shutil
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
HELPER = ROOT / "deploy/run-remote.sh"


def test_remote_helper_is_directly_executable_by_the_resolver() -> None:
    assert HELPER.stat().st_mode & stat.S_IXUSR


def _workflow(name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    steps = [step for step in job["steps"] if step.get("name") == name]
    assert len(steps) == 1, f"expected one {name!r} step"
    return steps[0]


def test_public_monitor_helper_accepts_the_exact_projected_environment_without_network(
    tmp_path: Path,
) -> None:
    private_environment = tmp_path / "public-monitor.env"
    private_environment.write_text('YANDEX_MONITORING_API_KEY="private-monitor-token"\n')
    private_environment.chmod(0o600)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    invocation = tmp_path / "monitor-invocation"
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'printf \'%s|%s|%s|%s\\n\' "$MONITOR_TARGET" "$MONITOR_CHECK" '
        '"$YANDEX_CLOUD_FOLDER_ID" "$*" > "$PUBLIC_MONITOR_INVOCATION"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    result = subprocess.run(
        ["sh", HELPER, "public-monitor"],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FINDME_ENV_FILE": str(private_environment),
            "MONITOR_TARGET": "https://findme-photo.ru/health/",
            "MONITOR_CHECK": "canonical-health",
            "YANDEX_CLOUD_FOLDER_ID": "folder-id",
            "PUBLIC_MONITOR_INVOCATION": str(invocation),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "[remote] stage=public-monitor status=ok\n"
    assert stat.S_IMODE(private_environment.stat().st_mode) == 0o600
    invocation_line = invocation.read_text(encoding="utf-8")
    assert invocation_line.startswith("https://findme-photo.ru/health/|canonical-health|folder-id|")
    assert str(private_environment) in invocation_line
    assert "private-monitor-token" not in result.stdout + result.stderr


def _write_executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\nset -eu\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)


def _deployment_values() -> dict[str, str]:
    return {
        "APP_IMAGE": "ghcr.io/peter-nikitin/photo-prjct:test-image",
        "DEPLOY_WEB": "true",
        "DEPLOY_IMPORT": "false",
        "DEPLOY_COMMERCE": "false",
        "IMPORT_WORKER_IMAGE": "import:test-image",
        "PHOTO_IMPORT_BUILD": "test-image",
        "PHOTO_IMPORT_ENABLED": "False",
        "WORKER_IMAGE": "ghcr.io/peter-nikitin/photo-prjct-worker:test-image",
        "DEBUG": "False",
        "ALLOWED_HOSTS": "staging.findme-photo.ru",
        "GUNICORN_WORKERS": "5",
        "GUNICORN_THREADS": "2",
        "GUNICORN_TIMEOUT": "180",
        "GUNICORN_MAX_REQUESTS": "0",
        "GUNICORN_MAX_REQUESTS_JITTER": "0",
        "DB_NAME": "photo",
        "DB_USER": "photo",
        "PUBLIC_DOMAIN": "findme-photo.ru",
        "PUBLIC_DOMAIN_ALIAS": "www.findme-photo.ru",
        "GALLERY_CDN_ORIGIN": "https://img.findme-photo.ru",
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
        "PHOTO_WORKER_BULK_PROCESSOR_IDENTITIES": (
            "1/capture_metadata/2,2/generate_preview/1,"
            "2/generate_watermarked_preview/1,"
            "3/face_embedding/5,1/bib_recognition/1"
        ),
        "PHOTO_WORKER_BULK_PROCESSOR_TYPES": (
            "bib_recognition,face_embedding,capture_metadata,generate_preview,"
            "generate_watermarked_preview"
        ),
        "PHOTO_WORKER_BULK_HTTP_TIMEOUT_SECONDS": "180",
        "PHOTO_WORKER_SELFIE_PROCESSOR_IDENTITIES": "1/selfie_query/2",
        "PHOTO_WORKER_SELFIE_PROCESSOR_TYPES": "selfie_query",
        "PHOTO_WORKER_SELFIE_HTTP_TIMEOUT_SECONDS": "900",
        "PHOTO_WORKER_REPLICAS": "1",
        "PHOTO_WORKER_CPUS": "2.0",
        "PHOTO_WORKER_MEMORY_LIMIT": "3584m",
        "SELFIE_SEARCH_MAX_UPLOAD_BYTES": "20971520",
        "SELFIE_SEARCH_MAX_PIXELS": "25000000",
        "SELFIE_SEARCH_DOWNLOAD_TTL_SECONDS": "120",
        "SELFIE_SEARCH_TEMPORARY_PREFIX": "selfie-search/",
        "SELFIE_SEARCH_LIFECYCLE_MAX_AGE_HOURS": "24",
        "SELFIE_FEEDBACK_ENABLED": "False",
        "SELFIE_FEEDBACK_S3_BUCKET": "feedback",
        "SELFIE_FEEDBACK_S3_ENDPOINT_URL": "https://storage.yandexcloud.net",
        "SELFIE_FEEDBACK_S3_REGION": "ru-central1",
        "SELFIE_FEEDBACK_KMS_KEY_ID": "kms-id",
        "SELFIE_FEEDBACK_STORAGE_PREFLIGHT_CONFIRMED": "False",
        "COMMERCE_WORKER_ENABLED": "True",
        "COMMERCE_PUBLIC_ORIGIN": "https://findme-photo.ru",
        "COMMERCE_PAYMENT_GATEWAY_FACTORY": (
            "commerce.payment_simulator.payment_simulator_gateway_factory"
        ),
        "COMMERCE_EMAIL_SENDER_FACTORY": (
            "commerce.postbox_email_sender.postbox_email_sender_factory"
        ),
        "COMMERCE_WORKER_FACTORY": "commerce.runtime.commerce_worker_factory",
        "COMMERCE_EMAIL_FROM_ADDRESS": "orders@findme-photo.ru",
        "COMMERCE_SUPPORT_CONTACT": "support@findme-photo.ru",
        "COMMERCE_WORKER_HEALTH_MAX_READY_AGE_SECONDS": "300",
        "GHCR_USERNAME": "peter-nikitin",
    }


@pytest.fixture
def remote_boundary(tmp_path: Path) -> Path:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    _write_executable(binary_dir / "flock", "exit 0")
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
        deployment_target="$VM_USER@$VM_HOST:/opt/photo-prjct/.deployment-candidate."
        if [ "${target#"$deployment_target"}" != "$target" ] && \\
           [ "${EXECUTE_REMOTE_DEPLOY:-0}" = 1 ]; then
          source=''
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
            source=$argument
          done
          [ -n "$source" ] || exit 53
          cp "$source" "$REMOTE_DEPLOY_ROOT/${target##*/}"
          exit 0
        fi
        release_sha=${RELEASE_SHA:-missing-release-sha}
        release_root="/opt/photo-prjct/privileged-observability-releases/$release_sha"
        root_target="$VM_USER@$VM_HOST:$release_root/"
        deploy_target="$VM_USER@$VM_HOST:$release_root/deploy/"
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
            root:observability-release-sha)
              : > "$destination/observability-release-sha"
              ;;
            root:observability-source.sha256)
              : > "$destination/observability-source.sha256"
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
        if [ "${SSH_FAIL_AFTER_OUTPUT:-0}" = 1 ]; then
          [ -z "${SSH_STDOUT:-}" ] || printf '%s\n' "$SSH_STDOUT"
          exit 48
        fi
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
        case "$remote_command" in
          *" 'deploy'")
            if [ "${EXECUTE_REMOTE_DEPLOY:-0}" = 1 ]; then
              escaped_remote_root=$(printf '%s' "$REMOTE_DEPLOY_ROOT" | sed 's/[&|]/\\&/g')
              rewritten_command="$(
                printf '%s' "$remote_command" | sed "s|/opt/photo-prjct|$escaped_remote_root|g"
              )"
              sh -c "$rewritten_command" < "$SSH_STDIN"
              exit $?
            fi
            ;;
          *" 'verify-deployed-image'")
            escaped_remote_root=$(printf '%s' "$REMOTE_DEPLOY_ROOT" | sed 's/[&|]/\\&/g')
            rewritten_command="$(
              printf '%s' "$remote_command" | sed "s|/opt/photo-prjct|$escaped_remote_root|g"
            )"
            sh -c "$rewritten_command" < "$SSH_STDIN"
            exit $?
            ;;
        esac
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
    remote_deploy_root = tmp_path / "remote-photo-prjct"
    remote_deploy_root.mkdir()
    (remote_deploy_root / "deployed-image").write_text(
        "ghcr.io/peter-nikitin/photo-prjct:test-image\n", encoding="utf-8"
    )
    (remote_deploy_root / ".env").write_text(
        "PHOTO_WORKER_BULK_PROCESSOR_IDENTITIES="
        "1/capture_metadata/2,2/generate_preview/1,2/generate_watermarked_preview/1,"
        "3/face_embedding/5,1/bib_recognition/1\n"
        "PHOTO_WORKER_SELFIE_PROCESSOR_IDENTITIES=1/selfie_query/2\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        **_deployment_values(),
        "PATH": f"{remote_boundary}{os.pathsep}{os.environ['PATH']}",
        "TMPDIR": str(tmp_path),
        "FINDME_ENV_FILE": str(environment_file),
        "VM_HOST": "staging.example.test",
        "VM_USER": "deployer",
        "VM_SSH_KNOWN_HOSTS": "staging.example.test ssh-ed25519 known-host-sentinel",
        "SCP_ARGUMENTS": str(tmp_path / "scp-arguments"),
        "SCP_CALLS": str(tmp_path / "scp-calls"),
        "SSH_ARGUMENTS": str(tmp_path / "ssh-arguments"),
        "SSH_STDIN": str(tmp_path / "ssh-stdin"),
        "SSH_CHILD_PID": str(tmp_path / "ssh-child-pid"),
        "SSH_SIGNAL": str(tmp_path / "ssh-signal"),
        "SSH_READY": str(tmp_path / "ssh-ready"),
        "REMOTE_RELEASES": str(tmp_path / "remote-releases"),
        "REMOTE_DEPLOY_ROOT": str(remote_deploy_root),
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


def _initial_deployment_helper(tmp_path: Path, *, apply_status: int) -> tuple[Path, Path, bytes]:
    project_root = tmp_path / "candidate-project"
    deploy_dir = project_root / "deploy"
    deploy_dir.mkdir(parents=True)
    helper = deploy_dir / "run-remote.sh"
    shutil.copy2(HELPER, helper)
    (deploy_dir / "worker-pools").mkdir()
    shutil.copy2(ROOT / "deploy/package-deployment.sh", deploy_dir / "package-deployment.sh")
    for name in ("__init__.py", "services/__init__.py", "services/worker_pool_cloud.py"):
        target = project_root / "src/backend/processing" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "src/backend/processing" / name, target)
    candidate_compose = b"services:\n  web:\n    image: ${APP_IMAGE}\n"
    (project_root / "docker-compose.deployment.yml").write_bytes(candidate_compose)
    (project_root / "docker-compose.https.yml").write_text(
        "services:\n  nginx:\n    image: nginx:candidate\n", encoding="utf-8"
    )
    apply_log = tmp_path / "remote-apply.log"
    _write_executable(
        deploy_dir / "apply-deployment.sh",
        f"printf 'apply-invoked\\n' >> {apply_log}\nexit {apply_status}",
    )
    return helper, apply_log, candidate_compose


def _initial_deployment_environment(
    tmp_path: Path, remote_boundary: Path
) -> tuple[dict[str, str], str]:
    environment, sentinel = _remote_environment(tmp_path, remote_boundary)
    remote_root = Path(environment["REMOTE_DEPLOY_ROOT"])
    (remote_root / ".env").unlink()
    (remote_root / "deployed-image").unlink()
    environment["EXECUTE_REMOTE_DEPLOY"] = "1"
    return environment, sentinel


def test_partial_candidate_install_failure_preserves_untouched_predecessor_entries(
    tmp_path: Path, remote_boundary: Path
) -> None:
    helper, apply_log, _candidate_compose = _initial_deployment_helper(tmp_path, apply_status=0)
    environment, _sentinel = _initial_deployment_environment(tmp_path, remote_boundary)
    remote_root = Path(environment["REMOTE_DEPLOY_ROOT"])
    remote_deploy = remote_root / "deploy"
    remote_deploy.mkdir()
    (remote_deploy / "worker-pools").mkdir()
    (remote_deploy / "worker-pools/release.py").write_text(
        "def require_native_compatible_release(*args): pass\n"
    )
    (remote_root / "worker-pools-current.json").write_text("{}")
    (remote_root / "worker-pools-release.json").write_text('{"phase":"committed"}')
    previous_compose = b"previous main compose\n"
    previous_overlay = b"previous https compose\n"
    previous_helper = b"previous helpers\n"
    (remote_root / "docker-compose.deployment.yml").write_bytes(previous_compose)
    (remote_root / "docker-compose.https.yml").write_bytes(previous_overlay)
    (remote_deploy / "package-version").write_bytes(previous_helper)
    _write_executable(
        remote_boundary / "mv",
        """
        case "${1-}" in
          */.deployment-candidate.*/docker-compose.deployment.yml) exit 61 ;;
        esac
        exec /bin/mv "$@"
        """,
    )

    result = _run_helper(["deploy"], environment, helper=helper)

    assert result.returncode == 2
    assert not apply_log.exists()
    assert (remote_root / "docker-compose.deployment.yml").read_bytes() == previous_compose
    assert (remote_root / "docker-compose.https.yml").read_bytes() == previous_overlay
    assert (remote_root / "deploy" / "package-version").read_bytes() == previous_helper
    assert list(remote_root.glob(".deployment-*")) == []


def test_public_monitor_keeps_api_key_out_of_argv_output_and_remote_transport(
    tmp_path: Path, remote_boundary: Path
) -> None:
    checkout = tmp_path / "checkout"
    (checkout / "deploy").mkdir(parents=True)
    (checkout / "scripts").mkdir()
    helper = checkout / "deploy/run-remote.sh"
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
