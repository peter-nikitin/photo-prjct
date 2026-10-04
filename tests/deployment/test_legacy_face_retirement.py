"""Native-only candidate activation establishes the forward recovery boundary."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_native_only_activation_establishes_forward_recovery_boundary(tmp_path):
    source = (ROOT / "deploy/apply-deployment.sh").read_text()
    boundary = source.index("native_only_activation_started=1")
    assert (
        source.index('mv "$requested_env_tmp" "$DEPLOY_ROOT/.env"')
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
