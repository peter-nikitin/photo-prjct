"""The isolated image package's effective deployment boundary."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "deploy/image-origin"
TEST_ENV = {
    "PRIVATE_MEDIA_S3_BUCKET": "image-origin-contract",
    "GALLERY_IMGPROXY_KEY": "11" * 32,
    "GALLERY_IMGPROXY_SALT": "22" * 32,
    "IMAGE_ORIGIN_HEADER_SECRET": "contract-origin-secret-0123456789",
    "IMAGE_ORIGIN_S3_ACCESS_KEY_ID": "contract-access",
    "IMAGE_ORIGIN_S3_SECRET_ACCESS_KEY": "contract-secret-0123456789",
}


def test_effective_compose_keeps_image_compute_isolated_and_bounded():
    # Removing a digest, hardening control, or resource bound must fail this seam.
    assert (PACKAGE / "compose.yml").is_file(), "The isolated origin package is missing"
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "acme",
            "-f",
            str(PACKAGE / "compose.yml"),
            "config",
            "--format",
            "json",
        ],
        env={**os.environ, **TEST_ENV},
        capture_output=True,
        text=True,
        check=True,
    )
    services = json.loads(result.stdout)["services"]
    assert set(services) == {"nginx", "imgproxy", "certbot"}
    for name, service in services.items():
        assert re.fullmatch(r".+@sha256:[0-9a-f]{64}", service["image"]), name
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in service["security_opt"]
        assert 0 < float(service["cpus"]) <= 2
        assert 0 < int(service["mem_limit"]) <= 4 * 1024**3
        assert 0 < service["pids_limit"] <= 256
        assert service["tmpfs"]
        assert service["healthcheck"]["test"]
        assert service.get("privileged", False) is False
        assert service.get("network_mode") != "host"
    assert not services["imgproxy"].get("ports")
    assert not services["imgproxy"].get("volumes")
    assert set(services["imgproxy"]["environment"]).isdisjoint(
        {"DATABASE_URL", "GALLERY_CDN_TOKEN_SECRET", "IMGPROXY_TRUSTED_SIGNATURES"}
    )


def test_unsupported_imgproxy_options_fail_closed_without_exposing_values():
    result = subprocess.run(
        ["sh", str(PACKAGE / "imgproxy-start.sh")],
        env={**os.environ, "IMGPROXY_UNSUPPORTED_OPTION": "private-config-sentinel"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Unsupported imgproxy configuration" in result.stderr
    assert "private-config-sentinel" not in result.stdout + result.stderr


def test_apply_rejects_an_invalid_release_before_installation(tmp_path):
    result = subprocess.run(
        ["sh", str(PACKAGE / "apply.sh")],
        env={
            **os.environ,
            **TEST_ENV,
            "IMAGE_ORIGIN_ROOT": str(tmp_path / "origin"),
            "IMAGE_ORIGIN_RELEASE": "../../unsafe",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Invalid release" in result.stderr
    assert not (tmp_path / "origin").exists()


def test_apply_keeps_non_root_compose_configs_readable_in_installed_release(tmp_path):
    root = tmp_path / "origin"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    image = tmp_path / "image.jpg"
    Image.new("RGB", (960, 640)).save(image, format="JPEG", progressive=True)
    driver = (
        f"#!{sys.executable}\n"
        + """
import os
import pathlib
import sys

args = sys.argv[1:]
if pathlib.Path(sys.argv[0]).name == "curl":
    pathlib.Path(args[args.index("--output") + 1]).write_bytes(
        pathlib.Path(os.environ["TEST_IMAGE"]).read_bytes()
    )
    pathlib.Path(args[args.index("--dump-header") + 1]).write_text(
        "HTTP/1.1 200 OK\\nContent-Type: image/jpeg\\n"
        "Cache-Control: public, max-age=21600, s-maxage=2592000\\n"
    )
