from django.core.management.base import BaseCommand, CommandError

from selfie_search.services.face_search_queue import prune_terminal_tasks


class Command(BaseCommand):
    help = "Delete one bounded batch of terminal face search tasks and their vectors."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=500)

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        if batch_size < 1:
            raise CommandError("--batch-size must be positive")
        deleted = prune_terminal_tasks(batch_size=batch_size)
        self.stdout.write(str(deleted))
