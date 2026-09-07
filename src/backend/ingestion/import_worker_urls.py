from django.urls import path

from ingestion import import_worker_views

urlpatterns = [
    path("readiness", import_worker_views.readiness, name="import_worker_readiness"),
    path("claim", import_worker_views.claim, name="import_worker_claim"),
    path(
        "attempts/<uuid:attempt_id>/renew",
        import_worker_views.renew,
        name="import_worker_renew",
    ),
    path(
        "attempts/<uuid:attempt_id>/manifest/pages",
        import_worker_views.manifest_page,
        name="import_worker_manifest_page",
    ),
    path(
        "attempts/<uuid:attempt_id>/manifest/finalize",
        import_worker_views.manifest_finalize,
        name="import_worker_manifest_finalize",
    ),
    path(
        "attempts/<uuid:attempt_id>/prepare-upload",
        import_worker_views.prepare_upload,
        name="import_worker_prepare_upload",
    ),
    path(
        "attempts/<uuid:attempt_id>/complete",
        import_worker_views.complete,
        name="import_worker_complete",
    ),
    path(
        "attempts/<uuid:attempt_id>/fail",
        import_worker_views.fail,
        name="import_worker_fail",
    ),
]
