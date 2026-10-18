"""Work operations, independent of Journey persistence and Git synchronization."""

from copy import deepcopy
from typing import Any, Mapping

from .model import (DATE_FIELDS, STATUSES, STATUS_DATE_FIELDS, local_calendar_date,
                    new_record_id, parse_date, utc_timestamp)
from .service import CatalogService, InvalidRequest, InvalidTransition, TRANSITIONS, TRANSITION_DATE
from .work_storage import WorkStore


class WorkService:
    def __init__(self, store: WorkStore) -> None:
        self.store = store

    @staticmethod
    def _public(stored):
        return {"entry": {"record": stored.value, "etag": stored.etag}}

    def entry(self, record_id: str) -> dict[str, Any]:
        return self._public(self.store.get(record_id))

    def catalog(self, query: dict[str, str]) -> dict[str, Any]:
        return self.store.catalog(query) | {"statuses": list(STATUSES)}

    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._create(payload, legacy=False)

    def create_legacy(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Explicit historical entry: select its status without inventing dates."""
        return self._create(payload, legacy=True)

    def _create(self, payload: Mapping[str, Any], *, legacy: bool) -> dict[str, Any]:
        fields = {"work_title", "unit_title", "kind", "category_id", "release_year", "notes"}
        required = {"work_title", "kind", "category_id"}
        if legacy:
            fields |= {"status", "priority"}
            required.add("status")
        CatalogService._require_payload(payload,
            fields, required=required)
        now = utc_timestamp()
        title = CatalogService._required_title(payload["work_title"], "work_title", 300)
        record = {
            "id": new_record_id(title, now), "work_title": title,
            "unit_title": CatalogService._optional_title(payload.get("unit_title"), "unit_title", 200),
            "kind": payload["kind"], "category_id": payload["category_id"],
            "status": payload["status"] if legacy else "investigate",
            "priority": payload.get("priority") if legacy else None,
            "release_year": CatalogService._release_year(payload.get("release_year")),
            "notes": CatalogService._notes(payload.get("notes")),
            "dates": dict.fromkeys(DATE_FIELDS),
            "created_at": now, "updated_at": now, "deleted_at": None, "legacy_history": legacy,
        }
        if not legacy:
            record["dates"]["investigated_on"] = local_calendar_date()
        return self._public(self.store.create(record))

    def update(self, record_id: str, payload: Mapping[str, Any], *, expected_etag: str) -> dict[str, Any]:
        CatalogService._require_payload(payload,
            {"work_title", "unit_title", "kind", "category_id", "release_year", "notes", "priority"}, required=set())

        def operation(record):
            CatalogService._require_active(record)
            before = deepcopy(record)
            for field, maximum, optional in (("work_title", 300, False), ("unit_title", 200, True)):
                if field in payload:
                    cleaner = CatalogService._optional_title if optional else CatalogService._required_title
                    record[field] = cleaner(payload[field], field, maximum)
            for field in ("kind", "category_id", "priority"):
                if field in payload:
                    record[field] = payload[field]
            if "release_year" in payload:
                record["release_year"] = CatalogService._release_year(payload["release_year"])
            if "notes" in payload:
                record["notes"] = CatalogService._notes(payload["notes"])
            if record != before:
                record["updated_at"] = utc_timestamp(after=record["updated_at"])
            return record

        return self._public(self.store.mutate(record_id, expected_etag, operation))

    def transition(self, record_id: str, payload: Mapping[str, Any], *, expected_etag: str) -> dict[str, Any]:
        CatalogService._require_payload(payload, {"status", "on", "priority", "notes", "adjust_prior_dates"}, required={"status"})
        target = payload["status"]
        if target not in STATUSES:
            raise InvalidRequest("Work status is invalid.")
        adjust = payload.get("adjust_prior_dates", False)
        if not isinstance(adjust, bool):
            raise InvalidRequest("adjust_prior_dates must be true or false.")
        on = payload.get("on") or local_calendar_date()
        parsed = parse_date(on)

        def operation(record):
            CatalogService._require_active(record)
            source = record["status"]
            if target not in TRANSITIONS[source]:
                raise InvalidTransition(f"Cannot move {source} directly to {target}.")
            if source == "investigate" and payload.get("priority") not in ("high", "low"):
                raise InvalidRequest("Choose High or Low priority to move this work to Planned.")
            if "priority" in payload:
                record["priority"] = payload["priority"]
            if target != "paused" and not (source == "paused" and target == "ongoing"):
                prior = STATUS_DATE_FIELDS[target][:-1]
                fields = [f for f in prior if record["dates"][f] is None or parse_date(record["dates"][f]) > parsed]
                if fields and not adjust:
                    raise InvalidTransition("The selected date requires earlier lifecycle dates to be aligned first.")
                for field in fields:
                    record["dates"][field] = on
                record["dates"][TRANSITION_DATE[target]] = on
            record["status"] = target
            if "notes" in payload:
                record["notes"] = CatalogService._notes(payload["notes"])
            record["updated_at"] = utc_timestamp(after=record["updated_at"])
            return record

        return self._public(self.store.mutate(record_id, expected_etag, operation))

    def correct_history(self, record_id: str, payload: Mapping[str, Any], *, expected_etag: str) -> dict[str, Any]:
        # Dates can be corrected, but this endpoint cannot bypass sequential statuses
        # or turn an ordinary record into a legacy import.
        CatalogService._require_payload(payload, {"dates"}, required={"dates"})

        def operation(record):
            CatalogService._require_active(record)
            if record["dates"] != payload["dates"]:
                record["dates"] = deepcopy(payload["dates"])
                record["updated_at"] = utc_timestamp(after=record["updated_at"])
            return record

        return self._public(self.store.mutate(record_id, expected_etag, operation))

    def delete(self, record_id: str, *, expected_etag: str) -> dict[str, Any]:
        def operation(record):
            CatalogService._require_active(record)
            now = utc_timestamp(after=record["updated_at"])
            return record | {"updated_at": now, "deleted_at": now}
        return self._public(self.store.mutate(record_id, expected_etag, operation))

    def restore(self, record_id: str, *, expected_etag: str) -> dict[str, Any]:
        def operation(record):
            if record["deleted_at"] is None:
                raise InvalidRequest("This work is not in Trash.")
            return record | {"updated_at": utc_timestamp(after=record["updated_at"]), "deleted_at": None}
        return self._public(self.store.mutate(record_id, expected_etag, operation))
