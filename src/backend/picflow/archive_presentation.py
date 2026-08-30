from dataclasses import dataclass


@dataclass(frozen=True)
class ArchivePageAction:
    label: str
    helper_text: str | None


def archive_page_action(
    *, item_count: int, page_number: int, page_count: int
) -> ArchivePageAction | None:
    """Return the shared archive action for one rendered collection page."""
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in (item_count, page_number, page_count)
    ):
        raise ValueError("archive page values must be integers")
    if item_count < 0 or page_count < 1 or not 1 <= page_number <= page_count:
        raise ValueError("invalid archive page bounds")
    if item_count < 2:
        return None
    if page_count == 1:
        return ArchivePageAction(label="Скачать все", helper_text=None)
    return ArchivePageAction(
        label="Скачать эту страницу",
        helper_text=(
            f"В архив попадут {item_count} {_photo_noun(item_count)} со страницы {page_number} "
            f"из {page_count}. Остальные страницы можно скачать отдельно."
        ),
    )


def _photo_noun(count: int) -> str:
    remainder = count % 100
    if 11 <= remainder <= 14:
        return "фотографий"
    last_digit = count % 10
    if last_digit == 1:
        return "фотография"
    if 2 <= last_digit <= 4:
        return "фотографии"
    return "фотографий"
