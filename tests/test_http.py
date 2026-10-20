from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pocket_entertainment_catalog.config import Settings
from pocket_entertainment_catalog.server import PocketCatalogServer

from .helpers import sample_record, write_catalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class HTTPApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        catalog = root / "data" / "catalog.jsonl"
        write_catalog(catalog, [sample_record()])
        settings = Settings(
            host="127.0.0.1",
            port=0,
            public_host="127.0.0.1",
            catalog_path=catalog,
            static_path=PROJECT_ROOT / "pocket_entertainment_catalog" / "static",
            repository_path=root,
        )
        self.server = PocketCatalogServer(settings)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = self.server.url.rstrip("/")
        self.owner_headers = {}
        status, grant = self.call(
            "/api/controller/claim", method="POST", payload={"page_id": "page-a"}
        )
        self.assertEqual(200, status)
        self.owner_headers = {"X-Page-Id": "page-a", "X-Controller-Token": grant["token"]}

    def tearDown(self) -> None:
        self.server.close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def call(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, object]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request_headers = dict(self.owner_headers if headers is None else headers)
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.base}{path}",
            data=body,
            method=method,
            headers=request_headers,
        )
        try:
            with urlopen(request, timeout=2) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
                value = json.loads(raw) if "json" in content_type else raw.decode("utf-8")
                return response.status, value
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_static_application_and_owned_read_api(self) -> None:
        status, health = self.call("/health", headers={})
        self.assertEqual(200, status)
        self.assertTrue(health["ok"])
        self.assertEqual("pocket_entertainment_catalog", health["app"])
        self.assertTrue(health["instance_id"])
        status, html = self.call("/")
        self.assertEqual(200, status)
        self.assertIn("Entertainment Catalog", html)

        status, payload = self.call("/api/catalog?deleted=include")
        self.assertEqual(200, status)
        self.assertEqual(1, len(payload["entries"]))  # type: ignore[index]
        self.assertIn("paused", payload["statuses"])  # type: ignore[index]

        status, lightweight = self.call("/api/state")
        self.assertEqual(200, status)
        self.assertEqual(payload["catalog_revision"], lightweight["catalog_revision"])  # type: ignore[index]
        self.assertNotIn("entries", lightweight)  # type: ignore[operator]

    def test_another_server_cannot_share_either_data_location(self):
        with self.assertRaisesRegex(RuntimeError, "Another server"):
            PocketCatalogServer(self.server.settings)
        other_catalog = self.server.settings.catalog_path.parent / "other.jsonl"
        write_catalog(other_catalog, [])
        with self.assertRaisesRegex(RuntimeError, "Another server"):
            PocketCatalogServer(replace(self.server.settings, catalog_path=other_catalog))

    def test_takeover_waits_for_the_complete_authorized_save(self):
        entered = threading.Event()
        finish_save = threading.Event()
        transferred = threading.Event()
        results = {}
        original_create = self.server.service.create

        def slow_create(payload):
            entered.set()
            if not finish_save.wait(2):
                raise RuntimeError("Test save was never released")
            return original_create(payload)

        def save():
            results["save"] = self.call(
                "/api/entries",
                method="POST",
                payload={"work_title": "Saved before takeover", "media_type": "manga"},
            )

        def takeover():
            results["takeover"] = self.call(
                "/api/controller/takeover", method="POST", payload={"page_id": "new-page"}
            )
            transferred.set()

        with patch.object(self.server.service, "create", slow_create):
            saving = threading.Thread(target=save)
            taking = threading.Thread(target=takeover)
            saving.start()
            try:
                self.assertTrue(entered.wait(1))
                taking.start()
                self.assertFalse(transferred.wait(0.1))
            finally:
                finish_save.set()
                saving.join(3)
                if taking.ident is not None:
                    taking.join(3)
        self.assertEqual(201, results["save"][0])
        self.assertEqual(200, results["takeover"][0])
        self.assertEqual(423, self.call("/api/catalog")[0])

    def test_controller_blocks_reads_and_explicit_takeover_revokes_previous_page(self) -> None:
        for path in (
            "/api/catalog",
            "/api/state",
            "/api/work/catalog",
            "/api/work/backup",
            "/api/work/export",
        ):
            status, payload = self.call(path, headers={})
            self.assertEqual(423, status, path)
            self.assertEqual("controller_transferred", payload["error"]["code"])
        status, payload = self.call(
            "/api/controller/claim", method="POST", payload={"page_id": "page-b"}
        )
        self.assertEqual(423, status)
        self.assertEqual("controller_occupied", payload["error"]["code"])
        status, grant = self.call(
            "/api/controller/takeover", method="POST", payload={"page_id": "page-b"}
        )
        self.assertEqual(200, status)
        self.assertEqual(423, self.call("/api/catalog")[0])
        current = {"X-Page-Id": "page-b", "X-Controller-Token": grant["token"]}
        self.assertEqual(200, self.call("/api/catalog", headers=current)[0])
        status, _ = self.call(
            "/api/controller/release",
            method="POST",
            payload={"page_id": "page-b", "token": grant["token"]},
        )
        self.assertEqual(200, status)
        self.assertEqual(409, self.call("/api/catalog", headers=current)[0])

    def test_mutation_needs_owner_headers_and_current_etag(self) -> None:
        status, catalog = self.call("/api/catalog?deleted=include")
        entry = catalog["entries"][0]  # type: ignore[index]
        path = f"/api/entries/{entry['record']['id']}"

        status, _payload = self.call(
            path, method="PATCH", payload={"notes": "No control"}, headers={}
        )
        self.assertEqual(423, status)
        headers = self.owner_headers | {"If-Match": entry["etag"]}
        status, updated = self.call(
            path, method="PATCH", payload={"notes": "Owned edit"}, headers=headers
        )
        self.assertEqual(200, status)
        self.assertEqual("Owned edit", updated["entry"]["record"]["notes"])  # type: ignore[index]

        status, stale = self.call(path, method="PATCH", payload={"notes": "Stale"}, headers=headers)
        self.assertEqual(412, status)
        self.assertEqual("edit_conflict", stale["error"]["code"])  # type: ignore[index]

    def work_owner(self):
        return dict(self.owner_headers)

    def test_work_starts_empty_and_mutations_are_isolated_from_journey_and_git(self):
        status, data = self.call("/api/work/catalog")
        self.assertEqual(200, status)
        self.assertEqual([], data["entries"])
        self.assertEqual("Uncategorized", data["categories"][0]["name"])
        before = self.server.store.path.read_bytes()
        payload = {"work_title": "Kaleido Star", "kind": "project", "category_id": "uncategorized"}
        self.assertEqual(
            423, self.call("/api/work/entries", method="POST", payload=payload, headers={})[0]
        )
        headers = self.work_owner()
        with patch.object(self.server.git_sync, "record_change") as git_change:
            status, response = self.call(
                "/api/work/entries", method="POST", payload=payload, headers=headers
            )
            self.assertEqual(201, status)
            entry = response["entry"]
            route = "/api/work/entries/" + entry["record"]["id"]
            headers["If-Match"] = entry["etag"]
            self.assertEqual(
                400,
                self.call(
                    route + "/transition",
                    method="POST",
                    payload={"status": "planned"},
                    headers=headers,
                )[0],
            )
            status, planned = self.call(
                route + "/transition",
                method="POST",
                payload={"status": "planned", "priority": "high"},
                headers=headers,
            )
            self.assertEqual(200, status)
            self.assertEqual("high", planned["entry"]["record"]["priority"])
            self.assertEqual(
                412,
                self.call(route, method="PATCH", payload={"notes": "stale"}, headers=headers)[0],
            )
            git_change.assert_not_called()
        self.assertEqual(before, self.server.store.path.read_bytes())

    def test_work_categories_require_control_and_current_version(self):
        path = "/api/work/categories"
        self.assertEqual(
            423, self.call(path, method="POST", payload={"name": "Anime"}, headers={})[0]
        )
        headers = self.work_owner()
        status, data = self.call(path, method="POST", payload={"name": "Anime"}, headers=headers)
        self.assertEqual(201, status)
        category = next(c for c in data["categories"] if c["name"] == "Anime")
        self.assertEqual(
            400, self.call(path, method="POST", payload={"name": " anime "}, headers=headers)[0]
        )
        route = path + "/" + category["id"]
        headers["If-Match"] = category["etag"]
        self.assertEqual(
            200, self.call(route, method="PATCH", payload={"name": "Animation"}, headers=headers)[0]
        )
        self.assertEqual(412, self.call(route, method="DELETE", headers=headers)[0])
        default = data["categories"][0]
        headers["If-Match"] = default["etag"]
        self.assertEqual(
            400, self.call(path + "/uncategorized", method="DELETE", headers=headers)[0]
        )

    def test_work_requests_share_the_origin_checks_and_filter_validation(self):
        headers = self.work_owner() | {"Origin": "http://untrusted.example"}
        self.assertEqual(
            403, self.call("/api/work/entries", method="POST", payload={}, headers=headers)[0]
        )
        self.assertEqual(400, self.call("/api/work/catalog?page_size=1000")[0])
        self.assertEqual(400, self.call("/api/work/catalog?status=all&status=planned")[0])
        self.assertEqual(400, self.call("/api/work/catalog?unknown=value")[0])
        self.assertEqual(200, self.call("/static/work.js")[0])

    def test_work_backup_download_is_sqlite(self):
        with urlopen(
            Request(self.base + "/api/work/backup", headers=self.owner_headers), timeout=2
        ) as response:
            self.assertEqual("application/vnd.sqlite3", response.headers["Content-Type"])
            self.assertTrue(response.read().startswith(b"SQLite format 3\0"))
        status, exported = self.call("/api/work/export")
        self.assertEqual(200, status)
        self.assertEqual(1, exported["schema_version"])
        self.assertEqual([], exported["works"])

    def test_explicit_legacy_route_requires_ownership_and_keeps_normal_creation_strict(self):
        payload = {
            "work_title": "Historical anime",
            "kind": "project",
            "category_id": "uncategorized",
            "status": "completed",
            "priority": "high",
        }
        self.assertEqual(
            423, self.call("/api/work/legacy", method="POST", payload=payload, headers={})[0]
        )
        headers = self.work_owner()
        self.assertEqual(
            400, self.call("/api/work/entries", method="POST", payload=payload, headers=headers)[0]
        )
        status, saved = self.call(
            "/api/work/legacy", method="POST", payload=payload, headers=headers
        )
        self.assertEqual(201, status)
        self.assertEqual("completed", saved["entry"]["record"]["status"])
        self.assertTrue(saved["entry"]["record"]["legacy_history"])
        self.assertTrue(all(v is None for v in saved["entry"]["record"]["dates"].values()))
        self.assertEqual(
            400,
            self.call(
                "/api/work/legacy",
                method="POST",
                payload=payload | {"priority": None},
                headers=headers,
            )[0],
        )


if __name__ == "__main__":
    unittest.main()
