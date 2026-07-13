import json
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


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
    staging_workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    production_workflow = (ROOT / ".github/workflows/promote-production.yml").read_text(
        encoding="utf-8"
    )

    assert "ports" not in app_compose["services"]["web"]
    assert shared_path.is_file(), "Missing docker-compose.https.yml"

    shared = yaml.safe_load(shared_path.read_text(encoding="utf-8"))

    assert shared["services"]["nginx"]["ports"] == ["80:80", "443:443"]
    assert "certbot" in shared["services"]
    for workflow_path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow_uses_shared_edge = "docker-compose.https.yml" in workflow_path.read_text(
            encoding="utf-8"
        )
        assert workflow_uses_shared_edge == (workflow_path.name == "promote-production.yml")

    assert "docker-compose.https.yml" in production_workflow
    assert "docker-compose.staging.yml" in staging_workflow
    assert not (ROOT / "docker-compose.production.yml").exists()


def test_public_edge_configuration_is_versioned_and_wired_to_workflows() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    staging = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    production = (ROOT / ".github/workflows/promote-production.yml").read_text(encoding="utf-8")

    public_variables = (
        "PUBLIC_DOMAIN",
        "PUBLIC_DOMAIN_ALIAS",
        "EXPECTED_PUBLIC_IPV4",
    )
    for variable in public_variables:
        assert re.search(rf"^{variable}=", example, re.MULTILINE)
        workflow_assignment = f"{variable}: ${{{{ vars.{variable} }}}}"
        assert workflow_assignment in staging
        assert workflow_assignment in production
        assert re.search(rf"^\s*envs: .*\b{variable}\b", staging, re.MULTILINE)
        assert re.search(rf"^\s*envs: .*\b{variable}\b", production, re.MULTILINE)

    assert "LETSENCRYPT_EMAIL" not in staging
    assert "LETSENCRYPT_EMAIL: ${{ secrets.LETSENCRYPT_EMAIL }}" in production
    assert "docker-compose.https.yml" in production


def test_deployment_workflows_delegate_edge_and_marker_operations_to_scripts() -> None:
    script_paths = (
        "deploy/certbot/reconcile-certificate.sh",
        "deploy/finalize-deployment.sh",
        "deploy/rollback-deployment.sh",
        "deploy/verify-public-edge.sh",
    )
    for relative_path in script_paths:
        assert (ROOT / relative_path).is_file(), f"Missing {relative_path}"

    staging = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    production = (ROOT / ".github/workflows/promote-production.yml").read_text(encoding="utf-8")
    apply_script = (ROOT / "deploy/apply-deployment.sh").read_text(encoding="utf-8")

    assert "/deploy/apply-deployment.sh" in staging
    assert "/deploy/finalize-deployment.sh" in staging
    assert "deploy/verify-public-edge.sh" not in staging

    for script_path in (
        "/deploy/apply-deployment.sh",
        "deploy/verify-public-edge.sh",
        "/deploy/finalize-deployment.sh",
        "/deploy/rollback-deployment.sh",
    ):
        assert script_path in production

    assert "/deploy/certbot/reconcile-certificate.sh" in apply_script

    for workflow in (staging, production):
        for embedded_operation in (
            "certbot certonly",
            "certbot renew",
            "fullchain.pem",
            "> /opt/photo-prjct/candidate-image",
            "> /opt/photo-prjct/previous-image",
            "> /opt/photo-prjct/deployed-image",
        ):
            assert embedded_operation not in workflow


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
