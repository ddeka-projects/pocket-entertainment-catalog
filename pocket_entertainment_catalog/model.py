"""Catalog schema, validation, identifiers, timestamps, and ETags."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
import secrets
import unicodedata
from typing import Any, Mapping


MEDIA_TYPES = (
    "anime",
    "animation",
    "game",
    "live_action_movie",
    "live_action_series",
    "manga",
    "visual_novel",
)
STATUSES = ("investigate", "planned", "ongoing", "completed", "discontinued")
TERMINAL_STATUSES = frozenset({"completed", "discontinued"})
DATE_FIELDS = ("investigated_on", "planned_on", "started_on", "ended_on")

_REQUIRED_FIELDS = {
    "id",
    "work_title",
    "unit_title",
    "media_type",
    "status",
    "rating",
    "release_year",
    "dates",
    "notes",
    "external_ids",
    "created_at",
    "updated_at",
    "deleted_at",
}
_OPTIONAL_FIELDS = {"import"}
_ID_PATTERN = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*--[0-9A-HJKMNP-TV-Z]{26}$"
)
_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)
_CROCKFORD_BASE32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class ModelError(ValueError):
    """Raised when a record violates the catalog data contract."""


def utc_timestamp(*, after: str | None = None) -> str:
    """Return a millisecond UTC timestamp, monotonically after a prior value."""

    instant = datetime.now(timezone.utc)
    if after is not None:
        prior = parse_timestamp(after)
        if instant <= prior:
            instant = prior + timedelta(milliseconds=1)
        elif instant.replace(microsecond=(instant.microsecond // 1000) * 1000) <= prior:
            instant = prior + timedelta(milliseconds=1)
    return instant.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def local_calendar_date() -> str:
    return datetime.now().astimezone().date().isoformat()


def parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP_PATTERN.fullmatch(value):
        raise ModelError("Timestamp must use UTC ISO 8601 with milliseconds.")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ModelError("Timestamp is not a valid calendar instant.") from error


def parse_date(value: str) -> date:
    if not isinstance(value, str):
        raise ModelError("Lifecycle date must be a YYYY-MM-DD string or null.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ModelError("Lifecycle date must use YYYY-MM-DD.") from error
    if parsed.isoformat() != value:
        raise ModelError("Lifecycle date must use YYYY-MM-DD.")
    return parsed


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).replace("&", " and ")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_value = re.sub(r"['’]", "", ascii_value.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return slug[:64].rstrip("-") or "item"


def _encode_ulid(timestamp_ms: int, entropy: bytes) -> str:
    if not 0 <= timestamp_ms < 2**48 or len(entropy) != 10:
        raise ModelError("Could not generate a valid catalog identifier.")
    value = (timestamp_ms << 80) | int.from_bytes(entropy, "big")
    encoded = ["0"] * 26
    for index in range(25, -1, -1):
        encoded[index] = _CROCKFORD_BASE32[value & 31]
        value >>= 5
    return "".join(encoded)


def new_record_id(work_title: str, created_at: str) -> str:
    instant = parse_timestamp(created_at)
    timestamp_ms = int(instant.timestamp() * 1000)
    return f"{slugify(work_title)}--{_encode_ulid(timestamp_ms, secrets.token_bytes(10))}"


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def record_etag(record: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()
    return f'"{digest}"'


def semantic_key(record: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(record["media_type"]),
        str(record["work_title"]).strip().casefold(),
        str(record.get("unit_title") or "").strip().casefold(),
    )


def validate_record(record: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ModelError("Catalog record must be an object.")
    keys = set(record)
    if not _REQUIRED_FIELDS <= keys or keys - _REQUIRED_FIELDS - _OPTIONAL_FIELDS:
        missing = sorted(_REQUIRED_FIELDS - keys)
        extra = sorted(keys - _REQUIRED_FIELDS - _OPTIONAL_FIELDS)
        raise ModelError(f"Catalog record fields are invalid (missing={missing}, extra={extra}).")

    value = deepcopy(dict(record))
    record_id = value["id"]
    if not isinstance(record_id, str) or not _ID_PATTERN.fullmatch(record_id):
        raise ModelError("Catalog ID is invalid.")

    work_title = value["work_title"]
    if (
        not isinstance(work_title, str)
        or not work_title.strip()
        or work_title != work_title.strip()
        or len(work_title) > 300
        or any(character in work_title for character in "\r\n\t")
    ):
        raise ModelError("work_title must be a trimmed, non-empty single-line string.")

    unit_title = value["unit_title"]
    if unit_title is not None and (
        not isinstance(unit_title, str)
        or not unit_title.strip()
        or unit_title != unit_title.strip()
        or len(unit_title) > 200
        or any(character in unit_title for character in "\r\n\t")
    ):
        raise ModelError("unit_title must be a trimmed single-line string or null.")

    media_type = value["media_type"]
    if media_type not in MEDIA_TYPES:
        raise ModelError(f"media_type must be one of: {', '.join(MEDIA_TYPES)}.")
    status = value["status"]
    if status not in STATUSES:
        raise ModelError(f"status must be one of: {', '.join(STATUSES)}.")

    rating = value["rating"]
    if rating is not None:
        if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 10:
            raise ModelError("rating must be an integer from 1 to 10 or null.")
        if status not in TERMINAL_STATUSES:
            raise ModelError("Only completed or discontinued entries may have a rating.")

    release_year = value["release_year"]
    if release_year is not None and (
        isinstance(release_year, bool)
        or not isinstance(release_year, int)
        or not 1000 <= release_year <= 9999
    ):
        raise ModelError("release_year must be a four-digit integer or null.")

    dates = value["dates"]
    if not isinstance(dates, dict) or set(dates) != set(DATE_FIELDS):
        raise ModelError("dates must contain exactly the four lifecycle date fields.")
    parsed_dates: list[date | None] = []
    for field in DATE_FIELDS:
        item = dates[field]
        parsed_dates.append(None if item is None else parse_date(item))
    known_dates = [item for item in parsed_dates if item is not None]
    if known_dates != sorted(known_dates):
        raise ModelError("Lifecycle dates must be chronological when they are known.")
    if status == "investigate" and any(dates[field] is not None for field in DATE_FIELDS[1:]):
        raise ModelError("Investigate entries cannot have later lifecycle dates.")
    if status == "planned" and (dates["started_on"] is not None or dates["ended_on"] is not None):
        raise ModelError("Planned entries cannot have started or ended dates.")
    if status == "ongoing" and dates["ended_on"] is not None:
        raise ModelError("Ongoing entries cannot have an ended date.")
    if status not in TERMINAL_STATUSES and dates["ended_on"] is not None:
        raise ModelError("Only terminal entries may have an ended date.")

    notes = value["notes"]
    if notes is not None and (not isinstance(notes, str) or len(notes) > 20_000):
        raise ModelError("notes must be a string of at most 20,000 characters or null.")

    external_ids = value["external_ids"]
    if not isinstance(external_ids, dict):
        raise ModelError("external_ids must be an object.")
    for key, external_value in external_ids.items():
        if (
            not isinstance(key, str)
            or not key.strip()
            or len(key) > 80
            or not isinstance(external_value, str)
            or not external_value.strip()
            or len(external_value) > 300
        ):
            raise ModelError("external_ids keys and values must be non-empty strings.")

    created_at = parse_timestamp(value["created_at"])
    updated_at = parse_timestamp(value["updated_at"])
    if updated_at < created_at:
        raise ModelError("updated_at cannot be earlier than created_at.")
    if value["deleted_at"] is not None:
        deleted_at = parse_timestamp(value["deleted_at"])
        if deleted_at < created_at or updated_at < deleted_at:
            raise ModelError("deleted_at must fall between created_at and updated_at.")

    if "import" in value:
        import_data = value["import"]
        if not isinstance(import_data, dict) or import_data.get("source") not in {
            "completed",
            "planned",
        }:
            raise ModelError("import.source must be completed or planned.")
        if import_data["source"] == "completed":
            if set(import_data) != {"source", "position"} or (
                isinstance(import_data.get("position"), bool)
                or not isinstance(import_data.get("position"), int)
                or import_data["position"] < 1
            ):
                raise ModelError("Completed imports require a positive position.")
        elif set(import_data) != {"source"}:
            raise ModelError("Planned imports contain only source.")

    return value


def validate_catalog(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(records, list):
        raise ModelError("Catalog must be a list of JSON objects.")
    validated: list[dict[str, Any]] = []
    ids: set[str] = set()
    journeys: dict[tuple[str, str, str], str] = {}
    for record in records:
        item = validate_record(record)
        if item["id"] in ids:
            raise ModelError(f"Duplicate catalog ID: {item['id']}.")
        ids.add(item["id"])
        key = semantic_key(item)
        if key in journeys:
            raise ModelError(
                f"Duplicate journey unit: {journeys[key]} and {item['id']}."
            )
        journeys[key] = item["id"]
        validated.append(item)
    return validated

