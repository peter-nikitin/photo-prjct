from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Rejected until a separately versioned SCRFD benchmark generation is approved."

    def add_arguments(self, parser):
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--event")
        source.add_argument("--source-run")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--label", required=True)

    def handle(self, *args, **options):
        raise CommandError("SCRFD benchmark generation is not approved")
