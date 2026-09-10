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

        self.assertEqual(310, len(validated))
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

    def test_technical_timestamp_is_distinct_from_lifecycle_date(self) -> None:
        record = sample_record(created_at="2026-09-09", updated_at="2026-09-09")
        with self.assertRaisesRegex(ModelError, "Timestamp"):
            validate_record(record)


if __name__ == "__main__":
    unittest.main()
