"""Finite catalog of durable work observed by the queue dashboard."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SegmentDefinition:
    key: str
    title: str
    color: str


@dataclass(frozen=True)
class QueueDefinition:
    key: str
    title: str
    unit: str
    segments: tuple[SegmentDefinition, ...]


WAIT = SegmentDefinition("wait", "Ожидание первого захвата", "#7895b2")
RETRY = SegmentDefinition("retry", "Предыдущие попытки и интервалы повторов", "#a882bd")
DOWNLOAD = SegmentDefinition("download", "Получение входных данных", "#4c91cd")
COMPUTE = SegmentDefinition("compute", "Вычисление результата", "#47a878")
ACCEPT = SegmentDefinition("accept", "Доставка и принятие результата", "#e5a743")
UPLOAD = SegmentDefinition("upload", "Загрузка в S3", "#d97656")

RECOGNITION = (WAIT, DOWNLOAD, COMPUTE, ACCEPT, RETRY)
PREVIEW = (
    WAIT,
    DOWNLOAD,
    SegmentDefinition("compute", "Создание изображения", COMPUTE.color),
    SegmentDefinition("accept", "Загрузка и принятие изображения", ACCEPT.color),
    RETRY,
)
MATCHING = (
    WAIT,
    SegmentDefinition("query", "Выполнение поискового запроса", COMPUTE.color),
    SegmentDefinition("expand", "Расширение и подготовка результатов", DOWNLOAD.color),
    SegmentDefinition("publish", "Публикация результата", ACCEPT.color),
    RETRY,
)

QUEUES = (
    QueueDefinition("selfie_recognition", "Распознавание селфи", "задачи", RECOGNITION),
    QueueDefinition("selfie_matching", "Поиск совпадений селфи", "задачи", MATCHING),
    QueueDefinition("gallery_matching", "Поиск по фотографии галереи", "задачи", MATCHING),
    QueueDefinition(
        "photo_capture_metadata",
        "Метаданные фотографий",
        "задачи",
        (
            WAIT,
            DOWNLOAD,
            SegmentDefinition("compute", "Извлечение метаданных", COMPUTE.color),
            ACCEPT,
            RETRY,
        ),
    ),
    QueueDefinition("photo_generate_preview", "Превью фотографий", "задачи", PREVIEW),
    QueueDefinition(
        "photo_generate_watermarked_preview", "Превью с водяным знаком", "задачи", PREVIEW
    ),
    QueueDefinition("photo_face_embedding", "Распознавание лиц на фото", "задачи", RECOGNITION),
    QueueDefinition("photo_person_appearance", "Признаки внешности", "задачи", RECOGNITION),
    QueueDefinition("photo_bib_recognition", "Распознавание номеров", "задачи", RECOGNITION),
    QueueDefinition(
        "disk_import",
        "Импорт с Яндекс Диска",
        "партии",
        (
            WAIT,
            SegmentDefinition("manifest", "Получение списка файлов", COMPUTE.color),
            DOWNLOAD,
            UPLOAD,
            SegmentDefinition("publish", "Принятие оригиналов", ACCEPT.color),
            SegmentDefinition("idle", "Интервалы между операциями", "#a0a36a"),
            SegmentDefinition("retry", "Повторы и паузы", RETRY.color),
        ),
    ),
    QueueDefinition(
        "photo_archive",
        "Подготовка архивов",
        "задачи",
        (
            WAIT,
            SegmentDefinition("capacity", "Ожидание места", RETRY.color),
            SegmentDefinition("build", "Сборка ZIP", COMPUTE.color),
            UPLOAD,
            RETRY,
        ),
    ),
    QueueDefinition(
        "commerce_email",
        "Отправка писем покупателям",
        "письма",
        (
            WAIT,
            SegmentDefinition("prepare", "Подготовка сообщения", COMPUTE.color),
            SegmentDefinition("send", "Отправка и принятие результата", ACCEPT.color),
            RETRY,
        ),
    ),
    QueueDefinition(
        "commerce_payment",
        "Сверка платежей",
        "попытки оплаты",
        (
            WAIT,
            SegmentDefinition("gateway", "Обращения к шлюзу", COMPUTE.color),
            SegmentDefinition("accept", "Принятие наблюдений", ACCEPT.color),
            SegmentDefinition("interval", "Интервалы между сверками", RETRY.color),
        ),
    ),
)
