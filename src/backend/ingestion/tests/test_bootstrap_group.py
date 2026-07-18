from io import StringIO
from pathlib import Path

from django.contrib.auth.models import Group, Permission
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase


class BootstrapPhotographerGroupTests(TestCase):
    def upload_permission(self):
        return Permission.objects.get(content_type__app_label="ingestion", codename="upload_photos")

    def test_first_run_creates_group_and_assigns_permission(self) -> None:
        call_command("bootstrap_photographer_group")
        group = Group.objects.get(name="Photographer")
        self.assertTrue(group.permissions.filter(pk=self.upload_permission().pk).exists())

    def test_repeat_is_idempotent_and_preserves_unrelated_permissions(self) -> None:
        group = Group.objects.create(name="Photographer")
        unrelated = Permission.objects.exclude(pk=self.upload_permission().pk).first()
        assert unrelated is not None
        group.permissions.add(unrelated)
        call_command("bootstrap_photographer_group")
        call_command("bootstrap_photographer_group")
        group.refresh_from_db()
        self.assertEqual(Group.objects.filter(name="Photographer").count(), 1)
        self.assertTrue(group.permissions.filter(pk=unrelated.pk).exists())
        self.assertTrue(group.permissions.filter(pk=self.upload_permission().pk).exists())

    def test_missing_permission_raises_clear_error(self) -> None:
        self.upload_permission().delete()
        with self.assertRaisesMessage(
            CommandError, "ingestion.upload_photos permission is missing"
        ):
            call_command("bootstrap_photographer_group", stdout=StringIO())


class EntrypointOrderingTests(SimpleTestCase):
    def test_bootstrap_runs_between_migrate_and_collectstatic(self) -> None:
        entrypoint = Path(__file__).parents[2] / "entrypoint.sh"
        contents = entrypoint.read_text()
        self.assertLess(
            contents.index("python manage.py migrate --noinput"),
            contents.index("python manage.py bootstrap_photographer_group"),
        )
        self.assertLess(
            contents.index("python manage.py bootstrap_photographer_group"),
            contents.index("python manage.py collectstatic --noinput"),
        )
