import time

from config.queue_telemetry_protocol import PHOTO_IMPORT_QUEUES
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction
from processing.models import PhotoImportCounter
from processing.photo_import_telemetry import (
    read_photo_import_backlog_page,
    read_photo_import_backlog_upper_bound,
)

PAGE_PAUSE_SECONDS = 0.1


class Command(BaseCommand):
    help = "Initialize or replace approximate photo import backlog counters manually."
    requires_system_checks: list[str] = []

    def handle(self, *args, **options) -> None:
        totals = dict.fromkeys(PHOTO_IMPORT_QUEUES, (0, 0))
        try:
            upper_bound = read_photo_import_backlog_upper_bound()
            after = None
            while upper_bound is not None and after != upper_bound:
                page = read_photo_import_backlog_page(after=after, upper_bound=upper_bound)
                if page is None:
                    break
                after, counts = page
                for queue, (pending, failed) in counts.items():
                    total_pending, total_failed = totals[queue]
                    totals[queue] = (total_pending + pending, total_failed + failed)
                if after != upper_bound:
                    time.sleep(PAGE_PAUSE_SECONDS)

            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = 100")
                cursor.execute("SET LOCAL lock_timeout = 10")
                for queue, (pending, failed) in sorted(totals.items()):
                    updated = PhotoImportCounter.objects.filter(pk=queue).update(
                        pending=pending, failed=failed, initialized=True
                    )
                    if updated != 1:
                        raise CommandError(f"Could not rebuild: counter row {queue} is missing")
        except DatabaseError as error:
            raise CommandError(f"Could not rebuild photo import counters: {error}") from error

        for queue, (pending, failed) in sorted(totals.items()):
            self.stdout.write(f"{queue}: pending={pending}, failed={failed}")
