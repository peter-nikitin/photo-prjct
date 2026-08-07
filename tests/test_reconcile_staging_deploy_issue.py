import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts/reconcile_staging_deploy_issue.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("reconcile_staging_deploy_issue", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def close(self) -> None:
        pass


class _Api:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.requests = []

    def __call__(self, request, *, timeout: float):
        self.requests.append((request, timeout))
        return self.responses.pop(0)


def _arguments(*, conclusion: str) -> list[str]:
    return [
        "--repository",
        "findme/photo",
        "--token-env",
        "GITHUB_TOKEN",
        "--mode",
        "production",
        "--conclusion",
        conclusion,
        "--sha",
        "a" * 40,
        "--run-url",
        "https://github.com/findme/photo/actions/runs/42",
        "--phase",
        "compose-reconcile",
    ]


def _request_body(request) -> dict[str, str]:
    return json.loads(request.data.decode("utf-8"))


def test_first_failure_creates_one_sanitized_production_issue(monkeypatch) -> None:
    module = _load_module()
    api = _Api([_Response(200, []), _Response(201, {"number": 7})])
    monkeypatch.setenv("GITHUB_TOKEN", "private-token")
    monkeypatch.setattr(module, "_utc_timestamp", lambda: "2026-08-07T00:00:00Z")

    with patch.object(module.urllib.request, "urlopen", api):
        assert module.main(_arguments(conclusion="failure")) == 0

    assert len(api.requests) == 2
    list_request, create_request = (request for request, _timeout in api.requests)
    assert list_request.full_url == (
        "https://api.github.com/repos/findme/photo/issues?state=open&per_page=100"
    )
    assert create_request.full_url == "https://api.github.com/repos/findme/photo/issues"
    assert _request_body(create_request) == {
        "title": "[staging deployment] main is not deployed",
        "body": (
            f"commit={'a' * 40}\n"
            "run_url=https://github.com/findme/photo/actions/runs/42\n"
            "phase=compose-reconcile\n"
            "recorded_at=2026-08-07T00:00:00Z"
        ),
    }
    assert all(timeout == 10 for _request, timeout in api.requests)
    assert all(
        request.get_header("Authorization") == "Bearer private-token" for request, _ in api.requests
    )


def test_repeated_failure_comments_on_the_matching_open_issue(monkeypatch) -> None:
    module = _load_module()
    api = _Api(
        [
            _Response(200, [{"number": 7, "title": "[staging deployment] main is not deployed"}]),
            _Response(201, {"id": 9}),
        ]
    )
    monkeypatch.setenv("GITHUB_TOKEN", "private-token")

    with patch.object(module.urllib.request, "urlopen", api):
        assert module.main(_arguments(conclusion="failure")) == 0

    assert [request.full_url for request, _ in api.requests] == [
        "https://api.github.com/repos/findme/photo/issues?state=open&per_page=100",
        "https://api.github.com/repos/findme/photo/issues/7/comments",
    ]
    assert _request_body(api.requests[1][0])["body"].startswith(f"commit={'a' * 40}\n")


def test_success_comments_then_closes_the_matching_open_issue(monkeypatch) -> None:
    module = _load_module()
    api = _Api(
        [
            _Response(200, [{"number": 7, "title": "[staging deployment] main is not deployed"}]),
            _Response(201, {"id": 9}),
            _Response(200, {"number": 7, "state": "closed"}),
        ]
    )
    monkeypatch.setenv("GITHUB_TOKEN", "private-token")

    with patch.object(module.urllib.request, "urlopen", api):
        assert module.main(_arguments(conclusion="success")) == 0

    assert [request.get_method() for request, _ in api.requests] == ["GET", "POST", "PATCH"]
    assert api.requests[2][0].full_url == "https://api.github.com/repos/findme/photo/issues/7"
    assert _request_body(api.requests[2][0]) == {"state": "closed"}


def test_success_with_no_open_matching_issue_does_nothing(monkeypatch) -> None:
    module = _load_module()
    api = _Api([_Response(200, [])])
    monkeypatch.setenv("GITHUB_TOKEN", "private-token")

    with patch.object(module.urllib.request, "urlopen", api):
        assert module.main(_arguments(conclusion="success")) == 0

    assert len(api.requests) == 1


def test_matching_is_exact_and_bounded_to_first_one_hundred_open_issues(monkeypatch) -> None:
    module = _load_module()
    issues = [
        {"number": number, "title": "[staging deployment] main is not deployed now"}
        for number in range(1, 101)
    ]
    api = _Api([_Response(200, issues), _Response(201, {"number": 101})])
    monkeypatch.setenv("GITHUB_TOKEN", "private-token")

    with patch.object(module.urllib.request, "urlopen", api):
        assert module.main(_arguments(conclusion="failure")) == 0

    assert len(api.requests) == 2
    assert "per_page=100" in api.requests[0][0].full_url
    assert api.requests[1][0].full_url == "https://api.github.com/repos/findme/photo/issues"


def test_http_errors_fail_without_echoing_token_or_response_body(monkeypatch, capsys) -> None:
    module = _load_module()
    monkeypatch.setenv("GITHUB_TOKEN", "private-token")
    error = HTTPError(
        "https://api.github.com/repos/findme/photo/issues",
        429,
        "rate limited",
        {},
        io.BytesIO(b'{"message":"private response body"}'),
    )

    with patch.object(module.urllib.request, "urlopen", side_effect=error):
        assert module.main(_arguments(conclusion="failure")) == 1

    captured = capsys.readouterr()
    assert "private-token" not in captured.err
    assert "private response body" not in captured.err


def test_malformed_api_responses_fail_without_echoing_the_response(monkeypatch, capsys) -> None:
    module = _load_module()
    api = _Api([_Response(200, []), _Response(201, {"private": "response body"})])
    monkeypatch.setenv("GITHUB_TOKEN", "private-token")

    with patch.object(module.urllib.request, "urlopen", api):
        assert module.main(_arguments(conclusion="failure")) == 1

    captured = capsys.readouterr()
    assert "private-token" not in captured.err
    assert "response body" not in captured.err


def test_invalid_arguments_make_no_network_request(monkeypatch, capsys) -> None:
    module = _load_module()
    monkeypatch.setenv("GITHUB_TOKEN", "private-token")

    with patch.object(module.urllib.request, "urlopen") as urlopen:
        assert module.main(_arguments(conclusion="failure")[:-2] + ["--phase", "not-a-phase"]) == 1

    assert not urlopen.called
    assert "private-token" not in capsys.readouterr().err


def test_non_actions_github_urls_are_rejected_before_any_network_request(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setenv("GITHUB_TOKEN", "private-token")
    invalid_urls = (
        "https://github.com/findme/photo/issues/7",
        "https://github.com/other/photo/actions/runs/42",
        "https://github.com/findme/photo/actions/runs/not-a-number",
    )

    for invalid_url in invalid_urls:
        arguments = _arguments(conclusion="failure")
        arguments[arguments.index("--run-url") + 1] = invalid_url
        with patch.object(
            module.urllib.request, "urlopen", side_effect=AssertionError("unexpected network")
        ):
            assert module.main(arguments) == 1
