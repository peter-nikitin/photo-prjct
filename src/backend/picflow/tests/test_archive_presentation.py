from django.test import SimpleTestCase

from picflow.archive_presentation import ArchivePageAction, archive_page_action


class ArchivePageActionTests(SimpleTestCase):
    def test_hides_the_action_when_the_current_page_has_fewer_than_two_items(self) -> None:
        self.assertIsNone(archive_page_action(item_count=0, page_number=1, page_count=1))
        self.assertIsNone(archive_page_action(item_count=1, page_number=2, page_count=3))

    def test_presents_the_exact_single_page_label_without_helper(self) -> None:
        self.assertEqual(
            archive_page_action(item_count=2, page_number=1, page_count=1),
            ArchivePageAction(label="Скачать все", helper_text=None),
        )

    def test_presents_current_page_count_and_page_bounds_for_multiple_pages(self) -> None:
        self.assertEqual(
            archive_page_action(item_count=17, page_number=5, page_count=5),
            ArchivePageAction(
                label="Скачать эту страницу",
                helper_text=(
                    "В архив попадут 17 фотографий со страницы 5 из 5. "
                    "Остальные страницы можно скачать отдельно."
                ),
            ),
        )

    def test_uses_russian_photo_pluralization_for_multiple_pages(self) -> None:
        expected = {
            2: "фотографии",
            5: "фотографий",
            21: "фотография",
            22: "фотографии",
            25: "фотографий",
            111: "фотографий",
        }

        for count, noun in expected.items():
            with self.subTest(count=count):
                action = archive_page_action(item_count=count, page_number=2, page_count=3)
                assert action is not None
                self.assertEqual(
                    action.helper_text,
                    f"В архив попадут {count} {noun} со страницы 2 из 3. "
                    "Остальные страницы можно скачать отдельно.",
                )

    def test_rejects_invalid_page_bounds(self) -> None:
        with self.assertRaises(ValueError):
            archive_page_action(item_count=2, page_number=2, page_count=1)
