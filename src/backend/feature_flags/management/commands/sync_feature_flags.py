from django.core.management.base import BaseCommand
from django.db import transaction

from feature_flags.models import FeatureFlag
from feature_flags.registry import FEATURE_DEFINITIONS, validate_feature_definitions


class Command(BaseCommand):
    help = "Reconcile code-owned feature definitions with the database."

    def handle(self, *args, **options) -> None:
        del args, options
        validate_feature_definitions(FEATURE_DEFINITIONS)

        with transaction.atomic():
            flags_by_key = {
                flag.key: flag for flag in FeatureFlag.objects.select_for_update().all()
            }
            created = 0
            updated = 0
            preserved = 0

            for definition in FEATURE_DEFINITIONS:
                flag = flags_by_key.pop(definition.key, None)
                if flag is None:
                    FeatureFlag.objects.create(
                        key=definition.key,
                        description=definition.description,
                        state=FeatureFlag.State.OFF,
                    )
                    created += 1
                elif flag.description != definition.description:
                    flag.description = definition.description
                    flag.save(update_fields=("description", "updated_at"))
                    updated += 1
                else:
                    preserved += 1

            deleted, _ = FeatureFlag.objects.filter(
                pk__in=[flag.pk for flag in flags_by_key.values()]
            ).delete()

        self.stdout.write(
            "Feature flags synchronized: "
            f"created={created} updated={updated} preserved={preserved} deleted={deleted}."
        )
