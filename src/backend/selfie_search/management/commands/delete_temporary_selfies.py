from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from selfie_search.models import SelfieSearch
from selfie_search.services.jobs import CleanupPending, confirm_search_cleanup
from selfie_search.storage import TemporarySelfieStorage


class Command(BaseCommand):
    help = "Delete temporary selfies for a bounded batch of completed searches."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=10)

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        if batch_size < 1:
            raise CommandError("--batch-size must be positive")

        pending = SelfieSearch.objects.filter(
            terminal_at__isnull=False, temporary_object_key__gt=""
        )
        ids = list(
            pending.order_by("state_changed_at", "id").values_list("id", flat=True)[:batch_size]
        )
        deleted = 0
        deferred = 0
        if ids:
            storage = TemporarySelfieStorage.for_matching_cleanup()
            for search_id in ids:
                # Rotate failures behind older pending work, including if the process times out.
                if not pending.filter(pk=search_id).update(state_changed_at=timezone.now()):
                    continue
                try:
                    confirm_search_cleanup(search_id=search_id, storage=storage)
                except CleanupPending:
                    deferred += 1
                    continue
                deleted += 1
        if deferred:
            self.stderr.write(f"Private selfie cleanup deferred for {deferred} searches.")
        self.stdout.write(str(deleted))
