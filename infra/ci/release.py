"""Resolve an explicitly approved PR plan using GitHub's run and artifact IDs."""

import json
import os
import re
import sys
from datetime import datetime
from urllib.request import Request, urlopen

REPOSITORY = "peter-nikitin/photo-prjct"
WORKFLOW = ".github/workflows/infrastructure.yml"
APPROVAL = re.compile(r"^/infra approve ([1-9][0-9]*)$")


def github_get(path: str) -> dict | list:
    token = os.environ.get("GH_TOKEN") or os.environ["GITHUB_TOKEN"]
    request = Request(
        f"https://api.github.com/repos/{REPOSITORY}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def pages(path: str) -> list:
    result = []
    for page in range(1, 20):
        separator = "&" if "?" in path else "?"
        batch = github_get(f"{path}{separator}per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ValueError("GitHub list response expected")
        result.extend(batch)
        if len(batch) < 100:
            return result
    raise ValueError("too many GitHub API pages; inspect approval manually")


def write_permission(login: str) -> bool:
    result = github_get(f"/collaborators/{login}/permission")
    return isinstance(result, dict) and result.get("permission") in {
        "admin",
        "write",
        "maintain",
    }


def _date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def choose_plan(
    pr: dict, comments: list[dict], run: dict, artifacts: list[dict], approver_can_write: bool
) -> dict:
    if pr.get("base", {}).get("ref") != "main":
        raise ValueError("PR target must be main")
    if pr.get("head", {}).get("repo", {}).get("full_name") != REPOSITORY:
        raise ValueError("fork plans cannot be approved")
    head_sha = pr["head"]["sha"]
    if run.get("event") != "pull_request" or run.get("conclusion") != "success":
        raise ValueError("PR plan run is not successful")
    if not str(run.get("path", "")).endswith(WORKFLOW):
        raise ValueError("run belongs to another workflow")
    # GitHub run.head_sha is immutable. run.pull_requests can become empty after
    # merge, and its nested PR head can move after another push.
    if run.get("head_sha") != head_sha or run.get("repository", {}).get("full_name") != REPOSITORY:
        raise ValueError("plan run does not belong to the current PR head")
    approved = [
        comment
        for comment in comments
        if (match := APPROVAL.fullmatch(comment.get("body", "").strip()))
        and int(match.group(1)) == run["id"]
        and _date(comment["created_at"]) >= _date(run["updated_at"])
    ]
    if not approved or not approver_can_write:
        raise ValueError("no current write-authorized approval for this plan run")
    matching = [
        artifact
        for artifact in artifacts
        if artifact.get("name") == f"terraform-plan-{pr['number']}"
        and not artifact.get("expired", True)
        and _date(artifact["created_at"]) <= max(_date(c["created_at"]) for c in approved)
    ]
    if len(matching) != 1:
        raise ValueError("unique unexpired plan artifact unavailable")
    return {
        "pr_number": pr["number"],
        "run_id": run["id"],
        "artifact_id": matching[0]["id"],
        "head_sha": head_sha,
    }


def approved_plan(pr: dict) -> dict:
    comments = pages(f"/issues/{pr['number']}/comments")
    candidates = []
    for comment in comments:
        match = APPROVAL.fullmatch(comment.get("body", "").strip())
        if match and write_permission(comment["user"]["login"]):
            candidates.append((comment, int(match.group(1))))
    if not candidates:
        raise ValueError("no write-authorized /infra approve RUN_ID comment")
    # A later approval supersedes an earlier run; failure does not fall back.
    comment, run_id = max(candidates, key=lambda item: item[0]["created_at"])
    run = github_get(f"/actions/runs/{run_id}")
    artifacts = github_get(f"/actions/runs/{run_id}/artifacts")
    if not isinstance(run, dict) or not isinstance(artifacts, dict):
        raise ValueError("invalid GitHub run metadata")
    return choose_plan(pr, [comment], run, artifacts.get("artifacts", []), True)


def merged_pr(merge_sha: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", merge_sha):
        raise ValueError("invalid merge SHA")
    candidates = github_get(f"/commits/{merge_sha}/pulls")
    if not isinstance(candidates, list):
        raise ValueError("invalid associated PR response")
    matching = [
        pr
        for pr in candidates
        if pr.get("merged_at")
        and pr.get("merge_commit_sha") == merge_sha
        and pr.get("base", {}).get("ref") == "main"
    ]
    if len(matching) != 1:
        raise ValueError("merge must identify exactly one main PR")
    return matching[0]


def main() -> None:
    mode, value = sys.argv[1:3]
    if mode == "verify-merge":
        pr = merged_pr(value)
    elif mode == "verify-pr":
        if not re.fullmatch(r"[1-9][0-9]*", value):
            raise ValueError("invalid PR number")
        pr = github_get(f"/pulls/{value}")
        if not isinstance(pr, dict):
            raise ValueError("invalid PR metadata")
    elif mode == "authorize-actor":
        if not write_permission(value):
            raise ValueError("PR author lacks write permission")
        return
    else:
        raise ValueError("unknown release mode")
    print(json.dumps(approved_plan(pr), sort_keys=True))


if __name__ == "__main__":
    main()
