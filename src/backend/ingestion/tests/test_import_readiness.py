import json

from django.test import TestCase, override_settings
from feature_flags.models import FeatureFlag
from ingestion.models import ImportAttempt


class ImportReadinessTests(TestCase):
    @override_settings(PHOTO_IMPORT_ENABLED=True, PHOTO_IMPORT_WORKER_TOKEN="ready-token")
    def test_readiness_with_gate_off_is_authenticated_versioned_and_nonmutating(self):
        before = list(FeatureFlag.objects.values())
        for token, version, status in [
            ("ready-token", 1, 200),
            ("wrong", 1, 401),
            ("ready-token", 2, 400),
        ]:
            response = self.client.post(
                "/internal/photo-import/v1/readiness",
                data=json.dumps(dict(contract_version=version)),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer " + token,
            )
            self.assertEqual(response.status_code, status)
        self.assertEqual(list(FeatureFlag.objects.values()), before)
        self.assertEqual(ImportAttempt.objects.count(), 0)
