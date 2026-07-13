import json
import re
import shlex
import subprocess
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


def _executable_script(step: dict[str, Any], field: str) -> str:
    if field == "run":
        assert "uses" not in step
        script = step.get("run")
    else:
        assert field == "with.script"
        assert step.get("uses") == "appleboy/ssh-action@v1.0.3"
        script = step.get("with", {}).get("script")

    assert isinstance(script, str), f"Step {step.get('name')!r} has no {field}"
    return script


def _job_executable_scripts(workflow: dict[str, Any], job_name: str) -> list[str]:
    scripts = []
    for step in workflow["jobs"][job_name]["steps"]:
        if "run" in step:
            scripts.append(_executable_script(step, "run"))
        if "script" in step.get("with", {}):
            scripts.append(_executable_script(step, "with.script"))
    return scripts


def _normalize_shell(script: str) -> str:
    return re.sub(r"\\\r?\n\s*", " ", script)


def _script_calls(script: str, script_name: str) -> list[tuple[int, list[str]]]:
    allowed_paths = {
        f"deploy/{script_name}",
        f"./deploy/{script_name}",
        f"/opt/photo-prjct/deploy/{script_name}",
        f"$DEPLOY_ROOT/deploy/{script_name}",
        f"${{DEPLOY_ROOT}}/deploy/{script_name}",
    }
    calls = []
    offset = 0
    for line in _normalize_shell(script).splitlines(keepends=True):
        search_offset = 0
        for segment in re.split(r"\s*(?:&&|\|\||;)\s*", line):
            segment_offset = line.find(segment, search_offset)
            search_offset = segment_offset + len(segment)
            stripped = segment.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                tokens = shlex.split(stripped)
            except ValueError:
                continue

            index = 0
            if tokens and tokens[0] == "env":
                index += 1
                while index < len(tokens) and (
                    tokens[index].startswith("-") or "=" in tokens[index]
                ):
                    index += 1
            else:
                while index < len(tokens) and re.fullmatch(
                    r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[index]
                ):
                    index += 1

            if index < len(tokens) and tokens[index] in {"sh", "bash"}:
                index += 1
                while index < len(tokens) and tokens[index].startswith("-"):
                    index += 1
                if index < len(tokens) and tokens[index] in allowed_paths:
                    calls.append((offset + segment_offset, tokens[index + 1 :]))
        offset += len(line)
    return calls


def _script_call_position(script: str, script_name: str) -> int | None:
    calls = _script_calls(script, script_name)
    return calls[0][0] if calls else None


def _required_script_call_position(script: str, script_name: str) -> int:
    position = _script_call_position(script, script_name)
    assert position is not None, f"Missing executable call to deploy/{script_name}"
    return position


def _envs(step: dict[str, Any]) -> set[str]:
    envs = step.get("with", {}).get("envs", "")
    assert isinstance(envs, str)
    return {name.strip() for name in envs.split(",") if name.strip()}


def _contains_readonly_eligibility_check(script: str) -> bool:
    allowed_markers = {
        "/opt/photo-prjct/deployed-image",
        "$DEPLOY_ROOT/deployed-image",
        "${DEPLOY_ROOT}/deployed-image",
    }
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            tokens = shlex.split(stripped)
        except ValueError:
            continue
        if len(tokens) != 4 or tokens[0] != "test" or tokens[2] not in {"=", "=="}:
            continue

        marker_match = re.fullmatch(r"\$\(cat\s+([^\s)]+)\)", tokens[1])
        if (
            marker_match
            and marker_match.group(1) in allowed_markers
            and tokens[3]
            in {
                "$APP_IMAGE",
                "${APP_IMAGE}",
            }
        ):
            return True
    return False


def _approved_certbot_delegation(segment: str) -> bool:
    if re.search(r"[`]|\$\(|<\(|>\(", segment):
        return False

    approved_calls = [
        call
        for script_name in (
            "certbot/renew-certificates.sh",
            "certbot/reconcile-certificate.sh",
        )
        for call in _script_calls(segment, script_name)
    ]
    if len(approved_calls) != 1 or approved_calls[0][0] != 0:
        return False

    forbidden_arguments = re.compile(r"(?:^|/)(?:docker|sh|bash|certbot|certonly|renew)(?::|$|/)")
    return all(
        not re.search(r"[`();<>|&]", argument) and not forbidden_arguments.search(argument)
        for argument in approved_calls[0][1]
    )


