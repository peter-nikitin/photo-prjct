import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

import pytest
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


def _required_script_call_arguments(script: str, script_name: str) -> list[str]:
    calls = _script_calls(script, script_name)
    assert len(calls) == 1, f"Expected one executable call to deploy/{script_name}"
    arguments = calls[0][1]
    assert arguments, f"deploy/{script_name} requires arguments"
    return arguments


def _required_script_call_position(script: str, script_name: str) -> int:
    position = _script_call_position(script, script_name)
    assert position is not None, f"Missing executable call to deploy/{script_name}"
    return position


def _assert_success_dependent_sequence(script: str, first_script: str, second_script: str) -> None:
    normalized = _normalize_shell(script)
    first_position = _required_script_call_position(normalized, first_script)
    second_position = _required_script_call_position(normalized, second_script)
    assert first_position < second_position

    between_calls = normalized[first_position:second_position]
    assert "||" not in between_calls
    assert not re.search(r"(?<!\|)\|(?!\|)|(?<!&)&(?!&)", between_calls)
    assert not re.search(r";\s*(?:true|:)\b", between_calls)

    fail_fast = False
    for match in re.finditer(r"(?m)^\s*set\s+([+-][A-Za-z]+)", normalized[:first_position]):
        flags = match.group(1)
        if "e" in flags:
            fail_fast = flags.startswith("-")

    same_line = "\n" not in normalized[first_position:second_position]
    chained = same_line and "&&" in between_calls and ";" not in between_calls
    if not chained:
        assert "&&" not in between_calls
    assert fail_fast or chained, (
        f"{second_script} must depend on successful completion of {first_script}"
    )


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


def _assert_no_embedded_certificate_or_marker_mutation(script: str) -> None:
    normalized = _normalize_shell(script)
    for line in normalized.splitlines():
        for segment in re.split(r"\s*(?:&&|\|\||;)\s*", line):
            if _script_call_position(segment, "certbot/renew-certificates.sh") is not None:
                continue
            if _script_call_position(segment, "certbot/reconcile-certificate.sh") is not None:
                continue
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


def test_workflow_script_calls_are_executable_and_path_scoped() -> None:
    accepted_calls = (
        'sh "$DEPLOY_ROOT/deploy/finalize-deployment.sh" "$APP_IMAGE"',
        'sh "$DEPLOY_ROOT"/deploy/finalize-deployment.sh "$APP_IMAGE"',
        "env APP_IMAGE=image sh /opt/photo-prjct/deploy/finalize-deployment.sh image",
        "bash ./deploy/finalize-deployment.sh image",
    )
    for script in accepted_calls:
        assert _script_call_position(script, "finalize-deployment.sh") == 0
    assert _required_script_call_arguments(accepted_calls[0], "finalize-deployment.sh") == [
        "$APP_IMAGE"
    ]
    with pytest.raises(AssertionError):
        _required_script_call_arguments(
            "sh deploy/finalize-deployment.sh", "finalize-deployment.sh"
        )
    assert _required_script_call_arguments(
        "bash deploy/rollback-deployment.sh $APP_IMAGE https",
        "rollback-deployment.sh",
    ) == ["$APP_IMAGE", "https"]

    assert (
        _script_call_position(
            "sh /tmp/fake-deploy/finalize-deployment.sh image",
            "finalize-deployment.sh",
        )
        is None
    )


