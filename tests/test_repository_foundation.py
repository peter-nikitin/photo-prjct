import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load_workflow(workflow_name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / ".github/workflows" / workflow_name).read_text(encoding="utf-8"))


def _workflow_step(workflow: dict[str, Any], job_name: str, step_name: str) -> dict[str, Any]:
    matching_steps = [
        step for step in workflow["jobs"][job_name]["steps"] if step.get("name") == step_name
    ]
    assert len(matching_steps) == 1, f"Expected one {step_name!r} step"
    return matching_steps[0]


def _notification_arguments_from_workflow(
    tmp_path: Path,
    *,
    build_result: str,
    deploy_result: str,
    release_sha: str,
    failed_log: str,
) -> list[str]:
    workflow = _load_workflow("deploy.yml")
    run = _workflow_step(workflow, "reconcile-deploy-issue", "Reconcile issue state")["run"]
    run = run.replace("${{ needs.build.result }}", build_result)
    run = run.replace("${{ needs.deploy.result }}", deploy_result)
    run = run.replace("${{ needs.classify-release.outputs.release_sha }}", release_sha)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    phase_arguments = tmp_path / "phase-arguments"
    for name, source in {
        "gh": '#!/bin/sh\nprintf "%s\\n" "$GH_LOG"\n',
        "python": '#!/bin/sh\nprintf "%s\\n" "$@" > "$PHASE_ARGUMENTS"\n',
    }.items():
        executable = fake_bin / name
        executable.write_text(source, encoding="utf-8")
        executable.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "GH_LOG": failed_log,
        "PHASE_ARGUMENTS": str(phase_arguments),
        "GITHUB_REPOSITORY": "findme/photo",
        "GITHUB_SHA": "f" * 40,
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": "42",
    }
    subprocess.run(["/bin/sh", "-c", run], check=True, cwd=ROOT, env=environment)
    return phase_arguments.read_text(encoding="utf-8").splitlines()


def _notification_phase_from_workflow(tmp_path: Path, failed_log: str) -> str:
    arguments = _notification_arguments_from_workflow(
        tmp_path,
        build_result="success",
        deploy_result="failure",
        release_sha="a" * 40,
        failed_log=failed_log,
    )
    return arguments[arguments.index("--phase") + 1]


def test_adr_index_lists_all_accepted_decisions() -> None:
    index = (ROOT / "docs/adr/README.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs/architecture.md").read_text(encoding="utf-8")
    open_decisions = architecture.partition("## Open decisions")[2].partition("## Change rules")[0]

    for number in (*range(1, 8), 11, 12, 13, 14, 17):
        assert re.search(rf"\| {number:04d} \|.*\| Accepted \|", index)
    for number in (8, 9, 10):
        assert re.search(rf"\| {number:04d} \|.*\| Superseded \|", index)
    assert "Authentication model and photographer/operator permissions" not in open_decisions
    assert "Private media lifecycle and retention policy" not in open_decisions
    assert "Background task framework, broker, retry semantics" not in open_decisions
    assert "Stage 3 background-processing worker, broker, retry contract" not in open_decisions
    assert "Stage 3 processing SLA" in open_decisions


def _envs(step: dict[str, Any]) -> set[str]:
    if "env" in step:
        return set(step["env"])
    envs = step.get("with", {}).get("envs", "")
    assert isinstance(envs, str)
    return {name.strip() for name in envs.split(",") if name.strip()}


def test_project_skill_ui_configuration_is_valid() -> None:
    for skill_name in (
        "deliver-operational-change",
        "execute-implementation-plan",
        "manage-yandex-cloud",
        "update-visual-design",
        "write-adr",
        "write-plan",
        "write-spec",
    ):
        skill_dir = ROOT / ".agents" / "skills" / skill_name
        ui_config = yaml.safe_load(
            (skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")
        )
        assert set(ui_config["interface"]) == {
            "display_name",
            "short_description",
            "default_prompt",
        }


def test_implementation_plan_harness_has_project_specific_role_contracts() -> None:
    skill_dir = ROOT / ".agents" / "skills" / "execute-implementation-plan"
    skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")

    assert "superpowers:subagent-driven-development" in skill
    assert "make worktree" in skill
    assert "working-tree diff" in skill
    assert "blocking" in skill
    assert "future" in skill
    assert "luna_worker" in skill
    assert "git add" in skill

    for prompt_name, required_fields in {
        "implementer-prompt.md": {"Worktree", "Task brief", "Report", "Model reason"},
        "reviewer-prompt.md": {
            "Task brief",
            "Implementer report",
            "Review package",
            "Risk class",
        },
        "re-review-prompt.md": {"Review package", "Prior review", "Fix report"},
    }.items():
        prompt = (skill_dir / prompt_name).read_text(encoding="utf-8")
        assert required_fields <= set(re.findall(r"^([A-Z][A-Za-z ]+):", prompt, re.MULTILINE))


def test_implementation_review_package_includes_tracked_and_untracked_files(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repository, check=True)
    tracked = repository / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repository, check=True)

    tracked.write_text("after\n", encoding="utf-8")
    (repository / "untracked.txt").write_text("new task file\n", encoding="utf-8")
    output = repository / "review.md"
    script = (
        ROOT
        / ".agents"
        / "skills"
        / "execute-implementation-plan"
        / "scripts"
        / "review-package.py"
    )

    subprocess.run(
        [sys.executable, str(script), str(output)],
        cwd=repository,
        check=True,
    )

    package = output.read_text(encoding="utf-8")
    assert "tracked.txt" in package
    assert "-before" in package
    assert "+after" in package
    assert "untracked.txt" in package
    assert "+new task file" in package


def test_ci_reuses_visual_image_with_read_only_package_access() -> None:
    ci = _load_workflow("ci.yml")
    quality = ci["jobs"]["quality"]

    assert ci[True]["push"]["branches"] == ["main"]
    assert "pull_request" in ci[True]
    assert quality["permissions"] == {"contents": "read", "packages": "read"}

    login = _workflow_step(ci, "quality", "Log in to GHCR for visual tests")
    visual = _workflow_step(ci, "quality", "Run containerized visual regression tests")
    assert login["uses"] == "docker/login-action@v3"
    assert login["with"] == {
        "registry": "ghcr.io",
        "username": "${{ github.actor }}",
        "password": "${{ secrets.GITHUB_TOKEN }}",
    }
    assert visual["env"] == {
        "VISUAL_TEST_IMAGE_PREFIX": "ghcr.io/${{ github.repository }}-visual-tests"
    }
    assert "PUSH_VISUAL_TEST_IMAGE" not in visual["env"]


def test_ci_checks_pull_request_migration_identity_with_full_git_history() -> None:
    ci = _load_workflow("ci.yml")
    checkout = _workflow_step(ci, "quality", "Check out repository")
    immutability = _workflow_step(ci, "quality", "Check migration immutability")
    migration_drift = _workflow_step(ci, "quality", "Check migration drift")

    assert checkout["with"] == {"fetch-depth": 0}
    assert immutability["if"] == "github.event_name == 'pull_request'"
    assert immutability["run"] == (
        "python scripts/check_migration_immutability.py "
        "--base ${{ github.event.pull_request.base.sha }} "
        "--head ${{ github.event.pull_request.head.sha }}"
    )
    assert migration_drift["run"] == "python src/backend/manage.py makemigrations --check --dry-run"


def test_public_health_monitor_workflow_is_scheduled_and_uses_only_its_monitoring_credentials() -> (
    None
):
    workflow = _load_workflow("monitor-public-health.yml")
    dispatch = workflow[True]["workflow_dispatch"]
    job = workflow["jobs"]["probe"]
    checkout = _workflow_step(workflow, "probe", "Check out repository")
    run_probe = _workflow_step(workflow, "probe", "Probe public health and write metrics")

    assert workflow[True]["schedule"] == [{"cron": "*/5 * * * *"}]
    assert dispatch["inputs"]["target"] == {
        "description": "Controlled public health target for validation metrics",
        "required": True,
        "default": "https://findme-photo.ru/health/",
        "type": "choice",
        "options": [
            "https://findme-photo.ru/health/",
            "https://example.invalid/health/",
        ],
    }
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert "environment" not in job
    assert checkout["uses"] == "actions/checkout@v4"
    assert checkout["with"] == {"persist-credentials": False}
    assert run_probe["env"] == {
        "MONITOR_TARGET": (
            "${{ github.event_name == 'schedule' && "
            "'https://findme-photo.ru/health/' || inputs.target }}"
        ),
        "MONITOR_CHECK": (
            "${{ github.event_name == 'schedule' && 'canonical-health' || 'validation-health' }}"
        ),
        "YANDEX_CLOUD_FOLDER_ID": "${{ vars.YANDEX_CLOUD_FOLDER_ID }}",
    }
    command = run_probe["run"]
    assert "scripts/run-with-environment-secrets.py" in command
    assert "--consumer public-monitor" in command
    assert "--identity github-oidc" in command
    assert "public-monitor" in command
    assert "YANDEX_MONITORING_API_KEY" not in command


