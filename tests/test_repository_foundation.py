import json
import re
import subprocess
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
        [str(ROOT / ".venv" / "bin" / "python"), str(script), str(output)],
        cwd=repository,
        check=True,
    )

    package = output.read_text(encoding="utf-8")
    assert "tracked.txt" in package
    assert "-before" in package
    assert "+after" in package
    assert "untracked.txt" in package
    assert "+new task file" in package


def test_deployment_workflows_separate_staging_and_production() -> None:
    staging = _load_workflow("deploy.yml")
    production = _load_workflow("promote-production.yml")

    assert staging[True]["push"]["branches"] == ["main"]
    assert staging["jobs"]["deploy"]["environment"] == "staging"
    assert staging["jobs"]["deploy"]["concurrency"]["group"] == "deploy-staging"
    assert set(production[True]) == {"workflow_dispatch"}
    assert production["jobs"]["promote"]["environment"] == "production"
    assert production["jobs"]["promote"]["concurrency"]["group"] == "deploy-production"


def test_staging_face_embedding_benchmark_is_manual_and_bounded() -> None:
    workflow = _load_workflow("staging-face-embedding-benchmark.yml")
    dispatch = workflow[True]["workflow_dispatch"]
    benchmark = workflow["jobs"]["benchmark"]
    run = _workflow_step(workflow, "benchmark", "Run bounded benchmark operation")

    assert set(workflow[True]) == {"workflow_dispatch"}
    assert benchmark["environment"] == "staging"
    assert benchmark["concurrency"] == {
        "group": "deploy-staging",
        "cancel-in-progress": False,
    }
    assert dispatch["inputs"]["operation"] == {
        "description": "Create a baseline cohort, replay a closed cohort, or print a closed report",
        "required": True,
        "type": "choice",
        "options": ["baseline", "replay", "report"],
    }
    assert dispatch["inputs"]["event_slug"]["required"] is False
    assert dispatch["inputs"]["source_run_uuid"]["required"] is False
    assert run["uses"] == "appleboy/ssh-action@v1.0.3"
    assert "script_stop" not in run["with"]
    assert run["env"] == {
        "BENCHMARK_OPERATION": "${{ inputs.operation }}",
        "BENCHMARK_EVENT_SLUG": "${{ inputs.event_slug }}",
        "BENCHMARK_SOURCE_RUN_UUID": "${{ inputs.source_run_uuid }}",
    }
    assert "3/face_embedding_benchmark/1" in run["with"]["script"]
    assert "PHOTO_WORKER_REPLICAS" in run["with"]["script"]
    assert "PHOTO_PROCESSING_PREVIEW_ENABLED" in run["with"]["script"]
    assert 'test "$preview_enabled" = False' in run["with"]["script"]
    assert "run_face_embedding_benchmark" in run["with"]["script"]
    assert 'test -n "$BENCHMARK_EVENT_SLUG"' in run["with"]["script"]
    assert "printf '%s' \"$BENCHMARK_EVENT_SLUG\" | grep" not in run["with"]["script"]
    assert '--event "$BENCHMARK_EVENT_SLUG"' in run["with"]["script"]
    assert "run_web shell -c" in run["with"]["script"]
    assert "photos_per_minute" in run["with"]["script"]
    assert "run.report" not in run["with"]["script"]
    assert '"run_id"' not in run["with"]["script"]
    assert "secrets." not in run["with"]["script"]


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
    assert job["permissions"] == {"contents": "read"}
    assert job["environment"] == "staging"
    assert checkout["uses"] == "actions/checkout@v4"
    assert checkout["with"] == {"persist-credentials": False}
    assert job["env"] == {
        "YANDEX_MONITORING_API_KEY": "${{ secrets.YANDEX_MONITORING_API_KEY }}",
        "YANDEX_CLOUD_FOLDER_ID": "${{ vars.YANDEX_CLOUD_FOLDER_ID }}",
    }
    command = run_probe["run"]
    assert "python scripts/monitor_public_health.py" in command
    assert (
        "${{ github.event_name == 'schedule' && "
        "'https://findme-photo.ru/health/' || inputs.target }}" in command
    )
    assert "${{ github.event_name == 'schedule' && 'staging' || 'validation' }}" in command
    assert (
        "${{ github.event_name == 'schedule' && 'canonical-health' || 'validation-health' }}"
        in command
    )
    assert '--folder-id "$YANDEX_CLOUD_FOLDER_ID"' in command
    assert '--api-key "$YANDEX_MONITORING_API_KEY"' in command


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


