"""Opt-in local custom-dump rehearsal/restore and temporary HTTP edge smoke.

Run only with the freshly built stage1-runtime-web:fixture image. All database
bytes live in a unique disposable Compose named volume, never the host PG path.
"""

import hashlib
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB_IMAGE = "stage1-runtime-web:fixture"
NGINX_IMAGE = (
    "nginx:1.28.2-alpine@sha256:5b4900b042ccfa8b0a73df622c3a60f2322faeb2be800cbee5aa7b44d241649e"
)


def run(*args: str, env: dict[str, str] | None = None, check: bool = True):
    result = subprocess.run(args, env=env, capture_output=True)
    if check and result.returncode:
        detail = result.stderr.decode(errors="replace")[-2000:]
        raise RuntimeError(f"{' '.join(args)} failed ({result.returncode}): {detail}")
    return result


def main() -> None:
    run("docker", "image", "inspect", WEB_IMAGE)
    name = "findme-fixture-" + uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix="findme-recovery-fixture-") as directory:
        temp = Path(directory)
        source = (ROOT / "docker-compose.deployment.yml").read_text()
        old_mount = "/var/lib/findme-photo/postgres:/var/lib/postgresql/data"
        assert source.count(old_mount) == 1
        compose_file = temp / "docker-compose.deployment.yml"
        compose_file.write_text(
            source.replace("name: photo-prjct", f"name: {name}", 1).replace(
                old_mount, "fixturepg:/var/lib/postgresql/data", 1
            )
            + "  fixturepg:\n"
        )
        assert old_mount not in compose_file.read_text()
        values = {
            "APP_IMAGE": WEB_IMAGE,
            "SECRET_KEY": "local-fixture-only-secret",
            "DEBUG": "False",
            "ALLOWED_HOSTS": "192.0.2.44,web,localhost",
            "DB_NAME": "app",
            "DB_USER": "app",
            "DB_PASSWORD": "local-fixture-only-password",
            "GUNICORN_WORKERS": "1",
            "GUNICORN_THREADS": "2",
            "GUNICORN_TIMEOUT": "30",
            "GUNICORN_MAX_REQUESTS": "100",
            "GUNICORN_MAX_REQUESTS_JITTER": "10",
            "PUBLIC_DOMAIN": "findme-photo.ru",
            "MEDIA_STORAGE_BACKEND": "s3",
            "PHOTO_PROCESSING_ENABLED": "True",
            "PHOTO_PROCESSING_FACE_ENABLED": "True",
            "MEDIA_S3_PUBLIC_BUCKET": "public-fixture",
            "MEDIA_S3_ACCESS_KEY_ID": "public-fixture-key",
            "MEDIA_S3_SECRET_ACCESS_KEY": "public-fixture-secret",
            "PRIVATE_MEDIA_S3_BUCKET": "private-fixture",
            "PRIVATE_MEDIA_S3_ACCESS_KEY_ID": "private-fixture-key",
            "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY": "private-fixture-secret",
            "GALLERY_CDN_ORIGIN": "http://192.0.2.44",
            "GALLERY_CDN_TOKEN_SECRET": "local-fixture-cdn-secret",
            "GALLERY_IMGPROXY_KEY": "local-fixture-imgproxy-key",
            "GALLERY_IMGPROXY_SALT": "local-fixture-imgproxy-salt",
        }
        env_file = temp / ".env"
        env_file.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
        env_file.chmod(0o600)
        env = {**os.environ, "COMPOSE_PROJECT_NAME": name, "DEPLOY_ROOT": str(temp)}
        compose = ("docker", "compose", "--env-file", str(env_file), "-f", str(compose_file))

        def dc(*args: str, check: bool = True):
            return run(*compose, *args, env=env, check=check)

        def sql(query: str, *, database: str = "app") -> str:
            return (
                dc("exec", "-T", "db", "psql", "-U", "app", "-d", database, "-At", "-c", query)
                .stdout.decode()
                .strip()
            )

        def web_command(*args: str) -> None:
            dc("run", "--rm", "--no-deps", "--entrypoint", "python", "web", "manage.py", *args)

        edge = name + "-edge"
        dummy = name + "-imgproxy"
        try:
            dc("up", "-d", "--wait", "db")
            web_command("migrate", "--noinput")
            web_command(
                "shell",
                "-c",
                "from datetime import date; from picflow.models import Event; "
                "from django.contrib.auth import get_user_model; "
                "e=Event.objects.create(name='Fixture Event',slug='fixture-event',"
                "start_date=date(2026,1,1),end_date=date(2026,1,1),city='Test'); "
                "get_user_model().objects.create_user(username='fixture-user')",
            )
            assert sql("SELECT count(*) FROM picflow_event WHERE slug='fixture-event'") == "1"
            assert sql("SELECT count(*) FROM auth_user WHERE username='fixture-user'") == "1"
            ledger_count = int(sql("SELECT count(*) FROM django_migrations"))
            assert ledger_count > 0
            dump = temp / "current.dump"
            dump.write_bytes(
                dc("exec", "-T", "db", "pg_dump", "-U", "app", "-d", "app", "-Fc").stdout
            )
            assert dump.read_bytes().startswith(b"PGDMP")
            digest = hashlib.sha256(dump.read_bytes()).hexdigest()
            dc("exec", "-T", "db", "dropdb", "-U", "app", "app")
            dc("exec", "-T", "db", "createdb", "-U", "app", "app")
            assert sql("SELECT to_regclass('public.django_migrations') IS NULL") == "t"

            broken = temp / "broken.dump"
            broken.write_bytes(b"corrupt custom archive")
            corrupt = run(
                "sh",
                str(ROOT / "scripts/restore-recovery-database.sh"),
                "--dump",
                str(broken),
                env=env,
                check=False,
            )
            assert corrupt.returncode != 0
            assert b"RESTORE_RESULT=verified" not in corrupt.stdout
            assert sql("SELECT to_regclass('public.django_migrations') IS NULL") == "t"

            rehearsal = run(
                "sh", str(ROOT / "scripts/rehearse-recovery.sh"), "--dump", str(dump), env=env
            )
            assert b"REHEARSAL_RESULT=verified" in rehearsal.stdout
            assert (
                sql(
                    "SELECT count(*) FROM pg_database WHERE datname LIKE 'recovery_rehearsal_%'",
                    database="postgres",
                )
                == "0"
            )
            assert sql("SELECT to_regclass('public.django_migrations') IS NULL") == "t"

            restore = run(
                "sh",
                str(ROOT / "scripts/restore-recovery-database.sh"),
                "--dump",
                str(dump),
                env=env,
            )
            assert b"RESTORE_RESULT=verified" in restore.stdout
            assert sql("SELECT count(*) FROM picflow_event WHERE slug='fixture-event'") == "1"
            assert sql("SELECT count(*) FROM auth_user WHERE username='fixture-user'") == "1"
            assert int(sql("SELECT count(*) FROM django_migrations")) >= ledger_count
            print(f"FIXTURE_CUSTOM_DUMP_SHA256={digest}")
            print(f"RESTORED_EVENTS=1 USERS=1 MIGRATION_LEDGER>={ledger_count}")
            print("CORRUPT_ARCHIVE_REJECTED_BEFORE_MIGRATE=yes REHEARSAL_DB_CLEANED=yes")

            dc("up", "-d", "--wait", "web")
            dummy_config = temp / "dummy-imgproxy.conf"
            dummy_config.write_text('server { listen 8080; location / { return 200 "image"; } }\n')
            run(
                "docker",
                "run",
                "-d",
                "--rm",
                "--network",
                f"{name}_default",
                "--network-alias",
                "imgproxy",
                "--name",
                dummy,
                "-v",
                f"{dummy_config}:/etc/nginx/conf.d/default.conf:ro",
                NGINX_IMAGE,
            )
            run(
                "docker",
                "run",
                "-d",
                "--rm",
                "--network",
                f"{name}_default",
                "--name",
                edge,
                "-p",
                "127.0.0.1::8080",
                "--user",
                "101:101",
                "--read-only",
                "--tmpfs",
                "/var/cache/nginx:size=16777216,uid=101,gid=101",
                "--tmpfs",
                "/var/run:size=1048576,uid=101,gid=101",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "-v",
                f"{ROOT / 'deploy/nginx/recovery-nginx.conf'}:/etc/nginx/nginx.conf:ro",
                "-v",
                f"{ROOT / 'deploy/nginx/recovery-http.conf'}:/etc/nginx/conf.d/default.conf:ro",
                "-v",
                f"{name}_staticfiles:/static:ro",
                NGINX_IMAGE,
            )
            address = run("docker", "port", edge, "8080/tcp").stdout.decode().strip()
            assert address.startswith("127.0.0.1:")

            def http(path: str, *, method: str = "GET") -> int:
                request = urllib.request.Request(
                    f"http://{address}{path}", headers={"Host": "192.0.2.44"}, method=method
                )
                try:
                    with urllib.request.urlopen(request, timeout=3) as response:
                        return response.status
                except urllib.error.HTTPError as error:
                    return error.code

            for _ in range(30):
                try:
                    if http("/") == 200:
                        break
                except urllib.error.URLError:
                    time.sleep(0.5)
            assert http("/") == 200
            assert http("/", method="HEAD") == 200
            assert http("/admin/") == 404
            assert http("/photographer/login/") == 404
            assert http("/photographer/") == 404
            assert http("/orders/", method="POST") == 405
            assert http("/ready/") == 200
            print("HTTP_EDGE_PUBLIC_GET_HEAD=200 ADMIN=404 PHOTOGRAPHER=404 BUSINESS_POST=405")
        finally:
            run("docker", "rm", "-f", edge, check=False)
            run("docker", "rm", "-f", dummy, check=False)
            dc("down", "--volumes", "--remove-orphans", check=False)


if __name__ == "__main__":
    main()
