"""Catalog application operations independent of the HTTP transport."""

from __future__ import annotations

from copy import deepcopy
import re
import threading
from typing import Any, Mapping

from .git_sync import GitSync
from .lease import EditorLease
from .model import (
    DATE_FIELDS,
    MEDIA_TYPES,
    STATUSES,
    STATUS_DATE_FIELDS,
    TERMINAL_STATUSES,
    ModelError,
    local_calendar_date,
    new_record_id,
    parse_date,
    utc_timestamp,
    validate_record,
)
from .storage import CatalogConflict, CatalogStore, StoredRecord


class ServiceError(RuntimeError):
    code = "service_error"


class InvalidRequest(ServiceError):
    code = "invalid_request"


class DeletedEntry(ServiceError):
    code = "entry_deleted"


class InvalidTransition(ServiceError):
    code = "invalid_transition"


TRANSITIONS = {
    "investigate": frozenset({"planned"}),
    "planned": frozenset({"ongoing"}),
    "ongoing": frozenset({"paused", "completed", "discontinued"}),
    "paused": frozenset({"ongoing"}),
    "completed": frozenset(),
    "discontinued": frozenset(),
}
TRANSITION_DATE = {
    "planned": "planned_on",
    "ongoing": "started_on",
    "completed": "ended_on",
    "discontinued": "ended_on",
}


