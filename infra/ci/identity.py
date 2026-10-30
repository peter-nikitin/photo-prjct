"""Use the repository's validated GitHub OIDC exchange for infrastructure jobs."""

import importlib.util
import os
import sys
from pathlib import Path


def publish_token(token: str, environment_file: str) -> None:
    # GitHub runner command registers the token as a secret before future step
    # environment headers, failures or command output could include it.
    print(f"::add-mask::{token}", flush=True)
    with open(environment_file, "a", encoding="utf-8") as env_file:
        env_file.write(f"YC_TOKEN={token}\n")


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    source = root / "scripts/run-with-environment-secrets.py"
    spec = importlib.util.spec_from_file_location("findme_identity", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("OIDC identity helper unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    repository = os.environ["GITHUB_REPOSITORY"]
    if repository != "peter-nikitin/photo-prjct":
        raise RuntimeError("unexpected repository")
    config = {
        "github_oidc": {
            "issuer": "https://token.actions.githubusercontent.com",
            "audience": "https://github.com/peter-nikitin",
            "subject": f"repo:{repository}:environment:infrastructure",
            "repository": repository,
            "ref": os.environ["GITHUB_REF"],
            "service_account_id": os.environ["YC_INFRA_SERVICE_ACCOUNT_ID"],
            "allowed_workflows": [os.environ["GITHUB_WORKFLOW_REF"]],
        }
    }
    token = module._github_identity(config, os.environ)
    publish_token(token, os.environ["GITHUB_ENV"])
    print("Infrastructure OIDC exchange succeeded")


if __name__ == "__main__":
    main()
