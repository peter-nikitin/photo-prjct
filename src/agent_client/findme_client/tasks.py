import fcntl
import hashlib
import os
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryFile
from typing import BinaryIO
from uuid import UUID, uuid4

from findme_client.api import ClientError, Json
from findme_client.config import data_dir

MAX_FILES = 10_000
MAX_FILE_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class Source:
    files: list[Path]
    total_bytes: int
    skipped_count: int

    def summary(self) -> Json:
        return {
            "file_count": len(self.files),
            "total_bytes": self.total_bytes,
            "skipped_count": self.skipped_count,
        }


def scan(path: Path) -> Source:
    path = path.absolute()
    if path.is_symlink() or not path.exists():
        raise ClientError("invalid_source", "Choose an existing file or directory.")
    candidates: Iterator[Path]

    def walk() -> Iterator[Path]:
        def failed(exc: OSError) -> None:
            raise exc

        for root, directories, files in os.walk(path, onerror=failed):
            directories.sort()
            for directory in directories:
                child = Path(root) / directory
                if child.is_symlink():
                    yield child
            yield from (Path(root) / name for name in sorted(files))

    candidates = walk() if path.is_dir() else iter([path])
    total_bytes = 0
    skipped = 0
    photos: list[Path] = []
    for file in candidates:
        info = file.lstat()
        if stat.S_ISREG(info.st_mode) and file.suffix.lower() in {".jpg", ".jpeg"}:
            if len(photos) == MAX_FILES:
                raise ClientError("too_many_files", "An upload can contain at most 10,000 JPEGs.")
            photos.append(file)
            total_bytes += info.st_size
        else:
            skipped += 1
    return Source(photos, total_bytes, skipped)


