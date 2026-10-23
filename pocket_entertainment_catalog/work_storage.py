"""Local SQLite storage. Each request owns a connection and a short transaction."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import logging
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from typing import Any, Callable, Iterator

from .model import DATE_FIELDS, new_record_id, record_etag, utc_timestamp
from .service import InvalidRequest
from .storage import CatalogBusy, CatalogConflict, CatalogNotFound, StorageError, StoredRecord
from .work_model import UNCATEGORIZED, category_name, validate_work


# Keep the version 1 base schema so fresh databases and existing installations
# follow the same transactional migration path.
SCHEMA = """
CREATE TABLE metadata (revision INTEGER NOT NULL);
INSERT INTO metadata VALUES (0);
CREATE TABLE categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE
);
CREATE TRIGGER protect_default_delete BEFORE DELETE ON categories
WHEN OLD.id = 'uncategorized' BEGIN SELECT RAISE(ABORT, 'Uncategorized is reserved.'); END;
CREATE TRIGGER protect_default_update BEFORE UPDATE ON categories
WHEN OLD.id = 'uncategorized' BEGIN SELECT RAISE(ABORT, 'Uncategorized is reserved.'); END;
CREATE TABLE works (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    work_title TEXT NOT NULL CHECK(length(trim(work_title)) BETWEEN 1 AND 300),
    unit_title TEXT CHECK(unit_title IS NULL OR length(trim(unit_title)) BETWEEN 1 AND 200),
    kind TEXT NOT NULL CHECK(kind IN ('project','task')),
    category_id TEXT NOT NULL REFERENCES categories(id),
    status TEXT NOT NULL CHECK(status IN ('investigate','planned','ongoing','paused','completed','discontinued')),
    priority TEXT,
    release_year INTEGER CHECK(release_year IS NULL OR (typeof(release_year)='integer' AND release_year BETWEEN 1000 AND 9999)),
    notes TEXT CHECK(notes IS NULL OR length(notes) <= 20000),
    investigated_on TEXT, planned_on TEXT, started_on TEXT, ended_on TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL CHECK(updated_at >= created_at),
    deleted_at TEXT CHECK(deleted_at IS NULL OR (deleted_at >= created_at AND deleted_at <= updated_at)),
    legacy_history INTEGER NOT NULL DEFAULT 0 CHECK(legacy_history IN (0,1)),
    CHECK((status = 'investigate' AND priority IS NULL) OR
          (status <> 'investigate' AND priority IS NOT NULL AND priority IN ('high','low'))),
    CHECK((legacy_history = 1 AND investigated_on IS NULL AND planned_on IS NULL AND started_on IS NULL AND ended_on IS NULL) OR
          (investigated_on IS NOT NULL AND
           (status = 'investigate' OR planned_on IS NOT NULL) AND
           (status IN ('investigate','planned') OR started_on IS NOT NULL) AND
           (status NOT IN ('completed','discontinued') OR ended_on IS NOT NULL))),
    CHECK(status <> 'investigate' OR (planned_on IS NULL AND started_on IS NULL AND ended_on IS NULL)),
    CHECK(status <> 'planned' OR (started_on IS NULL AND ended_on IS NULL)),
    CHECK(status IN ('completed','discontinued') OR ended_on IS NULL),
    CHECK(investigated_on IS NULL OR planned_on IS NULL OR investigated_on <= planned_on),
    CHECK(planned_on IS NULL OR started_on IS NULL OR planned_on <= started_on),
    CHECK(started_on IS NULL OR ended_on IS NULL OR started_on <= ended_on)
);
CREATE INDEX works_status ON works(deleted_at, status);
CREATE INDEX works_category ON works(category_id);
CREATE INDEX works_kind ON works(kind);
CREATE INDEX works_priority ON works(priority);
CREATE INDEX works_updated ON works(updated_at);
PRAGMA user_version = 1;
"""
WORK_COLUMNS = (
    "id",
    "work_title",
    "unit_title",
    "kind",
    "source_ongoing",
    "category_id",
    "status",
    "priority",
    "release_year",
    "notes",
    *DATE_FIELDS,
    "created_at",
    "updated_at",
    "deleted_at",
    "legacy_history",
)


class WorkStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._ready = False
        self._backup_lock = threading.Lock()
        if self.path.is_symlink():
            raise StorageError("Work database must not be a symbolic link.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection(write=True) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                # executescript commits implicitly; include the schema and seed in
                # one explicit transaction so initialization is never half-installed.
                triggers = "".join(
                    f"CREATE TRIGGER revision_{table}_{action} AFTER {action} ON {table} "
                    "BEGIN UPDATE metadata SET revision = revision + 1; END;\n"
                    for table in ("works", "categories")
                    for action in ("INSERT", "UPDATE", "DELETE")
                )
                db.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + SCHEMA
                    + triggers
                    + "INSERT INTO categories VALUES ('uncategorized','Uncategorized','uncategorized');"
                )
                version = 1
            if version == 1:
                # execute (not executescript) keeps DDL, backfill, and version in
                # the open transaction. Preserve every other field, including
                # timestamps; revision triggers still observe the explicit update.
                db.execute(
                    "ALTER TABLE works ADD COLUMN source_ongoing INTEGER NOT NULL DEFAULT 0 "
                    "CHECK(source_ongoing IN (0,1) AND (kind = 'project' OR source_ongoing = 0))"
                )
                db.execute("UPDATE works SET source_ongoing = 0")
                db.execute("PRAGMA user_version = 2")
            elif version != 2:
                raise StorageError("Unsupported Work database version.")
        self._ready = True

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.create_function("fold", 1, lambda s: (s or "").casefold(), deterministic=True)
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
            if write and self._ready:
                # A backup error must not report a committed mutation as a failed
                # save. The primary database remains authoritative.
                try:
                    self._daily_backup(db)
                except (OSError, sqlite3.Error):
                    logging.getLogger(__name__).exception("Work saved, but its daily backup failed")
        except sqlite3.IntegrityError as error:
            raise InvalidRequest("The change violates a Work data constraint.") from error
        except sqlite3.OperationalError as error:
            if "locked" in str(error).lower() or "busy" in str(error).lower():
                raise CatalogBusy("Another Work writer is active. Try again shortly.") from error
            raise StorageError("The Work database could not be accessed.") from error
        except sqlite3.DatabaseError as error:
            raise StorageError("The Work database could not be accessed safely.") from error
        finally:
            if db is not None:
                db.close()  # Rolls back an unfinished transaction on every failure.

    @staticmethod
    def revision(db: sqlite3.Connection) -> str:
        return str(db.execute("SELECT revision FROM metadata").fetchone()[0])

    @staticmethod
    def _stored(row: sqlite3.Row) -> StoredRecord:
        value = {column: row[column] for column in WORK_COLUMNS if column not in DATE_FIELDS}
        value["legacy_history"] = bool(value["legacy_history"])
        value["source_ongoing"] = bool(value["source_ongoing"])
        value["dates"] = {field: row[field] for field in DATE_FIELDS}
        return StoredRecord(value, record_etag(value))

    def _get(self, db: sqlite3.Connection, record_id: str) -> StoredRecord:
        row = db.execute("SELECT * FROM works WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            raise CatalogNotFound("This work does not exist.")
        return self._stored(row)

    def get(self, record_id: str) -> StoredRecord:
        with self.connection() as db:
            return self._get(db, record_id)

    @staticmethod
    def _values(record: dict[str, Any]) -> list[Any]:
        return [record["dates"][c] if c in DATE_FIELDS else record[c] for c in WORK_COLUMNS]

    @staticmethod
    def _require_category(db: sqlite3.Connection, category_id: str) -> None:
        if db.execute("SELECT 1 FROM categories WHERE id = ?", (category_id,)).fetchone() is None:
            raise InvalidRequest("Choose an existing category. Manage categories separately.")

    def create(self, record: dict[str, Any]) -> StoredRecord:
        record = validate_work(record)
        with self.connection(write=True) as db:
            self._require_category(db, record["category_id"])
            db.execute(
                f"INSERT INTO works ({','.join(WORK_COLUMNS)}) VALUES ({','.join('?' for _ in WORK_COLUMNS)})",
                self._values(record),
            )
            return self._get(db, record["id"])

    def mutate(
        self,
        record_id: str,
        expected_etag: str,
        operation: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> StoredRecord:
        with self.connection(write=True) as db:
            current = self._get(db, record_id)
            if current.etag != expected_etag:
                raise CatalogConflict(
                    "This work changed after it was opened. Reload before saving."
                )
            updated = validate_work(operation(deepcopy(current.value)))
            self._require_category(db, updated["category_id"])
            if updated != current.value:
                db.execute(
                    f"UPDATE works SET {','.join(c + ' = ?' for c in WORK_COLUMNS)} WHERE id = ?",
                    [*self._values(updated), record_id],
                )
            return self._get(db, record_id)

    @staticmethod
    def categories(db: sqlite3.Connection) -> list[dict[str, Any]]:
        return [
            dict(row) | {"etag": record_etag({"id": row["id"], "name": row["name"]})}
            for row in db.execute(
                "SELECT c.id, c.name, COUNT(w.id) AS work_count FROM categories c "
                "LEFT JOIN works w ON w.category_id = c.id GROUP BY c.id "
                "ORDER BY c.id <> 'uncategorized', c.name_key, c.id"
            )
        ]

    def change_category(
        self,
        name: Any = None,
        *,
        category_id: str | None = None,
        expected_etag: str | None = None,
        delete: bool = False,
    ) -> dict[str, Any]:
        normalized = None if delete else category_name(name)
        with self.connection(write=True) as db:
            if category_id is not None:
                row = db.execute(
                    "SELECT id,name FROM categories WHERE id = ?", (category_id,)
                ).fetchone()
                if row is None:
                    raise CatalogNotFound("This category does not exist.")
                if category_id == UNCATEGORIZED:
                    raise InvalidRequest("Uncategorized cannot be renamed or deleted.")
                if record_etag(dict(row)) != expected_etag:
                    raise CatalogConflict("This category changed. Reopen Categories before saving.")
            if delete:
                for row in db.execute(
                    "SELECT id,updated_at FROM works WHERE category_id = ?", (category_id,)
                ).fetchall():
                    db.execute(
                        "UPDATE works SET category_id = ?, updated_at = ? WHERE id = ?",
                        (UNCATEGORIZED, utc_timestamp(after=row["updated_at"]), row["id"]),
                    )
                db.execute("DELETE FROM categories WHERE id = ?", (category_id,))
            else:
                display, key = normalized
                duplicate = db.execute(
                    "SELECT id FROM categories WHERE name_key = ?", (key,)
                ).fetchone()
                if duplicate is not None and duplicate["id"] != category_id:
                    raise InvalidRequest("A category with that name already exists.")
                if category_id is None:
                    category_id = new_record_id(display, utc_timestamp())
                    db.execute("INSERT INTO categories VALUES (?,?,?)", (category_id, display, key))
                else:
                    db.execute(
                        "UPDATE categories SET name = ?, name_key = ? WHERE id = ?",
                        (display, key, category_id),
                    )
            return {"categories": self.categories(db), "catalog_revision": self.revision(db)}

    def catalog(self, query: dict[str, str]) -> dict[str, Any]:
        allowed = {
            "status",
            "category",
            "kind",
            "priority",
            "source_ongoing",
            "search",
            "sort",
            "direction",
            "page",
            "page_size",
        }
        if set(query) - allowed:
            raise InvalidRequest("Unknown Work filter.")
        status = query.get("status", "all")
        from .model import STATUSES

        if status not in (*STATUSES, "all", "trash"):
            raise InvalidRequest("Invalid status filter.")
        clauses = ["w.deleted_at IS NOT NULL" if status == "trash" else "w.deleted_at IS NULL"]
        args: list[Any] = []
        if status not in {"all", "trash"}:
            clauses.append("w.status = ?")
            args.append(status)
        for key, column, values in (
            ("category", "category_id", None),
            ("kind", "kind", ("project", "task")),
            ("priority", "priority", ("high", "low", "none")),
        ):
            item = query.get(key, "all")
            if item == "all":
                continue
            if values is not None and item not in values:
                raise InvalidRequest(f"Invalid {key} filter.")
            if key == "priority" and item == "none":
                clauses.append("w.priority IS NULL")
            else:
                clauses.append(f"w.{column} = ?")
                args.append(item)
        source_ongoing = query.get("source_ongoing", "all")
        if source_ongoing not in {"all", "true", "false"}:
            raise InvalidRequest("Invalid source_ongoing filter.")
        if source_ongoing != "all":
            clauses.extend(["w.kind = 'project'", "w.source_ongoing = ?"])
            args.append(1 if source_ongoing == "true" else 0)
        search = query.get("search", "").strip()
        if len(search) > 1000:
            raise InvalidRequest("Search is too long.")
        if search:
            clauses.append(
                "instr(fold(w.work_title || ' ' || coalesce(w.unit_title,'') || ' ' || "
                "coalesce(w.notes,'') || ' ' || c.name || ' ' || w.kind || ' ' || "
                "coalesce(w.release_year,'')), ?) > 0"
            )
            args.append(search.casefold())
        sort = query.get("sort", "journey")
        sorts = {
            "journey": "w.sequence",
            "title": "fold(w.work_title)",
            "updated": "w.updated_at",
            "priority": "CASE w.priority WHEN 'high' THEN 2 WHEN 'low' THEN 1 ELSE 0 END",
        }
        direction = query.get("direction", "desc")
        if sort not in sorts or direction not in {"asc", "desc"}:
            raise InvalidRequest("Invalid Work sort.")
        try:
            page, page_size = int(query.get("page", "1")), int(query.get("page_size", "48"))
        except ValueError as error:
            raise InvalidRequest("Invalid page.") from error
        if page < 1 or not 1 <= page_size <= 100:
            raise InvalidRequest("Invalid page or page size.")
        where = " AND ".join(clauses)
        with self.connection() as db:
            total = db.execute(
                "SELECT COUNT(*) FROM works w JOIN categories c ON c.id=w.category_id WHERE "
                + where,
                args,
            ).fetchone()[0]
            pages = max(1, math.ceil(total / page_size))
            page = min(page, pages)
            order = sorts[sort] + " " + direction
            if sort == "title":
                order += ", fold(coalesce(w.unit_title,'')) " + direction
            rows = db.execute(
                "SELECT w.* FROM works w JOIN categories c ON c.id=w.category_id WHERE "
                + where
                + " ORDER BY "
                + order
                + ", w.sequence "
                + direction
                + " LIMIT ? OFFSET ?",
                [*args, page_size, (page - 1) * page_size],
            ).fetchall()
            counts = {
                row["bucket"]: row["n"]
                for row in db.execute(
                    "SELECT CASE WHEN deleted_at IS NOT NULL THEN 'trash' ELSE status END AS bucket, "
                    "COUNT(*) AS n FROM works GROUP BY bucket"
                )
            }
            counts["all"] = sum(v for k, v in counts.items() if k != "trash")
            return {
                "entries": [{"record": s.value, "etag": s.etag} for s in map(self._stored, rows)],
                "categories": self.categories(db),
                "counts": counts,
                "total": total,
                "page": page,
                "pages": pages,
                "page_size": page_size,
                "catalog_revision": self.revision(db),
            }

    def state(self) -> dict[str, Any]:
        with self.connection() as db:
            return {"catalog_revision": self.revision(db)}

    def export(self) -> dict[str, Any]:
        with self.connection() as db:
            return {
                "schema_version": 2,
                "categories": [
                    dict(row)
                    for row in db.execute("SELECT id,name FROM categories ORDER BY name_key")
                ],
                "works": [
                    self._stored(row).value
                    for row in db.execute("SELECT * FROM works ORDER BY sequence")
                ],
            }

    def backup(self, destination: Path) -> None:
        """SQLite's backup API also produces a consistent copy while the app runs."""
        with self.connection() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()

    def _daily_backup(self, source: sqlite3.Connection) -> None:
        with self._backup_lock:
            directory = self.path.parent / "work-backups"
            day = datetime.now().astimezone().date().isoformat()
            destination = directory / f"{self.path.stem}-{day}.sqlite3"
            if destination.exists():
                return
            directory.mkdir(parents=True, exist_ok=True)
            descriptor, filename = tempfile.mkstemp(prefix=".backup-", dir=directory)
            os.close(descriptor)
            temporary = Path(filename)
            try:
                target = sqlite3.connect(temporary)
                try:
                    source.backup(target)
                finally:
                    target.close()
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            snapshots = sorted(
                p for p in directory.glob("*.sqlite3") if p.name.startswith(self.path.stem + "-")
            )
            for old in snapshots[:-7]:
                old.unlink()
