import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

import keyring
from keyring.errors import KeyringError

from findme_client.api import Api, ClientError, Json, secure_destination


def data_dir() -> Path:
    path = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "findme-photo"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def valid_profile(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value):
        raise ClientError(
            "invalid_profile", "Use letters, digits, dashes or underscores in a profile."
        )
    return value


def server_url(value: str) -> str:
    url = urlsplit(value.rstrip("/"))
    if not (
        secure_destination(value)
        and url.hostname
        and not url.username
        and not url.password
        and not url.path
        and not url.query
        and not url.fragment
    ):
        raise ClientError("invalid_server", "Enter the HTTPS server origin.")
    return url.geturl()


def profile_file(profile: str) -> Path:
    return data_dir() / f"{valid_profile(profile)}.json"


def read_server(profile: str) -> str:
    try:
        return server_url(json.loads(profile_file(profile).read_text())["server"])
    except FileNotFoundError:
        raise ClientError("login_required", "Run findme auth login for this profile.") from None
    except (KeyError, TypeError, ValueError, OSError):
        raise ClientError("profile_error", "Cannot read the local profile.") from None


def key_name(profile: str, server: str) -> str:
    return f"{profile}@{server}"


@contextmanager
def api_for(profile: str) -> Iterator[Api]:
    server = read_server(profile)
    try:
        token = keyring.get_password("findme-photo", key_name(profile, server))
    except KeyringError:
        raise keyring_error() from None
    if not token:
        raise ClientError("login_required", "Run findme auth login for this profile.")
    with Api.connect(server, token) as api:
        yield api


def login(profile: str, server: str, secret: str) -> Json:
    target = profile_file(profile)
    server = server_url(server)
    with Api.connect(server, secret) as api:
        identity = api.request("whoami/")
    try:
        keyring.set_password("findme-photo", key_name(profile, server), secret)
    except KeyringError:
        raise keyring_error() from None
    with NamedTemporaryFile(mode="w", dir=target.parent, delete=False) as file:
        json.dump({"profile": profile, "server": server}, file)
        temporary = Path(file.name)
    try:
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"profile": profile, "server": server, **identity}


def logout(profile: str) -> None:
    server = read_server(profile)
    key = key_name(profile, server)
    try:
        if keyring.get_password("findme-photo", key) is not None:
            keyring.delete_password("findme-photo", key)
    except KeyringError:
        raise keyring_error() from None
    profile_file(profile).unlink(missing_ok=True)


def keyring_error() -> ClientError:
    return ClientError("keyring_error", "Unlock or configure the system keyring and try again.")
