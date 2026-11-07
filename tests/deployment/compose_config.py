"""Render Compose in a disposable directory with synthetic environment values."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def render_compose(
    directory: Path,
    *filenames: str,
    profiles: tuple[str, ...] = (),
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    values = {
        "APP_IMAGE": "test-app-image",
        "IMPORT_WORKER_IMAGE": "test-import-image",
        "SECRET_KEY": "test-secret-key",
        "DEBUG": "False",
        "ALLOWED_HOSTS": "example.invalid",
        "DB_NAME": "app",
        "DB_USER": "app",
        "DB_PASSWORD": "test-db-password",
        "PUBLIC_DOMAIN": "example.invalid",
        "GALLERY_CDN_ORIGIN": "https://images.example.invalid",
        "GUNICORN_WORKERS": "1",
        "GUNICORN_THREADS": "1",
        "GUNICORN_TIMEOUT": "30",
        "GUNICORN_MAX_REQUESTS": "0",
        "GUNICORN_MAX_REQUESTS_JITTER": "0",
        **(environment or {}),
    }
    directory.mkdir(parents=True, exist_ok=True)
    env_file = directory / ".env"
    env_file.write_text(
        "".join(
            f"{name}={json.dumps(value, ensure_ascii=False)}\n" for name, value in values.items()
        )
    )
    command = ["docker", "compose", "--env-file", str(env_file)]
    for filename in filenames:
        copied = directory / filename
        copied.write_bytes((ROOT / filename).read_bytes())
        command.extend(["-f", str(copied)])
    for profile in profiles:
        command.extend(["--profile", profile])
    command.extend(["config", "--format", "json"])
    result = subprocess.run(
        command,
        cwd=directory,
        env={**os.environ, **values},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)