def test_production_compose_uses_an_immutable_application_image() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))

    assert compose["services"]["web"]["image"] == "${APP_IMAGE:?APP_IMAGE must be set}"
    assert compose["services"]["web"]["env_file"] == ["${APP_ENV_FILE:-.env}"]
    assert "healthcheck" in compose["services"]["web"]


def test_deployment_workflows_forward_the_bounded_gunicorn_profile() -> None:
    """Both delivery paths provide the required web-process bound to remote deployment."""
    profiles = {
        "GUNICORN_WORKERS": "5",
        "GUNICORN_THREADS": "2",
        "GUNICORN_TIMEOUT": "180",
        "GUNICORN_MAX_REQUESTS": "1000",
        "GUNICORN_MAX_REQUESTS_JITTER": "100",
    }
    deployments = (
        (_load_workflow("deploy.yml"), "deploy", "Apply staging deployment"),
        (_load_workflow("promote-production.yml"), "promote", "Apply production deployment"),
    )

    for workflow, job_name, step_name in deployments:
        apply = _workflow_step(workflow, job_name, step_name)
        for name, value in profiles.items():
            assert apply["env"][name] == value
            assert name in _envs(apply)


def test_staging_builds_and_both_deployments_forward_an_immutable_opt_in_worker_image() -> None:
    """Both deployment workflows pass the opt-in worker contract, disabled by default."""
    staging = _load_workflow("deploy.yml")
    production = _load_workflow("promote-production.yml")
    build = staging["jobs"]["build"]
    staging_apply = _workflow_step(staging, "deploy", "Apply staging deployment")
    production_apply = _workflow_step(production, "promote", "Apply production deployment")

    assert build["outputs"]["worker_image"] == "${{ steps.image.outputs.worker_image }}"
    image_step = _workflow_step(staging, "build", "Select image references")
    assert "worker_image=ghcr.io/${GITHUB_REPOSITORY}-worker:${GITHUB_SHA}" in image_step["run"]
    worker_build = _workflow_step(staging, "build", "Build and push worker image")
    assert worker_build["with"] == {
        "context": ".",
        "file": "./Dockerfile.worker",
        "push": True,
        "tags": "${{ steps.image.outputs.worker_image }}",
    }

    expected = {
        "WORKER_IMAGE": "${{ needs.build.outputs.worker_image }}",
        "PHOTO_PROCESSING_ENABLED": "${{ vars.PHOTO_PROCESSING_ENABLED || 'False' }}",
        "PHOTO_PROCESSING_PREVIEW_ENABLED": (
            "${{ vars.PHOTO_PROCESSING_PREVIEW_ENABLED || 'False' }}"
        ),
        "PHOTO_PROCESSING_WORKER_TOKEN": "${{ secrets.PHOTO_PROCESSING_WORKER_TOKEN }}",
        "PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS": (
            "${{ vars.PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS || '120' }}"
        ),
        "PHOTO_PROCESSING_MAX_REQUEST_BYTES": (
            "${{ vars.PHOTO_PROCESSING_MAX_REQUEST_BYTES || '131072' }}"
        ),
        "PHOTO_WORKER_BUILD": "${{ vars.PHOTO_WORKER_BUILD || 'capture-metadata-v1' }}",
        "PHOTO_WORKER_LEASE_SECONDS": "${{ vars.PHOTO_WORKER_LEASE_SECONDS || '120' }}",
        "PHOTO_WORKER_PROCESSOR_IDENTITIES": (
            "${{ vars.PHOTO_WORKER_PROCESSOR_IDENTITIES || '1/capture_metadata/1' }}"
        ),
        "PHOTO_WORKER_REPLICAS": "${{ vars.PHOTO_WORKER_REPLICAS || '1' }}",
        "PHOTO_WORKER_PROCESSOR_TYPES": (
            "${{ vars.PHOTO_WORKER_PROCESSOR_TYPES || "
            "'selfie_query,face_embedding,capture_metadata,generate_preview' }}"
        ),
    }
    for name, value in expected.items():
        assert staging_apply["env"][name] == value
        assert name in _envs(staging_apply)

    production_expected = {
        "WORKER_IMAGE": "ghcr.io/${{ github.repository }}-worker:${{ inputs.image_sha }}",
        "PHOTO_PROCESSING_ENABLED": "${{ vars.PHOTO_PROCESSING_ENABLED || 'False' }}",
        "PHOTO_PROCESSING_PREVIEW_ENABLED": (
            "${{ vars.PHOTO_PROCESSING_PREVIEW_ENABLED || 'False' }}"
        ),
        "PHOTO_PROCESSING_WORKER_TOKEN": "${{ secrets.PHOTO_PROCESSING_WORKER_TOKEN }}",
        "PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS": (
            "${{ vars.PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS || '120' }}"
        ),
        "PHOTO_PROCESSING_MAX_REQUEST_BYTES": (
            "${{ vars.PHOTO_PROCESSING_MAX_REQUEST_BYTES || '131072' }}"
        ),
        "PHOTO_WORKER_BUILD": "${{ vars.PHOTO_WORKER_BUILD || 'capture-metadata-v1' }}",
        "PHOTO_WORKER_LEASE_SECONDS": "${{ vars.PHOTO_WORKER_LEASE_SECONDS || '120' }}",
        "PHOTO_WORKER_PROCESSOR_IDENTITIES": (
            "${{ vars.PHOTO_WORKER_PROCESSOR_IDENTITIES || '1/capture_metadata/1' }}"
        ),
        "PHOTO_WORKER_PROCESSOR_TYPES": (
            "${{ vars.PHOTO_WORKER_PROCESSOR_TYPES || "
            "'selfie_query,face_embedding,capture_metadata,generate_preview' }}"
        ),
    }
    for name, value in production_expected.items():
        assert production_apply["env"].get(name) == value
        assert name in _envs(production_apply)

    for name, value in {
        "PHOTO_PROCESSING_FACE_ENABLED": "${{ vars.PHOTO_PROCESSING_FACE_ENABLED || 'False' }}",
        "SELFIE_SEARCH_ENABLED": "${{ vars.SELFIE_SEARCH_ENABLED || 'False' }}",
        "SELFIE_SEARCH_MAX_UPLOAD_BYTES": (
            "${{ vars.SELFIE_SEARCH_MAX_UPLOAD_BYTES || '20971520' }}"
        ),
        "SELFIE_SEARCH_MAX_PIXELS": "${{ vars.SELFIE_SEARCH_MAX_PIXELS || '25000000' }}",
        "SELFIE_SEARCH_DOWNLOAD_TTL_SECONDS": (
            "${{ vars.SELFIE_SEARCH_DOWNLOAD_TTL_SECONDS || '120' }}"
        ),
        "SELFIE_SEARCH_EMBEDDING_MODEL": "${{ vars.SELFIE_SEARCH_EMBEDDING_MODEL || 'sface' }}",
        "SELFIE_SEARCH_EMBEDDING_DIMENSIONS": (
            "${{ vars.SELFIE_SEARCH_EMBEDDING_DIMENSIONS || '128' }}"
        ),
        "SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD": (
            "${{ vars.SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD || '0.363' }}"
        ),
        "SELFIE_SEARCH_TEMPORARY_PREFIX": (
            "${{ vars.SELFIE_SEARCH_TEMPORARY_PREFIX || 'selfie-search/' }}"
        ),
        "SELFIE_SEARCH_LIFECYCLE_MAX_AGE_HOURS": (
            "${{ vars.SELFIE_SEARCH_LIFECYCLE_MAX_AGE_HOURS || '24' }}"
        ),
    }.items():
        assert staging_apply["env"][name] == value
        assert production_apply["env"][name] == value
        assert name in _envs(staging_apply)
        assert name in _envs(production_apply)

    storage_preflight = _workflow_step(
        staging, "deploy", "Verify selfie-search temporary storage contract"
    )
    assert (
        "test \"$(sed -n 's/^SELFIE_SEARCH_ENABLED=//p' .env | head -n 1)\" = False"
        in (storage_preflight["with"]["script"])
    )


