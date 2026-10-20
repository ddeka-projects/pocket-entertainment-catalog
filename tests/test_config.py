"""Settings tests use temporary literal .env files, never personal configuration."""

from pathlib import Path
import tempfile
import unittest

from pocket_entertainment_catalog.config import PROJECT_ROOT, load_settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = Path(self.directory.name) / ".env"
        self.addCleanup(self.directory.cleanup)

    def test_defaults_and_environment_override(self):
        self.env.write_text("POCKET_ENTERTAINMENT_CATALOG_PORT=8000\n", encoding="utf-8")
        settings = load_settings(self.env, environ={"POCKET_ENTERTAINMENT_CATALOG_PORT": "8767"})
        self.assertEqual(8767, settings.port)
        self.assertEqual(PROJECT_ROOT / "data/catalog.jsonl", settings.catalog_path)
        self.assertEqual(PROJECT_ROOT / "data/editing.sqlite3", settings.work_path)

    def test_settings_are_independent_of_process_working_directory(self):
        self.env.write_text(
            "POCKET_ENTERTAINMENT_CATALOG_DATA=custom/library.jsonl\n"
            "POCKET_ENTERTAINMENT_CATALOG_WORK_DATA=custom/work.sqlite3\n",
            encoding="utf-8",
        )
        settings = load_settings(self.env, environ={})
        self.assertEqual(PROJECT_ROOT / "custom/library.jsonl", settings.catalog_path)
        self.assertEqual(PROJECT_ROOT / "custom/work.sqlite3", settings.work_path)

    def test_invalid_configuration_reports_the_setting(self):
        for name, value in [
            ("PORT", "bad"),
            ("PORT", "0"),
            ("PORT", "65536"),
            ("HOST", ""),
            ("DATA", ""),
            ("WORK_DATA", ""),
        ]:
            with self.subTest(name=name, value=value):
                key = "POCKET_ENTERTAINMENT_CATALOG_" + name
                with self.assertRaisesRegex(ValueError, key):
                    load_settings(self.env, environ={key: value})

    def test_journey_and_work_must_not_share_a_file(self):
        with self.assertRaisesRegex(ValueError, "different data files"):
            load_settings(
                self.env,
                environ={
                    "POCKET_ENTERTAINMENT_CATALOG_DATA": "data/shared",
                    "POCKET_ENTERTAINMENT_CATALOG_WORK_DATA": "data/shared",
                },
            )

    def test_retired_keys_cannot_silently_select_default_data(self):
        for suffix in ("HOST", "PORT", "PUBLIC_HOST", "DATA", "WORK_DATA"):
            previous = f"POCKET_CATALOG_{suffix}"
            current = f"POCKET_ENTERTAINMENT_CATALOG_{suffix}"
            with self.subTest(setting=previous):
                with self.assertRaisesRegex(ValueError, f"Rename {previous} to {current}"):
                    load_settings(self.env, environ={previous: "custom"})
        settings = load_settings(
            self.env,
            environ={
                "POCKET_CATALOG_DATA": "old/location.jsonl",
                "POCKET_ENTERTAINMENT_CATALOG_DATA": "current/location.jsonl",
            },
        )
        self.assertEqual(PROJECT_ROOT / "current/location.jsonl", settings.catalog_path)
