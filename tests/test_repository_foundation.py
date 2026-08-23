import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ADR_INDEX_ROW = re.compile(
    r"^\| (?P<number>\d{4}) \| \[[^]]+\]\((?P<link>[^)#]+)\) \| "
    r"(?P<status>Accepted|Proposed|Rejected|Superseded) \|$",
    re.MULTILINE,
)
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^]]*\]\((?P<target>[^)]+)\)")


def _load_workflow(workflow_name: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / ".github/workflows" / workflow_name).read_text(encoding="utf-8"))


def _workflow_step(workflow: dict[str, Any], job_name: str, step_name: str) -> dict[str, Any]:
    matching_steps = [
        step for step in workflow["jobs"][job_name]["steps"] if step.get("name") == step_name
    ]
    assert len(matching_steps) == 1, f"Expected one {step_name!r} step"
    return matching_steps[0]


def _assert_adr_index(adr_root: Path) -> None:
    indexed = {
        match["number"]: (match["link"], match["status"])
        for match in ADR_INDEX_ROW.finditer((adr_root / "README.md").read_text(encoding="utf-8"))
    }
    decisions = sorted(
        path
        for path in adr_root.glob("[0-9][0-9][0-9][0-9]-*.md")
        if path.name != "0000-template.md"
    )

    assert set(indexed) == {path.stem[:4] for path in decisions}
    for decision in decisions:
        status = re.search(
            r"^- Status: (Accepted|Proposed|Rejected|Superseded)$",
            decision.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        assert status is not None, decision
        link, indexed_status = indexed[decision.stem[:4]]
        assert indexed_status == status.group(1), decision
        assert (adr_root / link).resolve() == decision.resolve()


def _assert_local_markdown_links_resolve(sources: tuple[Path, ...]) -> None:
    for entry in sources:
        markdown_sources = (entry,) if entry.is_file() else entry.rglob("*.md")
        for source in markdown_sources:
            for match in MARKDOWN_LINK.finditer(source.read_text(encoding="utf-8")):
                target = match["target"].strip().strip("<>").split(maxsplit=1)[0]
                location = target.split("#", maxsplit=1)[0]
                if not location or "://" in location or location.startswith(("mailto:", "/")):
                    continue
                assert (source.parent / location).resolve().exists(), f"{source}: {target}"


def _assert_project_skill_metadata(skills_root: Path) -> None:
    skill_dirs = sorted(path.parent for path in skills_root.glob("*/SKILL.md"))
    assert skill_dirs, f"No project skills found in {skills_root}"
    interface_keys = {"display_name", "short_description", "default_prompt"}

    for skill_dir in skill_dirs:
        skill_parts = (skill_dir / "SKILL.md").read_text(encoding="utf-8").split("---", 2)
        assert len(skill_parts) == 3 and not skill_parts[0].strip(), (
            f"{skill_dir}: SKILL.md must begin with YAML frontmatter"
        )
        frontmatter = yaml.safe_load(skill_parts[1])
        assert isinstance(frontmatter, dict), f"{skill_dir}: invalid skill frontmatter"
        assert set(frontmatter) == {"name", "description"}, f"{skill_dir}: invalid skill schema"
        assert frontmatter["name"] == skill_dir.name, (
            f"{skill_dir}: skill name must match directory"
        )
        assert isinstance(frontmatter["description"], str) and frontmatter["description"].strip(), (
            f"{skill_dir}: skill description must be non-empty"
        )

        ui_config = yaml.safe_load(
            (skill_dir / "agents" / "openai.yaml").read_text(encoding="utf-8")
        )
        assert isinstance(ui_config, dict), f"{skill_dir}: invalid UI configuration"
        assert set(ui_config) == {"interface"}, f"{skill_dir}: invalid UI configuration schema"
        interface = ui_config["interface"]
        assert isinstance(interface, dict) and set(interface) == interface_keys, (
            f"{skill_dir}: invalid interface schema"
        )
        assert all(isinstance(value, str) and value.strip() for value in interface.values()), (
            f"{skill_dir}: interface values must be non-empty strings"
        )


def test_adr_index_lists_each_decision_with_its_status_and_local_file() -> None:
    _assert_adr_index(ROOT / "docs" / "adr")


def test_repository_markdown_links_resolve_to_local_files() -> None:
    _assert_local_markdown_links_resolve(
        (
            ROOT / "AGENTS.md",
            ROOT / "docs" / "architecture.md",
            ROOT / "docs" / "product-jobs.md",
            ROOT / "docs" / "engineering-jobs.md",
            ROOT / "docs" / "plans" / "2026-08-23-pareto-test-suite-refactor.md",
            ROOT
            / "docs"
            / "superpowers"
            / "specs"
            / "2026-08-23-pareto-test-suite-refactor-design.md",
            ROOT / "docs" / "adr",
            ROOT / ".agents" / "skills",
        )
    )


def test_project_skill_ui_configuration_is_valid() -> None:
    _assert_project_skill_metadata(ROOT / ".agents" / "skills")


def test_project_skill_ui_configuration_rejects_incomplete_interface(tmp_path: Path) -> None:
    skill_dir = tmp_path / "example-skill"
    (skill_dir / "agents").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: example-skill\ndescription: Example project skill\n---\n",
        encoding="utf-8",
    )
    (skill_dir / "agents" / "openai.yaml").write_text(
        "interface:\n  display_name: Example\n  short_description: Example\n",
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="interface"):
        _assert_project_skill_metadata(tmp_path)


