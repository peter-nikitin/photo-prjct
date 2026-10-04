"""Contraction cannot precede candidate activation or trigger old-image rollback."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_retirement_order_and_failed_postcommit_guard(tmp_path):
    source = (ROOT / "deploy/apply-deployment.sh").read_text()
    commit = source.index("deployment_committed=1")
    drop = source.index("retire_json_face_embeddings --execute")
    assert source.index("retire_json_face_embeddings;") < source.index(
        'mv "$requested_env_tmp" "$DEPLOY_ROOT/.env"'
    )
    assert source.index("phase local-health") < commit < drop
    assert '"$deployment_committed" -eq 0' in source
    tail = source[source.index("# Physical contraction is deliberately") :]
    phase_function = re.search(r"^phase\(\) \{\n.*?^\}", source, re.M | re.S)[0]
    script = (
        """elapsed_seconds() { echo 0; }
"""
        + phase_function
        + """
RETIRE_JSON_FACE_EMBEDDINGS=True
JSON_FACE_RETIREMENT_REVIEWED=True
JSON_FACE_OLD_PROCESSES_DRAINED=True
requested_image=candidate
compose() { echo container; }
docker() {
  case "$1" in
    inspect) echo candidate;;
    image) echo 0123456789012345678901234567890123456789;;
  esac
}
run_private_candidate_command() { return 1; }
fail() { echo "$1"; exit 1; }
"""
    )
    result = subprocess.run(["/bin/sh", "-eu", "-c", script + tail], capture_output=True, text=True)
    assert result.returncode == 1
    assert "DEPLOY_JSON_RETIREMENT_RESULT=incomplete" in result.stdout
    assert "recover forward" in result.stdout
    assert "recover_previous_deployment" not in tail


def test_retirement_without_review_leaves_schema_retained():
    source = (ROOT / "deploy/apply-deployment.sh").read_text()
    tail = source[source.index("# Physical contraction is deliberately") :]
    result = subprocess.run(["/bin/sh", "-eu", "-c", tail], capture_output=True, text=True, env={})
    assert result.returncode == 0
    assert result.stdout.strip() == "DEPLOY_JSON_RETIREMENT_RESULT=retained"


def test_native_only_activation_establishes_forward_recovery_boundary(tmp_path):
    source = (ROOT / "deploy/apply-deployment.sh").read_text()
    boundary = source.index("native_only_activation_started=1")
    assert (
        source.index("retire_json_face_embeddings;")
        < boundary
        < source.index("if compose_up_command;")
    )
    trap = source[source.index("on_exit() {", source.index("recover_previous_deployment()")) :]
    trap = re.search(r"^on_exit\(\) \{\n.*?^\}", trap, re.M | re.S)[0]
    for activated, expected in [(0, "old-image-restored"), (1, "candidate-stopped")]:
        harness = f'''mutation_started=1
deployment_committed=0
native_only_activation_started={activated}
RECOVER_FORWARD=False
forward_verify_only=0
recovery_in_progress=0
observability_installed=0
candidate_import_worker_start_attempted=0
DEPLOY_ROOT="{tmp_path}"
deployment_phase=local-health
previous_upload_enabled=False
previous_cart_cleanup_present=True
cleanup() {{ :; }}
elapsed_seconds() {{ echo 0; }}
recover_previous_deployment() {{ echo old-image-restored; }}
compose() {{ echo candidate-stopped; }}
restore_previous_deployment_markers() {{ :; }}
sh() {{ :; }}
'''
        result = subprocess.run(
            ["/bin/sh", "-eu", "-c", harness + trap + "\ntrap on_exit EXIT; false"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert expected in result.stdout
        if activated:
            assert "old-image-restored" not in result.stdout
