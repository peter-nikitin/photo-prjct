import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string

from commerce.worker import (
    CommerceWorker,
    acquire_commerce_worker_lock,
    release_commerce_worker_lock,
)


class Command(BaseCommand):
    help = "Run the bounded Commerce email and payment-reconciliation poller."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-seconds", type=int, default=5)

    def handle(self, *args, **options) -> None:
        poll_seconds = options["poll_seconds"]
        if not isinstance(poll_seconds, int) or poll_seconds <= 0:
            raise CommandError("--poll-seconds must be positive.")
        worker = _configured_worker()
        if not acquire_commerce_worker_lock():
            raise CommandError("A Commerce worker is already running.")
        try:
            while True:
                result = worker.run_once()
                self.stdout.write(
                    "commerce worker pass: "
                    f"email_deliveries={result.email_deliveries} "
                    f"payment_reconciliations={result.payment_reconciliations} "
                    f"attention_reminders={result.attention_reminders}"
                )
                if options["once"]:
                    return
                time.sleep(poll_seconds)
        finally:
            release_commerce_worker_lock()


def _configured_worker() -> CommerceWorker:
    factory_path = getattr(settings, "COMMERCE_WORKER_FACTORY", "")
    if not isinstance(factory_path, str) or not factory_path:
        raise CommandError(
            "COMMERCE_WORKER_FACTORY is required before starting the Commerce worker."
        )
    try:
        factory = import_string(factory_path)
        worker = factory()
    except Exception as error:
        raise CommandError("Configured Commerce worker could not be created.") from error
    if not isinstance(worker, CommerceWorker):
        raise CommandError("COMMERCE_WORKER_FACTORY must return a CommerceWorker.")
    return worker