def test_public_environments_share_one_https_edge_overlay() -> None:
    app_compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    shared_path = ROOT / "docker-compose.https.yml"
    staging_workflow = _load_workflow("deploy.yml")
    production_workflow = _load_workflow("promote-production.yml")

    assert "ports" not in app_compose["services"]["web"]
    assert shared_path.is_file(), "Missing docker-compose.https.yml"

    shared = yaml.safe_load(shared_path.read_text(encoding="utf-8"))

    assert shared["services"]["nginx"]["ports"] == [
        "80:80",
        "443:443",
        "127.0.0.1:8080:8080",
    ]
    assert "certbot" in shared["services"]
    staging_copy = _workflow_step(staging_workflow, "deploy", "Copy staging deployment files")
    production_copy = _workflow_step(
        production_workflow, "promote", "Copy production deployment files"
    )
    assert "docker-compose.https.yml" in staging_copy["with"]["source"].split(",")
    assert "docker-compose.staging.yml" not in staging_copy["with"]["source"].split(",")
    assert "docker-compose.https.yml" in production_copy["with"]["source"].split(",")
    assert not (ROOT / "docker-compose.production.yml").exists()


def test_public_edge_configuration_is_versioned_and_wired_to_workflows() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    staging = _load_workflow("deploy.yml")
    production = _load_workflow("promote-production.yml")
    staging_apply = _workflow_step(staging, "deploy", "Apply staging deployment")
    production_apply = _workflow_step(production, "promote", "Apply production deployment")
    public_variables = ("PUBLIC_DOMAIN", "PUBLIC_DOMAIN_ALIAS")
    for variable in public_variables:
        assert re.search(rf"^{variable}=", example, re.MULTILINE)
        expected_value = f"${{{{ vars.{variable} }}}}"
        assert staging_apply["env"][variable] == expected_value
        assert production_apply["env"][variable] == expected_value
        assert variable in _envs(staging_apply)
        assert variable in _envs(production_apply)

    assert "EXPECTED_PUBLIC_IPV4" not in example
    assert "EXPECTED_PUBLIC_IPV4" not in json.dumps(staging)
    assert "EXPECTED_PUBLIC_IPV4" not in json.dumps(production)
    assert staging_apply["env"]["LETSENCRYPT_EMAIL"] == ("${{ secrets.LETSENCRYPT_EMAIL }}")
    assert production_apply["env"]["LETSENCRYPT_EMAIL"] == ("${{ secrets.LETSENCRYPT_EMAIL }}")
    assert "LETSENCRYPT_EMAIL" in _envs(staging_apply)
    assert "LETSENCRYPT_EMAIL" in _envs(production_apply)