def test_ci_reuses_visual_image_with_read_only_package_access() -> None:
    ci = _load_workflow("ci.yml")
    selector = ci["jobs"]["select-test-suites"]
    quality = ci["jobs"]["quality"]
    visual_job = ci["jobs"]["visual"]

    assert ci[True]["push"]["branches"] == ["main"]
    assert "pull_request" in ci[True]
    assert selector["name"] == "Select test suites"
    assert quality["name"] == "Quality checks"
    assert ci["jobs"]["operational"]["name"] == "Operational tests"
    assert ci["jobs"]["migration"]["name"] == "Migration tests"
    assert visual_job["name"] == "Visual tests"
    assert quality["needs"] == ["select-test-suites"]
    assert visual_job["needs"] == ["select-test-suites"]
    assert quality["permissions"] == {"contents": "read", "packages": "read"}

    login = _workflow_step(ci, "visual", "Log in to GHCR for visual tests")
    visual = _workflow_step(ci, "visual", "Run containerized visual regression tests")
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


def test_optional_ci_jobs_print_untrusted_selector_reasons_as_environment_data() -> None:
    ci = _load_workflow("ci.yml")

    for job_name, suite in (
        ("operational", "operational"),
        ("migration", "migrations"),
        ("visual", "visual"),
    ):
        reason = _workflow_step(ci, job_name, "Selector reason")

        assert reason["env"] == {
            "SELECTOR_REASON": f"${{{{ needs.select-test-suites.outputs.{suite}_reason }}}}"
        }
        assert reason["run"] == "printf '%s\\n' \"$SELECTOR_REASON\""
        assert f"needs.select-test-suites.outputs.{suite}_reason" not in reason["run"]


def test_selected_operational_ci_has_the_core_django_environment() -> None:
    ci = _load_workflow("ci.yml")
    operational = ci["jobs"]["operational"]

    assert operational["env"] == ci["jobs"]["quality"]["env"]
    assert "services" not in operational
    assert _workflow_step(ci, "operational", "Test operational layer")["if"] == (
        "needs.select-test-suites.outputs.operational == 'true'"
    )


def test_unselected_migration_ci_has_no_service_dependency() -> None:
    ci = _load_workflow("ci.yml")
    migration = ci["jobs"]["migration"]
    startup = _workflow_step(ci, "migration", "Start PostgreSQL")

    assert "services" not in migration
    assert startup["if"] == "needs.select-test-suites.outputs.migrations == 'true'"
    assert "for attempt in $(seq 1 15); do" in startup["run"]
    assert "docker ps --filter name=migration-postgres" in startup["run"]
    assert "docker logs --tail 100 migration-postgres || true" in startup["run"]
    assert startup["run"].rstrip().endswith("exit 1")


def test_ci_checks_pull_request_migration_identity_with_full_git_history() -> None:
    ci = _load_workflow("ci.yml")
    checkout = _workflow_step(ci, "migration", "Check out repository")
    immutability = _workflow_step(ci, "migration", "Check migration immutability")
    migration_drift = _workflow_step(ci, "quality", "Check migration drift")

    assert checkout["with"] == {"fetch-depth": 0}
    assert immutability["if"] == (
        "needs.select-test-suites.outputs.migrations == 'true' && "
        "github.event_name == 'pull_request'"
    )
    assert immutability["run"] == (
        "python scripts/check_migration_immutability.py "
        "--base ${{ github.event.pull_request.base.sha }} "
        "--head ${{ github.event.pull_request.head.sha }}"
    )
    assert migration_drift["run"] == "python src/backend/manage.py makemigrations --check --dry-run"