@dataclass(frozen=True)
class Original:
    id: str
    path: Path
    size: int
    mtime_ns: int
    sha256: str

    @classmethod
    def capture(cls, path: Path, max_bytes: int) -> "Original":
        with open_regular(path) as file:
            before = os.fstat(file.fileno())
            if not 1 <= before.st_size <= max_bytes:
                raise ClientError("invalid_file_size", f"JPEG size is out of range: {path.name}")
            hash_value = hashlib.sha256()
            while chunk := file.read(1024 * 1024):
                hash_value.update(chunk)
            digest = hash_value.hexdigest()
            after = os.fstat(file.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise changed_file(path)
        return cls(str(uuid4()), path, before.st_size, before.st_mtime_ns, digest)

    def registration(self, folder_id: int | None) -> Json:
        return {
            "client_item_id": self.id,
            "filename": self.path.name,
            "content_type": "image/jpeg",
            "size": self.size,
            "folder_id": folder_id,
            "last_modified_ms": self.mtime_ns // 1_000_000,
            "ambiguous_sha256": self.sha256,
        }

    def check_stat(self, file: BinaryIO) -> None:
        info = os.fstat(file.fileno())
        if (info.st_size, info.st_mtime_ns) != (self.size, self.mtime_ns):
            raise changed_file(self.path)

    @contextmanager
    def verified(self) -> Iterator[BinaryIO]:
        with TemporaryFile(mode="w+b") as snapshot:
            with open_regular(self.path) as file:
                self.check_stat(file)
                digest = hashlib.sha256()
                remaining = self.size
                while remaining:
                    chunk = file.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise changed_file(self.path)
                    digest.update(chunk)
                    snapshot.write(chunk)
                    remaining -= len(chunk)
                if digest.hexdigest() != self.sha256:
                    raise changed_file(self.path)
                self.check_stat(file)
            snapshot.seek(0)
            yield snapshot


@contextmanager
def open_regular(path: Path) -> Iterator[BinaryIO]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise changed_file(path) from None
    with os.fdopen(fd, "rb") as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise changed_file(path)
        yield file


def changed_file(path: Path) -> ClientError:
    return ClientError("file_changed", f"JPEG changed or is unavailable: {path.name}")


@dataclass(frozen=True)
class Task:
    directory: Path

    @property
    def id(self) -> str:
        return self.directory.name

    @property
    def stopped(self) -> bool:
        return (self.directory / "stop").exists()

    @classmethod
    def load(cls, value: str) -> "Task":
        try:
            key = str(UUID(value))
        except ValueError:
            raise ClientError("invalid_task", "Enter the task UUID.") from None
        task = cls(data_dir() / key)
        if not (task.directory / "manifest.sqlite3").is_file():
            raise ClientError("unknown_task", "This upload task does not exist.")
        return task

    @classmethod
    def create(
        cls,
        *,
        profile: str,
        server: str,
        owner: int,
        event: int,
        folder: int | None,
        originals: Sequence[Original],
    ) -> "Task":
        task = cls(data_dir() / str(uuid4()))
        task.directory.mkdir(mode=0o700)
        with task.db() as db:
            db.executescript("""
                CREATE TABLE task (
                    profile TEXT, server TEXT, owner INTEGER, event INTEGER, folder INTEGER,
                    batch TEXT, state TEXT, error TEXT
                );
                CREATE TABLE files (
                    id TEXT PRIMARY KEY, path TEXT, size INTEGER, mtime_ns INTEGER,
                    sha256 TEXT, item TEXT, state TEXT, error TEXT
                );
            """)
            db.execute(
                "INSERT INTO task VALUES (?, ?, ?, ?, ?, NULL, 'queued', NULL)",
                (profile, server, owner, event, folder),
            )
            db.executemany(
                "INSERT INTO files VALUES (?, ?, ?, ?, ?, NULL, 'unresolved', NULL)",
                ((o.id, str(o.path), o.size, o.mtime_ns, o.sha256) for o in originals),
            )
        return task

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.directory / "manifest.sqlite3")) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                yield connection

    def metadata(self) -> sqlite3.Row:
        with self.db() as db:
            return db.execute("SELECT * FROM task").fetchone()

    def files(self) -> list[sqlite3.Row]:
        with self.db() as db:
            return db.execute("SELECT * FROM files ORDER BY rowid").fetchall()

    def record_batch(self, batch: str) -> None:
        with self.db() as db:
            db.execute("UPDATE task SET batch = ?", (batch,))

    def record_items(self, items: list[Json]) -> None:
        with self.db() as db:
            db.executemany(
                """UPDATE files SET item = ?, state = CASE
                    WHEN ? = 'uploaded' THEN 'confirmed' ELSE state END,
                    error = CASE WHEN ? = 'uploaded' THEN NULL ELSE error END WHERE id = ?""",
                (
                    (item["id"], item["status"], item["status"], item["client_item_id"])
                    for item in items
                ),
            )

    def record_file(self, original_id: str, state: str, error: str | None = None) -> None:
        with self.db() as db:
            db.execute(
                "UPDATE files SET state = ?, error = ? WHERE id = ?", (state, error, original_id)
            )

    def record_state(self, state: str, error: str | None = None) -> None:
        with self.db() as db:
            db.execute("UPDATE task SET state = ?, error = ?", (state, error))

    @contextmanager
    def lock(self) -> Iterator[int | None]:
        fd = os.open(self.directory / "lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield None
            else:
                yield fd
        finally:
            os.close(fd)

    def launch(self) -> None:
        with self.lock() as fd:
            if fd is None:
                raise ClientError("task_running", "This upload task is already running.")
            if self.metadata()["state"] == "completed":
                raise ClientError("task_completed", "All originals have already been accepted.")
            (self.directory / "stop").unlink(missing_ok=True)
            self.record_state("running")
            try:
                subprocess.Popen(
                    [sys.executable, "-m", "findme_client", "_run", self.id, str(fd)],
                    pass_fds=(fd,),
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                self.record_state("interrupted", "worker_start_failed")
                raise ClientError(
                    "worker_start_failed", "Cannot start the upload process."
                ) from None

    def stop(self) -> None:
        with self.lock() as fd:
            if fd is not None:
                raise ClientError("task_inactive", "This upload task is inactive.")
            (self.directory / "stop").touch(mode=0o600, exist_ok=True)

    def summary(self) -> Json:
        with self.lock() as fd:
            running = fd is None
        with self.db() as db:
            metadata = db.execute("SELECT state, batch, error FROM task").fetchone()
            counts = dict(db.execute("SELECT state, count(*) FROM files GROUP BY state").fetchall())
            errors = dict(
                db.execute(
                    "SELECT error, count(*) FROM files WHERE error IS NOT NULL GROUP BY error"
                ).fetchall()
            )
            failures = db.execute(
                "SELECT path, error AS error_code FROM files WHERE error IS NOT NULL "
                "ORDER BY rowid LIMIT 100"
            ).fetchall()
        state = metadata["state"]
        if state == "running":
            state = ("stopping" if self.stopped else "running") if running else "interrupted"
        return {
            "task_id": self.id,
            "state": state,
            "batch_id": metadata["batch"],
            "error_code": metadata["error"],
            "file_errors": errors,
            "failed_files": [dict(row) for row in failures],
            "failed_files_truncated": sum(errors.values()) > len(failures),
            "originals": {key: counts.get(key, 0) for key in ("confirmed", "failed", "unresolved")},
        }
