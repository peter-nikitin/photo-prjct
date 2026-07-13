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

    for number in range(1, 9):
        assert re.search(rf"\| 000{number} \|.*\| Accepted \|", index)


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


def test_production_compose_has_a_private_application_behind_https_edge() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))

    assert "ports" not in compose["services"]["web"]
    assert compose["services"]["nginx"]["ports"] == ["80:80", "443:443"]
    assert compose["services"]["nginx"]["depends_on"]["web"]["condition"] == "service_healthy"
    assert "certbot" in compose["services"]
    assert compose["services"]["certbot"]["entrypoint"] == [
        "/bin/sh",
        "/opt/certbot/renew-certificates.sh",
    ]
    assert compose["services"]["nginx"]["healthcheck"]["test"] == [
        "CMD-SHELL",
        "wget -q -O /dev/null http://127.0.0.1/health/",
    ]
    assert "letsencrypt" in compose["volumes"]
    assert (ROOT / "deploy/nginx/http.conf").is_file()
    assert (ROOT / "deploy/nginx/https.conf").is_file()


def test_deployment_workflows_configure_https_edge_without_committing_values() -> None:
    for workflow_name in ("deploy.yml", "promote-production.yml"):
        workflow = (ROOT / ".github/workflows" / workflow_name).read_text(encoding="utf-8")

        assert "PUBLIC_DOMAIN: ${{ vars.PUBLIC_DOMAIN }}" in workflow
        assert "LETSENCRYPT_EMAIL: ${{ secrets.LETSENCRYPT_EMAIL }}" in workflow
        assert "certbot" in workflow
        assert "https://$PUBLIC_DOMAIN/health/" in workflow


def test_staging_can_temporarily_disable_https_without_changing_production_default() -> None:
    staging = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    production = (ROOT / ".github/workflows/promote-production.yml").read_text(encoding="utf-8")
    nginx_startup = (ROOT / "deploy/nginx/reload-nginx.sh").read_text(encoding="utf-8")

    assert "ENABLE_HTTPS: ${{ vars.ENABLE_HTTPS || 'false' }}" in staging
    assert "ENABLE_HTTPS: ${{ vars.ENABLE_HTTPS || 'true' }}" in production
    assert 'if [ "$ENABLE_HTTPS" != "true" ] && [ "$ENABLE_HTTPS" != "false" ]; then' in staging
    assert 'if [ "$ENABLE_HTTPS" != "true" ] && [ "$ENABLE_HTTPS" != "false" ]; then' in production
    assert 'if [ "$ENABLE_HTTPS" = "true" ]; then' in staging
    assert 'if [ "$ENABLE_HTTPS" = "true" ]; then' in production
    assert 'ENABLE_HTTPS="${ENABLE_HTTPS:-true}"' in nginx_startup


def test_deployment_shell_scripts_have_valid_bash_syntax() -> None:
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
        result = subprocess.run(
            ["bash", "-n"],
            input=apply_step["with"]["script"],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr


def test_deployment_workflows_always_allow_the_public_domain() -> None:
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

        assert (
            'ALLOWED_HOSTS="${ALLOWED_HOSTS:+$ALLOWED_HOSTS,}$PUBLIC_DOMAIN"'
            in apply_step["with"]["script"]
        )


def test_deployment_workflows_retry_and_report_edge_health_failures() -> None:
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

        assert " up -d --wait" not in script
        assert " up -d\n" in script
        assert "max_attempts=12" in script
        assert "until curl --fail-with-body --silent --show-error" in script
        assert "Deployment health check failed after $max_attempts attempts" in script


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