def test_root_quality_contract_includes_processing_and_standalone_worker() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]
    pytest_config = pyproject["pytest"]["ini_options"]
    ci = _load_workflow("ci.yml")
    development_requirements = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")

    assert pyproject["mypy"]["files"] == ["src/backend", "src/worker/photo_worker"]
    assert pytest_config["pythonpath"] == [".", "src/backend", "src/worker"]
    assert pytest_config["testpaths"] == ["src/backend", "src/worker/tests", "tests"]
    assert "pytest-xdist>=3.8,<4" in development_requirements.splitlines()
    assert pyproject["coverage"]["run"]["source"] == [
        "src/backend/config",
        "src/backend/commerce",
        "src/backend/feature_flags",
        "src/backend/ingestion",
        "src/backend/picflow",
        "src/backend/processing",
        "src/backend/selfie_search",
        "src/worker/photo_worker",
    ]
    assert _workflow_step(ci, "quality", "Static analysis")["run"] == (
        "make static RUFF=ruff MYPY=mypy"
    )
    assert _workflow_step(ci, "quality", "Test core with coverage")["run"] == (
        'pytest -n 4 --dist loadscope -m "not operational and not migration and '
        'not clone_deployed_slow" --cov --cov-report=term-missing'
    )
    python_setup = _workflow_step(ci, "quality", "Set up Python")
    assert "src/worker/requirements.txt" in python_setup["with"]["cache-dependency-path"]
    assert _workflow_step(ci, "quality", "Install dependencies")["run"] == (
        "pip install -r requirements-dev.txt -r src/worker/requirements.txt"
    )
    assert ci["jobs"]["quality"]["env"]["TEST_DB_NAME"] == (
        "findme_test_${{ github.run_id }}_${{ github.run_attempt }}"
    )


def test_python_pre_commit_runs_full_mypy_without_a_filename_filter() -> None:
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hooks = [hook for repository in config["repos"] for hook in repository["hooks"]]
    matching_hooks = [hook for hook in hooks if hook["id"] == "mypy"]

    assert matching_hooks == [
        {
            "id": "mypy",
            "name": "mypy",
            "entry": ".venv/bin/mypy",
            "language": "system",
            "pass_filenames": False,
            "types": ["python"],
        }
    ]


def test_local_node_version_matches_ci_and_visual_container() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    node_setup = _workflow_step(_load_workflow("ci.yml"), "visual", "Set up Node.js")
    dockerfile = (ROOT / "Dockerfile.visual-tests").read_text(encoding="utf-8")

    assert (ROOT / ".nvmrc").read_text(encoding="utf-8").strip() == "22"
    assert package["engines"]["node"] == ">=22 <23"
    assert node_setup["with"]["node-version"] == "22"
    assert "FROM node:22-bookworm-slim@sha256:" in dockerfile


def test_clone_deployed_suite_has_default_and_exhaustive_selection_contract() -> None:
    def make_dry_run(target: str, tests: str = "", workers: int = 4) -> list[str]:
        result = subprocess.run(
            ["make", "-n", f"PYTEST_XDIST_WORKERS={workers}", f"TESTS={tests}", target],
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

    core_pytest = (
        "sh scripts/run-in-test-env.sh .venv/bin/pytest "
        '-n 4 --dist loadscope -m "not operational and not migration and not clone_deployed_slow"'
    )
    assert make_dry_run("test") == [core_pytest]
    assert make_dry_run("check").count(f"{core_pytest} --cov --cov-report=term-missing") == 1
    assert make_dry_run("test-operational") == [
        "sh scripts/run-in-test-env.sh .venv/bin/pytest "
        '-n 4 --dist loadscope -m "operational and not clone_deployed_slow"'
    ]
    assert make_dry_run("test-migrations") == [
        "sh scripts/run-in-test-env.sh .venv/bin/pytest -n 4 --dist loadscope -m migration"
    ]
    assert make_dry_run("test-all") == [
        "sh scripts/run-in-test-env.sh .venv/bin/pytest -n 4 --dist loadscope"
    ]

    clone_pytest = (
        "sh scripts/run-in-test-env.sh .venv/bin/pytest "
        "tests/deployment/test_clone_deployed_database.py"
    )
    assert make_dry_run("test-clone-deployed") == [clone_pytest]
    assert "clone_deployed_slow" not in make_dry_run("test-clone-deployed")[0]