def _assert_no_embedded_certificate_or_marker_mutation(script: str) -> None:
    normalized = _normalize_shell(script)
    for line in normalized.splitlines():
        segments = re.split(r"\s*(?:&&|\|\||;)\s*", line)
        for segment in segments:
            if _approved_certbot_delegation(segment):
                assert len(segments) == 1
                continue
            assert not any(
                _script_calls(segment, script_name)
                for script_name in (
                    "certbot/renew-certificates.sh",
                    "certbot/reconcile-certificate.sh",
                )
            ), "Approved Certbot scripts must be invoked as standalone commands"
            executable_line = r"(?m)^\s*(?!#)[^\n]*"
            assert not re.search(
                executable_line + r"certbot(?:/certbot(?::[^\s]+)?)?[^\n]*\b(?:certonly|renew)\b",
                segment,
            )

    executable_line = r"(?m)^\s*(?!#)[^\n]*"
    assert not re.search(executable_line + r"fullchain\.pem", normalized)

    marker_root = r"[\"']?(?:/opt/photo-prjct|\$(?:DEPLOY_ROOT|\{DEPLOY_ROOT\}))[\"']?"
    marker_path = rf"{marker_root}/[\"']?(?:candidate-image|previous-image|deployed-image)[\"']?"
    assert "candidate-image" not in normalized
    assert "previous-image" not in normalized
    assert not re.search(executable_line + rf"(?:>>?)\s*{marker_path}", normalized)
    assert not re.search(
        rf"(?m)(?:^|[;&|])\s*(?!#)(?:sudo\s+)?"
        rf"(?:mv|cp|tee|rm|touch|install|truncate)"
        rf"\b[^\n]*{marker_path}",
        normalized,
    )


def test_documentation_foundation_exists() -> None:
    expected_paths = (
        "docs/architecture.md",
        "docs/adr/README.md",
        "docs/adr/0000-template.md",
        "docs/plans/README.md",
        "docs/plans/0000-template.md",
    )

    for relative_path in expected_paths:
        assert (ROOT / relative_path).is_file(), f"Missing {relative_path}"


def test_adr_index_lists_all_accepted_decisions() -> None:
    index = (ROOT / "docs/adr/README.md").read_text(encoding="utf-8")

    for number in (*range(1, 8), 10):
        assert re.search(rf"\| {number:04d} \|.*\| Accepted \|", index)
    for number in (8, 9):
        assert re.search(rf"\| {number:04d} \|.*\| Superseded \|", index)


def test_project_skills_have_valid_metadata_and_ui_configuration() -> None:
    for skill_name in (
        "manage-yandex-cloud",
        "update-visual-design",
        "write-adr",
        "write-plan",
    ):
        skill_dir = ROOT / ".agents" / "skills" / skill_name
        skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---\n", skill_text, re.DOTALL)
        assert match, f"{skill_name} has no YAML frontmatter"

        frontmatter = yaml.safe_load(match.group(1))
        assert set(frontmatter) == {"name", "description"}
        assert frontmatter["name"] == skill_name
        assert frontmatter["description"].startswith("Use when ")

        ui_config = yaml.safe_load(
            (skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")
        )
        assert set(ui_config["interface"]) == {
            "display_name",
            "short_description",
            "default_prompt",
        }


def test_skills_reference_repository_templates() -> None:
    references = {
        "write-adr": "docs/adr/0000-template.md",
        "write-plan": "docs/plans/0000-template.md",
    }

    for skill_name, template in references.items():
        skill = (ROOT / ".agents" / "skills" / skill_name / "SKILL.md").read_text(encoding="utf-8")
        assert template in skill


def test_yandex_cloud_skill_requires_pricing_confirmation() -> None:
    skill_dir = ROOT / ".agents" / "skills" / "manage-yandex-cloud"
    skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    inventory = (skill_dir / "references" / "inventory.md").read_text(encoding="utf-8")

    assert "explicit manual confirmation" in skill
    assert "Approval of a plan is not approval to execute" in skill
    assert "yc config list" in skill
    assert "must not" in skill.partition("yc config list")[2][:120]
    assert "b1gmcsmr51o5kvp86l55" in inventory
    assert "b1g2qttgfhb4gdunvlge" in inventory
    assert "token" not in inventory.lower()


def test_deployment_workflows_separate_staging_and_production() -> None:
    staging_text = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    staging = yaml.safe_load(staging_text)
    production = yaml.safe_load(
        (ROOT / ".github/workflows/promote-production.yml").read_text(encoding="utf-8")
    )

    assert staging[True]["push"]["branches"] == ["main"]
    assert staging["jobs"]["deploy"]["environment"] == "staging"
    assert staging["jobs"]["deploy"]["concurrency"]["group"] == "deploy-staging"
    assert set(production[True]) == {"workflow_dispatch"}
    assert production["jobs"]["promote"]["environment"] == "production"
    assert production["jobs"]["promote"]["concurrency"]["group"] == "deploy-production"

    assert ".staging-reset-v1" not in staging_text
    assert "--project-name photo-prjct down" not in staging_text
    assert "down --volumes" not in staging_text


def test_production_compose_uses_an_immutable_application_image() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))

    assert compose["services"]["web"]["image"] == "${APP_IMAGE:?APP_IMAGE must be set}"
    assert "healthcheck" in compose["services"]["web"]


