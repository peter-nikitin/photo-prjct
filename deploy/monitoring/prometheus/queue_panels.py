"""Build the same instruments for every catalogued queue without copying widgets."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

if TYPE_CHECKING:
    from config.queue_catalog import QueueDefinition

STATES = (
    ("waiting", "Ожидает", "#7895b2"),
    ("processing", "Обрабатывается", "#47a878"),
    ("retry_wait", "Ждёт повтора", "#e5a743"),
    ("paused", "Приостановлено", "#a882bd"),
)
OUTCOMES = (
    ("success", "Успех", "#47a878"),
    ("failed", "Ошибка", "#d56464"),
    ("canceled", "Отмена", "#7895b2"),
)


def identity(queue: str, instrument: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"findme-photo/queues/{queue}/{instrument}"))


def _selector(metric: str, queue: str, job: str = "findme-queue-telemetry", **labels: str) -> str:
    values = {"job": job, "queue": queue, **labels}
    return metric + "{" + ",".join(f'{key}="{value}"' for key, value in values.items()) + "}"


def _fresh(queue: str, source: str) -> str:
    job = "findme-http" if source == "ready" else "findme-queue-telemetry"
    observed = _selector(f"findme_queue_{source}_observed_timestamp_seconds", queue, job)
    # Center the allowed age interval [0, 150] at 75 to use the selector once.
    return f"(abs(time() - max({observed}) - 75) <= 75)"


def _gate(expression: str, queue: str, source: str) -> str:
    result = f"({expression}) and on() {_fresh(queue, source)}"
    if source == "work":
        result += f" and on() (max({_selector('findme_queue_work_available', queue)}) == 1)"
    if source == "results":
        result += (
            " and on() (max(findme_queue_results_listener_connected"
            '{job="findme-queue-telemetry"}) == 1)'
        )
    return result


def _reset_difference(last: str, first: str) -> str:
    # A filter comparison keeps the left-hand value, which is the new value after a reset.
    return f"(({last}-{first}>=0) or ({last}<{first}))"


def _interval_increment(counter: str) -> str:
    """Observe each transition in a minute bucket, including two jittered polls.

    The single minute-aligned agent route can place two neighboring scrapes in
    one evaluation bucket. Comparing only the last pair loses the first step;
    comparing only endpoints loses a peak before a reset. Reconstruct both steps.
    """
    count = f"count_over_time({counter}[1m])"
    previous = counter + " offset 1m"
    one = f"{_reset_difference(counter, previous)} and ({count}==1)"
    difference = f"idelta({counter}[1m])"
    first = f"({counter} - {difference})"
    preceding = f"{_reset_difference(first, previous)} or (0*{counter})"
    last_increment = f"({difference}>=0) or ({counter} and ({difference}<0))"
    two = f"(({preceding}) + ({last_increment})) and ({count}==2)"
    before_range = f"timestamp({counter})<=timestamp({counter} @ start())"
    return f"sum((({one}) or ({two})) unless ({before_range}))"


def _chart(
    queue: QueueDefinition,
    role: str,
    title: str,
    description: str,
    expressions: list[tuple[str, str, str]],
    workspace_id: str,
    *,
    x: int,
    y: int,
    visualization: str = "LINE",
    unit: str = "",
    aggregation: str | None = None,
) -> dict[str, Any]:
    chart_id = identity(queue.key, role)
    source_id = identity(queue.key, role + "/source")
    settings: dict[str, Any] = {
        "type": "VISUALIZATION_TYPE_" + visualization,
        "colorSchemeSettings": {"automatic": {}},
        "yaxisSettings": {
            "left": {"type": "YAXIS_TYPE_LINEAR", "min": "0"},
            "right": {"type": "YAXIS_TYPE_LINEAR"},
        },
    }
    if unit:
        settings["yaxisSettings"]["left"]["title"] = unit
    if aggregation:
        settings["aggregation"] = "SERIES_AGGREGATION_" + aggregation
    targets, overrides = [], []
    for index, (expression, name, color) in enumerate(expressions):
        target_name = f"Q{index}"
        targets.append(
            {
                "prometheusTarget": {
                    "dataSourceId": source_id,
                    "workspaceId": workspace_id,
                    "query": expression,
                    "textMode": True,
                    "name": target_name,
                }
            }
        )
        overrides.append(
            {
                "matcher": {"query": {"names": [target_name]}},
                "settings": {"name": name, "color": color},
            }
        )
    return {
        "id": chart_id,
        "position": {
            **({"x": str(x)} if x else {}),
            **({"y": str(y)} if y else {}),
            "w": "12",
            "h": "8",
        },
        "multiSourceChart": {
            "id": chart_id,
            "title": title,
            "description": description,
            "targets": targets,
            "dataSources": [{"prometheusDataSource": {"id": source_id, "step": "60000"}}],
            "visualizationSettings": settings,
            "displayLegend": True,
            "seriesOverrides": overrides,
        },
    }


def _panel(queue: QueueDefinition, workspace_id: str, y: int) -> dict[str, Any]:
    def chart(role, title, description, expressions, x, row, **options):
        return _chart(
            queue, role, title, description, expressions, workspace_id, x=x, y=row, **options
        )

    def gauge(metric: str) -> str:
        return _gate(f"sum({_selector(metric, queue.key, 'findme-http')})", queue.key, "ready")

    result_counter = "findme_queue_results_total"
    measured = _selector("findme_queue_duration_observations_total", queue.key)
    means, deltas = [], []
    for segment in queue.segments:
        counter = _selector(
            "findme_queue_segment_duration_seconds_total", queue.key, segment=segment.key
        )
        mean = f"sum(rate({counter}[5m])) / (sum(rate({measured}[5m])) > 0)"
        means.append((_gate(mean, queue.key, "results"), segment.title, segment.color))
        # SUM reduces disjoint minute buckets over the whole selected interval.
        delta = _interval_increment(counter)
        deltas.append((_gate(f"({delta}) > 0", queue.key, "results"), segment.title, segment.color))

    widgets = [
        chart(
            "states",
            "Незавершённые задачи по состояниям",
            "Приблизительные остатки. Состояние записи не означает занятость CPU. "
            "Ноль — наблюдаемое отсутствие задач; разрыв — нет доступного свежего счётчика.",
            [
                (
                    _gate(
                        f"sum({_selector('findme_queue_work_count', queue.key, state=state)})",
                        queue.key,
                        "work",
                    ),
                    label,
                    color,
                )
                for state, label, color in STATES
            ],
            0,
            0,
            visualization="STACK",
            unit=queue.unit,
        ),
        chart(
            "ready",
            "Готово к запуску",
            "Работа, которую исполнитель может взять сейчас. Будущие повторы не входят. "
            "Это отдельный разрез остатка; складывать эти два прибора нельзя.",
            [(gauge("findme_queue_ready_count"), "Готово к запуску", "#4c91cd")],
            12,
            0,
            unit=queue.unit,
        ),
        chart(
            "oldest",
            "Возраст старейшей готовой задачи, с",
            "Полный возраст текущего цикла среди доступной сейчас работы, включая паузы "
            "и повторы. Пустая готовая очередь — 0; неизвестный возраст — разрыв.",
            [(gauge("findme_queue_oldest_ready_age_seconds"), "Возраст", "#e5a743")],
            0,
            8,
            unit="Секунды",
        ),
        chart(
            "results",
            "Завершения в минуту, среднее за 5 мин",
            "Принятые терминальные исходы задач. Повторяемая ошибка не является завершением. "
            "Счётчики описывают наблюдённые события и могут теряться при отключении подписки.",
            [
                (
                    _gate(
                        "sum(rate("
                        + _selector(result_counter, queue.key, outcome=outcome)
                        + "[5m])) * 60",
                        queue.key,
                        "results",
                    ),
                    label,
                    color,
                )
                for outcome, label, color in OUTCOMES
            ],
            12,
            8,
            unit=queue.unit + "/мин",
        ),
        chart(
            "durations",
            "Среднее время по сегментам за 5 мин, с",
            "Собственные сегменты очереди, одинаковая выборка полностью измеренных успехов. "
            "Сумма слоёв — полное среднее время. Без измеренных успехов оценки нет.",
            means,
            0,
            16,
            visualization="STACK",
            unit="Секунды",
        ),
        chart(
            "shares",
            "Доли времени по сегментам за выбранный период",
            "Сумма наблюдённых приращений секунд каждого сегмента за весь выбранный период, "
            "с точностью минутного сбора. Каждый новый образец учитывается один раз, "
            "сброс counters обработан. Без положительного измеренного времени пирог пуст. "
            "При увеличении шага источника сверх минуты нужен такой же шаг расчёта дельт.",
            deltas,
            12,
            16,
            visualization="PIE",
            aggregation="SUM",
        ),
    ]
    return {
        "position": {**({"y": str(y)} if y else {}), "w": "24", "h": "24"},
        "group": {
            "id": identity(queue.key, "panel"),
            "title": queue.title,
            "widgets": widgets,
        },
    }


def panels(
    queues: tuple[QueueDefinition, ...], workspace_id: str, *, start_y: int = 0
) -> list[dict[str, Any]]:
    return [_panel(queue, workspace_id, start_y + index * 24) for index, queue in enumerate(queues)]