def test_public_edges_deny_the_private_processing_prefix_before_the_proxy_catchall() -> None:
    for relative_path in ("deploy/nginx/https.conf.template", "deploy/nginx/staging.conf"):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        deny = "location ^~ /internal/photo-processing/ {\n        return 404;\n    }"
        assert deny in source
        assert source.index(deny) < source.rindex("location / {")


def test_public_edges_sanitize_bearer_access_logs_and_split_referrer_policies() -> None:
    for relative_path in ("deploy/nginx/https.conf.template", "deploy/nginx/staging.conf"):
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

    for relative_path in ("deploy/nginx/https.conf.template", "deploy/nginx/staging.conf"):
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
    staging = _workflow_step(_load_workflow("deploy.yml"), "deploy", "Apply staging deployment")
    production = _workflow_step(
        _load_workflow("promote-production.yml"), "promote", "Apply production deployment"
    )
    expected = {
        "PHOTO_UPLOAD_ENABLED": "${{ vars.PHOTO_UPLOAD_ENABLED || 'False' }}",
        "PRIVATE_MEDIA_S3_BUCKET": "${{ vars.PRIVATE_MEDIA_S3_BUCKET }}",
        "PRIVATE_MEDIA_S3_ACCESS_KEY_ID": "${{ secrets.PRIVATE_MEDIA_S3_ACCESS_KEY_ID }}",
        "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY": ("${{ secrets.PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY }}"),
        "PRIVATE_MEDIA_ALLOWED_ORIGINS": "${{ vars.PRIVATE_MEDIA_ALLOWED_ORIGINS }}",
    }

    for name, value in expected.items():
        assert re.search(rf"^{name}=", example, re.MULTILINE)
        assert staging["env"][name] == value
        assert production["env"][name] == value
        assert name in _envs(staging)
        assert name in _envs(production)
        assert f"printf '{name}=%s\\n'" in apply_script


def test_staging_deployment_forwards_preview_processing_configuration() -> None:
    staging = _workflow_step(_load_workflow("deploy.yml"), "deploy", "Apply staging deployment")
    expected = {
        "PHOTO_PROCESSING_PREVIEW_ENABLED": (
            "${{ vars.PHOTO_PROCESSING_PREVIEW_ENABLED || 'False' }}"
        ),
        "PHOTO_PROCESSING_FACE_ENABLED": "${{ vars.PHOTO_PROCESSING_FACE_ENABLED || 'False' }}",
        "PHOTO_WORKER_PROCESSOR_IDENTITIES": (
            "${{ vars.PHOTO_WORKER_PROCESSOR_IDENTITIES || '1/capture_metadata/1' }}"
        ),
    }

    for name, value in expected.items():
        assert staging["env"][name] == value
        assert name in _envs(staging)


