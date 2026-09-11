from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest
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
        request_headers = dict(headers or {})
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

    def test_static_application_and_open_read_api(self) -> None:
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

    def test_one_editor_does_not_block_other_readers(self) -> None:
        owner = {"client_id": "client-a", "page_id": "page-a"}
        status, _payload = self.call("/api/editor/claim", method="POST", payload=owner)
        self.assertEqual(200, status)

        # A second device may continue browsing while the first one edits.
        status, payload = self.call("/api/catalog?deleted=include")
        self.assertEqual(200, status)
        self.assertTrue(payload["editor"]["claimed"])  # type: ignore[index]

        status, payload = self.call(
            "/api/editor/claim",
            method="POST",
            payload={"client_id": "client-b", "page_id": "page-b"},
        )
        self.assertEqual(423, status)
        self.assertEqual("lease_conflict", payload["error"]["code"])  # type: ignore[index]

    def test_mutation_needs_owner_headers_and_current_etag(self) -> None:
        status, catalog = self.call("/api/catalog?deleted=include")
        entry = catalog["entries"][0]  # type: ignore[index]
        path = f"/api/entries/{entry['record']['id']}"

        status, _payload = self.call(path, method="PATCH", payload={"notes": "No lease"})
        self.assertEqual(400, status)

        owner = {"client_id": "client-a", "page_id": "page-a"}
        self.call("/api/editor/claim", method="POST", payload=owner)
        headers = {
            "X-Catalog-Client": owner["client_id"],
            "X-Catalog-Page": owner["page_id"],
            "If-Match": entry["etag"],
        }
        status, updated = self.call(path, method="PATCH", payload={"notes": "Owned edit"}, headers=headers)
        self.assertEqual(200, status)
        self.assertEqual("Owned edit", updated["entry"]["record"]["notes"])  # type: ignore[index]

        status, stale = self.call(path, method="PATCH", payload={"notes": "Stale"}, headers=headers)
        self.assertEqual(412, status)
        self.assertEqual("edit_conflict", stale["error"]["code"])  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
