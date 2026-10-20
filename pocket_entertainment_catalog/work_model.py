"""Work records share Journey's lifecycle, with independent categories and priority."""

from copy import deepcopy
import re
from typing import Any, Mapping
import unicodedata

from .model import ModelError, STATUSES, validate_lifecycle_dates, validate_timestamps


UNCATEGORIZED = "uncategorized"
KINDS = ("project", "task")
PRIORITIES = ("high", "low")
FIELDS = {
    "id",
    "work_title",
    "unit_title",
    "kind",
    "category_id",
    "status",
    "priority",
    "release_year",
    "notes",
    "dates",
    "created_at",
    "updated_at",
    "deleted_at",
    "legacy_history",
}


def category_name(value: Any) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ModelError("Category name must be text.")
    name = " ".join(unicodedata.normalize("NFKC", value).split())
    if not name or len(name) > 100 or any(ord(c) < 32 for c in name):
        raise ModelError("Category name must contain 1–100 characters.")
    return name, name.casefold()


def validate_work(record: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(record, Mapping) or set(record) != FIELDS:
        raise ModelError("Work record fields are invalid.")
    value = deepcopy(dict(record))
    if not isinstance(value["id"], str) or not re.fullmatch(
        r"[a-z0-9]+(?:-[a-z0-9]+)*--[0-9A-HJKMNP-TV-Z]{26}", value["id"]
    ):
        raise ModelError("Work ID is invalid.")
    for field, maximum, optional in (("work_title", 300, False), ("unit_title", 200, True)):
        item = value[field]
        if optional and item is None:
            continue
        if (
            not isinstance(item, str)
            or not item.strip()
            or item != item.strip()
            or len(item) > maximum
            or any(c in item for c in "\r\n\t")
        ):
            raise ModelError(f"{field} must be trimmed single-line text.")
    if value["kind"] not in KINDS:
        raise ModelError("kind must be project or task.")
    if not isinstance(value["category_id"], str) or not value["category_id"]:
        raise ModelError("Choose an existing category.")
    if value["status"] not in STATUSES:
        raise ModelError("Work status is invalid.")
    if value["status"] == "investigate":
        if value["priority"] is not None:
            raise ModelError("Investigate works cannot have a priority.")
    elif value["priority"] not in PRIORITIES:
        raise ModelError("Choose High or Low priority before leaving Investigate.")
    year = value["release_year"]
    if year is not None and (
        isinstance(year, bool) or not isinstance(year, int) or not 1000 <= year <= 9999
    ):
        raise ModelError("release_year must be a four-digit integer or null.")
    if value["notes"] is not None and (
        not isinstance(value["notes"], str) or len(value["notes"]) > 20_000
    ):
        raise ModelError("notes must contain at most 20,000 characters.")
    if not isinstance(value["legacy_history"], bool):
        raise ModelError("legacy_history must be a boolean.")
    validate_lifecycle_dates(value["dates"], value["status"], allow_unknown=value["legacy_history"])
    validate_timestamps(value)
    return value