"""
    )
    for name in ["docker", "curl"]:
        executable = fake_bin / name
        executable.write_text(driver)
        executable.chmod(0o755)

    probe = "/" + "A" * 43 + "/gallery-v1/czM6Ly9h.jpg"
    env = {
        **os.environ,
        **TEST_ENV,
        "PATH": f"{fake_bin}:{Path(sys.executable).parent}:{os.environ['PATH']}",
        "IMAGE_ORIGIN_ROOT": str(root),
        "IMAGE_ORIGIN_RELEASE": "b" * 40,
        "IMAGE_ORIGIN_PROBE_PATH": probe,
        "TEST_IMAGE": str(image),
    }
    result = subprocess.run(
        ["sh", str(PACKAGE / "apply.sh")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    release = root / "current"
    for service, relative_path in (
        ("imgproxy", "imgproxy-start.sh"),
        ("imgproxy", "presets.txt"),
        ("nginx", "nginx.conf.template"),
        ("nginx", "nginx-start.sh"),
        ("nginx", "monitoring/metrics.js"),
    ):
        config_path = release / relative_path
        mode = config_path.stat().st_mode & 0o777
        assert mode & 0o004, (
            f"{service} config source {relative_path} has mode {mode:04o}; "
            "its non-root container user cannot read it"
        )

    assert release.stat().st_mode & 0o077 == 0
    assert (release / ".env").stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("failure", ["none", "candidate", "public"])
def test_candidate_must_pass_image_check_before_replacing_active_release(tmp_path, failure):
    root = tmp_path / "origin"
    old = root / "releases" / ("a" * 40)
    old.mkdir(parents=True)
    shutil.copytree(PACKAGE, old, dirs_exist_ok=True)
    probe = "/" + "A" * 43 + "/gallery-v1/czM6Ly9h.jpg"
    (old / ".env").write_text(
        "\n".join(f"{name}={value}" for name, value in TEST_ENV.items())
        + f"\nIMAGE_ORIGIN_PROBE_PATH={probe}\n"
    )
    (root / "current").symlink_to(old)
    certificates = root / "certificates"
    certificates.mkdir()
    (certificates / "retained-certificate").write_text("keep-certificate")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    image = tmp_path / "image.jpg"
    Image.new("RGB", (960, 640)).save(image, format="JPEG", progressive=True)
    driver = (
        f"#!{sys.executable}\n"
        + """
import os, pathlib, sys
args = sys.argv[1:]
log = pathlib.Path(os.environ["TEST_OPERATION_LOG"])
if pathlib.Path(sys.argv[0]).name == "docker":
    assert "GALLERY_IMGPROXY_KEY" not in os.environ
    event = "docker " + " ".join(args)
else:
    config = sys.stdin.read()
    candidate = ":18443" in config
    event = "probe candidate" if candidate else "probe public"
    with log.open("a") as target: target.write(event + "\\n")
    if candidate and os.environ["TEST_FAILURE"] == "candidate": sys.exit(22)
    if (not candidate and os.environ["TEST_FAILURE"] == "public"
            and log.read_text().count("probe public") == 1): sys.exit(22)
    output = pathlib.Path(args[args.index("--output") + 1])
    output.write_bytes(pathlib.Path(os.environ["TEST_IMAGE"]).read_bytes())
    pathlib.Path(args[args.index("--dump-header") + 1]).write_text(
        "HTTP/1.1 200 OK\\nContent-Type: image/jpeg\\n"
        "Cache-Control: public, max-age=21600, s-maxage=2592000\\n")
    sys.exit(0)
with log.open("a") as target: target.write(event + "\\n")
"""
    )
    for name in ["docker", "curl"]:
        (fake_bin / name).write_text(driver)
        (fake_bin / name).chmod(0o755)
    log = tmp_path / "operations"
    env = {
        **os.environ,
        **TEST_ENV,
        "PATH": f"{fake_bin}:{Path(sys.executable).parent}:{os.environ['PATH']}",
        "IMAGE_ORIGIN_ROOT": str(root),
        "IMAGE_ORIGIN_RELEASE": "b" * 40,
        "IMAGE_ORIGIN_PROBE_PATH": probe,
        "TEST_OPERATION_LOG": str(log),
        "TEST_IMAGE": str(image),
        "TEST_FAILURE": failure,
    }
    result = subprocess.run(
        ["sh", str(PACKAGE / "apply.sh")], env=env, capture_output=True, text=True
    )
    assert (certificates / "retained-certificate").read_text() == "keep-certificate"
    assert log.exists(), "Deployment did not validate or check a candidate"
    operations = log.read_text()
    assert "probe candidate" in operations, result.stderr
    assert operations.index("run --rm --no-deps certbot --version") < operations.index(
        "up -d --wait --wait-timeout 45 imgproxy"
    ), "The ACME webroot must be ready before Nginx can become public"
    public_up = "-p findme-image-origin up"
    if failure != "none":
        assert result.returncode != 0
        assert (root / "current").resolve() == old
        if failure == "candidate":
            assert public_up not in operations
        else:
            assert operations.count(public_up) == 2
            assert "IMAGE_ORIGIN_ROLLBACK=restored_previous_package" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert operations.index("probe candidate") < operations.index(public_up)
        assert (root / "previous").resolve() == old
        assert (root / "current").resolve() == root / "releases" / ("b" * 40)
        assert (root / "current" / ".env").stat().st_mode & 0o777 == 0o600
        again = subprocess.run(
            ["sh", str(PACKAGE / "apply.sh")], env=env, capture_output=True, text=True
        )
        assert again.returncode == 0, again.stderr
        assert (root / "previous").resolve() == old
        rolled_back = subprocess.run(
            ["sh", str(PACKAGE / "apply.sh"), "rollback"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert rolled_back.returncode == 0, rolled_back.stderr
        assert (root / "current").resolve() == old
        assert (root / "previous").resolve() == root / "releases" / ("b" * 40)
    for secret in TEST_ENV.values():
        assert secret not in result.stdout + result.stderr + operations
