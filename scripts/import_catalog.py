#!/usr/bin/env python3
"""Build the initial entertainment catalog from the two Google Sheets exports.

This is intentionally a one-time, deterministic migration tool. Once the web app
starts managing the catalog, it—not this importer—owns existing records.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPLETED_CSV = PROJECT_ROOT / "Planning - Entertainment Completed.csv"
DEFAULT_PLANNED_CSV = PROJECT_ROOT / "Planning - Entertainment Planned.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "catalog.jsonl"

MEDIA_TYPES = {
    "animation",
    "anime",
    "game",
    "live_action_movie",
    "live_action_series",
    "manga",
    "visual_novel",
}
STATUSES = {"investigate", "planned", "ongoing", "completed", "discontinued"}
TERMINAL_STATUSES = {"completed", "discontinued"}
DATE_FIELDS = ("investigated_on", "planned_on", "started_on", "ended_on")

EXPECTED_SOURCE_RECORDS = 322
EXPECTED_EXCLUSIONS = 12
EXPECTED_OUTPUT_RECORDS = 310

CROCKFORD_BASE32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)


class CatalogImportError(RuntimeError):
    """Raised when the source data or generated catalog violates an invariant."""


def read_csv(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return list(csv.reader(source))


def clean_cell(value: str) -> str:
    """Remove spreadsheet-only whitespace, including embedded CR characters."""

    return re.sub(r"\s+", " ", value).strip()


def strip_discontinued_marker(title: str) -> tuple[str, bool]:
    match = re.match(r"^\[DC\]\s*", title, flags=re.IGNORECASE)
    if not match:
        return title, False
    return title[match.end() :], True


def should_exclude(raw_title: str) -> str | None:
    title, was_discontinued = strip_discontinued_marker(raw_title)
    folded = title.casefold()

    if folded.startswith("goddess of victory: nikke"):
        return "goddess-of-victory-nikke"
    if folded.startswith("wuthering waves"):
        return "wuthering-waves"
    if folded.startswith("neverness to everness") or re.match(r"^nte\b", folded):
        return "neverness-to-everness"
    if was_discontinued and folded == "persona 3 reload":
        return "incorrect-discontinued-persona-3-reload"
    if folded.startswith("trails recap:"):
        return "trails-recap"
    return None


TITLE_CORRECTIONS = {
    "Ex-Machina": "Ex Machina",
    "Doll's Frontline": "Girls' Frontline",
    "Hori san to Miyamura kun": "Hori-san to Miyamura-kun",
    "Black Myth Wukong": "Black Myth: Wukong",
    "Metaphor Refantazio": "Metaphor: ReFantazio",
    "Bâan -The Boundaries of Adulthood": "Bâan - The Boundaries of Adulthood",
    "Kaguya-sama: Love Is War -Stairway to Adulthood": (
        "Kaguya-sama: Love Is War - Stairway to Adulthood"
    ),
    "Spider-Man: Across The Spider-Verse": "Spider-Man: Across the Spider-Verse",
    "Alita Battle Angel": "Alita: Battle Angel",
    "FFVII REBIRTH": "Final Fantasy VII Rebirth",
    "FFVII REMAKE": "Final Fantasy VII Remake",
}


UNIT_OVERRIDES: dict[str, tuple[str, str | None]] = {
    "Attack on Titan Finale": ("Attack on Titan", "Finale"),
    "Blue Lock": ("Blue Lock", "Season 1"),
    "Medalist": ("Medalist", "Season 1"),
    "Chainsaw Man Part 2": ("Chainsaw Man", "Part 2"),
    "Final Fantasy XVI - Echoes of the Fallen DLC": (
        "Final Fantasy XVI",
        "Echoes of the Fallen DLC",
    ),
    "Elden Ring: Shadow of the Erdtree": ("Elden Ring", "Shadow of the Erdtree"),
    "Resident Evil Village: Shadows of Rose": (
        "Resident Evil Village",
        "Shadows of Rose",
    ),
    "Girl's Frontline 2: Exilium (Ch1-8 + lvl 50)": (
        "Girls' Frontline 2: Exilium",
        "Chapters 1-8 + Level 50",
    ),
    "Uma Musume: Pretty Derby - BNW no Chikai": (
        "Uma Musume: Pretty Derby",
        "BNW no Chikai",
    ),
    "Uma Musume: Pretty Derby - Road to the Top": (
        "Uma Musume: Pretty Derby",
        "Road to the Top",
    ),
}

# Animation produced outside the catalog's anime classification previously lived
# in the Movies and TV Series spreadsheet columns. It now has its own media type.
NON_ANIME_ANIMATION_WORKS = {
    "Arcane",
    "Castlevania",
    "Castlevania: Nocturne",
    "Invincible",
    "Spider-Man: Into the Spider-Verse",
    "Spider-Man: Across the Spider-Verse",
    "The Lion King",
}


def apply_source_corrections(title: str, source: str, status: str) -> str:
    if source == "planned" and status == "ongoing" and title == "Mushoku Tensei (S2)":
        return "Mushoku Tensei (S3)"
    if source == "completed" and title == "Invincible (S1, S2 Part 1)":
        return "Invincible (S2 Part 1)"
    return title


def split_title(raw_title: str) -> tuple[str, str | None, int | None]:
    """Split only known journey-unit patterns; arbitrary subtitles remain titles."""

    title = raw_title
    release_year: int | None = None

    year_match = re.search(r"\s+\((\d{4})\)$", title)
    if year_match:
        release_year = int(year_match.group(1))
        title = title[: year_match.start()].rstrip()

    if title in UNIT_OVERRIDES:
        work_title, unit_title = UNIT_OVERRIDES[title]
        return work_title, unit_title, release_year

    title = TITLE_CORRECTIONS.get(title, title)

    season_match = re.match(
        r"^(?P<work>.+?)\s+\(S(?P<season>\d+)"
        r"(?:\s+Part\s+(?P<part>\d+))?\)(?:\s+(?P<suffix>OVA))?$",
        title,
        flags=re.IGNORECASE,
    )
    if season_match:
        unit_title = f"Season {int(season_match.group('season'))}"
        if season_match.group("part"):
            unit_title += f", Part {int(season_match.group('part'))}"
        if season_match.group("suffix"):
            unit_title += " OVA"
        return season_match.group("work"), unit_title, release_year

    part_match = re.match(r"^(?P<work>.+?)\s+\(Part\s+(?P<part>\d+)\)$", title)
    if part_match:
        return (
            part_match.group("work"),
            f"Part {int(part_match.group('part'))}",
            release_year,
        )

    return title, None, release_year


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).replace("&", " and ")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_value = re.sub(r"['’]", "", ascii_value.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return (slug[:64].rstrip("-") or "item")


def parse_timestamp(timestamp: str) -> datetime:
    if not TIMESTAMP_PATTERN.fullmatch(timestamp):
        raise CatalogImportError(
            "Import timestamp must use UTC ISO 8601 with milliseconds, "
            "for example 2026-09-09T18:42:31.482Z"
        )
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def encode_ulid(timestamp: str, seed: str) -> str:
    instant = parse_timestamp(timestamp)
    timestamp_ms = int(instant.timestamp() * 1000)
    if not 0 <= timestamp_ms < 2**48:
        raise CatalogImportError("Timestamp is outside the ULID range")

    entropy = int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:10], "big")
    value = (timestamp_ms << 80) | entropy
    encoded = ["0"] * 26
    for index in range(25, -1, -1):
        encoded[index] = CROCKFORD_BASE32[value & 31]
        value >>= 5
    return "".join(encoded)


def make_record(
    *,
    raw_title: str,
    media_type: str,
    status: str,
    source: str,
    source_position: int,
    source_coordinate: str,
    timestamp: str,
) -> dict[str, Any]:
    cleaned_title = clean_cell(raw_title)
    title_without_marker, _ = strip_discontinued_marker(cleaned_title)
    corrected_title = apply_source_corrections(title_without_marker, source, status)
    work_title, unit_title, release_year = split_title(corrected_title)
    if work_title in NON_ANIME_ANIMATION_WORKS:
        media_type = "animation"

    ulid = encode_ulid(timestamp, source_coordinate)
    record: dict[str, Any] = {
        "id": f"{slugify(work_title)}--{ulid}",
        "work_title": work_title,
        "unit_title": unit_title,
        "media_type": media_type,
        "status": status,
        "rating": None,
        "release_year": release_year,
        "dates": {field: None for field in DATE_FIELDS},
        "notes": None,
        "external_ids": {},
        "import": {"source": source},
        "created_at": timestamp,
        "updated_at": timestamp,
        "deleted_at": None,
    }
    if source == "completed":
        record["import"]["position"] = source_position
    return record


def collect_completed(
    path: Path, timestamp: str
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    rows = read_csv(path)
    if len(rows) < 2 or rows[1][:6] != [
        "Gaming",
        "Anime",
        "Movies",
        "TV Series",
        "Visual Novels",
        "Manga",
    ]:
        raise CatalogImportError(f"Unexpected Completed sheet structure: {path}")

    media_by_column = {
        0: "game",
        1: "anime",
        2: "live_action_movie",
        3: "live_action_series",
        4: "visual_novel",
        5: "manga",
    }
    records: list[dict[str, Any]] = []
    exclusions: list[tuple[str, str]] = []

    for column, media_type in media_by_column.items():
        position = 0
        for row_number, row in enumerate(rows[2:], start=3):
            raw_title = row[column] if column < len(row) else ""
            if not clean_cell(raw_title):
                continue
            position += 1
            cleaned_title = clean_cell(raw_title)
            exclusion_reason = should_exclude(cleaned_title)
            if exclusion_reason:
                exclusions.append((cleaned_title, exclusion_reason))
                continue

            _, was_discontinued = strip_discontinued_marker(cleaned_title)
            status = "discontinued" if was_discontinued else "completed"
            records.append(
                make_record(
                    raw_title=cleaned_title,
                    media_type=media_type,
                    status=status,
                    source="completed",
                    source_position=position,
                    source_coordinate=f"completed:{column}:{row_number}",
                    timestamp=timestamp,
                )
            )

    return records, exclusions


def collect_planned(
    path: Path, timestamp: str
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    rows = read_csv(path)
    if len(rows) < 2:
        raise CatalogImportError(f"Unexpected Planned sheet structure: {path}")

    groups = {
        1: "game",
        4: "anime",
        7: "live_action_series",
        10: "live_action_movie",
        13: "manga",
    }
    expected_headers = {
        1: "GAMING",
        4: "ANIME",
        7: "LIVE ACTION SERIES",
        10: "LIVE ACTION MOVIES",
        13: "MANGA / Visual Novels",
    }
    for column, expected in expected_headers.items():
        if clean_cell(rows[0][column]) != expected:
            raise CatalogImportError(f"Unexpected Planned sheet header at column {column + 1}")

    records: list[dict[str, Any]] = []
    exclusions: list[tuple[str, str]] = []

    for base_column, media_type in groups.items():
        for offset in range(3):
            column = base_column + offset
            status = clean_cell(rows[1][column]).lower()
            if status not in {"ongoing", "planned", "investigate"}:
                raise CatalogImportError(f"Unexpected Planned status: {status!r}")

            position = 0
            for row_number, row in enumerate(rows[2:], start=3):
                raw_title = row[column] if column < len(row) else ""
                if not clean_cell(raw_title):
                    continue
                position += 1
                cleaned_title = clean_cell(raw_title)
                exclusion_reason = should_exclude(cleaned_title)
                if exclusion_reason:
                    exclusions.append((cleaned_title, exclusion_reason))
                    continue

                records.append(
                    make_record(
                        raw_title=cleaned_title,
                        media_type=media_type,
                        status=status,
                        source="planned",
                        source_position=position,
                        source_coordinate=f"planned:{column}:{row_number}",
                        timestamp=timestamp,
                    )
                )

    return records, exclusions


def semantic_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (
        record["media_type"],
        record["work_title"].casefold(),
        (record["unit_title"] or "").casefold(),
    )


def validate_catalog(records: list[dict[str, Any]], *, expected_count: int) -> None:
    if len(records) != expected_count:
        raise CatalogImportError(
            f"Expected {expected_count} records, generated {len(records)}"
        )

    ids: set[str] = set()
    semantic_records: dict[tuple[str, str, str], str] = {}

    for line_number, record in enumerate(records, start=1):
        record_id = record.get("id")
        if not isinstance(record_id, str) or not re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*--[0-9A-HJKMNP-TV-Z]{26}", record_id
        ):
            raise CatalogImportError(f"Invalid ID on output line {line_number}: {record_id!r}")
        if record_id in ids:
            raise CatalogImportError(f"Duplicate ID: {record_id}")
        ids.add(record_id)

        if record.get("media_type") not in MEDIA_TYPES:
            raise CatalogImportError(f"Invalid media type for {record_id}")
        if record.get("status") not in STATUSES:
            raise CatalogImportError(f"Invalid status for {record_id}")
        if not isinstance(record.get("work_title"), str) or not record["work_title"].strip():
            raise CatalogImportError(f"Missing work title for {record_id}")
        if re.search(r"[\r\n\t]", record["work_title"]):
            raise CatalogImportError(f"Control character in work title for {record_id}")
        if record.get("unit_title") is not None and not isinstance(record["unit_title"], str):
            raise CatalogImportError(f"Invalid unit title for {record_id}")
        if record.get("rating") is not None:
            rating = record["rating"]
            if record["status"] not in TERMINAL_STATUSES or not isinstance(rating, int) or not 1 <= rating <= 10:
                raise CatalogImportError(f"Invalid rating for {record_id}")
        if record.get("release_year") is not None and not isinstance(record["release_year"], int):
            raise CatalogImportError(f"Invalid release year for {record_id}")
        if record.get("dates") != {field: None for field in DATE_FIELDS}:
            raise CatalogImportError(f"Initial import must have null lifecycle dates: {record_id}")
        if record.get("notes") is not None or record.get("external_ids") != {}:
            raise CatalogImportError(f"Unexpected notes or external IDs for {record_id}")

        import_data = record.get("import")
        if not isinstance(import_data, dict) or import_data.get("source") not in {
            "completed",
            "planned",
        }:
            raise CatalogImportError(f"Invalid import metadata for {record_id}")
        if import_data["source"] == "completed":
            if set(import_data) != {"source", "position"} or not isinstance(
                import_data["position"], int
            ):
                raise CatalogImportError(f"Invalid completed import position for {record_id}")
        elif set(import_data) != {"source"}:
            raise CatalogImportError(f"Planned import must not retain position: {record_id}")

        if not TIMESTAMP_PATTERN.fullmatch(record.get("created_at", "")):
            raise CatalogImportError(f"Invalid created_at for {record_id}")
        if record.get("updated_at") != record["created_at"]:
            raise CatalogImportError(f"Initial updated_at differs from created_at: {record_id}")
        if record.get("deleted_at") is not None:
            raise CatalogImportError(f"Initial record is unexpectedly deleted: {record_id}")

        key = semantic_key(record)
        if key in semantic_records:
            raise CatalogImportError(
                "Duplicate journey unit: "
                f"{semantic_records[key]} and {record_id} share {key!r}"
            )
        semantic_records[key] = record_id

    forbidden_titles = (
        "goddess of victory: nikke",
        "wuthering waves",
        "neverness to everness",
    )
    for record in records:
        folded = record["work_title"].casefold()
        if any(title in folded for title in forbidden_titles) or folded == "nte":
            raise CatalogImportError(f"Excluded work remains in catalog: {record['id']}")

    expected_journeys = {
        ("anime", "blue lock", "season 1", "completed"),
        ("anime", "medalist", "season 1", "completed"),
        ("anime", "mushoku tensei", "season 2", "completed"),
        ("anime", "mushoku tensei", "season 3", "ongoing"),
        ("animation", "invincible", "season 1", "completed"),
        ("animation", "invincible", "season 2, part 1", "completed"),
        ("game", "persona 3 reload", "", "ongoing"),
    }
    actual_journeys = {
        (
            record["media_type"],
            record["work_title"].casefold(),
            (record["unit_title"] or "").casefold(),
            record["status"],
        )
        for record in records
    }
    missing = expected_journeys - actual_journeys
    if missing:
        raise CatalogImportError(f"Expected corrected journeys are missing: {sorted(missing)!r}")

    persona_records = [
        record for record in records if record["work_title"].casefold() == "persona 3 reload"
    ]
    if len(persona_records) != 1 or persona_records[0]["status"] != "ongoing":
        raise CatalogImportError("Persona 3 Reload correction was not applied exactly once")

    expected_animation_counts = {
        "Arcane": 1,
        "Castlevania": 4,
        "Castlevania: Nocturne": 1,
        "Invincible": 4,
        "Spider-Man: Into the Spider-Verse": 1,
        "Spider-Man: Across the Spider-Verse": 1,
        "The Lion King": 1,
    }
    actual_animation_counts = Counter(
        record["work_title"]
        for record in records
        if record["media_type"] == "animation"
    )
    if actual_animation_counts != Counter(expected_animation_counts):
        raise CatalogImportError(
            "Unexpected non-anime animation classification: "
            f"{dict(actual_animation_counts)!r}"
        )


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                raise CatalogImportError(f"Blank line in JSONL at line {line_number}")
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise CatalogImportError(
                    f"Invalid JSON on line {line_number}: {error}"
                ) from error
            if not isinstance(value, dict):
                raise CatalogImportError(f"JSONL line {line_number} is not an object")
            records.append(value)
    return records


def existing_import_timestamp(path: Path) -> str | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    records = load_jsonl(path)
    timestamps = {
        record.get("created_at")
        for record in records
        if isinstance(record.get("import"), dict)
        and record["import"].get("source") in {"completed", "planned"}
    }
    if not timestamps:
        raise CatalogImportError("Existing catalog contains no initial-import records")
    if len(timestamps) != 1:
        raise CatalogImportError(
            "Existing import records do not share one created_at timestamp"
        )
    timestamp = timestamps.pop()
    if not isinstance(timestamp, str):
        raise CatalogImportError("Existing catalog has an invalid created_at timestamp")
    parse_timestamp(timestamp)
    return timestamp


def current_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            for record in records:
                output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                output.write("\n")
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def summarize(records: list[dict[str, Any]], exclusions: list[tuple[str, str]]) -> str:
    media_counts = Counter(record["media_type"] for record in records)
    status_counts = Counter(record["status"] for record in records)
    exclusion_counts = Counter(reason for _, reason in exclusions)
    return "\n".join(
        [
            f"records={len(records)}",
            "media_types=" + ", ".join(f"{key}:{media_counts[key]}" for key in sorted(media_counts)),
            "statuses=" + ", ".join(f"{key}:{status_counts[key]}" for key in sorted(status_counts)),
            "exclusions="
            + ", ".join(f"{key}:{exclusion_counts[key]}" for key in sorted(exclusion_counts)),
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--completed", type=Path, default=DEFAULT_COMPLETED_CSV)
    parser.add_argument("--planned", type=Path, default=DEFAULT_PLANNED_CSV)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--created-at",
        help="Shared initial-import UTC timestamp; defaults to the existing catalog timestamp or now",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the existing output without regenerating it",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite a differing existing catalog (unsafe after the web app begins managing it)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.check:
        records = load_jsonl(args.output)
        validate_catalog(records, expected_count=EXPECTED_OUTPUT_RECORDS)
        print(summarize(records, []))
        return

    timestamp = args.created_at or existing_import_timestamp(args.output) or current_timestamp()
    parse_timestamp(timestamp)

    completed_records, completed_exclusions = collect_completed(args.completed, timestamp)
    planned_records, planned_exclusions = collect_planned(args.planned, timestamp)
    records = completed_records + planned_records
    exclusions = completed_exclusions + planned_exclusions

    observed_source_records = len(records) + len(exclusions)
    if observed_source_records != EXPECTED_SOURCE_RECORDS:
        raise CatalogImportError(
            f"Expected {EXPECTED_SOURCE_RECORDS} source records, found {observed_source_records}"
        )
    if len(exclusions) != EXPECTED_EXCLUSIONS:
        raise CatalogImportError(
            f"Expected {EXPECTED_EXCLUSIONS} exclusions, applied {len(exclusions)}"
        )

    validate_catalog(records, expected_count=EXPECTED_OUTPUT_RECORDS)
    if args.output.exists() and not args.force:
        existing_records = load_jsonl(args.output)
        if existing_records != records:
            raise CatalogImportError(
                f"Refusing to overwrite a differing catalog at {args.output}. "
                "Review the differences and pass --force only if replacement is intentional."
            )
    write_jsonl(args.output, records)
    reloaded = load_jsonl(args.output)
    validate_catalog(reloaded, expected_count=EXPECTED_OUTPUT_RECORDS)
    print(summarize(reloaded, exclusions))


if __name__ == "__main__":
    main()
