import re
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

    for number in range(1, 6):
        assert re.search(rf"\| 000{number} \|.*\| Accepted \|", index)


def test_project_skills_have_valid_metadata_and_ui_configuration() -> None:
    for skill_name in ("manage-yandex-cloud", "write-adr", "write-plan"):
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
