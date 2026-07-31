from django.test import SimpleTestCase

from picflow.pagination import InvalidCursor, SignedCursor


class SignedCursorTests(SimpleTestCase):
    def test_cursor_is_versioned_and_bound_to_its_collection(self) -> None:
        cursor = SignedCursor().encode(collection="gallery:42", last_key="photo-100")

        self.assertEqual(SignedCursor().decode(cursor=cursor, collection="gallery:42"), "photo-100")
        with self.assertRaises(InvalidCursor):
            SignedCursor().decode(cursor=cursor, collection="gallery:43")

    def test_tampered_cursor_is_invalid(self) -> None:
        cursor = SignedCursor().encode(collection="gallery:42", last_key="photo-100")

        with self.assertRaises(InvalidCursor):
            SignedCursor().decode(cursor=f"{cursor}x", collection="gallery:42")