def test_workflow_shell_guards_reject_embedded_operations_and_suppressed_errors() -> None:
    with pytest.raises(AssertionError):
        _assert_no_embedded_certificate_or_marker_mutation(
            "docker run --rm certbot/certbot:v2.11.0 \\\n                certonly -d example.com"
        )

    _assert_no_embedded_certificate_or_marker_mutation(
        'sh "$DEPLOY_ROOT/deploy/certbot/reconcile-certificate.sh"'
    )
    _assert_no_embedded_certificate_or_marker_mutation("bash deploy/certbot/renew-certificates.sh")

    _assert_success_dependent_sequence(
        "set -eu\nsh deploy/apply-deployment.sh\nsh deploy/finalize-deployment.sh $APP_IMAGE",
        "apply-deployment.sh",
        "finalize-deployment.sh",
    )
    _assert_success_dependent_sequence(
        "sh deploy/apply-deployment.sh && sh deploy/finalize-deployment.sh $APP_IMAGE",
        "apply-deployment.sh",
        "finalize-deployment.sh",
    )
    for bypass in (
        "sh deploy/apply-deployment.sh || true\nsh deploy/finalize-deployment.sh $APP_IMAGE",
        "set -e\nsh deploy/apply-deployment.sh; true; sh deploy/finalize-deployment.sh $APP_IMAGE",
        "set -e\nsh deploy/apply-deployment.sh && true\n"
        "sh deploy/finalize-deployment.sh $APP_IMAGE",
        "set -e\nsh deploy/apply-deployment.sh | tee /tmp/apply.log\n"
        "sh deploy/finalize-deployment.sh $APP_IMAGE",
        "sh deploy/apply-deployment.sh\nsh deploy/finalize-deployment.sh $APP_IMAGE",
    ):
        with pytest.raises(AssertionError):
            _assert_success_dependent_sequence(
                bypass, "apply-deployment.sh", "finalize-deployment.sh"
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
    production_verify = _workflow_step(production, "promote", "Verify production public edge")

    public_variables = (
        "PUBLIC_DOMAIN",
        "PUBLIC_DOMAIN_ALIAS",
        "EXPECTED_PUBLIC_IPV4",
    )
    for variable in public_variables:
        assert re.search(rf"^{variable}=", example, re.MULTILINE)
        expected_value = f"${{{{ vars.{variable} }}}}"
        assert staging_apply["env"][variable] == expected_value
        assert production_apply["env"][variable] == expected_value
        assert production_verify["env"][variable] == expected_value
        assert variable in _envs(staging_apply)
        assert variable in _envs(production_apply)

    assert "LETSENCRYPT_EMAIL" not in json.dumps(staging)
    assert production_apply["env"]["LETSENCRYPT_EMAIL"] == ("${{ secrets.LETSENCRYPT_EMAIL }}")
    assert "LETSENCRYPT_EMAIL" in _envs(production_apply)
    assert "LETSENCRYPT_EMAIL" not in production_verify.get("env", {})


def test_focused_deployment_scripts_are_versioned() -> None:
    for relative_path in (
        "deploy/certbot/reconcile-certificate.sh",
        "deploy/finalize-deployment.sh",
        "deploy/rollback-deployment.sh",
        "deploy/verify-public-edge.sh",
    ):
        assert (ROOT / relative_path).is_file(), f"Missing {relative_path}"


def test_deployment_workflows_delegate_edge_and_marker_operations_to_scripts() -> None:
    staging = _load_workflow("deploy.yml")
    production = _load_workflow("promote-production.yml")
    staging_apply_step = _workflow_step(staging, "deploy", "Apply staging deployment")
    production_eligibility_step = _workflow_step(
        production, "verify-staging", "Confirm image was deployed successfully to staging"
    )
    production_apply_step = _workflow_step(production, "promote", "Apply production deployment")
    production_verify_step = _workflow_step(production, "promote", "Verify production public edge")
    production_finalize_step = _workflow_step(
        production, "promote", "Finalize production deployment"
    )
    production_rollback_step = _workflow_step(
        production, "promote", "Roll back production deployment"
    )
    production_fail_step = _workflow_step(production, "promote", "Fail production deployment")

    staging_apply = _executable_script(staging_apply_step, "with.script")
    production_eligibility = _executable_script(production_eligibility_step, "with.script")
    production_apply = _executable_script(production_apply_step, "with.script")
    production_verify = _executable_script(production_verify_step, "run")
    production_finalize = _executable_script(production_finalize_step, "with.script")
    production_rollback = _executable_script(production_rollback_step, "with.script")
    production_fail = _executable_script(production_fail_step, "run")
    apply_script = (ROOT / "deploy/apply-deployment.sh").read_text(encoding="utf-8")

    assert _contains_readonly_eligibility_check(production_eligibility)
    for executable in (
        *_job_executable_scripts(staging, "deploy"),
        *_job_executable_scripts(production, "verify-staging"),
        *_job_executable_scripts(production, "promote"),
    ):
        _assert_no_embedded_certificate_or_marker_mutation(executable)

    _assert_success_dependent_sequence(
        staging_apply, "apply-deployment.sh", "finalize-deployment.sh"
    )
    assert _script_call_position(staging_apply, "verify-public-edge.sh") is None
    assert _script_call_position(staging_apply, "rollback-deployment.sh") is None
    assert all(
        _script_call_position(script, "verify-public-edge.sh") is None
        and _script_call_position(script, "rollback-deployment.sh") is None
        for script in _job_executable_scripts(staging, "deploy")
    )

    _required_script_call_position(production_apply, "apply-deployment.sh")
    _required_script_call_position(production_verify, "verify-public-edge.sh")

    image_arguments = {"$APP_IMAGE", "${APP_IMAGE}"}
    staging_finalize_arguments = _required_script_call_arguments(
        staging_apply, "finalize-deployment.sh"
    )
    production_finalize_arguments = _required_script_call_arguments(
        production_finalize, "finalize-deployment.sh"
    )
    production_rollback_arguments = _required_script_call_arguments(
        production_rollback, "rollback-deployment.sh"
    )
    assert staging_finalize_arguments
    assert production_finalize_arguments
    assert len(production_rollback_arguments) >= 2
    assert staging_finalize_arguments[0] in image_arguments
    assert production_finalize_arguments[0] in image_arguments
    assert production_rollback_arguments[0] in image_arguments
    assert production_rollback_arguments[1] == "https"

    verify_id = production_verify_step.get("id")
    assert isinstance(verify_id, str) and verify_id
    assert production_verify_step.get("continue-on-error") is True
    success_condition = f"steps.{verify_id}.outcome == 'success'"
    failure_condition = f"always() && steps.{verify_id}.outcome == 'failure'"
    assert production_finalize_step.get("if") == success_condition
    assert production_rollback_step.get("if") == failure_condition
    assert production_fail_step.get("if") == failure_condition
    assert re.search(r"(?m)^\s*exit\s+[1-9][0-9]*\s*$", production_fail)

    promote_steps = production["jobs"]["promote"]["steps"]
    apply_index = promote_steps.index(production_apply_step)
    verify_index = promote_steps.index(production_verify_step)
    assert apply_index < verify_index
    assert verify_index < promote_steps.index(production_finalize_step)
    assert verify_index < promote_steps.index(production_rollback_step)
    assert verify_index < promote_steps.index(production_fail_step)

    assert "DEPLOYMENT_TARGET=staging" in staging_apply
    assert "DEPLOYMENT_TARGET=production" in production_apply
    _required_script_call_position(apply_script, "certbot/reconcile-certificate.sh")
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
    result = subprocess.run(
        ["sh", "-n", ROOT / "deploy/apply-deployment.sh"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


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
    assert '"$running_image" = "$APP_IMAGE"' in script
    assert 'printf \'%s\\n\' "$APP_IMAGE" > "$DEPLOY_ROOT/deployed-image"' in script


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
