from django.core.management.base import BaseCommand, CommandError

from processing.services.person_appearance import request_person_appearance


class Command(BaseCommand):
    help = "Поставить в очередь обработку внешности для одного принятого clean preview."

    def add_arguments(self, parser):
        parser.add_argument("photo_id")
        parser.add_argument("preview_key")

    def handle(self, *args, **options):
        if not request_person_appearance(
            options["photo_id"], options["preview_key"], staff_rehearsal=True
        ):
            raise CommandError("Флаг выключен или указанный preview уже не актуален.")
        self.stdout.write("Запрос обработки внешности принят.")
