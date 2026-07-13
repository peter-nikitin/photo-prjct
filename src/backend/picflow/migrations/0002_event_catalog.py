from django.db import migrations, models
from django.db.models import F, Q
from django.utils.text import slugify

import picflow.models


def forwards(apps, schema_editor):
    Event = apps.get_model("picflow", "Event")
    used_slugs: set[str] = set()
    for event in Event.objects.order_by("id").iterator():
        base = slugify(event.name, allow_unicode=True) or f"event-{event.pk}"
        slug = base if base not in used_slugs else f"{base}-{event.pk}"
        used_slugs.add(slug)
        event.slug = slug
        event.start_date = event.date_from
        event.end_date = event.date_to
        event.city = event.location
        event.access_type = "free"
        event.publication_status = "published" if event.active else "draft"
        event.save(
            update_fields=(
                "slug",
                "start_date",
                "end_date",
                "city",
                "access_type",
                "publication_status",
            )
        )


def backwards(apps, schema_editor):
    Event = apps.get_model("picflow", "Event")
    for event in Event.objects.iterator():
        event.date_from = event.start_date
        event.date_to = event.end_date
        event.location = event.city
        event.active = event.publication_status == "published"
        event.save(update_fields=("date_from", "date_to", "location", "active"))


class Migration(migrations.Migration):
    dependencies = [("picflow", "0001_initial")]

    operations = [
        migrations.RunSQL(
            "SET LOCAL lock_timeout = '2s'",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AddField(
            model_name="event",
            name="access_type",
            field=models.CharField(
                choices=[("free", "Free"), ("paid", "Paid")],
                db_default="free",
                default="free",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="event",
            name="city",
            field=models.CharField(max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="event",
            name="cover",
            field=models.ImageField(blank=True, upload_to=picflow.models.event_cover_key),
        ),
        migrations.AddField(
            model_name="event",
            name="end_date",
            field=models.DateField(null=True),
        ),
        migrations.AddField(
            model_name="event",
            name="publication_status",
            field=models.CharField(
                choices=[("draft", "Draft"), ("published", "Published")],
                db_default="draft",
                default="draft",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="event",
            name="slug",
            field=models.CharField(max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="event",
            name="start_date",
            field=models.DateField(null=True),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name="event", name="city", field=models.CharField(max_length=255)
        ),
        migrations.AlterField(model_name="event", name="end_date", field=models.DateField()),
        migrations.AlterField(
            model_name="event",
            name="slug",
            field=models.SlugField(allow_unicode=True, max_length=255, unique=True),
        ),
        migrations.AlterField(model_name="event", name="start_date", field=models.DateField()),
        migrations.RemoveField(model_name="event", name="active"),
        migrations.RemoveField(model_name="event", name="date_from"),
        migrations.RemoveField(model_name="event", name="date_to"),
        migrations.RemoveField(model_name="event", name="location"),
        migrations.AlterModelOptions(
            name="event", options={"ordering": ["start_date", "name"]}
        ),
        migrations.AddConstraint(
            model_name="event",
            constraint=models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")),
                name="event_end_date_gte_start_date",
            ),
        ),
    ]
