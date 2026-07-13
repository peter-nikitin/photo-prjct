from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create the Photographer group and grant photo upload permission."

    def handle(self, *args, **options):
        del args, options
        group, _ = Group.objects.get_or_create(name="Photographer")
        try:
            permission = Permission.objects.get(
                content_type__app_label="ingestion",
                codename="upload_photos",
            )
        except Permission.DoesNotExist as exc:
            raise CommandError(
                "ingestion.upload_photos permission is missing; run migrations first."
            ) from exc
        group.permissions.add(permission)
        self.stdout.write(self.style.SUCCESS("Photographer group is ready."))
