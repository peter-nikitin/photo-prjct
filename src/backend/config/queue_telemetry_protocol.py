"""Wire identifiers shared by queue telemetry publishers and the listener."""

CHANNEL = "queue_telemetry"
COMPLETED_EVENT = "queue.task.completed"
BACKLOG_CHANGED_EVENT = "queue.backlog.changed"
COMPLETION_VERSION = 1
BACKLOG_VERSION = 2


GALLERY_QUEUE = "photo_import_gallery"
FACE_QUEUE = "photo_import_face"
PHOTO_IMPORT_QUEUES = frozenset((GALLERY_QUEUE, FACE_QUEUE))

type PhotoImportBacklogs = dict[str, tuple[int, int]]
