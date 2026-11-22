import argparse
import getpass
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Never

from findme_client.api import ClientError, Json
from findme_client.config import api_for, login, logout, valid_profile
from findme_client.tasks import Task, scan
from findme_client.uploader import prepare, status, worker


class CommandParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        failure = ClientError("invalid_arguments", "Use findme --help to check the arguments.")
        self.exit(2, json.dumps(failure.payload()) + "\n")


def parser() -> argparse.ArgumentParser:
    command = CommandParser(prog="findme")
    command.add_argument("--profile", default="default")
    sections = command.add_subparsers(dest="section", required=True)

    auth = sections.add_parser("auth").add_subparsers(dest="operation", required=True)
    sign_in = auth.add_parser("login")
    sign_in.add_argument("server")
    auth.add_parser("status")
    auth.add_parser("logout")

    uploads = sections.add_parser("upload").add_subparsers(dest="operation", required=True)
    inspect = uploads.add_parser("inspect")
    inspect.add_argument("path", type=Path)
    start = uploads.add_parser("start")
    start.add_argument("path", type=Path)
    start.add_argument("--event", required=True, type=int)
    start.add_argument("--folder", type=int)
    for action in ("status", "stop", "resume"):
        uploads.add_parser(action).add_argument("task_id")

    sections.add_parser("mcp")
    return command


def execute(args: argparse.Namespace) -> Json:
    if args.section == "auth":
        if args.operation == "login":
            if not sys.stdin.isatty():
                raise ClientError("terminal_required", "Run login in an interactive terminal.")
            secret = getpass.getpass("Personal token: ").strip()
            return login(args.profile, args.server, secret)
        if args.operation == "logout":
            logout(args.profile)
            return {"profile": args.profile, "authenticated": False}
        with api_for(args.profile) as api:
            return {"profile": args.profile, "server": api.server, **api.request("whoami/")}
    if args.operation == "inspect":
        return scan(args.path).summary()
    if args.operation == "start":
        task = prepare(args.profile, args.event, args.folder, args.path)
        task.launch()
        return task.summary()
    task = Task.load(args.task_id)
    if args.operation == "status":
        return status(task)
    if args.operation == "resume":
        task.launch()
    else:
        task.stop()
    return task.summary()


def main() -> int:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if sys.argv[1:2] == ["_run"]:
        if len(sys.argv) != 4:
            return 2
        try:
            return worker(Task.load(sys.argv[2]), int(sys.argv[3]))
        except (ClientError, OSError, ValueError):
            return 2

    args = parser().parse_args()
    if args.section == "mcp":
        from findme_client.mcp_server import run_mcp

        run_mcp(args.profile)
        return 0
    try:
        valid_profile(args.profile)
        result = execute(args)
    except ClientError as exc:
        print(json.dumps(exc.payload(), ensure_ascii=False))
        return 1
    except (OSError, ValueError, sqlite3.Error):
        failure = ClientError("local_error", "Cannot read or write local FindMe Photo data.")
        print(json.dumps(failure.payload()))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0
