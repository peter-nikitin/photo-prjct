#!/usr/bin/env python3
"""Prepare the private PostgreSQL exporter role from an already provisioned secret."""

import argparse
import stat
import subprocess
import sys
from pathlib import Path


def _password(secret_path: Path) -> str:
    if secret_path.is_symlink():
        raise ValueError("monitoring credential must be a regular file")
    metadata = secret_path.stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o640
        or metadata.st_gid != 65534
    ):
        raise ValueError("monitoring credential needs owner/group read permissions only")
    if metadata.st_size > 256:
        raise ValueError("monitoring credential is too large")
    value = secret_path.read_text(encoding="ascii").removesuffix("\n")
    if not 32 <= len(value) <= 255 or not value.isascii() or not value.isprintable():
        raise ValueError("monitoring credential must be a single printable ASCII line")
    return value


def prepare_role(deploy_root: Path) -> None:
    password = _password(deploy_root / "secrets/postgres-exporter-password")
    quoted_password = password.replace("'", "''")
    sql = f"""
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'findme_monitor') THEN
        CREATE ROLE findme_monitor LOGIN;
    END IF;
END $$;
ALTER ROLE findme_monitor WITH LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION CONNECTION LIMIT 2 PASSWORD '{quoted_password}';
GRANT pg_monitor TO findme_monitor;
GRANT CONNECT ON DATABASE app TO findme_monitor;
"""
    command = [
        "docker",
        "compose",
        "--env-file",
        str(deploy_root / ".env"),
        "-f",
        str(deploy_root / "docker-compose.deployment.yml"),
        "exec",
        "-T",
        "db",
        "sh",
        "-c",
        'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1',
    ]
    result = subprocess.run(command, input=sql, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("PostgreSQL monitoring role preparation failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy-root", type=Path, default=Path("/opt/photo-prjct"))
    args = parser.parse_args()
    try:
        prepare_role(args.deploy_root)
    except (OSError, UnicodeError, ValueError, RuntimeError):
        print("PostgreSQL monitoring role preparation failed", file=sys.stderr)
        return 1
    print("PostgreSQL monitoring role prepared")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
