import base64
import hashlib
import re
import secrets

_BROWSER_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _encode_browser_token(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def generate_browser_token() -> str:
    return _encode_browser_token(secrets.token_bytes(32))


def parse_browser_token(token: str) -> bytes:
    if not isinstance(token, str) or _BROWSER_TOKEN_PATTERN.fullmatch(token) is None:
        raise ValueError("Invalid browser token.")
    try:
        decoded = base64.b64decode(token + "=", altchars=b"-_", validate=True)
    except (ValueError, UnicodeEncodeError) as error:
        raise ValueError("Invalid browser token.") from error
    if len(decoded) != 32 or _encode_browser_token(decoded) != token:
        raise ValueError("Invalid browser token.")
    return decoded


def browser_token_sha256(token: str) -> str:
    parse_browser_token(token)
    return hashlib.sha256(token.encode("ascii")).hexdigest()
