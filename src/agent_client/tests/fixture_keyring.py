"""Process-local test backend for driving CLI and MCP subprocesses without a desktop keychain."""

import os
from pathlib import Path

from keyring.backend import KeyringBackend


class TestKeyring(KeyringBackend):
    priority = 1

    def get_password(self, service, username):
        return Path(os.environ["FINDME_TEST_KEY_FILE"]).read_text()

    def set_password(self, service, username, password):
        raise NotImplementedError

    def delete_password(self, service, username):
        raise NotImplementedError
