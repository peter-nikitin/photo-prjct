import json
import sys

from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from processing.services.worker_pool_observation import observe_cloud, validate_config


class Command(BaseCommand):
    help = (
        "Read complete cloud membership; --record explicitly submits trusted coordinator evidence."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--config", required=True, help="nonsecret JSON path, or - for stdin")
        parser.add_argument("--record", action="store_true")

    def handle(self, *args, **options) -> None:
        try:
            if options["config"] == "-":
                raw = sys.stdin.read(16_385)
            else:
                with open(options["config"]) as stream:
                    raw = stream.read(16_385)
            if len(raw) > 16_384:
                raise ValueError("configuration too large")
            config = json.loads(raw)
            validate_config(config)
            if not options["record"]:
                result = {"recorded": False, "groups": config["groups"]}
            else:
                result = {
                    "recorded": all([observe_cloud(name, config) for name in config["groups"]])
                }
                if not result["recorded"]:
                    raise ValueError("observation rejected")
        except (OSError, ValueError, KeyError, TypeError, DatabaseError, ObjectDoesNotExist):
            raise CommandError("worker cloud observation failed") from None
        self.stdout.write(json.dumps(result, separators=(",", ":"), sort_keys=True))
