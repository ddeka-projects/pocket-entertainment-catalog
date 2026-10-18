from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from pocket_entertainment_catalog.model import DATE_FIELDS, ModelError, new_record_id
from pocket_entertainment_catalog.service import InvalidRequest, InvalidTransition
from pocket_entertainment_catalog.storage import CatalogConflict
from pocket_entertainment_catalog.work_model import validate_work
from pocket_entertainment_catalog.work_service import WorkService
from pocket_entertainment_catalog.work_storage import WorkStore


class WorkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "editing.sqlite3"
        self.store = WorkStore(self.path)
        self.service = WorkService(self.store)

    def create(self, **overrides):
        return self.service.create({"work_title": "Kaleido Star", "unit_title": "Season 1",
                                    "kind": "project", "category_id": "uncategorized"} | overrides)["entry"]

    def transition(self, entry, status, **extra):
        return self.service.transition(entry["record"]["id"], {"status": status} | extra,
                                       expected_etag=entry["etag"])["entry"]

    def category(self, name):
        return next(c for c in self.store.change_category(name)["categories"] if c["name"] == name)

    def test_starts_empty_with_only_the_reserved_category_and_survives_restart(self):
        self.assertEqual([], self.service.catalog({})["entries"])
        self.assertEqual(["Uncategorized"], [c["name"] for c in self.service.catalog({})["categories"]])
        entry = self.create()
        restarted = WorkService(WorkStore(self.path))
        self.assertEqual(entry, restarted.entry(entry["record"]["id"])["entry"])

    def test_explicit_legacy_creation_selects_status_without_fabricating_dates(self):
        base = {"work_title": "Old project", "kind": "project", "category_id": "uncategorized"}
        for status in ("investigate", "planned", "ongoing", "paused", "completed", "discontinued"):
            priority = None if status == "investigate" else "high"
            entry = self.service.create_legacy(base | {"status": status, "priority": priority})["entry"]
            self.assertEqual(status, entry["record"]["status"])
            self.assertEqual(priority, entry["record"]["priority"])
            self.assertTrue(entry["record"]["legacy_history"])
            self.assertEqual(dict.fromkeys(DATE_FIELDS), entry["record"]["dates"])
        ordinary = self.create()
        self.assertFalse(ordinary["record"]["legacy_history"])
        self.assertIsNotNone(ordinary["record"]["dates"]["investigated_on"])

    def test_legacy_creation_enforces_priority_and_server_owned_fields(self):
        base = {"work_title": "Old task", "kind": "task", "category_id": "uncategorized", "status": "completed", "priority": "low"}
        for extra in ({"priority": None}, {"priority": "medium"}, {"status": "investigate"}, {"status": "invalid"}):
            with self.assertRaises(ModelError):
                self.service.create_legacy(base | extra)
        for extra in ({"dates": dict.fromkeys(DATE_FIELDS)}, {"legacy_history": True}, {"created_at": "2020-01-01T00:00:00.000Z"}, {"id": "custom"}):
            with self.assertRaises(InvalidRequest):
                self.service.create_legacy(base | extra)
        with self.assertRaises(InvalidRequest):
            self.service.create_legacy({k: v for k, v in base.items() if k != "status"})
        with self.assertRaises(InvalidRequest):
            self.service.create(base)
        self.assertEqual(0, self.service.catalog({})["total"])

    def test_legacy_ongoing_work_still_needs_sequential_transitions_and_alignment(self):
        entry = self.service.create_legacy({"work_title": "Old project", "kind": "project", "category_id": "uncategorized", "status": "planned", "priority": "low"})["entry"]
        with self.assertRaises(InvalidTransition):
            self.transition(entry, "completed")
        with self.assertRaises(InvalidTransition):
            self.transition(entry, "ongoing")
        ongoing = self.transition(entry, "ongoing", on="2020-02-03", adjust_prior_dates=True)
        self.assertEqual(["2020-02-03"] * 3 + [None], list(ongoing["record"]["dates"].values()))

    def test_duplicates_have_stable_readable_independent_ids(self):
        a, b = self.create(), self.create()
        self.assertNotEqual(a["record"]["id"], b["record"]["id"])
        self.assertTrue(a["record"]["id"].startswith("kaleido-star--"))
        updated = self.service.update(a["record"]["id"], {"work_title": "New title"}, expected_etag=a["etag"])["entry"]
        self.assertEqual(a["record"]["id"], updated["record"]["id"])
        self.assertEqual(b, self.service.entry(b["record"]["id"])["entry"])

    def test_priority_is_atomic_with_planning_and_statuses_are_sequential(self):
        entry = self.create()
        for payload in ({"status": "planned"}, {"status": "planned", "priority": "medium"}, {"status": "completed", "priority": "high"}):
            with self.assertRaises((InvalidRequest, InvalidTransition)):
                self.service.transition(entry["record"]["id"], payload, expected_etag=entry["etag"])
        self.assertEqual(entry, self.service.entry(entry["record"]["id"])["entry"])
        with self.assertRaises(ModelError):
            self.service.update(entry["record"]["id"], {"priority": "high"}, expected_etag=entry["etag"])
        planned = self.transition(entry, "planned", priority="high")
        self.assertEqual("high", planned["record"]["priority"])
        ongoing = self.transition(planned, "ongoing")
        paused = self.transition(ongoing, "paused")
        with self.assertRaises(InvalidTransition):
            self.transition(paused, "completed")
        resumed = self.transition(paused, "ongoing", on="2020-01-01")
        self.assertEqual(ongoing["record"]["dates"], resumed["record"]["dates"])
        completed = self.transition(resumed, "completed")
        self.assertEqual("high", completed["record"]["priority"])
        self.assertIsNotNone(completed["record"]["dates"]["ended_on"])
        with self.assertRaises(InvalidTransition):
            self.transition(completed, "ongoing")

    def test_backdating_requires_confirmation_and_corrects_all_affected_dates(self):
        entry = self.create()
        with self.assertRaises(InvalidTransition):
            self.transition(entry, "planned", priority="low", on="2020-01-01")
        planned = self.transition(entry, "planned", priority="low", on="2020-01-01", adjust_prior_dates=True)
        self.assertEqual("2020-01-01", planned["record"]["dates"]["investigated_on"])
        self.assertEqual("2020-01-01", planned["record"]["dates"]["planned_on"])

    def test_unknown_dates_are_reserved_for_backfill_and_partial_history_is_invalid(self):
        record = self.create()["record"]
        record["dates"] = dict.fromkeys(DATE_FIELDS)
        with self.assertRaises(ModelError):
            validate_work(record)
        record["legacy_history"] = True
        record["status"], record["priority"] = "completed", "low"
        stored = self.store.create(record | {"id": new_record_id("Legacy", record["created_at"])})
        self.assertEqual(dict.fromkeys(DATE_FIELDS), stored.value["dates"])
        record["dates"]["ended_on"] = "2020-01-01"
        with self.assertRaises(ModelError):
            validate_work(record)

    def test_history_correction_cannot_skip_statuses_or_erase_normal_history(self):
        entry = self.create()
        with self.assertRaises(InvalidRequest):
            self.service.correct_history(entry["record"]["id"], {"status": "completed", "dates": {}}, expected_etag=entry["etag"])
        with self.assertRaises(ModelError):
            self.service.correct_history(entry["record"]["id"], {"dates": dict.fromkeys(DATE_FIELDS)}, expected_etag=entry["etag"])
        dates = entry["record"]["dates"] | {"investigated_on": "2020-01-01"}
        updated = self.service.correct_history(entry["record"]["id"], {"dates": dates}, expected_etag=entry["etag"])["entry"]
        self.assertEqual("investigate", updated["record"]["status"])
        self.assertEqual(dates, updated["record"]["dates"])

    def test_all_server_fields_and_nonexistent_categories_are_rejected(self):
        entry = self.create()
        for field in ("id", "created_at", "updated_at", "deleted_at", "status", "legacy_history", "rating"):
            with self.assertRaises(InvalidRequest):
                self.service.update(entry["record"]["id"], {field: None}, expected_etag=entry["etag"])
        with self.assertRaises(InvalidRequest):
            self.create(category_id="does-not-exist")
        with self.assertRaises(InvalidRequest):
            self.service.update(entry["record"]["id"], {"category_id": "does-not-exist"}, expected_etag=entry["etag"])

    def test_category_names_are_unique_under_unicode_case_and_whitespace_normalization(self):
        self.category("Anime")
        for name in ("anime", " Anime ", "Ａｎｉｍｅ", "UNCATEGORIZED"):
            with self.assertRaises(InvalidRequest):
                self.store.change_category(name)
        self.category("Live Action")
        with self.assertRaises(InvalidRequest):
            self.store.change_category("live   action")
        for name in (None, "", "   ", "x" * 101):
            with self.assertRaises(ModelError):
                self.store.change_category(name)

    def test_category_rename_and_delete_reassign_including_trash_and_invalidate_edits(self):
        category = self.category("Anime")
        live = self.create(category_id=category["id"])
        trash = self.create(category_id=category["id"])
        trash = self.service.delete(trash["record"]["id"], expected_etag=trash["etag"])["entry"]
        renamed = self.store.change_category("Animation", category_id=category["id"], expected_etag=category["etag"])
        category = next(c for c in renamed["categories"] if c["id"] == category["id"])
        self.assertEqual(2, category["work_count"])
        self.assertEqual(live["record"]["category_id"], category["id"])
        self.store.change_category(category_id=category["id"], expected_etag=category["etag"], delete=True)
        for previous in (live, trash):
            actual = self.service.entry(previous["record"]["id"])["entry"]
            self.assertEqual("uncategorized", actual["record"]["category_id"])
            self.assertGreater(actual["record"]["updated_at"], previous["record"]["updated_at"])
            self.assertEqual(previous["record"]["deleted_at"], actual["record"]["deleted_at"])
            with self.assertRaises(CatalogConflict):
                self.service.update(previous["record"]["id"], {"notes": "stale"}, expected_etag=previous["etag"])
        restored = self.service.restore(trash["record"]["id"], expected_etag=self.service.entry(trash["record"]["id"])["entry"]["etag"])["entry"]
        self.assertEqual("uncategorized", restored["record"]["category_id"])

    def test_reserved_category_is_protected_and_rename_conflicts_are_atomic(self):
        default = self.service.catalog({})["categories"][0]
        for delete in (False, True):
            with self.assertRaises(InvalidRequest):
                self.store.change_category("Other", category_id=default["id"], expected_etag=default["etag"], delete=delete)
        a, b = self.category("Anime"), self.category("Animation")
        revision = self.store.state()
        with self.assertRaises(InvalidRequest):
            self.store.change_category("animation", category_id=a["id"], expected_etag=a["etag"])
        self.assertEqual(revision, self.store.state())
        with self.assertRaises(sqlite3.IntegrityError):
            with closing(sqlite3.connect(self.path)) as db:
                db.execute("DELETE FROM categories WHERE id='uncategorized'")

    def test_stale_work_and_category_versions_cannot_overwrite_newer_changes(self):
        entry = self.create()
        self.service.update(entry["record"]["id"], {"notes": "New"}, expected_etag=entry["etag"])
        with self.assertRaises(CatalogConflict):
            self.service.update(entry["record"]["id"], {"notes": "Old"}, expected_etag=entry["etag"])
        category = self.category("Anime")
        self.store.change_category("Animation", category_id=category["id"], expected_etag=category["etag"])
        with self.assertRaises(CatalogConflict):
            self.store.change_category(category_id=category["id"], expected_etag=category["etag"], delete=True)

    def test_filters_search_sort_and_pagination_execute_across_the_full_dataset(self):
        category = self.category("Anime")
        for n in range(109):
            entry = self.create(work_title=f"Work {n:03}", kind="task" if n % 2 else "project",
                                category_id=category["id"] if n < 10 else "uncategorized")
            if n < 5:
                self.transition(entry, "planned", priority="high")
        page = self.service.catalog({"page": "2", "page_size": "48", "sort": "title", "direction": "asc"})
        self.assertEqual((109, 3, 48), (page["total"], page["pages"], len(page["entries"])))
        self.assertEqual("Work 048", page["entries"][0]["record"]["work_title"])
        self.assertEqual(5, self.service.catalog({"priority": "high", "status": "planned"})["total"])
        self.assertEqual(5, self.service.catalog({"kind": "task", "category": category["id"]})["total"])
        self.assertEqual(10, self.service.catalog({"search": "ANIME"})["total"])
        self.assertEqual(1, self.service.catalog({"search": "Work 108"})["total"])
        self.assertEqual(1, self.service.catalog({"page": "999", "search": "Work 108"})["page"])
        for query in ({"page": "0"}, {"page_size": "101"}, {"sort": "notes;DROP TABLE works"}, {"status": "bad"}, {"priority": "medium"}):
            with self.assertRaises(InvalidRequest):
                self.service.catalog(query)

    def test_sql_constraints_guard_priority_and_foreign_keys(self):
        entry = self.create()
        with self.assertRaises(InvalidRequest):
            with self.store.connection(write=True) as db:
                db.execute("UPDATE works SET priority='high' WHERE id=?", (entry["record"]["id"],))
        with self.assertRaises(InvalidRequest):
            with self.store.connection(write=True) as db:
                db.execute("UPDATE works SET category_id='missing' WHERE id=?", (entry["record"]["id"],))
        self.assertEqual(entry, self.service.entry(entry["record"]["id"])["entry"])

    def test_backup_is_a_complete_consistent_database(self):
        self.create()
        path = self.path.with_name("backup.sqlite3")
        self.store.backup(path)
        self.assertEqual(1, WorkService(WorkStore(path)).catalog({})["total"])
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual("ok", db.execute("PRAGMA integrity_check").fetchone()[0])

    def test_unsupported_database_versions_are_not_overwritten(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA user_version=999")
        from pocket_entertainment_catalog.storage import StorageError
        with self.assertRaises(StorageError):
            WorkStore(self.path)

    def test_category_delete_rolls_back_reassignment_if_delete_fails(self):
        category = self.category("Anime")
        entry = self.create(category_id=category["id"])
        with self.store.connection(write=True) as db:
            db.execute("CREATE TRIGGER simulate_delete_failure BEFORE DELETE ON categories "
                       "BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
        revision = self.store.state()
        with self.assertRaises(InvalidRequest):
            self.store.change_category(category_id=category["id"], expected_etag=category["etag"], delete=True)
        self.assertEqual(entry, self.service.entry(entry["record"]["id"])["entry"])
        self.assertEqual(revision, self.store.state())

    def test_two_connections_with_the_same_version_cannot_both_save(self):
        entry = self.create()
        other = WorkService(WorkStore(self.path))
        barrier = threading.Barrier(2)
        results = []
        def write(service, note):
            barrier.wait()
            try:
                service.update(entry["record"]["id"], {"notes": note}, expected_etag=entry["etag"])
                results.append("saved")
            except CatalogConflict:
                results.append("conflict")
        threads = [threading.Thread(target=write, args=(s, str(i))) for i, s in enumerate((self.service, other))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(["conflict", "saved"], sorted(results))

    def test_export_includes_trash_and_daily_backups_are_valid_snapshots(self):
        entry = self.create()
        self.service.delete(entry["record"]["id"], expected_etag=entry["etag"])
        exported = self.store.export()
        self.assertEqual(1, exported["schema_version"])
        self.assertEqual(1, len(exported["works"]))
        self.assertIsNotNone(exported["works"][0]["deleted_at"])
        backups = list((self.path.parent / "work-backups").glob("*.sqlite3"))
        self.assertEqual(1, len(backups))
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual("ok", db.execute("PRAGMA integrity_check").fetchone()[0])

    def test_more_than_one_thousand_works_are_browsed_in_bounded_pages(self):
        for n in range(1201):
            self.create(work_title=f"Work {n:04}")
        last = self.service.catalog({"page": "26"})
        self.assertEqual((1201, 26, 1), (last["total"], last["pages"], len(last["entries"])))
        self.assertEqual("Work 0000", last["entries"][0]["record"]["work_title"])
        found = self.service.catalog({"search": "Work 1200"})
        self.assertEqual(1, found["total"])
        self.assertEqual("Work 1200", found["entries"][0]["record"]["work_title"])

    def test_daily_backups_keep_the_latest_seven_days_and_do_not_overwrite_a_day(self):
        with patch("pocket_entertainment_catalog.work_storage.datetime") as clock:
            date = clock.now.return_value.astimezone.return_value.date.return_value
            for day in range(1, 10):
                date.isoformat.return_value = f"2026-10-{day:02}"
                self.create()
            self.create()
        snapshots = sorted((self.path.parent / "work-backups").glob("*.sqlite3"))
        self.assertEqual(7, len(snapshots))
        self.assertTrue(snapshots[0].name.endswith("2026-10-03.sqlite3"))
        with closing(sqlite3.connect(snapshots[-1])) as db:
            self.assertEqual(9, db.execute("SELECT COUNT(*) FROM works").fetchone()[0])

    def test_backup_failure_does_not_misreport_a_committed_save(self):
        with patch.object(self.store, "_daily_backup", side_effect=OSError("Full backup disk")):
            with self.assertLogs("pocket_entertainment_catalog.work_storage", level="ERROR"):
                entry = self.create()
        self.assertEqual(entry, self.service.entry(entry["record"]["id"])["entry"])