def test_root_quality_contract_includes_processing_and_standalone_worker() -> None:
    """Delivered processing code must be collected, typed, and counted by the root CI commands."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]
    pytest_config = pyproject["pytest"]["ini_options"]
    ci = _load_workflow("ci.yml")

    assert pyproject["mypy"]["files"] == ["src/backend", "src/worker/photo_worker"]
    assert pytest_config["pythonpath"] == [".", "src/backend", "src/worker"]
    assert pytest_config["testpaths"] == ["src/backend", "src/worker/tests", "tests"]
    assert pyproject["coverage"]["run"]["source"] == [
        "src/backend/config",
        "src/backend/ingestion",
        "src/backend/picflow",
        "src/backend/processing",
        "src/backend/selfie_search",
        "src/worker/photo_worker",
    ]
    assert _workflow_step(ci, "quality", "Type check")["run"] == "mypy"
    assert _workflow_step(ci, "quality", "Test with coverage")["run"] == (
        "pytest --cov --cov-report=term-missing"
    )
    python_setup = _workflow_step(ci, "quality", "Set up Python")
    assert "src/worker/requirements.txt" in python_setup["with"]["cache-dependency-path"]
    assert _workflow_step(ci, "quality", "Install dependencies")["run"] == (
        "pip install -r requirements-dev.txt -r src/worker/requirements.txt"
    )
    assert ci["jobs"]["quality"]["env"]["TEST_DB_NAME"] == (
        "findme_test_${{ github.run_id }}_${{ github.run_attempt }}"
    )


def test_visual_image_publisher_is_main_only_and_dependency_keyed() -> None:
    publisher = _load_workflow("visual-test-image.yml")
    build = publisher["jobs"]["build"]
    push = publisher[True]["push"]

    assert set(publisher[True]) == {"push", "workflow_dispatch"}
    assert push["branches"] == ["main"]
    assert set(push["paths"]) == {
        ".github/workflows/visual-test-image.yml",
        "Dockerfile.visual-tests",
        "package-lock.json",
        "src/backend/requirements.txt",
    }
    assert build["permissions"] == {"contents": "read", "packages": "write"}

    image = _workflow_step(publisher, "build", "Select visual test image reference")
    login = _workflow_step(publisher, "build", "Log in to GHCR")
    publish = _workflow_step(publisher, "build", "Build and publish visual test image")
    assert image["id"] == "image"
    for dependency in (
        "Dockerfile.visual-tests",
        "package-lock.json",
        "src/backend/requirements.txt",
    ):
        assert f"git hash-object {dependency}" in image["run"]
    assert "ghcr.io/${GITHUB_REPOSITORY}-visual-tests:${dependency_key}" in image["run"]
    assert login["uses"] == "docker/login-action@v3"
    assert publish["uses"] == "docker/build-push-action@v6"
    assert publish["with"] == {
        "context": ".",
        "file": "./Dockerfile.visual-tests",
        "push": True,
        "tags": "${{ steps.image.outputs.image }}",
    }


def test_focused_deployment_scripts_are_versioned() -> None:
    for relative_path in (
        "deploy/certbot/reconcile-certificate.sh",
        "deploy/install-upload-cleanup-cron.sh",
        "deploy/run-upload-cleanup.sh",
        "deploy/verify-public-edge.sh",
    ):
        assert (ROOT / relative_path).is_file(), f"Missing {relative_path}"
    assert not (ROOT / "deploy/finalize-deployment.sh").exists()
    assert not (ROOT / "deploy/rollback-deployment.sh").exists()


def test_deployment_migration_history_preflight_is_versioned_and_read_only() -> None:
    command = ROOT / "src/backend/picflow/management/commands/verify_migration_history.py"
    apply = (ROOT / "deploy/apply-deployment.sh").read_text(encoding="utf-8")

    assert command.is_file()
    assert "MigrationLoader(connection, ignore_no_migrations=True)" in command.read_text(
        encoding="utf-8"
    )
    assert "MigrationRecorder(connection).applied_migrations()" in command.read_text(
        encoding="utf-8"
    )
    assert "migration-history-ok" in command.read_text(encoding="utf-8")
    assert "manage.py verify_migration_history" in apply
    assert "manage.py showmigrations --plan" in apply
    assert apply.index("manage.py verify_migration_history") < apply.index("mutation_started=1")
    assert apply.index("manage.py showmigrations --plan") < apply.index("mutation_started=1")
    assert apply.index("manage.py verify_migration_history") < apply.index(
        "verify_observability_bootstrap ||"
    )


def test_versioned_deployment_script_has_valid_shell_syntax() -> None:
    for relative_path in (
        "deploy/apply-deployment.sh",
        "deploy/install-upload-cleanup-cron.sh",
        "deploy/run-upload-cleanup.sh",
        "deploy/verify-public-edge.sh",
        "deploy/certbot/reconcile-certificate.sh",
    ):
        result = subprocess.run(
            ["sh", "-n", ROOT / relative_path],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{relative_path}: {result.stderr}"


def test_upload_cleanup_schedule_is_bounded_and_deployment_managed() -> None:
    apply_script = (ROOT / "deploy/apply-deployment.sh").read_text(encoding="utf-8")
    install_script = (ROOT / "deploy/install-upload-cleanup-cron.sh").read_text(encoding="utf-8")
    run_script = (ROOT / "deploy/run-upload-cleanup.sh").read_text(encoding="utf-8")

    assert install_script.count("# BEGIN photo-prjct-upload-cleanup") == 2
    assert install_script.count("# END photo-prjct-upload-cleanup") == 2
    assert "17 3 * * *" in install_script
    assert "crontab -l" in install_script
    assert "flock -n -E 75" in run_script
    assert "exec -T web python manage.py cleanup_stale_uploads" in run_script
    assert "DEPLOYMENT_TARGET" not in apply_script
    assert "project=photo-prjct" in run_script
    assert 'install-upload-cleanup-cron.sh" install' in apply_script
    assert 'install-upload-cleanup-cron.sh" remove' in apply_script


def test_django_trusts_the_https_scheme_from_the_edge_proxy() -> None:
    settings = (ROOT / "src/backend/config/settings.py").read_text(encoding="utf-8")

    assert 'SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")' in settings


def test_prototype_archive_and_legacy_demo_assets_are_removed() -> None:
    assert not (ROOT / "src/proto").exists()

    templates = ROOT / "src/backend/templates"
    assert not list(templates.glob("*.html")), "Legacy top-level UI templates remain"

    static = ROOT / "src/backend/static"
    assert not list(static.glob("*.js")), "Legacy demo JavaScript remains"
    assert not list((static / "assets").glob("*")), "Legacy duplicate demo assets remain"


def test_legacy_prototype_stylesheet_is_removed() -> None:
    static = ROOT / "src/backend/static"

    assert not (static / "styles.css").exists(), "Legacy prototype stylesheet remains"


def test_event_cards_keep_keyboard_focus_visible_inside_clipped_card() -> None:
    catalog_css = (ROOT / "src/backend/static/ui/catalog.css").read_text(encoding="utf-8")

    assert ".event-card:focus-within" in catalog_css
    assert ".event-card-link:focus-visible" in catalog_css
    assert "outline-offset: -4px" in catalog_css


def test_event_gallery_lightbox_respects_reduced_motion_and_touch_targets() -> None:
    catalog_css = (ROOT / "src/backend/static/ui/catalog.css").read_text(encoding="utf-8")

    assert ".glightbox-container .gclose," in catalog_css
    assert ".glightbox-container .gprev," in catalog_css
    assert ".glightbox-container .gnext" in catalog_css
    assert "min-width: 44px;" in catalog_css
    assert "min-height: 44px;" in catalog_css
    assert "@media (prefers-reduced-motion: reduce)" in catalog_css
    assert ".glightbox-container .gslider," in catalog_css
    assert ".glightbox-container .gfadeIn," in catalog_css
    assert ".glightbox-container .gzoomOut" in catalog_css
    assert "transition: none !important;" not in catalog_css
    assert "animation: none !important;" not in catalog_css
    assert "transition-duration: 0.01ms !important;" in catalog_css
    assert "animation-duration: 0.01ms !important;" in catalog_css
    assert "animation-iteration-count: 1 !important;" in catalog_css


def _runbook_section(runbook: str, title: str) -> str:
    heading = re.search(rf"^## {re.escape(title)}$", runbook, re.MULTILINE)
    assert heading, f"Missing runbook procedure: {title}"
    next_heading = re.search(r"^## ", runbook[heading.end() :], re.MULTILINE)
    if next_heading is None:
        return runbook[heading.start() :]
    return runbook[heading.start() : heading.end() + next_heading.start()]


def _markdown_table(source: str, title: str) -> tuple[tuple[str, ...], list[tuple[str, ...]]]:
    section = _runbook_section(source, title)
    lines = [line for line in section.splitlines() if line.startswith("|")]
    assert len(lines) >= 2, f"Missing Markdown table for {title}"

    header = tuple(cell.strip() for cell in lines[0].split("|")[1:-1])
    assert all(cell for cell in header)
    assert all(re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in lines[1].split("|")[1:-1])
    rows = [tuple(cell.strip() for cell in line.split("|")[1:-1]) for line in lines[2:]]
    assert all(len(row) == len(header) for row in rows)
    return header, rows


def _inline_code(value: str) -> str:
    assert value.startswith("`") and value.endswith("`")
    return value[1:-1]


def test_environment_secret_inventory_matches_the_complete_manifest_schema() -> None:
    """A schema change must update every exact key projection in the operator inventory."""
    manifest = json.loads((ROOT / "deploy/environment-secrets.json").read_text(encoding="utf-8"))
    inventory = (ROOT / "docs/runbooks/environment-secrets-inventory.md").read_text(
        encoding="utf-8"
    )

    header, rows = _markdown_table(inventory, "Manifest key schema and consumer projections")
    assert header == ("Manifest key", "Target", "Type", "Local", "Consumers")
    actual: dict[str, tuple[str, str, str, set[str]]] = {}
    for key, target, kind, local, consumers in rows:
        name = _inline_code(key)
        assert name not in actual, f"Duplicate manifest inventory row: {name}"
        actual[name] = (
            _inline_code(target),
            kind,
            local,
            {_inline_code(value.strip()) for value in consumers.split(",")},
        )

    expected = {
        entry["key"]: (
            entry["target"],
            entry["type"],
            "yes" if entry["local"] else "no",
            {name for name, keys in manifest["consumers"].items() if entry["key"] in keys},
        )
        for entry in manifest["entries"]
    }
    assert actual == expected

    workflows = set(
        re.findall(
            r"^- `([^`]+)`$",
            _runbook_section(inventory, "Stable deployment identity ledger"),
            re.MULTILINE,
        )
    )
    assert workflows == set(manifest["github_oidc"]["allowed_workflows"])


def test_environment_secret_inventory_maps_each_github_secret_to_its_exact_source() -> None:
    """Cleanup must remove the actual source authority, not a same-named secret elsewhere."""
    inventory = (ROOT / "docs/runbooks/environment-secrets-inventory.md").read_text(
        encoding="utf-8"
    )
    header, rows = _markdown_table(inventory, "GitHub staging secret migration inventory")
    assert header == (
        "Former GitHub Actions Secret",
        "Source scope",
        "Owner",
        "Destination",
        "Rotation trigger",
    )
    actual: dict[str, tuple[str, str, str, str]] = {}
    for name, source_scope, owner, destination, trigger in rows:
        secret_name = _inline_code(name)
        assert secret_name not in actual, f"Duplicate migration row: {secret_name}"
        actual[secret_name] = (source_scope, owner, destination, trigger)

    secret_id = "e6q85jjl76r45maigtfb"
    variable_destination = "`staging` GitHub Environment variable"
    expected = {
        "ALLOWED_HOSTS": ("repository", "Application maintainer", variable_destination),
        "DB_NAME": ("repository", "Database maintainer", variable_destination),
        "DB_PASSWORD": (
            "repository",
            "Database maintainer",
            f"Lockbox `{secret_id}` entry `DB_PASSWORD`",
        ),
        "DB_USER": ("repository", "Database maintainer", variable_destination),
        "GHCR_READ_TOKEN": (
            "repository",
            "Registry maintainer",
            f"Lockbox `{secret_id}` entry `GHCR_READ_TOKEN`",
        ),
        "GHCR_USERNAME": ("repository", "Registry maintainer", variable_destination),
        "LETSENCRYPT_EMAIL": (
            "repository",
            "Edge maintainer",
            f"Lockbox `{secret_id}` entry `LETSENCRYPT_EMAIL`",
        ),
        "MEDIA_S3_ACCESS_KEY_ID": (
            "repository",
            "Media storage maintainer",
            f"Lockbox `{secret_id}` entry `MEDIA_S3_ACCESS_KEY_ID`",
        ),
        "MEDIA_S3_SECRET_ACCESS_KEY": (
            "repository",
            "Media storage maintainer",
            f"Lockbox `{secret_id}` entry `MEDIA_S3_SECRET_ACCESS_KEY`",
        ),
        "PHOTO_PROCESSING_WORKER_TOKEN": (
            "staging Environment",
            "Processing maintainer",
            f"Lockbox `{secret_id}` entry `PHOTO_PROCESSING_WORKER_TOKEN`",
        ),
        "PRIVATE_MEDIA_S3_ACCESS_KEY_ID": (
            "repository",
            "Private media maintainer",
            f"Lockbox `{secret_id}` entry `PRIVATE_MEDIA_S3_ACCESS_KEY_ID`",
        ),
        "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY": (
            "repository",
            "Private media maintainer",
            f"Lockbox `{secret_id}` entry `PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY`",
        ),
        "SECRET_KEY": (
            "repository",
            "Application maintainer",
            f"Lockbox `{secret_id}` entry `SECRET_KEY`",
        ),
        "SELFIE_FEEDBACK_S3_ACCESS_KEY_ID": (
            "staging Environment",
            "Selfie feedback maintainer",
            f"Lockbox `{secret_id}` entry `SELFIE_FEEDBACK_S3_ACCESS_KEY_ID`",
        ),
        "SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY": (
            "staging Environment",
            "Selfie feedback maintainer",
            f"Lockbox `{secret_id}` entry `SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY`",
        ),
        "VM_HOST": ("repository", "Staging operations maintainer", variable_destination),
        "VM_SSH_KEY": (
            "repository",
            "Staging operations maintainer",
            f"Lockbox `{secret_id}` binary entry `VM_SSH_KEY`",
        ),
        "VM_USER": ("repository", "Staging operations maintainer", variable_destination),
        "YANDEX_MONITORING_API_KEY": (
            "staging Environment",
            "Monitoring maintainer",
            f"Lockbox `{secret_id}` entry `YANDEX_MONITORING_API_KEY`",
        ),
    }
    assert {name: values[:3] for name, values in actual.items()} == expected
    assert all(values[3] for values in actual.values())
    assert sum(values[0] == "repository" for values in actual.values()) == 15
    assert sum(values[0] == "staging Environment" for values in actual.values()) == 4


def test_environment_secret_docs_require_exact_secret_metadata_and_payload_reader_roles() -> None:
    """Resolver readers need metadata visibility as well as payload access, without broad IAM."""
    runbook = (ROOT / "docs/runbooks/environment-secrets.md").read_text(encoding="utf-8")
    inventory = (ROOT / "docs/runbooks/environment-secrets-inventory.md").read_text(
        encoding="utf-8"
    )

    for document in (runbook, inventory):
        assert "`lockbox.viewer`" in document
        assert "`lockbox.payloadViewer`" in document
        assert "exact secret" in document
    assert "metadata and access-binding view" in inventory
    assert "neither payload access nor secret management" in inventory
    assert "CI service account and approved human reader already have both exact-secret roles" in (
        inventory
    )
    assert "nor declares the rollout complete" in inventory
    assert "`VM_SSH_KNOWN_HOSTS`" in inventory
    assert "required non-secret `staging` GitHub Environment variable" in inventory


def test_environment_secret_runbook_has_safe_operator_procedures() -> None:
    """Removing any required operator check leaves a live change unsafe and must fail CI."""
    runbook_path = ROOT / "docs/runbooks/environment-secrets.md"
    operations_path = ROOT / "docs/operations.md"

    assert runbook_path.is_file(), "Missing environment-secrets operator runbook"
    assert operations_path.is_file(), "Missing operations index"
    runbook = runbook_path.read_text(encoding="utf-8")
    operations = operations_path.read_text(encoding="utf-8")

    for procedure in (
        "Setup",
        "Inventory",
        "Payload version validation and population",
        "Local use",
        "CI OIDC preflight",
        "Rotation",
        "Revocation",
        "Rollback",
        "Lost device or account recovery",
        "GitHub secret cleanup",
        "Incident and non-disclosure",
    ):
        section = _runbook_section(runbook, procedure)
        for check in (
            "### Preflight",
            "### Success evidence",
            "### Rollback",
            "### Non-disclosure",
        ):
            assert check in section, f"{procedure} lacks {check}"

    assert "fresh operator approval" in runbook.lower()
    assert "never command arguments" in runbook.lower()
    assert "payload rotation is not identity revocation" in runbook.lower()
    assert "surviving cloud organization administrator" in runbook.lower()
    assert "break-glass ownership" in runbook.lower()
    assert "[Environment secrets operator runbook](runbooks/environment-secrets.md)" in operations
    assert (
        "[Environment secrets inventory](runbooks/environment-secrets-inventory.md)" in operations
    )


def test_environment_secret_runbook_checks_exact_setup_and_github_source_scopes() -> None:
    """Least-privilege and cleanup evidence needs the provider scopes that actually hold values."""
    runbook = (ROOT / "docs/runbooks/environment-secrets.md").read_text(encoding="utf-8")
    secret_id = "e6q85jjl76r45maigtfb"
    service_account_id = "ajeaekiue94ogksguh0h"
    folder_id = "b1g2qttgfhb4gdunvlge"
    setup = _runbook_section(runbook, "Setup")

    for command in (
        f"yc iam key list --service-account-id {service_account_id} --format json",
        f"yc iam api-key list --service-account-id {service_account_id} --format json",
        f"yc iam access-key list --service-account-id {service_account_id} --format json",
        f"yc resource-manager folder list-access-bindings --id {folder_id} --format json",
        'yc resource-manager cloud list-access-bindings --id "$cloud_id" --format json',
    ):
        assert command in setup
    assert "all three key lists are empty" in setup.lower()
    assert re.search(r"absent from the folder and cloud\s+bindings", setup)
    assert "incident/design review" in setup

    inventory = _runbook_section(runbook, "Inventory")
    cleanup = _runbook_section(runbook, "GitHub secret cleanup")
    repository_list = "gh secret list --repo peter-nikitin/photo-prjct --json name,updatedAt"
    environment_list = (
        "gh secret list --repo peter-nikitin/photo-prjct --env staging --json name,updatedAt"
    )
    variable_list = (
        "gh variable list --repo peter-nikitin/photo-prjct --env staging --json name,updatedAt"
    )
    for section in (inventory, cleanup):
        assert repository_list in section
        assert environment_list in section
        assert variable_list in section
    cleanup_preflight = cleanup.split("### Success evidence", maxsplit=1)[0]
    expected_environment_variables = {
        "ALLOWED_HOSTS",
        "DB_NAME",
        "DB_USER",
        "GHCR_USERNAME",
        "VM_SSH_KNOWN_HOSTS",
        "VM_HOST",
        "VM_USER",
    }
    required_variables = re.search(
        r"The exact required `staging` Environment variable-name set\s+is:\n\n"
        r"(?P<names>(?:- `[A-Z0-9_]+`\n)+)",
        cleanup_preflight,
    )
    assert required_variables
    assert set(re.findall(r"`([A-Z0-9_]+)`", required_variables["names"])) == (
        expected_environment_variables
    )
    assert "all seven required environment variables must be present before approval" in (
        cleanup_preflight.lower()
    )
    assert "reviewed tracked configuration" not in cleanup
    expected_deletions = {
        "ALLOWED_HOSTS": "repository",
        "DB_NAME": "repository",
        "DB_PASSWORD": "repository",
        "DB_USER": "repository",
        "GHCR_READ_TOKEN": "repository",
        "GHCR_USERNAME": "repository",
        "LETSENCRYPT_EMAIL": "repository",
        "MEDIA_S3_ACCESS_KEY_ID": "repository",
        "MEDIA_S3_SECRET_ACCESS_KEY": "repository",
        "PHOTO_PROCESSING_WORKER_TOKEN": "staging Environment",
        "PRIVATE_MEDIA_S3_ACCESS_KEY_ID": "repository",
        "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY": "repository",
        "SECRET_KEY": "repository",
        "SELFIE_FEEDBACK_S3_ACCESS_KEY_ID": "staging Environment",
        "SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY": "staging Environment",
        "VM_HOST": "repository",
        "VM_SSH_KEY": "repository",
        "VM_USER": "repository",
        "YANDEX_MONITORING_API_KEY": "staging Environment",
    }
    delete_rows = re.findall(
        r"^gh secret delete ([A-Z0-9_]+) --repo peter-nikitin/photo-prjct"
        r"(?: --env (staging))?$",
        cleanup,
        re.MULTILINE,
    )
    actual_deletions = {
        name: "staging Environment" if environment == "staging" else "repository"
        for name, environment in delete_rows
    }
    assert actual_deletions == expected_deletions
    assert len(delete_rows) == len(expected_deletions)
    assert "Require all 15 repository source names and all four" in cleanup
    assert "Environment source names to be absent" in cleanup
    assert secret_id in runbook


def test_environment_secret_runbook_separates_initial_rotation_and_rollback_versions() -> None:
    """An empty secret and a later rollback use different provider-valid version operations."""
    runbook = (ROOT / "docs/runbooks/environment-secrets.md").read_text(encoding="utf-8")
    payload = _runbook_section(runbook, "Payload version validation and population")
    rotation = _runbook_section(runbook, "Rotation")
    rollback = _runbook_section(runbook, "Rollback")

    initial_command = (
        "yc lockbox secret add-version --id e6q85jjl76r45maigtfb \\\n"
        '  --payload - --format json < "$candidate_payload"'
    )
    assert initial_command in payload
    assert 'test -n "$previous_version_id"' in rotation
    assert '--base-version-id "$previous_version_id" --payload - --format json' in rotation
    assert 'printf "[]" | yc lockbox secret add-version --id e6q85jjl76r45maigtfb' in rollback
    assert '--base-version-id "$rollback_version_id" --payload - --format json' in rollback
    assert "exact key set" in rollback
    assert "current version" in rollback
    assert "cancel-version-destruction" in rollback


def test_environment_secret_rotation_extracts_a_valid_base_from_cli_metadata() -> None:
    """The documented base-version extraction must execute against the CLI's snake-case JSON."""
    manifest = json.loads((ROOT / "deploy/environment-secrets.json").read_text(encoding="utf-8"))
    runbook = (ROOT / "docs/runbooks/environment-secrets.md").read_text(encoding="utf-8")
    rotation = _runbook_section(runbook, "Rotation")
    extractor = re.search(
        r"\.venv/bin/python -c '\n(?P<script>.*?)\n'\)\ntest -n \"\$previous_version_id\"",
        rotation,
        re.DOTALL,
    )
    assert extractor, "Rotation must contain an executable CLI metadata extractor"

    secret_id = manifest["lockbox"]["secret_id"]
    version = {
        "id": "version-base",
        "secret_id": secret_id,
        "status": "ACTIVE",
        "payload_entry_keys": sorted(entry["key"] for entry in manifest["entries"]),
    }

    def extract(metadata: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", extractor["script"]],
            cwd=ROOT,
            input=json.dumps(metadata),
            text=True,
            capture_output=True,
            check=False,
        )

    valid = extract({"id": secret_id, "current_version": version})
    assert valid.returncode == 0, valid.stderr
    assert valid.stdout.strip() == version["id"]

    for invalid in (
        {"id": secret_id},
        {"id": secret_id, "current_version": {**version, "secret_id": "wrong-secret"}},
        {"id": secret_id, "current_version": {**version, "status": "INACTIVE"}},
        {"id": secret_id, "current_version": {**version, "payload_entry_keys": []}},
    ):
        assert extract(invalid).returncode == 2


