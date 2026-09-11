from __future__ import annotations

import json
from pathlib import Path
import unittest

from pocket_entertainment_catalog.model import ModelError, validate_catalog, validate_record

from .helpers import sample_record


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CatalogModelTests(unittest.TestCase):
    def test_checked_in_catalog_satisfies_the_schema(self) -> None:
        records = [
            json.loads(line)
            for line in (PROJECT_ROOT / "data" / "catalog.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        validated = validate_catalog(records)

        self.assertGreaterEqual(len(validated), 310)
        self.assertEqual(310, sum("import" in record for record in validated))
        self.assertEqual(
            {"anime", "animation", "game", "live_action_movie", "live_action_series", "manga", "visual_novel"},
            {record["media_type"] for record in validated},
        )

    def test_nonterminal_rating_is_rejected(self) -> None:
        record = sample_record(rating=8)
        with self.assertRaisesRegex(ModelError, "Only completed or discontinued"):
            validate_record(record)

    def test_lifecycle_dates_must_be_chronological(self) -> None:
        record = sample_record(
            status="ongoing",
            dates={
                "investigated_on": "2026-09-09",
                "planned_on": "2026-09-08",
                "started_on": "2026-09-10",
                "ended_on": None,
            },
        )
        with self.assertRaisesRegex(ModelError, "chronological"):
            validate_record(record)

    def test_known_history_requires_every_date_through_current_status(self) -> None:
        record = sample_record(
            status="ongoing",
            dates={
                "investigated_on": None,
                "planned_on": None,
                "started_on": "2026-09-09",
                "ended_on": None,
            },
        )
        with self.assertRaisesRegex(ModelError, "every date through the current status"):
            validate_record(record)

    def test_only_legacy_imports_may_have_all_lifecycle_dates_unknown(self) -> None:
        unknown_dates = {
            "investigated_on": None,
            "planned_on": None,
            "started_on": None,
            "ended_on": None,
        }
        with self.assertRaisesRegex(ModelError, "Only legacy imported"):
            validate_record(sample_record(status="ongoing", dates=unknown_dates))

        legacy = sample_record(
            status="ongoing",
            dates=unknown_dates,
            **{"import": {"source": "planned"}},
        )
        self.assertEqual(unknown_dates, validate_record(legacy)["dates"])

    def test_paused_requires_a_complete_started_prefix(self) -> None:
        paused = sample_record(
            status="paused",
            dates={
                "investigated_on": "2026-09-07",
                "planned_on": "2026-09-08",
                "started_on": "2026-09-09",
                "ended_on": None,
            },
        )
        self.assertEqual("paused", validate_record(paused)["status"])

    def test_technical_timestamp_is_distinct_from_lifecycle_date(self) -> None:
        record = sample_record(created_at="2026-09-09", updated_at="2026-09-09")
        with self.assertRaisesRegex(ModelError, "Timestamp"):
            validate_record(record)


if __name__ == "__main__":
    unittest.main()
