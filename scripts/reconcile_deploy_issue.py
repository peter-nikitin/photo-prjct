#!/usr/bin/env python3
"""Reconcile the bounded deployment notification issue."""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

API_ROOT = "https://api.github.com"
REQUEST_TIMEOUT_SECONDS = 10
USER_AGENT = "findme-photo-deploy-reconciler/1.0"
TITLES = {
    "deploy": "[deployment] main is not deployed",
    "validation": "[deployment validation] notification drill",
}
PHASES = frozenset(
    {
        "build",
        "validate",
        "snapshot",
        "candidate-pull",
        "private-media-preflight",
        "migration-preflight",
        "observability-preflight",
        "observability-reconcile",
        "certificate",
        "compose-reconcile",
        "local-health",
        "worker-health",
        "public-health",
        "observability-verify",
        "commit",
        "unknown",
    }
)
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_TOKEN_ENV = re.compile(r"[A-Z_][A-Z0-9_]*\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")


class ReconciliationError(Exception):
    pass


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--token-env", required=True)
    parser.add_argument("--mode", required=True, choices=sorted(TITLES))
    parser.add_argument("--conclusion", required=True, choices=("success", "failure"))
    parser.add_argument("--sha", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--phase", required=True)
    return parser.parse_args(argv)


def _validate(arguments: argparse.Namespace) -> None:
    if not _REPOSITORY.fullmatch(arguments.repository):
        raise ReconciliationError
    if not _TOKEN_ENV.fullmatch(arguments.token_env):
        raise ReconciliationError
    if not _SHA.fullmatch(arguments.sha):
        raise ReconciliationError
    if arguments.phase not in PHASES:
        raise ReconciliationError

    parsed_url = urllib.parse.urlsplit(arguments.run_url)
    expected_actions_path = re.compile(rf"/{re.escape(arguments.repository)}/actions/runs/[0-9]+\Z")
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != "github.com"
        or not expected_actions_path.fullmatch(parsed_url.path)
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.port is not None
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise ReconciliationError


def _request(token: str, method: str, url: str, payload: dict[str, str] | None) -> Any:
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    try:
        response = urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS)
        try:
            if response.status not in ({200} if method in {"GET", "PATCH"} else {201}):
                raise ReconciliationError
            raw_body = response.read()
        finally:
            response.close()
        return json.loads(raw_body.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, urllib.error.HTTPError) as error:
        raise ReconciliationError from error


def _matching_open_issue(token: str, repository: str, title: str) -> int | None:
    issues = _request(
        token,
        "GET",
        f"{API_ROOT}/repos/{repository}/issues?state=open&per_page=100",
        None,
    )
    if not isinstance(issues, list):
        raise ReconciliationError
    for issue in issues:
        if not isinstance(issue, dict):
            raise ReconciliationError
        issue_title = issue.get("title")
        number = issue.get("number")
        if (
            not isinstance(issue_title, str)
            or not isinstance(number, int)
            or isinstance(number, bool)
            or number <= 0
        ):
            raise ReconciliationError
        if "pull_request" not in issue and issue_title == title:
            return number
    return None


def _current_main_sha(token: str, repository: str) -> str:
    response = _request(
        token,
        "GET",
        f"{API_ROOT}/repos/{repository}/branches/main",
        None,
    )
    commit = response.get("commit") if isinstance(response, dict) else None
    sha = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise ReconciliationError
    return sha


def _body(arguments: argparse.Namespace) -> str:
    return "\n".join(
        (
            f"commit={arguments.sha}",
            f"run_url={arguments.run_url}",
            f"phase={arguments.phase}",
            f"recorded_at={_utc_timestamp()}",
        )
    )


def _response_identifier(response: Any, field: str) -> int:
    identifier = response.get(field) if isinstance(response, dict) else None
    if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier <= 0:
        raise ReconciliationError
    return identifier


def reconcile(arguments: argparse.Namespace, token: str) -> None:
    if arguments.mode == "deploy" and _current_main_sha(token, arguments.repository) != (
        arguments.sha
    ):
        return

    title = TITLES[arguments.mode]
    issue_number = _matching_open_issue(token, arguments.repository, title)
    body = _body(arguments)
    issue_url = f"{API_ROOT}/repos/{arguments.repository}/issues"

    if arguments.conclusion == "failure":
        if issue_number is None:
            _response_identifier(
                _request(token, "POST", issue_url, {"title": title, "body": body}), "number"
            )
        else:
            _response_identifier(
                _request(token, "POST", f"{issue_url}/{issue_number}/comments", {"body": body}),
                "id",
            )
        return

    if issue_number is not None:
        _response_identifier(
            _request(token, "POST", f"{issue_url}/{issue_number}/comments", {"body": body}), "id"
        )
        _response_identifier(
            _request(token, "PATCH", f"{issue_url}/{issue_number}", {"state": "closed"}),
            "number",
        )


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _arguments(argv)
        _validate(arguments)
        token = os.environ.get(arguments.token_env)
        if not token:
            raise ReconciliationError
        reconcile(arguments, token)
    except (ReconciliationError, ValueError):
        print("error: deployment issue reconciliation failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