def test_environment_secret_runbook_cleans_candidate_payload_on_all_exit_paths() -> None:
    """A protected candidate must not become a persistent local secret after any failure path."""
    runbook = (ROOT / "docs/runbooks/environment-secrets.md").read_text(encoding="utf-8")
    payload = _runbook_section(runbook, "Payload version validation and population")

    creation = 'candidate_payload=$(mktemp "${TMPDIR:-/tmp}/findme-lockbox-payload.XXXXXX.json")'
    assert creation in payload
    assert payload.index(creation) < payload.index("trap finish_candidate EXIT")
    assert "set -e" in payload
    for signal, status in (("HUP", "129"), ("INT", "130"), ("TERM", "143")):
        assert f"trap 'exit {status}' {signal}" in payload
    assert "retained_path=$candidate_payload" in payload
    assert "editor must not create swap, backup, or cloud-synced copies" in payload
    assert 'rm -f -- "$candidate_payload"' in payload
    assert 'cat "$candidate_payload"' not in payload


def test_production_django_configuration_excludes_visual_references() -> None:
    for relative_path in ("src/backend/config/urls.py", "src/backend/config/settings.py"):
        production_config = (ROOT / relative_path).read_text(encoding="utf-8")

        assert "__visual__" not in production_config
        assert "tests.visual" not in production_config
        assert "design_reference" not in production_config


