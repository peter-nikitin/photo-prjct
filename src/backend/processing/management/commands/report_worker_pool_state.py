import json

from django.core.management.base import BaseCommand, CommandError

from processing.services.worker_pool_state import (
    BULK_IDENTITIES,
    SELFIE_IDENTITIES,
    build_worker_pool_state,
    identity_setting,
    parse_pool_identities,
)


class Command(BaseCommand):
    help = (
        "Report read-only, privacy-safe aggregates for the disjoint bulk and selfie worker pools."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--json", action="store_true", required=True)
        parser.add_argument("--bulk-identities", default=identity_setting(BULK_IDENTITIES))
        parser.add_argument("--selfie-identities", default=identity_setting(SELFIE_IDENTITIES))

    def handle(self, *args, **options) -> None:
        try:
            bulk = parse_pool_identities(options["bulk_identities"], BULK_IDENTITIES)
            selfie = parse_pool_identities(options["selfie_identities"], SELFIE_IDENTITIES)
        except ValueError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            json.dumps(
                build_worker_pool_state(bulk_identities=bulk, selfie_identities=selfie),
                separators=(",", ":"),
                sort_keys=True,
            )
        )