def test_staging_storage_probe_is_manual_explicit_and_uses_the_deployed_container() -> None:
    staging = _load_workflow("deploy.yml")
    workflow_dispatch = staging[True]["workflow_dispatch"]
    probe_input = workflow_dispatch["inputs"]["verify_private_storage"]
    probe = _workflow_step(staging, "deploy", "Verify private upload storage contract")

    assert probe_input["type"] == "boolean"
    assert probe_input["default"] is False
    assert probe["if"] == "${{ inputs.verify_private_storage }}"
    assert probe["env"]["PRIVATE_MEDIA_ALLOWED_ORIGINS"] == (
        "${{ vars.PRIVATE_MEDIA_ALLOWED_ORIGINS }}"
    )
    assert probe["with"]["envs"] == "PRIVATE_MEDIA_ALLOWED_ORIGINS"
    assert "exec -T -e PHOTO_UPLOAD_ENABLED=True web" in probe["with"]["script"]
    assert "--confirm-real-storage" in probe["with"]["script"]


def test_monitoring_agent_configuration_is_manual_staging_only_and_outside_deploy_rollback() -> (
    None
):
    staging = _load_workflow("deploy.yml")
    production = _load_workflow("promote-production.yml")
    dispatch = staging[True]["workflow_dispatch"]
    agent = staging["jobs"]["configure-monitoring-agent"]
    staging_deploy = staging["jobs"]["deploy"]

    assert dispatch["inputs"]["configure_monitoring_agent"] == {
        "description": (
            "Configure the staging Yandex Unified Agent without deploying the application"
        ),
        "required": True,
        "default": False,
        "type": "boolean",
    }
    assert agent["environment"] == "staging"
    assert (
        agent["if"]
        == "${{ github.event_name == 'workflow_dispatch' && inputs.configure_monitoring_agent }}"
    )
    assert "needs" not in agent
    assert (
        staging_deploy["if"]
        == "${{ github.event_name == 'push' || !inputs.configure_monitoring_agent }}"
    )
    assert "configure-monitoring-agent" not in json.dumps(staging_deploy)
    assert "configure-monitoring-agent" not in json.dumps(production)
    run = _workflow_step(staging, "configure-monitoring-agent", "Configure staging Unified Agent")
    assert run["env"] == {"YANDEX_CLOUD_FOLDER_ID": "${{ vars.YANDEX_CLOUD_FOLDER_ID }}"}
    assert run["with"]["envs"] == "YANDEX_CLOUD_FOLDER_ID"
    assert "YANDEX_MONITORING_API_KEY" not in json.dumps(agent)
    assert "sudo sh /opt/photo-prjct/deploy/configure-monitoring-agent.sh" in run["with"]["script"]


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


def test_http_edge_fallback_remains_available_for_manual_recovery() -> None:
    staging_compose = yaml.safe_load(
        (ROOT / "docker-compose.staging.yml").read_text(encoding="utf-8")
    )

    assert set(staging_compose["services"]) == {"nginx"}
    assert staging_compose["services"]["nginx"]["ports"] == ["80:80"]
    assert "certbot" not in staging_compose["services"]
    assert "letsencrypt" not in staging_compose.get("volumes", {})
    assert staging_compose["services"]["nginx"].get("command") is None
    assert (ROOT / "deploy/nginx/staging.conf").is_file()


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
    assert "printf '%s\\n' \"$DEPLOYMENT_TARGET\"" in apply_script
    assert "printf '%s\\n' \"$COMPOSE_PROJECT_NAME\"" in apply_script
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


def test_clone_staging_suite_has_default_and_exhaustive_selection_contract() -> None:
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
    assert any(marker.startswith("clone_staging_slow:") for marker in markers)

    default_pytest = 'sh scripts/run-in-test-env.sh .venv/bin/pytest -m "not clone_staging_slow"'
    assert make_dry_run("test") == [default_pytest]
    requested_selector = (
        "tests/test_repository_foundation.py::test_adr_index_lists_all_accepted_decisions"
    )
    assert make_dry_run("test", requested_selector) == [f"{default_pytest} {requested_selector}"]
    assert make_dry_run("check").count(f"{default_pytest} --cov --cov-report=term-missing") == 1

    clone_pytest = (
        "sh scripts/run-in-test-env.sh .venv/bin/pytest "
        "tests/deployment/test_clone_staging_database.py"
    )
    assert make_dry_run("test-clone-staging") == [clone_pytest]
    assert "clone_staging_slow" not in make_dry_run("test-clone-staging")[0]

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "make test-clone-staging" in readme
    assert "critical clone" in readme.lower()
    assert "exhaustive clone" in readme.lower()
