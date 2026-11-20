"""Private stdin/stdout peer of the deployed appearance backfill launcher."""

import json
import sys
from typing import Any

from django.core.management.base import BaseCommand

from processing.services.appearance_backfill import AppearanceBackfill, BackfillError, require_off

MAX_LINE_BYTES = 384 * 1024


class Command(BaseCommand):
    help = "Обслуживать одну ограниченную операторскую партию расчёта внешности по JSONL."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser) -> None:
        for name in ("event-id", "limit", "processes", "seed"):
            parser.add_argument(f"--{name}", required=True, type=int)

    def emit(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, default=str, separators=(",", ":"))
        if len(line.encode()) + 1 > MAX_LINE_BYTES:
            raise BackfillError("oversized_reply")
        self.stdout.write(line)
        self.stdout.flush()

    def handle(self, *args, **options) -> None:
        try:
            session = AppearanceBackfill(
                **{name: options[name] for name in ("event_id", "limit", "processes", "seed")}
            )
            require_off()
        except BackfillError as error:
            self.emit({"error": str(error)})
            return

        self.emit({"ready": True})
        while raw := sys.stdin.buffer.readline(MAX_LINE_BYTES + 1):
            if len(raw) > MAX_LINE_BYTES or not raw.endswith(b"\n"):
                self.emit({"error": "invalid_command"})
                return
            try:
                command = json.loads(raw)
                if command == {"op": "enroll"}:
                    reply = session.enroll()
                elif command == {"op": "claim"}:
                    reply = session.claim()
                elif (
                    isinstance(command, dict)
                    and command.keys() == {"op", "release"}
                    and command["op"] == "finish"
                    and isinstance(command["release"], bool)
                ):
                    self.emit(session.finish(release=command["release"]))
                    return
                elif (
                    isinstance(command, dict)
                    and command.keys() == {"op", "attempt_id"}
                    and command["op"] == "result"
                    and isinstance(command["attempt_id"], str)
                ):
                    reply = session.result(command["attempt_id"])
                else:
                    raise BackfillError("invalid_command")
                self.emit(reply)
            except BackfillError as error:
                self.emit({"error": str(error)})
            except Exception:
                # The channel may contain a signed grant. Report only fixed error codes.
                self.emit({"error": "backend_operation_failed"})
