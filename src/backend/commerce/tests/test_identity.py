import base64
from typing import Any, cast

from django.test import SimpleTestCase

from commerce.identity import browser_token_sha256, generate_browser_token, parse_browser_token


class BrowserTokenIdentityTests(SimpleTestCase):
    def test_generated_tokens_are_canonical_urlsafe_encodings_of_32_random_bytes(self) -> None:
        tokens = {generate_browser_token() for _ in range(8)}

        self.assertEqual(len(tokens), 8)
        for token in tokens:
            self.assertEqual(len(token), 43)
            self.assertRegex(token, r"^[A-Za-z0-9_-]{43}$")
            self.assertEqual(len(base64.urlsafe_b64decode(token + "=")), 32)

    def test_parser_accepts_only_the_canonical_unpadded_32_byte_encoding(self) -> None:
        token = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"

        self.assertEqual(parse_browser_token(token), bytes(range(32)))

        malformed_tokens = (
            None,
            token.encode(),
            "",
            token[:-1],
            token + "A",
            token + "=",
            token.replace("A", "+", 1),
            token.replace("A", "/", 1),
            token[:-1] + "9",
            "я" * 43,
        )
        for malformed in malformed_tokens:
            with self.subTest(token=malformed):
                with self.assertRaises(ValueError):
                    parse_browser_token(cast(Any, malformed))

    def test_digest_is_stable_lowercase_sha256_without_disclosing_the_token(self) -> None:
        token = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"

        digest = browser_token_sha256(token)

        self.assertEqual(
            digest,
            "ea866a757e4c38babfa8127cbe9a409d3e1f93a00ff1488ff735fcf917afffd0",
        )
        self.assertNotIn(token, digest)