def test_visual_regression_runs_in_a_pinned_container_environment() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    dockerfile = (ROOT / "Dockerfile.visual-tests").read_text(encoding="utf-8")
    compose = yaml.safe_load((ROOT / "docker-compose.visual.yml").read_text(encoding="utf-8"))
    visual_service = compose["services"]["visual-tests"]

    assert package["scripts"]["test:visual"] == "sh tests/visual/run-in-container.sh test"
    assert package["scripts"]["test:visual:update"] == (
        "sh tests/visual/run-in-container.sh update"
    )
    assert dockerfile.count("@sha256:") == 2
    assert "python:3.12-slim-bookworm@sha256:" in dockerfile
    assert "node:22-bookworm-slim@sha256:" in dockerfile
    assert "npx playwright install --with-deps chromium" in dockerfile
    assert "COPY . ." not in dockerfile
    assert visual_service["image"] == "${VISUAL_TEST_IMAGE:-photo-prjct-visual-deps:local}"
    assert set(visual_service["volumes"]) == {
        "./src:/workspace/src:ro",
        "./tests:/workspace/tests:ro",
        "./package.json:/workspace/package.json:ro",
        "./playwright.config.js:/workspace/playwright.config.js:ro",
        "./tests/visual/visual.spec.js-snapshots:/workspace/tests/visual/visual.spec.js-snapshots",
        "./playwright-report:/workspace/playwright-report",
        "./test-results:/workspace/test-results",
    }
    assert visual_service["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert visual_service["environment"]["CI"] == "${CI:-false}"
    assert visual_service["environment"]["NODE_PATH"] == "/opt/visual-test-deps/node_modules"


def test_local_node_version_matches_ci_and_visual_container() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    node_setup = _workflow_step(_load_workflow("ci.yml"), "quality", "Set up Node.js")
    dockerfile = (ROOT / "Dockerfile.visual-tests").read_text(encoding="utf-8")

    assert (ROOT / ".nvmrc").read_text(encoding="utf-8").strip() == "22"
    assert package["engines"]["node"] == ">=22 <23"
    assert node_setup["with"]["node-version"] == "22"
    assert "FROM node:22-bookworm-slim@sha256:" in dockerfile


def test_selfie_observability_is_owned_by_the_supported_deployment_entrypoint() -> None:
    apply = (ROOT / "deploy/apply-deployment.sh").read_text(encoding="utf-8")
    helper = (ROOT / "deploy/selfie-observability/root-helper.sh").read_text(encoding="utf-8")
    bootstrap = (ROOT / "deploy/bootstrap-selfie-observability.sh").read_text(encoding="utf-8")
    verifier = (ROOT / "deploy/verify-selfie-observability.sh").read_text(encoding="utf-8")

    assert apply.index('"$observability_helper" install') < apply.index("compose stop nginx")
    assert apply.index("verify-selfie-observability.sh") > apply.index("verify-public-edge.sh")
    assert '"$observability_helper" rollback' in apply
    for setting in ("Storage=persistent", "MaxRetentionSec=14day", "SystemMaxUse=1G"):
        assert setting in helper
    assert "/opt/photo-prjct" not in helper
    assert "NOPASSWD: ALL" not in bootstrap
    assert "journalctl --vacuum" not in helper
    assert "rm -rf" not in helper
    assert "systemd-analyze cat-config" not in verifier
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "journalctl -u selfie-search-summary.service --since '14 days ago' -o cat" in readme
    assert '| grep \'"event":"selfie_search_daily_summary"\'' in readme
    assert " -t selfie-search-daily-summary" not in readme
    assert "/usr/local/lib/findme-selfie-observability/run-daily-summary.sh" in readme


def test_clone_deployed_suite_has_default_and_exhaustive_selection_contract() -> None:
    def make_dry_run(target: str, tests: str = "") -> list[str]:
        result = subprocess.run(
            ["make", "-n", f"TESTS={tests}", target],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    markers = pyproject["tool"]["pytest"]["ini_options"]["markers"]
    assert any(marker.startswith("clone_deployed_slow:") for marker in markers)

    default_pytest = 'sh scripts/run-in-test-env.sh .venv/bin/pytest -m "not clone_deployed_slow"'
    assert make_dry_run("test") == [default_pytest]
    requested_selector = (
        "tests/test_repository_foundation.py::test_adr_index_lists_all_accepted_decisions"
    )
    assert make_dry_run("test", requested_selector) == [f"{default_pytest} {requested_selector}"]
    assert make_dry_run("check").count(f"{default_pytest} --cov --cov-report=term-missing") == 1

    clone_pytest = (
        "sh scripts/run-in-test-env.sh .venv/bin/pytest "
        "tests/deployment/test_clone_deployed_database.py"
    )
    assert make_dry_run("test-clone-deployed") == [clone_pytest]
    assert "clone_deployed_slow" not in make_dry_run("test-clone-deployed")[0]

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "make test-clone-deployed" in readme
    assert "critical clone" in readme.lower()
    assert "exhaustive clone" in readme.lower()


def test_deployment_compose_uses_an_immutable_application_image() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.deployment.yml").read_text(encoding="utf-8"))

    assert compose["services"]["web"]["image"] == "${APP_IMAGE:?APP_IMAGE must be set}"
    assert compose["services"]["web"]["env_file"] == ["${APP_ENV_FILE:-.env}"]
    assert "healthcheck" in compose["services"]["web"]


def test_deployment_workflow_forwards_the_bounded_gunicorn_profile() -> None:
    """The sole delivery path provides the required web-process bound to deployment."""
    profiles = {
        "GUNICORN_WORKERS": "5",
        "GUNICORN_THREADS": "2",
        "GUNICORN_TIMEOUT": "180",
        "GUNICORN_MAX_REQUESTS": "1000",
        "GUNICORN_MAX_REQUESTS_JITTER": "100",
    }
    deployments = ((_load_workflow("deploy.yml"), "deploy", "Run deployment"),)

    for workflow, job_name, step_name in deployments:
        apply = _workflow_step(workflow, job_name, step_name)
        for name, value in profiles.items():
            assert apply["env"][name] == value
            assert name in _envs(apply)


def test_public_deployment_uses_the_canonical_https_edge_overlay() -> None:
    app_compose = yaml.safe_load(
        (ROOT / "docker-compose.deployment.yml").read_text(encoding="utf-8")
    )
    shared_path = ROOT / "docker-compose.https.yml"
    deployment_workflow = _load_workflow("deploy.yml")

    assert "ports" not in app_compose["services"]["web"]
    assert shared_path.is_file(), "Missing docker-compose.https.yml"

    shared = yaml.safe_load(shared_path.read_text(encoding="utf-8"))

    assert shared["services"]["nginx"]["ports"] == [
        "80:80",
        "443:443",
        "127.0.0.1:8080:8080",
    ]
    assert "certbot" in shared["services"]
    deployment_deploy = _workflow_step(deployment_workflow, "deploy", "Run deployment")
    assert "scripts/run-with-environment-secrets.py" in deployment_deploy["run"]
    remote_helper = (ROOT / "deploy/run-remote.sh").read_text(encoding="utf-8")
    assert "docker-compose.https.yml" in remote_helper
    assert "docker-compose.deployment.yml" in remote_helper
    assert not (ROOT / "docker-compose.staging.yml").exists()


def test_public_edge_configuration_is_versioned_and_wired_to_workflows() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    deployment = _load_workflow("deploy.yml")
    deployment_apply = _workflow_step(deployment, "deploy", "Run deployment")
    public_variables = ("PUBLIC_DOMAIN", "PUBLIC_DOMAIN_ALIAS")
    for variable in public_variables:
        assert re.search(rf"^{variable}=", example, re.MULTILINE)
        expected_value = f"${{{{ vars.{variable} }}}}"
        assert deployment_apply["env"][variable] == expected_value
        assert variable in _envs(deployment_apply)

    assert "EXPECTED_PUBLIC_IPV4" not in example
    assert "EXPECTED_PUBLIC_IPV4" not in json.dumps(deployment)
    assert "LETSENCRYPT_EMAIL" not in deployment_apply["env"]
    assert "--consumer deploy" in deployment_apply["run"]


def test_public_edges_deny_the_private_processing_prefix_before_the_proxy_catchall() -> None:
    for relative_path in ("deploy/nginx/https.conf.template",):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        deny = "location ^~ /internal/photo-processing/ {\n        return 404;\n    }"
        assert deny in source
        assert source.index(deny) < source.rindex("location / {")


def test_public_edges_sanitize_bearer_access_logs_and_split_referrer_policies() -> None:
    for relative_path in ("deploy/nginx/https.conf.template",):
        source = (ROOT / relative_path).read_text(encoding="utf-8")

        assert "map $uri $selfie_search_access_client_address {" in source
        assert "map $uri $selfie_search_access_request {" in source
        assert "map $uri $selfie_search_access_referrer {" in source
        assert "map $uri $selfie_search_access_user_agent {" in source
        assert (
            '~^/events/[^/]+/selfie-search/[^/]+(?:/|$) "$request_method <selfie-search>";'
            in source
        )
        assert '~^/events/[^/]+/selfie-search/$ "$request_method <selfie-search>";' in source
        assert '"$selfie_search_access_referrer"' in source
        assert '"$selfie_search_access_user_agent"' in source
        assert "$request_time" in source
        assert "access_log /var/log/nginx/access.log selfie_search_safe;" in source

    https = (ROOT / "deploy/nginx/https.conf.template").read_text(encoding="utf-8")
    bearer_location = "location ~ ^/events/[^/]+/selfie-search/[^/]+(?:/|$) {"
    bearer_start = https.index(bearer_location)
    bearer_block = https[bearer_start : https.index("\n    }", bearer_start)]
    assert 'add_header Referrer-Policy "same-origin" always;' in https
    assert 'add_header Referrer-Policy "no-referrer" always;' in https
    assert 'add_header Referrer-Policy "no-referrer" always;' in bearer_block
    for header in (
        'add_header Strict-Transport-Security "max-age=86400" always;',
        'add_header X-Content-Type-Options "nosniff" always;',
        'add_header X-Frame-Options "DENY" always;',
    ):
        assert header in bearer_block
    assert "proxy_hide_header Referrer-Policy;" in https
    assert "access_log /var/log/nginx/access.log selfie_search_safe;" in (
        ROOT / "deploy/nginx/reload-nginx.sh"
    ).read_text(encoding="utf-8")


def test_public_edges_isolate_bearer_upstream_errors_without_changing_proxy_routing() -> None:
    bearer_location = (
        "location ~ ^/events/[^/]+/selfie-search/[^/]+(?:/|$) {\n        error_log /dev/null emerg;"
    )
    submission_location = (
        "location ~ ^/events/[^/]+/selfie-search/$ {\n        error_log /dev/null emerg;"
    )

    for relative_path in ("deploy/nginx/https.conf.template",):
        source = (ROOT / relative_path).read_text(encoding="utf-8")

        assert bearer_location in source
        assert submission_location in source
        assert source.count("error_log /dev/null emerg;") == 2
        bearer_start = source.index(bearer_location)
        submission_start = source.index(submission_location)
        internal_start = source.index("location ^~ /internal/photo-processing/ {")
        catchall_start = source.index("location / {", bearer_start)
        assert internal_start < submission_start < bearer_start < catchall_start

        bearer_block = source[bearer_start : source.index("\n    }", bearer_start)]
        submission_block = source[submission_start : source.index("\n    }", submission_start)]
        catchall_block = source[catchall_start : source.index("\n    }", catchall_start)]
        bearer_proxy_lines = [
            line.strip() for line in bearer_block.splitlines() if line.strip().startswith("proxy_")
        ]
        submission_proxy_lines = [
            line.strip()
            for line in submission_block.splitlines()
            if line.strip().startswith("proxy_")
        ]
        catchall_proxy_lines = [
            line.strip()
            for line in catchall_block.splitlines()
            if line.strip().startswith("proxy_")
        ]
        assert bearer_proxy_lines == catchall_proxy_lines
        assert submission_proxy_lines == catchall_proxy_lines

    alias = (ROOT / "deploy/nginx/reload-nginx.sh").read_text(encoding="utf-8")
    assert bearer_location in alias
    assert submission_location in alias
    assert alias.count("error_log /dev/null emerg;") == 2
    alias_bearer_start = alias.index(bearer_location)
    alias_submission_start = alias.index(submission_location)
    alias_catchall_start = alias.index("location / {", alias_bearer_start)
    assert alias_submission_start < alias_bearer_start < alias_catchall_start
    alias_bearer_block = alias[alias_bearer_start : alias.index("\n    }", alias_bearer_start)]
    alias_submission_block = alias[
        alias_submission_start : alias.index("\n    }", alias_submission_start)
    ]
    alias_catchall_block = alias[
        alias_catchall_start : alias.index("\n    }", alias_catchall_start)
    ]
    assert "return 308 https://${PUBLIC_DOMAIN}\\$request_uri;" in alias_bearer_block
    assert "return 308 https://${PUBLIC_DOMAIN}\\$request_uri;" in alias_submission_block
    assert "return 308 https://${PUBLIC_DOMAIN}\\$request_uri;" in alias_catchall_block
    assert [
        line.strip()
        for line in alias_submission_block.splitlines()
        if line.strip().startswith("return ")
    ] == ["return 308 https://${PUBLIC_DOMAIN}\\$request_uri;"]
    assert 'add_header Referrer-Policy "no-referrer" always;' in alias

    validator = (ROOT / "tests/deployment/validate-nginx.sh").read_text(encoding="utf-8")
    assert 'exercise_bearer_error_logging "$name" "$rendered" "$alias"' in validator
    assert 'bearer_token="bearer-log-token-$name-$$"' in validator
    assert "--add-host web:127.0.0.1" in validator


def test_private_upload_configuration_is_wired_to_deployments() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    apply_script = (ROOT / "deploy/apply-deployment.sh").read_text(encoding="utf-8")
    deployment = _workflow_step(_load_workflow("deploy.yml"), "deploy", "Run deployment")
    deployment_expected = {
        "PHOTO_UPLOAD_ENABLED": "${{ vars.PHOTO_UPLOAD_ENABLED || 'False' }}",
        "PRIVATE_MEDIA_S3_BUCKET": "${{ vars.PRIVATE_MEDIA_S3_BUCKET }}",
        "PRIVATE_MEDIA_ALLOWED_ORIGINS": "${{ vars.PRIVATE_MEDIA_ALLOWED_ORIGINS }}",
    }
    for name, value in deployment_expected.items():
        assert re.search(rf"^{name}=", example, re.MULTILINE)
        assert deployment["env"][name] == value
        assert name in _envs(deployment)
        assert f"printf '{name}=%s\\n'" in apply_script
    assert "PRIVATE_MEDIA_S3_ACCESS_KEY_ID" not in deployment["env"]
    assert "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY" not in deployment["env"]


def test_deployment_forwards_preview_processing_configuration() -> None:
    deployment = _workflow_step(_load_workflow("deploy.yml"), "deploy", "Run deployment")
    expected = {
        "PHOTO_PROCESSING_PREVIEW_ENABLED": (
            "${{ vars.PHOTO_PROCESSING_PREVIEW_ENABLED || 'True' }}"
        ),
        "PHOTO_PROCESSING_FACE_ENABLED": "${{ vars.PHOTO_PROCESSING_FACE_ENABLED || 'True' }}",
        "PHOTO_WORKER_PROCESSOR_IDENTITIES": (
            "${{ vars.PHOTO_WORKER_PROCESSOR_IDENTITIES || '1/capture_metadata/2,"
            "2/generate_preview/1,2/face_embedding/3,3/face_embedding/5,1/selfie_query/2' }}"
        ),
    }

    for name, value in expected.items():
        assert deployment["env"][name] == value
        assert name in _envs(deployment)
    assert (
        "2/generate_watermarked_preview/1"
        not in deployment["env"]["PHOTO_WORKER_PROCESSOR_IDENTITIES"]
    )


def test_paid_cart_rollout_never_enables_its_runtime_gate() -> None:
    deployment = _workflow_step(_load_workflow("deploy.yml"), "deploy", "Run deployment")
    apply_script = (ROOT / "deploy/apply-deployment.sh").read_text(encoding="utf-8")

    assert "paid-photo-cart" not in deployment["env"]
    assert "paid-photo-cart" not in apply_script


def test_monitoring_agent_configuration_is_manual_only_and_outside_deploy_rollback() -> None:
    deployment = _load_workflow("deploy.yml")
    dispatch = deployment[True]["workflow_dispatch"]
    agent = deployment["jobs"]["configure-monitoring-agent"]
    deployment_deploy = deployment["jobs"]["deploy"]

    assert dispatch["inputs"]["configure_monitoring_agent"] == {
        "description": ("Configure the Yandex Unified Agent without deploying the application"),
        "required": True,
        "default": False,
        "type": "boolean",
    }
    assert "environment" not in agent
    assert (
        agent["if"]
        == "${{ github.event_name == 'workflow_dispatch' && inputs.configure_monitoring_agent && "
        "!inputs.validate_deploy_issue && !inputs.preflight }}"
    )
    assert "needs" not in agent
    assert deployment_deploy["if"] == (
        "${{ !inputs.configure_monitoring_agent && !inputs.validate_deploy_issue && "
        "!inputs.preflight && !inputs.stage_paused_observability_release }}"
    )
    assert "configure-monitoring-agent" not in json.dumps(deployment_deploy)
    run = _workflow_step(deployment, "configure-monitoring-agent", "Configure Unified Agent")
    assert run["env"] == {
        "VM_HOST": "${{ vars.VM_HOST }}",
        "VM_USER": "${{ vars.VM_USER }}",
        "VM_SSH_KNOWN_HOSTS": "${{ vars.VM_SSH_KNOWN_HOSTS }}",
        "YANDEX_CLOUD_FOLDER_ID": "${{ vars.YANDEX_CLOUD_FOLDER_ID }}",
    }
    assert "YANDEX_MONITORING_API_KEY" not in json.dumps(agent)
    assert "--consumer remote-check" in run["run"]
    assert "configure-monitoring" in run["run"]


def test_deployment_stages_and_verifies_checksum_bound_privileged_source() -> None:
    workflow = _load_workflow("deploy.yml")
    dispatch = workflow[True]["workflow_dispatch"]
    stage = workflow["jobs"]["stage-observability-release"]
    classify = workflow["jobs"]["classify-release"]
    deploy = workflow["jobs"]["deploy"]

    assert dispatch["inputs"]["stage_paused_observability_release"]["type"] == "boolean"
    assert stage["if"] == "${{ inputs.stage_paused_observability_release && !inputs.preflight }}"
    assert stage["needs"] == ["classify-release"]
    assert stage["permissions"] == {"contents": "read", "id-token": "write"}
    assert "environment" not in stage
    assert "observability-release-sha" in json.dumps(stage)
    assert "observability-source.sha256" in json.dumps(stage)
    assert "stage-paused-observability-release" in json.dumps(stage)
    assert "verify-paused-observability-release" in json.dumps(deploy)
    assert classify["outputs"]["observability_source_manifest_sha256"] == (
        "${{ steps.classify.outputs.observability_source_manifest_sha256 }}"
    )
    assert "stage_paused_observability_release" in deploy["if"]


def test_deployment_issue_reconciliation_is_bounded_and_non_authoritative() -> None:
    workflow = _load_workflow("deploy.yml")
    dispatch = workflow[True]["workflow_dispatch"]
    reconcile = workflow["jobs"]["reconcile-deploy-issue"]
    validation = workflow["jobs"]["validate-deploy-issue"]
    notification = _workflow_step(workflow, "reconcile-deploy-issue", "Reconcile issue state")

    assert dispatch["inputs"]["validate_deploy_issue"] == {
        "description": "Exercise the bounded deployment issue notification drill",
        "required": True,
        "default": False,
        "type": "boolean",
    }
    assert "!inputs.validate_deploy_issue" in workflow["jobs"]["build"]["if"]
    assert "!inputs.validate_deploy_issue" in workflow["jobs"]["deploy"]["if"]
    assert reconcile["if"] == (
        "${{ always() && !inputs.configure_monitoring_agent && "
        "!inputs.validate_deploy_issue && !inputs.preflight && "
        "!inputs.stage_paused_observability_release }}"
    )
    assert reconcile["needs"] == ["classify-release", "build", "deploy"]
    assert reconcile["permissions"] == {"actions": "read", "contents": "read", "issues": "write"}
    assert "environment" not in reconcile
    assert "${{ secrets." not in json.dumps(reconcile)
    assert notification["continue-on-error"] is True
    assert notification["env"] == {"GITHUB_TOKEN": "${{ github.token }}"}
    run = notification["run"]
    assert "--mode deploy" in run
    assert '--sha "${{ needs.classify-release.outputs.release_sha }}"' in run
    assert '--sha "$GITHUB_SHA"' not in run
    assert 'gh run view "$GITHUB_RUN_ID" --log-failed' in run
    assert "DEPLOY_PHASE=(validate|snapshot|candidate-pull|private-media-preflight|" in run
    assert "observability-verify|commit)" in run
    assert 'print "unknown"' in run
    warning = _workflow_step(
        workflow, "reconcile-deploy-issue", "Warn when issue reconciliation fails"
    )
    assert warning["if"] == "${{ steps.reconcile.outcome == 'failure' }}"
    assert "remains authoritative" in warning["run"]

    assert validation["if"] == (
        "${{ github.event_name == 'workflow_dispatch' && inputs.validate_deploy_issue && "
        "!inputs.preflight }}"
    )
    assert "environment" not in validation
    assert validation["permissions"] == {"contents": "read", "issues": "write"}
    assert "${{ secrets." not in json.dumps(validation)


def test_deployment_issue_reconciliation_forwards_the_classified_release_sha(
    tmp_path: Path,
) -> None:
    release_sha = "a" * 40
    build_failure = _notification_arguments_from_workflow(
        tmp_path / "build-failure",
        build_result="failure",
        deploy_result="skipped",
        release_sha=release_sha,
        failed_log="",
    )
    deploy_success = _notification_arguments_from_workflow(
        tmp_path / "deploy-success",
        build_result="success",
        deploy_result="success",
        release_sha=release_sha,
        failed_log="",
    )

    def argument(arguments: list[str], name: str) -> str:
        return arguments[arguments.index(name) + 1]

    assert argument(build_failure, "--conclusion") == "failure"
    assert argument(build_failure, "--phase") == "build"
    assert argument(build_failure, "--sha") == release_sha
    assert argument(deploy_success, "--conclusion") == "success"
    assert argument(deploy_success, "--phase") == "commit"
    assert argument(deploy_success, "--sha") == release_sha


def test_deployment_issue_phase_parser_accepts_only_exact_phases(tmp_path: Path) -> None:
    prefixed_log = "\n".join(
        (
            "deploy\tRun deployment\t2026-08-19T10:00:00Z DEPLOY_PHASE=validate elapsed_seconds=1",
            "deploy\tRun deployment\t2026-08-19T10:01:00Z "
            "DEPLOY_PHASE=compose-reconcile elapsed_seconds=2",
            "deploy\tRun deployment\t2026-08-19T10:02:00Z "
            "DEPLOY_PHASE=commit-extra elapsed_seconds=3",
            "deploy\tRun deployment\t2026-08-19T10:03:00Z "
            "ignored DEPLOY_PHASE=commit elapsed_seconds=4",
        )
    )
    near_matches_only = "\n".join(
        (
            "deploy\tRun deployment\tDEPLOY_PHASE=compose-reconcile-extra elapsed_seconds=1",
            "deploy\tRun deployment\tprefixDEPLOY_PHASE=commit elapsed_seconds=1",
            "deploy\tRun deployment\tDEPLOY_PHASE=unknown elapsed_seconds=1",
        )
    )

    assert _notification_phase_from_workflow(tmp_path / "exact", prefixed_log) == "commit"
    assert (
        _notification_phase_from_workflow(tmp_path / "near-matches", near_matches_only) == "unknown"
    )


def test_deployment_issue_validation_filter_ignores_pull_requests_with_its_title() -> None:
    workflow = _load_workflow("deploy.yml")
    assertion = _workflow_step(
        workflow, "validate-deploy-issue", "Assert validation issue is closed"
    )
    match = re.search(r"--jq '([^']+)'", assertion["run"])
    assert match is not None
    response = [
        {
            "number": 17,
            "title": "[deployment validation] notification drill",
            "pull_request": {"url": "https://api.github.com/repos/findme/photo/pulls/17"},
        },
        {"number": 18, "title": "[deployment validation] notification drill"},
    ]
    result = subprocess.run(
        ["jq", "-r", match.group(1)],
        check=True,
        input=json.dumps(response),
        text=True,
        capture_output=True,
    )

    assert result.stdout == "18\n"


def test_face_embedding_benchmark_workflow_is_manual_bounded_and_non_environmental() -> None:
    workflow = _load_workflow("face-embedding-benchmark.yml")
    dispatch = workflow[True]["workflow_dispatch"]
    benchmark = workflow["jobs"]["benchmark"]
    run = _workflow_step(workflow, "benchmark", "Run bounded benchmark operation")

    assert set(workflow[True]) == {"workflow_dispatch"}
    assert "environment" not in benchmark
    assert benchmark["concurrency"] == {"group": "deploy", "cancel-in-progress": False}
    assert benchmark["permissions"] == {"contents": "read", "id-token": "write"}
    assert dispatch["inputs"]["operation"] == {
        "description": "Create a baseline cohort, replay a closed cohort, or print a closed report",
        "required": True,
        "type": "choice",
        "options": ["baseline", "replay", "report"],
    }
    assert dispatch["inputs"]["event_slug"]["required"] is False
    assert dispatch["inputs"]["source_run_uuid"]["required"] is False
    assert _workflow_step(workflow, "benchmark", "Check out repository")["with"] == {
        "persist-credentials": False
    }
    assert {
        key: run["env"][key]
        for key in ("BENCHMARK_OPERATION", "BENCHMARK_EVENT_SLUG", "BENCHMARK_SOURCE_RUN_UUID")
    } == {
        "BENCHMARK_OPERATION": "${{ inputs.operation }}",
        "BENCHMARK_EVENT_SLUG": "${{ inputs.event_slug }}",
        "BENCHMARK_SOURCE_RUN_UUID": "${{ inputs.source_run_uuid }}",
    }
    assert "--consumer remote-check" in run["run"]
    assert "--identity github-oidc" in run["run"]
    assert "face-embedding-benchmark" in run["run"]
    assert "${{ secrets." not in json.dumps(benchmark)
    helper = (ROOT / "deploy/run-remote.sh").read_text(encoding="utf-8")
    assert "3/face_embedding_benchmark/1" in helper
    assert 'test "$preview_enabled" = False' in helper
    assert "run_face_embedding_benchmark" in helper
    assert 'test -n "$BENCHMARK_EVENT_SLUG"' in helper
    assert '--event "$BENCHMARK_EVENT_SLUG"' in helper


def test_deployment_builds_and_forwards_the_immutable_worker_image() -> None:
    workflow = _load_workflow("deploy.yml")
    build = workflow["jobs"]["build"]
    deployment = _workflow_step(workflow, "deploy", "Run deployment")

    assert build["outputs"]["worker_image"] == "${{ steps.image.outputs.worker_image }}"
    image = _workflow_step(workflow, "build", "Select image references")
    assert "worker_image=ghcr.io/${GITHUB_REPOSITORY}-worker:${release_sha}" in image["run"]
    worker_build = _workflow_step(workflow, "build", "Build and push worker image")
    assert worker_build["with"] == {
        "context": ".",
        "file": "./Dockerfile.worker",
        "push": True,
        "tags": "${{ steps.image.outputs.worker_image }}",
        "cache-from": "type=gha,scope=worker",
        "cache-to": "type=gha,mode=max,scope=worker,ignore-error=true",
    }
    expected = {
        "WORKER_IMAGE": "${{ needs.build.outputs.worker_image }}",
        "PHOTO_PROCESSING_ENABLED": "${{ vars.PHOTO_PROCESSING_ENABLED || 'True' }}",
        "PHOTO_PROCESSING_PREVIEW_ENABLED": (
            "${{ vars.PHOTO_PROCESSING_PREVIEW_ENABLED || 'True' }}"
        ),
        "PHOTO_PROCESSING_FACE_ENABLED": "${{ vars.PHOTO_PROCESSING_FACE_ENABLED || 'True' }}",
        "PHOTO_WORKER_PROCESSOR_TYPES": (
            "${{ vars.PHOTO_WORKER_PROCESSOR_TYPES || "
            "'selfie_query,face_embedding,capture_metadata,generate_preview' }}"
        ),
    }
    for name, value in expected.items():
        assert deployment["env"][name] == value
        assert name in _envs(deployment)
    assert "PHOTO_PROCESSING_WORKER_TOKEN" not in deployment["env"]
    assert "--consumer deploy" in deployment["run"]


def test_deployment_worker_build_has_isolated_cache_and_reuses_unchanged_digest() -> None:
    workflow = _load_workflow("deploy.yml")
    classify = workflow["jobs"]["classify-release"]

    assert classify["outputs"]["worker_inputs_changed"] == (
        "${{ steps.classify.outputs.worker_inputs_changed }}"
    )
    classify_run = _workflow_step(workflow, "classify-release", "Classify deployment release")[
        "run"
    ]
    assert "Dockerfile.worker .dockerignore src/worker" in classify_run
    assert "worker_inputs_changed=false" in classify_run

    buildx = _workflow_step(workflow, "build", "Set up Docker Buildx")
    assert buildx["uses"] == "docker/setup-buildx-action@v3"
    assert buildx["with"]["driver"] == "docker-container"
    web_build = _workflow_step(workflow, "build", "Build and push image")
    worker_build = _workflow_step(workflow, "build", "Build and push worker image")
    assert web_build["with"]["cache-from"] == "type=gha,scope=web"
    assert web_build["with"]["cache-to"] == "type=gha,mode=max,scope=web,ignore-error=true"
    assert worker_build["with"]["cache-from"] == "type=gha,scope=worker"
    assert worker_build["with"]["cache-to"] == ("type=gha,mode=max,scope=worker,ignore-error=true")

    reuse = _workflow_step(workflow, "build", "Reuse unchanged worker image")
    assert reuse["if"] == (
        "${{ github.event_name == 'push' && "
        "needs.classify-release.outputs.worker_inputs_changed == 'false' }}"
    )
    assert reuse["continue-on-error"] is True
    assert "docker buildx imagetools inspect" in reuse["run"]
    assert "docker buildx imagetools create" in reuse["run"]
    assert reuse["env"]["PREVIOUS_WORKER_IMAGE"] == (
        "ghcr.io/${{ github.repository }}-worker:${{ github.event.before }}"
    )
    assert worker_build["if"] == (
        "${{ github.event_name == 'workflow_dispatch' || "
        "needs.classify-release.outputs.worker_inputs_changed == 'true' || "
        "steps.reuse-worker.outcome != 'success' }}"
    )


def test_manual_storage_probes_forward_each_input_to_its_exact_consumer_and_mode() -> None:
    workflow = _load_workflow("deploy.yml")
    dispatch = workflow[True]["workflow_dispatch"]
    expected = {
        "verify_private_storage": (
            "Verify private upload storage contract",
            "remote-check",
            "private-storage",
        ),
        "verify_selfie_search_storage": (
            "Verify selfie-search temporary storage contract",
            "remote-check",
            "selfie-storage",
        ),
        "verify_selfie_feedback_storage": (
            "Verify selfie-feedback storage contract",
            "deploy",
            "selfie-feedback-storage",
        ),
    }

    for input_name, (step_name, consumer, mode) in expected.items():
        assert dispatch["inputs"][input_name]["type"] == "boolean"
        assert dispatch["inputs"][input_name]["default"] is False
        step = _workflow_step(workflow, "deploy", step_name)
        assert step["if"] == f"${{{{ inputs.{input_name} }}}}"
        assert f"--consumer {consumer}" in step["run"]
        assert f"deploy/run-remote.sh {mode}" in step["run"]
        assert "--identity github-oidc" in step["run"]


def test_resolver_workflows_keep_credentials_ephemeral_and_vm_coordinates_nonsecret() -> None:
    workflows = {
        "deploy.yml": _load_workflow("deploy.yml"),
        "monitor-public-health.yml": _load_workflow("monitor-public-health.yml"),
        "face-embedding-benchmark.yml": _load_workflow("face-embedding-benchmark.yml"),
    }
    oidc_jobs = {
        (workflow_name, job_name)
        for workflow_name, workflow in workflows.items()
        for job_name, job in workflow["jobs"].items()
        if job.get("permissions", {}).get("id-token") == "write"
    }
    assert oidc_jobs == {
        ("deploy.yml", "stage-observability-release"),
        ("deploy.yml", "deploy"),
        ("deploy.yml", "configure-monitoring-agent"),
        ("deploy.yml", "lockbox-preflight"),
        ("monitor-public-health.yml", "probe"),
        ("face-embedding-benchmark.yml", "benchmark"),
    }

    resolver_jobs: list[tuple[str, str, dict[str, Any]]] = []
    for workflow_name, workflow in workflows.items():
        for job_name, job in workflow["jobs"].items():
            for step in job.get("steps", []):
                if step.get("uses") == "actions/checkout@v4":
                    assert step["with"]["persist-credentials"] is False
            if "run-with-environment-secrets.py" in json.dumps(job):
                resolver_jobs.append((workflow_name, job_name, job))

    for workflow_name, job_name, job in resolver_jobs:
        serialized = json.dumps(job)
        assert (workflow_name, job_name) in oidc_jobs
        assert "${{ secrets." not in serialized
        assert "outputs" not in job

    vm_coordinates = {
        "VM_HOST": "${{ vars.VM_HOST }}",
        "VM_USER": "${{ vars.VM_USER }}",
        "VM_SSH_KNOWN_HOSTS": "${{ vars.VM_SSH_KNOWN_HOSTS }}",
    }
    expected_remote_steps = (
        ("deploy.yml", "stage-observability-release", "Stage privileged observability source"),
        ("deploy.yml", "deploy", "Verify staged paused observability release"),
        ("deploy.yml", "deploy", "Run deployment"),
        ("deploy.yml", "deploy", "Verify private upload storage contract"),
        ("deploy.yml", "deploy", "Verify selfie-search temporary storage contract"),
        ("deploy.yml", "deploy", "Verify selfie-feedback storage contract"),
        ("deploy.yml", "configure-monitoring-agent", "Configure Unified Agent"),
        ("face-embedding-benchmark.yml", "benchmark", "Run bounded benchmark operation"),
    )
    for workflow_name, job_name, step_name in expected_remote_steps:
        step = _workflow_step(workflows[workflow_name], job_name, step_name)
        for name, value in vm_coordinates.items():
            assert step["env"][name] == value


def test_deployment_classifier_executes_injected_sha_and_stage_binding_is_exact(
    tmp_path: Path,
) -> None:
    workflow = _load_workflow("deploy.yml")
    classify_job = workflow["jobs"]["classify-release"]
    classify = _workflow_step(workflow, "classify-release", "Classify deployment release")
    stage = workflow["jobs"]["stage-observability-release"]
    bind = _workflow_step(workflow, "stage-observability-release", "Bind source to staged commit")

    assert _workflow_step(workflow, "classify-release", "Check out release history")["with"] == {
        "fetch-depth": 0,
        "persist-credentials": False,
    }
    assert classify["env"] == {"DEPLOYMENT_SHA": "${{ inputs.deployment_sha }}"}
    assert classify_job["outputs"]["release_sha"] == "${{ steps.classify.outputs.release_sha }}"
    assert _workflow_step(workflow, "stage-observability-release", "Check out staged commit")[
        "with"
    ] == {
        "ref": "${{ needs.classify-release.outputs.release_sha }}",
        "fetch-depth": 1,
        "persist-credentials": False,
    }
    assert 'release_sha="${{ needs.classify-release.outputs.release_sha }}"' in bind["run"]
    assert 'test "$(git rev-parse HEAD)" = "$release_sha"' in bind["run"]
    for job_name in ("build", "deploy", "reconcile-deploy-issue"):
        assert "!inputs.stage_paused_observability_release" in workflow["jobs"][job_name]["if"]
    assert stage["if"] == "${{ inputs.stage_paused_observability_release && !inputs.preflight }}"

    script = classify["run"]
    for expression, value in {
        "${{ github.sha }}": "f" * 40,
        "${{ github.event_name }}": "workflow_dispatch",
        "${{ inputs.configure_monitoring_agent }}": "false",
        "${{ inputs.validate_deploy_issue }}": "false",
        "${{ inputs.stage_paused_observability_release }}": "false",
        "${{ inputs.verify_paused_observability_release }}": "false",
    }.items():
        script = script.replace(expression, value)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        '#!/bin/sh\ncase "$1" in\n'
        "  cat-file) exit 0 ;;\n"
        "  rev-parse) printf '%s\\n' \"$EXPECTED_SHA\" ;;\n"
        "  *) exit 2 ;;\nesac\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    output = tmp_path / "classifier-output"
    summary = tmp_path / "classifier-summary"
    expected_sha = "a" * 40
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DEPLOYMENT_SHA": "A" * 40,
        "EXPECTED_SHA": expected_sha,
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    result = subprocess.run(
        ["/bin/sh", "-c", script], cwd=tmp_path, env=environment, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert f"release_sha={expected_sha}" in output.read_text(encoding="utf-8")

    bind_script = bind["run"].replace(
        "${{ needs.classify-release.outputs.release_sha }}", expected_sha
    )
    exact = tmp_path / "exact"
    exact.mkdir()
    exact_result = subprocess.run(
        ["/bin/sh", "-c", bind_script],
        cwd=exact,
        env={**environment, "EXPECTED_SHA": expected_sha},
        capture_output=True,
        text=True,
    )
    assert exact_result.returncode == 0, exact_result.stderr
    assert (exact / "observability-release-sha").read_text(encoding="utf-8") == f"{expected_sha}\n"
    mismatch = tmp_path / "mismatch"
    mismatch.mkdir()
    mismatch_result = subprocess.run(
        ["/bin/sh", "-c", bind_script],
        cwd=mismatch,
        env={**environment, "EXPECTED_SHA": "b" * 40},
        capture_output=True,
        text=True,
    )
    assert mismatch_result.returncode != 0
    assert not (mismatch / "observability-release-sha").exists()
