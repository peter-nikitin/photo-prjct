"""Wire identifiers shared by queue telemetry publishers and the listener."""

CHANNEL = "queue_telemetry"
COMPLETED_EVENT = "queue.task.completed"
COMPLETION_VERSION = 1


GALLERY_QUEUE = "photo_import_gallery"
FACE_QUEUE = "photo_import_face"
PHOTO_IMPORT_QUEUES = frozenset((GALLERY_QUEUE, FACE_QUEUE))

RESULT_EVENT = "queue.work.result"
RESULT_VERSION = 1
OUTCOMES = ("success", "failed", "canceled")
