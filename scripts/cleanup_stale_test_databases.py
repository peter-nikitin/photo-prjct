import os
from collections.abc import Callable, Iterable

import psycopg
from psycopg import sql
from psycopg.errors import ObjectInUse

PREFIX = "findme_test_"


def stale_database_names(
    database_names: Iterable[str],
    *,
    current_name: str,
    process_is_alive: Callable[[int], bool],
) -> list[str]:
    stale = []
    for name in database_names:
        if name == current_name:
            stale.append(name)
            continue
        if not name.startswith(PREFIX):
            continue
        try:
            pid = int(name.removeprefix(PREFIX))
        except ValueError:
            continue
        if not process_is_alive(pid):
            stale.append(name)
    return stale


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main() -> None:
    current_name = os.environ["TEST_DB_NAME"]
    with psycopg.connect(
        dbname="postgres",
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
        autocommit=True,
    ) as connection:
        rows = connection.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE %s",
            (f"{PREFIX}%",),
        ).fetchall()
        for name in stale_database_names(
            (row[0] for row in rows),
            current_name=current_name,
            process_is_alive=_process_is_alive,
        ):
            try:
                connection.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name))
                )
            except ObjectInUse:
                continue


if __name__ == "__main__":
    main()