def test_public_environments_share_one_https_edge_overlay() -> None:
    app_compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    shared_path = ROOT / "docker-compose.https.yml"
    staging_workflow = _load_workflow("deploy.yml")
    production_workflow = _load_workflow("promote-production.yml")

    assert "ports" not in app_compose["services"]["web"]
    assert shared_path.is_file(), "Missing docker-compose.https.yml"

    shared = yaml.safe_load(shared_path.read_text(encoding="utf-8"))

    assert shared["services"]["nginx"]["ports"] == ["80:80", "443:443"]
    assert "certbot" in shared["services"]
    for workflow_path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow_uses_shared_edge = "docker-compose.https.yml" in json.dumps(
            _load_workflow(workflow_path.name)
        )
        assert workflow_uses_shared_edge == (workflow_path.name == "promote-production.yml")

    staging_copy = _workflow_step(staging_workflow, "deploy", "Copy staging deployment files")
    production_copy = _workflow_step(
        production_workflow, "promote", "Copy production deployment files"
    )
    assert "docker-compose.staging.yml" in staging_copy["with"]["source"].split(",")
    assert "docker-compose.https.yml" not in staging_copy["with"]["source"].split(",")
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
    assert "LETSENCRYPT_EMAIL" not in json.dumps(staging)
    assert production_apply["env"]["LETSENCRYPT_EMAIL"] == ("${{ secrets.LETSENCRYPT_EMAIL }}")
    assert "LETSENCRYPT_EMAIL" in _envs(production_apply)


def test_focused_deployment_scripts_are_versioned() -> None:
    for relative_path in (
        "deploy/certbot/reconcile-certificate.sh",
        "deploy/verify-public-edge.sh",
    ):
        assert (ROOT / relative_path).is_file(), f"Missing {relative_path}"
    assert not (ROOT / "deploy/finalize-deployment.sh").exists()
    assert not (ROOT / "deploy/rollback-deployment.sh").exists()


def test_deployment_workflows_use_one_blocking_apply_step() -> None:
    staging = _load_workflow("deploy.yml")
    production = _load_workflow("promote-production.yml")
    staging_apply_step = _workflow_step(staging, "deploy", "Apply staging deployment")
    production_eligibility_step = _workflow_step(
        production, "verify-staging", "Confirm image was deployed successfully to staging"
    )
    production_apply_step = _workflow_step(production, "promote", "Apply production deployment")
    staging_apply = _executable_script(staging_apply_step, "with.script")
    production_eligibility = _executable_script(production_eligibility_step, "with.script")
    production_apply = _executable_script(production_apply_step, "with.script")
    apply_script = (ROOT / "deploy/apply-deployment.sh").read_text(encoding="utf-8")

    assert _contains_readonly_eligibility_check(production_eligibility)
    for executable in (
        *_job_executable_scripts(staging, "deploy"),
        *_job_executable_scripts(production, "verify-staging"),
        *_job_executable_scripts(production, "promote"),
    ):
        _assert_no_embedded_certificate_or_marker_mutation(executable)

    _required_script_call_position(production_apply, "apply-deployment.sh")
    _required_script_call_position(staging_apply, "apply-deployment.sh")

    assert staging_apply_step["env"]["APP_IMAGE"] == "${{ needs.build.outputs.app_image }}"
    assert "APP_IMAGE" in _envs(staging_apply_step)
    for workflow in (staging, production):
        serialized = json.dumps(workflow)
        assert "finalize-deployment.sh" not in serialized
        assert "rollback-deployment.sh" not in serialized
        assert "Verify production public edge" not in serialized

    assert "DEPLOYMENT_TARGET=staging" in staging_apply
    assert "DEPLOYMENT_TARGET=production" in production_apply
    assert 'sh "$DEPLOY_ROOT/deploy/certbot/reconcile-certificate.sh"' in apply_script
    assert re.search(r"(?m)^\s*(?!#)[^\n]*docker-compose\.https\.yml", apply_script)


def test_staging_can_temporarily_disable_https_without_changing_production_default() -> None:
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


