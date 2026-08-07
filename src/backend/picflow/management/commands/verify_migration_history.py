from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder


class Command(BaseCommand):
    help = "Verify that every applied migration identity exists in this candidate image."

    def handle(self, *args: object, **options: object) -> None:
        try:
            loader = MigrationLoader(connection, ignore_no_migrations=True)
            applied_migrations = MigrationRecorder(connection).applied_migrations()
        except Exception:
            raise CommandError("Could not verify migration history") from None

        missing = sorted(set(applied_migrations).difference(loader.disk_migrations))
        if missing:
            identities = ", ".join(f"{app_label}.{name}" for app_label, name in missing)
            raise CommandError(f"Applied migrations missing from candidate: {identities}")
        self.stdout.write("migration-history-ok")
