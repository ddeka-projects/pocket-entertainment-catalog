from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from pocket_entertainment_catalog.lease import EditorLease
from pocket_entertainment_catalog.service import CatalogService, InvalidRequest, InvalidTransition
from pocket_entertainment_catalog.storage import CatalogConflict, CatalogStore

from .helpers import LocalOnlyGitSync, sample_record, write_catalog


class CatalogServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.catalog_path = Path(self.temporary.name) / "catalog.jsonl"
        write_catalog(self.catalog_path, [sample_record()])
        self.git = LocalOnlyGitSync()
        self.service = CatalogService(
            CatalogStore(self.catalog_path),
            EditorLease(),
            self.git,  # type: ignore[arg-type]
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_create_populates_owned_fields_and_begins_at_investigate(self) -> None:
        result = self.service.create({"work_title": "A New Story", "media_type": "anime"})
        record = result["entry"]["record"]

        self.assertEqual("investigate", record["status"])
        self.assertIsNotNone(record["dates"]["investigated_on"])
        self.assertEqual(record["created_at"], record["updated_at"])
        self.assertIsNone(record["deleted_at"])
        self.assertTrue(record["id"].startswith("a-new-story--"))
        self.assertNotIn("import", record)

    def test_lifecycle_progresses_one_state_at_a_time(self) -> None:
        entry = self.service.catalog()["entries"][0]
        with self.assertRaises(InvalidTransition):
            self.service.transition(
                entry["record"]["id"],
                {"status": "ongoing", "on": "2026-09-10"},
                expected_etag=entry["etag"],
            )

        planned = self.service.transition(
            entry["record"]["id"],
            {"status": "planned", "on": "2026-09-10"},
            expected_etag=entry["etag"],
        )["entry"]
        ongoing = self.service.transition(
            planned["record"]["id"],
            {"status": "ongoing", "on": "2026-09-11"},
            expected_etag=planned["etag"],
        )["entry"]
        completed = self.service.transition(
            ongoing["record"]["id"],
            {
                "status": "completed",
                "on": "2026-09-12",
                "rating": 9,
                "notes": "A strong ending.",
            },
            expected_etag=ongoing["etag"],
        )["entry"]

        self.assertEqual("completed", completed["record"]["status"])
        self.assertEqual(9, completed["record"]["rating"])
        self.assertEqual("A strong ending.", completed["record"]["notes"])
        self.assertEqual("2026-09-12", completed["record"]["dates"]["ended_on"])

    def test_stale_etag_cannot_overwrite_a_newer_edit(self) -> None:
        entry = self.service.catalog()["entries"][0]
        self.service.update(
            entry["record"]["id"],
            {"notes": "First writer"},
            expected_etag=entry["etag"],
        )
        with self.assertRaises(CatalogConflict):
            self.service.update(
                entry["record"]["id"],
                {"notes": "Stale writer"},
                expected_etag=entry["etag"],
            )

    def test_technical_fields_cannot_be_edited(self) -> None:
        entry = self.service.catalog()["entries"][0]
        with self.assertRaises(InvalidRequest):
            self.service.update(
                entry["record"]["id"],
                {"created_at": "2030-01-01T00:00:00.000Z"},
                expected_etag=entry["etag"],
            )

    def test_delete_is_soft_and_restore_is_supported(self) -> None:
        entry = self.service.catalog()["entries"][0]
        deleted = self.service.delete(
            entry["record"]["id"], expected_etag=entry["etag"]
        )["entry"]

        self.assertIsNotNone(deleted["record"]["deleted_at"])
        self.assertEqual([], self.service.catalog()["entries"])
        self.assertEqual(1, len(self.service.catalog(include_deleted=True)["entries"]))

        restored = self.service.restore(
            deleted["record"]["id"], expected_etag=deleted["etag"]
        )["entry"]
        self.assertIsNone(restored["record"]["deleted_at"])


if __name__ == "__main__":
    unittest.main()
