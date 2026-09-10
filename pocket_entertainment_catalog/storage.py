"""Thread-safe JSONL persistence with conflict detection and atomic replacement."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
from typing import Any, Callable, Iterator

from .model import ModelError, record_etag, validate_catalog


if os.name == "nt":  # pragma: no cover - exercised on Windows
    import msvcrt
else:  # pragma: no cover - exercised on POSIX
    import fcntl


class StorageError(RuntimeError):
    code = "storage_error"


class CatalogNotFound(StorageError):
    code = "not_found"


class CatalogConflict(StorageError):
    code = "edit_conflict"


class CatalogBusy(StorageError):
    code = "catalog_busy"


class CatalogChangedExternally(StorageError):
    code = "catalog_changed"


@dataclass(frozen=True)
class StoredRecord:
    value: dict[str, Any]
    etag: str


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class CatalogStore:
    def __init__(self, catalog_path: str | Path) -> None:
        self.path = Path(catalog_path).resolve()
        self.lock_path = self.path.parent / ".catalog.lock"
        self._lock = threading.RLock()
        self._records: list[dict[str, Any]] = []
        self._index: dict[str, int] = {}
        self._disk_digest = ""
        self._load_initial()

    def list_records(self, *, include_deleted: bool = False) -> list[StoredRecord]:
        with self._lock:
            self._refresh_external_locked()
            return [
                StoredRecord(deepcopy(record), record_etag(record))
                for record in self._records
                if include_deleted or record["deleted_at"] is None
            ]

    def get_record(self, record_id: str, *, include_deleted: bool = True) -> StoredRecord:
        with self._lock:
            self._refresh_external_locked()
            record = self._record_locked(record_id)
            if not include_deleted and record["deleted_at"] is not None:
                raise CatalogNotFound("The catalog entry does not exist.")
            return StoredRecord(deepcopy(record), record_etag(record))

    def create(self, record: dict[str, Any]) -> StoredRecord:
        with self._lock, self._mutation_file_lock():
            self._refresh_external_locked()
            if record["id"] in self._index:
                raise CatalogConflict("A catalog entry with this ID already exists.")
            candidate = [*self._records, deepcopy(record)]
            validated = self._validate(candidate)
            self._write_locked(validated)
            created = validated[-1]
            return StoredRecord(deepcopy(created), record_etag(created))

    def mutate(
        self,
        record_id: str,
        *,
        expected_etag: str,
        operation: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> StoredRecord:
        with self._lock, self._mutation_file_lock():
            self._refresh_external_locked()
            current = self._record_locked(record_id)
            if record_etag(current) != expected_etag:
                raise CatalogConflict(
                    "This entry changed after it was opened. Reload it before saving."
                )
            updated = operation(deepcopy(current))
            if not isinstance(updated, dict):
                raise StorageError("Catalog mutation did not produce a record.")
            candidate = list(self._records)
            candidate[self._index[record_id]] = updated
            validated = self._validate(candidate)
            saved = validated[self._index[record_id]]
            if saved != current:
                self._write_locked(validated)
            return StoredRecord(deepcopy(saved), record_etag(saved))

    @property
    def catalog_digest(self) -> str:
        with self._lock:
            self._refresh_external_locked()
            return self._disk_digest

    def _load_initial(self) -> None:
        if not self.path.exists():
            raise StorageError(f"Catalog file does not exist: {self.path}")
        if self.path.is_symlink() or not self.path.is_file():
            raise StorageError("Catalog path must be a regular file, not a link.")
        with self._lock:
            payload = self._read_bytes()
            self._install_payload(payload)

    def _refresh_external_locked(self) -> None:
        payload = self._read_bytes()
        digest = _digest(payload)
        if digest == self._disk_digest:
            return
        try:
            self._install_payload(payload)
        except StorageError as error:
            raise CatalogChangedExternally(
                "The catalog changed outside the app and is no longer valid."
            ) from error

    def _read_bytes(self) -> bytes:
        try:
            if self.path.is_symlink() or not self.path.is_file():
                raise StorageError("Catalog path is no longer a safe regular file.")
            return self.path.read_bytes()
        except OSError as error:
            raise StorageError(f"Could not read the catalog: {error}") from error

    def _install_payload(self, payload: bytes) -> None:
        try:
            text = payload.decode("utf-8")
        except UnicodeError as error:
            raise StorageError("Catalog is not valid UTF-8.") from error
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                raise StorageError(f"Catalog contains a blank line at {line_number}.")
            try:
                item = json.loads(line)
            except json.JSONDecodeError as error:
                raise StorageError(f"Catalog JSON is invalid at line {line_number}.") from error
            if not isinstance(item, dict):
                raise StorageError(f"Catalog line {line_number} is not an object.")
            records.append(item)
        validated = self._validate(records)
        self._records = validated
        self._index = {record["id"]: index for index, record in enumerate(validated)}
        self._disk_digest = _digest(payload)

    @staticmethod
    def _validate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            return validate_catalog(records)
        except ModelError as error:
            raise StorageError(f"Catalog validation failed: {error}") from error

    def _record_locked(self, record_id: str) -> dict[str, Any]:
        try:
            return self._records[self._index[record_id]]
        except (KeyError, IndexError) as error:
            raise CatalogNotFound("The catalog entry does not exist.") from error

    def _write_locked(self, records: list[dict[str, Any]]) -> None:
        current_payload = self._read_bytes()
        if _digest(current_payload) != self._disk_digest:
            raise CatalogChangedExternally(
                "The catalog changed outside the app during this save. Reload and try again."
            )

        payload = "".join(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            for record in records
        ).encode("utf-8")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, self.path)
            self._fsync_parent()
        except BaseException:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        self._records = records
        self._index = {record["id"]: index for index, record in enumerate(records)}
        self._disk_digest = _digest(payload)

    def _fsync_parent(self) -> None:
        if os.name == "nt":
            return
        descriptor: int | None = None
        try:
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            os.fsync(descriptor)
        except OSError:
            # The file itself is already durable. Not every filesystem permits
            # opening a directory descriptor, so this is best-effort.
            return
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @contextmanager
    def _mutation_file_lock(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor: int | None = None
        try:
            flags = os.O_RDWR | os.O_CREAT
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.lock_path, flags, 0o600)
            information = os.fstat(descriptor)
            if not stat.S_ISREG(information.st_mode):
                raise CatalogBusy("Catalog lock is not a regular file.")
            if os.name == "nt":  # pragma: no cover - exercised on Windows
                if information.st_size == 0:
                    os.write(descriptor, b"\0")
                    os.fsync(descriptor)
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - exercised on POSIX
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        except CatalogBusy:
            raise
        except OSError as error:
            busy_errors = {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
            if hasattr(errno, "EWOULDBLOCK"):
                busy_errors.add(errno.EWOULDBLOCK)
            if error.errno in busy_errors:
                raise CatalogBusy("Another catalog writer is active.") from error
            raise StorageError(f"Could not lock the catalog: {error}") from error
        finally:
            if descriptor is not None:
                try:
                    if os.name == "nt":  # pragma: no cover - exercised on Windows
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:  # pragma: no cover - exercised on POSIX
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(descriptor)
