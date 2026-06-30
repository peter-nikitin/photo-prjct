from datetime import date

from django.db import migrations, models
from django.db.models import PROTECT


EVENTS = [
    {
        "name": "London 10K",
        "date_from": date(2026, 6, 8),
        "date_to": date(2026, 6, 9),
        "location": "London",
        "description": "Городской забег с несколькими точками съемки на трассе.",
        "active": True,
    },
    {
        "name": "Brighton Ride",
        "date_from": date(2026, 6, 13),
        "date_to": date(2026, 6, 14),
        "location": "Brighton",
        "description": "Велозаезд по набережной с отдельными зонами старта и финиша.",
        "active": True,
    },
    {
        "name": "Expo Run",
        "date_from": date(2026, 6, 16),
        "date_to": date(2026, 6, 17),
        "location": "Expo Hall",
        "description": "Фото из expo-зоны, портреты участников и финишные кадры.",
        "active": True,
    },
]

PHOTOS = [
    {"id": "LDN-1048", "event": "London 10K", "src": "photos/run-city-1842.png"},
    {"id": "LDN-1190", "event": "London 10K", "src": "photos/run-track-1190.png"},
    {"id": "LDN-1202", "event": "London 10K", "src": "photos/run-track-1190.png"},
    {"id": "LDN-1316", "event": "London 10K", "src": "photos/run-finish-1842.png"},
    {"id": "LDN-1432", "event": "London 10K", "src": "photos/run-finish-1842.png"},
    {"id": "BRI-2044", "event": "Brighton Ride", "src": "photos/run-park-1204.png"},
    {"id": "BRI-2148", "event": "Brighton Ride", "src": "photos/run-park-1204.png"},
    {"id": "BRI-2291", "event": "Brighton Ride", "src": "photos/run-park-1204.png"},
    {"id": "BRI-2366", "event": "Brighton Ride", "src": "photos/run-finish-1204.png"},
    {"id": "EXP-3011", "event": "Expo Run", "src": "photos/run-expo-3125.png"},
    {"id": "EXP-3125", "event": "Expo Run", "src": "photos/run-expo-3125.png"},
    {"id": "EXP-3270", "event": "Expo Run", "src": "photos/run-track-1190.png"},
    {"id": "EXP-3338", "event": "Expo Run", "src": "photos/run-finish-1842.png"},
]


def seed_data(apps, schema_editor):
    Event = apps.get_model("picflow", "Event")
    Photo = apps.get_model("picflow", "Photo")

    event_by_name = {}
    for event_data in EVENTS:
        event = Event.objects.create(**event_data)
        event_by_name[event.name] = event

    for photo_data in PHOTOS:
        photo_row = photo_data.copy()
        event = event_by_name.get(photo_row.pop("event"))
        if event is None:
            raise ValueError(f"Missing event for photo {photo_row['id']}")
        Photo.objects.create(event=event, **photo_row)


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Event",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255, unique=True)),
                ("date_from", models.DateField()),
                ("date_to", models.DateField()),
                ("location", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                ("active", models.BooleanField(default=True)),
            ],
            options={
                "ordering": ["date_from", "name"],
            },
        ),
        migrations.CreateModel(
            name="Photo",
            fields=[
                ("id", models.CharField(max_length=32, primary_key=True, serialize=False)),
                ("src", models.FileField(upload_to="photos/")),
                ("event", models.ForeignKey(on_delete=PROTECT, related_name="photos", to="picflow.event")),
            ],
            options={
                "ordering": ["id"],
            },
        ),
        migrations.RunPython(seed_data, migrations.RunPython.noop),
    ]