def test_deployment_workflows_always_allow_the_public_domain() -> None:
    for workflow_name in ("deploy.yml", "promote-production.yml"):
        workflow = (ROOT / ".github/workflows" / workflow_name).read_text(encoding="utf-8")
        assert "deploy/apply-deployment.sh" in workflow
        assert "ALLOWED_HOSTS" in workflow


def test_deployment_script_verifies_the_edge_and_exact_application_image() -> None:
    script = (ROOT / "deploy/apply-deployment.sh").read_text(encoding="utf-8")

    assert "compose_up_status=0" in script
    assert "compose up -d --remove-orphans || compose_up_status=$?" in script
    assert "Deployment health check attempt $attempt failed; retrying" in script
    assert "docker inspect --format '{{.Config.Image}}'" in script
    assert '"$running_image" = "$requested_image"' in script
    assert "candidate-image" not in script
    assert "previous-image" not in script
    assert 'mv "$marker_tmp" "$DEPLOY_ROOT/deployed-image"' in script
    assert 'sh "$DEPLOY_ROOT/deploy/verify-public-edge.sh"' in script
    assert "down --volumes" not in script


def test_apply_delegates_certificate_bootstrap_and_restores_edge_on_failure() -> None:
    script = (ROOT / "deploy/apply-deployment.sh").read_text(encoding="utf-8")

    assert "compose stop nginx" in script
    assert 'sh "$DEPLOY_ROOT/deploy/certbot/reconcile-certificate.sh"' in script
    assert "Certificate bootstrap failed" in script
    assert "recover_previous_deployment" in script
    assert "compose up -d --remove-orphans" in script
    assert "certbot/certbot:v2.11.0 certonly" not in script


def test_focused_scripts_encode_minimal_release_contracts() -> None:
    reconcile = (ROOT / "deploy/certbot/reconcile-certificate.sh").read_text(encoding="utf-8")
    verify = (ROOT / "deploy/verify-public-edge.sh").read_text(encoding="utf-8")

    assert reconcile.count("certbot/certbot:v2.11.0 certonly") == 1
    assert "--entrypoint sh" in reconcile
    assert "--force-renewal" not in reconcile
    assert "--network host" in reconcile
    assert "while " not in reconcile
    assert "subjectAltName" not in reconcile

    assert "dns.google" not in verify
    assert "openssl" not in verify
    assert "EXPECTED_PUBLIC_IPV4" not in verify
    assert "EXPECTED_PUBLIC_IPV4" not in reconcile
    assert 'if [ -n "$PUBLIC_DOMAIN_ALIAS" ]' in verify
    assert "--max-time" in verify
    assert "http_code" in verify
    assert "redirect_url" in verify


def test_deployment_workflows_use_the_versioned_deployment_script() -> None:
    for workflow_name, job_name in (
        ("deploy.yml", "deploy"),
        ("promote-production.yml", "promote"),
    ):
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows" / workflow_name).read_text(encoding="utf-8")
        )
        apply_step = next(
            step
            for step in workflow["jobs"][job_name]["steps"]
            if step["name"].startswith("Apply ")
        )

        script = apply_step["with"]["script"]

        assert "/deploy/apply-deployment.sh" in script
        assert "docker compose" not in script
        assert "cat > /opt/photo-prjct/.env" not in script


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


def test_visual_design_skill_has_required_files() -> None:
    skill = ROOT / ".agents/skills/update-visual-design"

    for relative_path in (
        "SKILL.md",
        "agents/openai.yaml",
        "references/screen-inventory.md",
    ):
        assert (skill / relative_path).is_file(), f"Missing {relative_path}"

    guidance = (skill / "SKILL.md").read_text(encoding="utf-8")
    inventory = (skill / "references/screen-inventory.md").read_text(encoding="utf-8")

    assert "Never create `src/proto`" in guidance
    assert "tests/visual/templates/design_reference/" in guidance
    assert "| Promotions | design-reference |" in inventory
    assert "| Catalog | production |" in inventory


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

    assert package["scripts"]["test:visual"] == "sh tests/visual/run-in-container.sh test"
    assert package["scripts"]["test:visual:update"] == (
        "sh tests/visual/run-in-container.sh update"
    )
    assert dockerfile.count("@sha256:") == 2
    assert "python:3.12-slim-bookworm@sha256:" in dockerfile
    assert "node:22-bookworm-slim@sha256:" in dockerfile
    assert "npx playwright install --with-deps chromium" in dockerfile
    assert compose["services"]["visual-tests"]["depends_on"]["postgres"]["condition"] == (
        "service_healthy"
    )
    assert compose["services"]["visual-tests"]["environment"]["CI"] == "${CI:-false}"
