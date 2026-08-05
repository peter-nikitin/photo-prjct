import os
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg import sql

from scripts.cleanup_stale_test_databases import _process_is_alive, stale_database_names

ROOT = Path(__file__).resolve().parents[1]


def test_cleanup_reclaims_current_and_dead_test_databases_without_touching_live_runs() -> None:
    alive_pids = {202}

    assert stale_database_names(
        [
            "findme_test_101",
            "findme_test_202",
            "findme_test_303",
            "findme_test_invalid",
            "unrelated_database",
        ],
        current_name="findme_test_101",
        process_is_alive=lambda pid: pid in alive_pids,
    ) == ["findme_test_101", "findme_test_303"]


def test_cleanup_treats_identifiers_larger_than_a_system_pid_as_not_alive() -> None:
    assert _process_is_alive(10**100) is False


def test_cleanup_reclaims_database_left_by_an_interrupted_run() -> None:
    stale_name = f"findme_test_{os.getpid()}_interrupted"
    connection_arguments = {
        "dbname": "postgres",
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
        "host": os.environ["DB_HOST"],
        "port": os.environ["DB_PORT"],
        "autocommit": True,
    }

    with psycopg.connect(**connection_arguments) as connection:
        connection.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(stale_name))
        )
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(stale_name)))

    try:
        subprocess.run(
            [sys.executable, "scripts/cleanup_stale_test_databases.py"],
            cwd=ROOT,
            env={**os.environ, "TEST_DB_NAME": stale_name},
            check=True,
        )
        with psycopg.connect(**connection_arguments) as connection:
            exists = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname = %s)",
                (stale_name,),
            ).fetchone()
        assert exists == (False,)
    finally:
        with psycopg.connect(**connection_arguments) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(stale_name)
                )
            )


def test_cleanup_never_forces_active_test_database_connections_closed() -> None:
    active_name = f"findme_test_{os.getpid()}_active"
    connection_arguments = {
        "dbname": "postgres",
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
        "host": os.environ["DB_HOST"],
        "port": os.environ["DB_PORT"],
        "autocommit": True,
    }

    with psycopg.connect(**connection_arguments) as maintenance:
        maintenance.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(active_name))
        )
        maintenance.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(active_name)))

    active_arguments = {**connection_arguments, "dbname": active_name}
    active_connection = psycopg.connect(**active_arguments)
    try:
        subprocess.run(
            [sys.executable, "scripts/cleanup_stale_test_databases.py"],
            cwd=ROOT,
            env={**os.environ, "TEST_DB_NAME": active_name},
            check=True,
        )
        with psycopg.connect(**connection_arguments) as maintenance:
            exists = maintenance.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname = %s)",
                (active_name,),
            ).fetchone()
        assert exists == (True,)
    finally:
        active_connection.close()
        with psycopg.connect(**connection_arguments) as maintenance:
            maintenance.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(active_name)
                )
            )
