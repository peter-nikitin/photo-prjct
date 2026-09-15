"""Offline Certbot webroot lifecycle in the pinned, hardened Certbot container."""

import argparse
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from certbot._internal.plugins.webroot import Authenticator

INSTALLATION = Path("/opt/photo-prjct-image-origin")
WEBROOT = INSTALLATION / "acme"


def prepare():
    assert not WEBROOT.exists(), "The test must start with a fresh installation volume"
    # Stop before Compose or certificate issuance: only the real default-path mkdir/umask runs.
    result = subprocess.run(
        ["sh", "/reviewed-package/apply.sh"],
        env={"PATH": os.environ["PATH"], "IMAGE_ORIGIN_RELEASE": "b" * 40},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, result.stderr
    assert "Invalid required configuration: PRIVATE_MEDIA_S3_BUCKET" in result.stderr
    assert WEBROOT.stat().st_uid == 0
    assert WEBROOT.stat().st_gid == 0
    assert WEBROOT.stat().st_mode & 0o777 == 0o700
    print("ACME_FRESH_APPLY_DIRECTORY=0700_root_root")


class OfflineChallenge:
    """Only the ACME server input is synthetic; Certbot writes and cleans the real files."""

    identifier = SimpleNamespace(typ="dns", value="img-origin.findme-photo.ru")

    def __init__(self, token):
        self.chall = SimpleNamespace(encode=lambda field: token)

    def response_and_validation(self):
        return None, "contract-acme-key-authorization"


def write():
    assert os.getuid() == 0 and 101 in os.getgroups(), "Use the packaged Certbot identity"
    plugin = Authenticator(
        argparse.Namespace(webroot_path=["/var/www/acme"], webroot_map={}), "webroot"
    )
    initial = OfflineChallenge("contract-initial-token")
    plugin.perform([initial])
    plugin.cleanup([initial])
    assert not (WEBROOT / ".well-known/acme-challenge/contract-initial-token").exists()
    plugin.perform([OfflineChallenge("contract-renewal-token")])
    print("ACME_CERTBOT_WRITE_CLEANUP_RENEWAL=pass")


def verify():
    for path in [WEBROOT, WEBROOT / ".well-known", WEBROOT / ".well-known/acme-challenge"]:
        info = path.stat()
        assert (info.st_uid, info.st_gid, info.st_mode & 0o777) == (0, 101, 0o750), path
    challenge = WEBROOT / ".well-known/acme-challenge/contract-renewal-token"
    assert challenge.stat().st_uid == 0
    assert challenge.stat().st_mode & 0o777 == 0o644  # Certbot's HTTP-01 file policy.
    for path in [INSTALLATION / "releases", INSTALLATION / "certificates"]:
        assert path.stat().st_mode & 0o777 == 0o700, path
    print("ACME_PERMISSIONS=0750_root_101 PRIVATE_INSTALLATION=0700")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "write", "verify"])
    {"prepare": prepare, "write": write, "verify": verify}[parser.parse_args().mode]()