class CatalogService:
    def __init__(
        self,
        store: CatalogStore,
        editor_lease: EditorLease,
        git_sync: GitSync,
    ) -> None:
        self.store = store
        self.editor_lease = editor_lease
        self.git_sync = git_sync
        self._mutation_lock = threading.RLock()

    def catalog(self, *, include_deleted: bool = False) -> dict[str, Any]:
        records = self.store.list_records(include_deleted=include_deleted)
        return {
            "entries": [self._public_record(record) for record in records],
            "media_types": list(MEDIA_TYPES),
            "statuses": list(STATUSES),
            "catalog_revision": self.store.catalog_digest,
            "editor": self._lease_payload(),
            "sync": self.git_sync.snapshot().as_dict(),
        }

    def entry(self, record_id: str) -> dict[str, Any]:
        return {"entry": self._public_record(self.store.get_record(record_id))}

    def catalog_state(self) -> dict[str, Any]:
        """Return lightweight state so readers can notice changes without reloading data."""

        return {
            "catalog_revision": self.store.catalog_digest,
            "editor": self._lease_payload(),
            "sync": self.git_sync.snapshot().as_dict(),
        }

    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"work_title", "unit_title", "media_type", "release_year", "notes"}
        self._require_payload(payload, allowed, required={"work_title", "media_type"})
        created_at = utc_timestamp()
        work_title = self._required_title(payload.get("work_title"), "work_title", 300)
        record = {
            "id": new_record_id(work_title, created_at),
            "work_title": work_title,
            "unit_title": self._optional_title(payload.get("unit_title"), "unit_title", 200),
            "media_type": self._media_type(payload.get("media_type")),
            "status": "investigate",
            "rating": None,
            "release_year": self._release_year(payload.get("release_year")),
            "dates": {
                "investigated_on": local_calendar_date(),
                "planned_on": None,
                "started_on": None,
                "ended_on": None,
            },
            "notes": self._notes(payload.get("notes")),
            "external_ids": {},
            "created_at": created_at,
            "updated_at": created_at,
            "deleted_at": None,
        }
        with self._mutation_lock:
            stored = self.store.create(validate_record(record))
            sync = self.git_sync.record_change(f"catalog: add {self._display(record)}")
        return {"entry": self._public_record(stored), "sync": sync.as_dict()}

    def update(
        self,
        record_id: str,
        payload: Mapping[str, Any],
        *,
        expected_etag: str,
    ) -> dict[str, Any]:
        allowed = {"work_title", "unit_title", "media_type", "release_year", "notes", "rating"}
        self._require_payload(payload, allowed, required=set())

        def operation(record: dict[str, Any]) -> dict[str, Any]:
            self._require_active(record)
            before = deepcopy(record)
            if "work_title" in payload:
                record["work_title"] = self._required_title(payload["work_title"], "work_title", 300)
            if "unit_title" in payload:
                record["unit_title"] = self._optional_title(payload["unit_title"], "unit_title", 200)
            if "media_type" in payload:
                record["media_type"] = self._media_type(payload["media_type"])
            if "release_year" in payload:
                record["release_year"] = self._release_year(payload["release_year"])
            if "notes" in payload:
                record["notes"] = self._notes(payload["notes"])
            if "rating" in payload:
                record["rating"] = self._rating(payload["rating"], record["status"])
            if record != before:
                record["updated_at"] = utc_timestamp(after=record["updated_at"])
            return validate_record(record)

        with self._mutation_lock:
            stored = self.store.mutate(record_id, expected_etag=expected_etag, operation=operation)
            sync = self.git_sync.record_change(f"catalog: update {self._display(stored.value)}")
        return {"entry": self._public_record(stored), "sync": sync.as_dict()}

    def transition(
        self,
        record_id: str,
        payload: Mapping[str, Any],
        *,
        expected_etag: str,
    ) -> dict[str, Any]:
        self._require_payload(
            payload,
            {"status", "on", "rating", "notes", "adjust_prior_dates"},
            required={"status"},
        )
        target = payload.get("status")
        if target not in STATUSES:
            raise InvalidRequest("status is invalid.")
        adjust_prior_dates = payload.get("adjust_prior_dates", False)
        if not isinstance(adjust_prior_dates, bool):
            raise InvalidRequest("adjust_prior_dates must be true or false.")
        transition_on = payload.get("on") or local_calendar_date()
        try:
            parse_date(transition_on)
        except ModelError as error:
            raise InvalidRequest(str(error)) from error

        def operation(record: dict[str, Any]) -> dict[str, Any]:
            self._require_active(record)
            source = record["status"]
            if target not in TRANSITIONS[source]:
                raise InvalidTransition(
                    f"Cannot move {source} directly to {target}."
                )
            record["status"] = target
            is_resume = source == "paused" and target == "ongoing"
            if target != "paused" and not is_resume:
                transition_field = TRANSITION_DATE[target]
                transition_date = parse_date(transition_on)
                prior_fields = STATUS_DATE_FIELDS[target][:-1]
                fields_to_adjust = [
                    field
                    for field in prior_fields
                    if record["dates"][field] is None
                    or parse_date(record["dates"][field]) > transition_date
                ]
                if fields_to_adjust and not adjust_prior_dates:
                    raise InvalidTransition(
                        "The selected date requires earlier lifecycle dates to be aligned first."
                    )
                for field in fields_to_adjust:
                    record["dates"][field] = transition_on
                record["dates"][transition_field] = transition_on
            record["rating"] = self._rating(payload.get("rating"), target)
            if "notes" in payload:
                record["notes"] = self._notes(payload.get("notes"))
            record["updated_at"] = utc_timestamp(after=record["updated_at"])
            return validate_record(record)

        with self._mutation_lock:
            stored = self.store.mutate(record_id, expected_etag=expected_etag, operation=operation)
            sync = self.git_sync.record_change(
                f"catalog: {target} {self._display(stored.value)}"
            )
        return {"entry": self._public_record(stored), "sync": sync.as_dict()}

    def correct_history(
        self,
        record_id: str,
        payload: Mapping[str, Any],
        *,
        expected_etag: str,
    ) -> dict[str, Any]:
        self._require_payload(
            payload,
            {"status", "dates", "rating"},
            required={"status", "dates", "rating"},
        )
        status = payload.get("status")
        dates = payload.get("dates")
        if status not in STATUSES or not isinstance(dates, dict) or set(dates) != set(DATE_FIELDS):
            raise InvalidRequest("Correction status or lifecycle dates are invalid.")

        def operation(record: dict[str, Any]) -> dict[str, Any]:
            self._require_active(record)
            record["status"] = status
            record["dates"] = deepcopy(dates)
            record["rating"] = self._rating(payload.get("rating"), status)
            record["updated_at"] = utc_timestamp(after=record["updated_at"])
            return validate_record(record)

        with self._mutation_lock:
            stored = self.store.mutate(record_id, expected_etag=expected_etag, operation=operation)
            sync = self.git_sync.record_change(
                f"catalog: correct history for {self._display(stored.value)}"
            )
        return {"entry": self._public_record(stored), "sync": sync.as_dict()}

    def delete(self, record_id: str, *, expected_etag: str) -> dict[str, Any]:
        def operation(record: dict[str, Any]) -> dict[str, Any]:
            self._require_active(record)
            timestamp = utc_timestamp(after=record["updated_at"])
            record["deleted_at"] = timestamp
            record["updated_at"] = timestamp
            return validate_record(record)

        with self._mutation_lock:
            stored = self.store.mutate(record_id, expected_etag=expected_etag, operation=operation)
            sync = self.git_sync.record_change(f"catalog: delete {self._display(stored.value)}")
        return {"entry": self._public_record(stored), "sync": sync.as_dict()}

    def restore(self, record_id: str, *, expected_etag: str) -> dict[str, Any]:
        def operation(record: dict[str, Any]) -> dict[str, Any]:
            if record["deleted_at"] is None:
                raise InvalidRequest("The catalog entry is not deleted.")
            record["deleted_at"] = None
            record["updated_at"] = utc_timestamp(after=record["updated_at"])
            return validate_record(record)

        with self._mutation_lock:
            stored = self.store.mutate(record_id, expected_etag=expected_etag, operation=operation)
            sync = self.git_sync.record_change(f"catalog: restore {self._display(stored.value)}")
        return {"entry": self._public_record(stored), "sync": sync.as_dict()}

    def retry_sync(self) -> dict[str, Any]:
        return {"sync": self.git_sync.retry().as_dict()}

    def _lease_payload(self) -> dict[str, Any]:
        snapshot = self.editor_lease.snapshot()
        return {"claimed": snapshot.claimed, "expires_at": snapshot.expires_at}

    @staticmethod
    def _public_record(record: StoredRecord) -> dict[str, Any]:
        return {"record": record.value, "etag": record.etag}

    @staticmethod
    def _require_active(record: Mapping[str, Any]) -> None:
        if record["deleted_at"] is not None:
            raise DeletedEntry("Restore this entry before editing it.")

    @staticmethod
    def _require_payload(
        payload: Mapping[str, Any], allowed: set[str], *, required: set[str]
    ) -> None:
        if not isinstance(payload, Mapping):
            raise InvalidRequest("Request body must be an object.")
        extra = set(payload) - allowed
        missing = required - set(payload)
        if extra or missing:
            raise InvalidRequest(
                f"Request fields are invalid (missing={sorted(missing)}, extra={sorted(extra)})."
            )

    @staticmethod
    def _required_title(value: Any, field: str, maximum: int) -> str:
        if not isinstance(value, str):
            raise InvalidRequest(f"{field} must be text.")
        cleaned = re.sub(r"\s+", " ", value).strip()
        if not cleaned or len(cleaned) > maximum:
            raise InvalidRequest(f"{field} must contain 1-{maximum} characters.")
        return cleaned

    @classmethod
    def _optional_title(cls, value: Any, field: str, maximum: int) -> str | None:
        if value is None or value == "":
            return None
        return cls._required_title(value, field, maximum)

    @staticmethod
    def _media_type(value: Any) -> str:
        if value not in MEDIA_TYPES:
            raise InvalidRequest("media_type is invalid.")
        return str(value)

    @staticmethod
    def _release_year(value: Any) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            raise InvalidRequest("release_year must be a four-digit integer.")
        if isinstance(value, str) and value.isdigit():
            value = int(value)
        if not isinstance(value, int) or not 1000 <= value <= 9999:
            raise InvalidRequest("release_year must be a four-digit integer.")
        return value

    @staticmethod
    def _notes(value: Any) -> str | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str) or len(value) > 20_000:
            raise InvalidRequest("notes must contain at most 20,000 characters.")
        return value.strip() or None

    @staticmethod
    def _rating(value: Any, status: str) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, str) and value.isdigit():
            value = int(value)
        if (
            status not in TERMINAL_STATUSES
            or isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 10
        ):
            raise InvalidRequest("rating must be an integer from 1 to 10 on a terminal entry.")
        return value

    @staticmethod
    def _display(record: Mapping[str, Any]) -> str:
        unit = record.get("unit_title")
        return f"{record['work_title']} — {unit}" if unit else str(record["work_title"])
