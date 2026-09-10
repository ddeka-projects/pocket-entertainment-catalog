from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pocket_entertainment_catalog.git_sync import SyncSnapshot
from pocket_entertainment_catalog.model import new_record_id


class LocalOnlyGitSync:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self._snapshot = SyncSnapshot(
            "unavailable",
            "Test catalog is local only.",
            None,
        )

    def snapshot(self) -> SyncSnapshot:
        return self._snapshot

    def record_change(self, message: str) -> SyncSnapshot:
        self.messages.append(message)
        return self._snapshot

    def retry(self) -> SyncSnapshot:
        return self._snapshot


def sample_record(**overrides: Any) -> dict[str, Any]:
    created_at = "2026-09-09T12:00:00.000Z"
    work_title = str(overrides.get("work_title", "Sample Journey"))
    value: dict[str, Any] = {
        "id": new_record_id(work_title, created_at),
        "work_title": work_title,
        "unit_title": "Season 1",
        "media_type": "animation",
        "status": "investigate",
        "rating": None,
        "release_year": 2026,
        "dates": {
            "investigated_on": "2026-09-09",
            "planned_on": None,
            "started_on": None,
            "ended_on": None,
        },
        "notes": None,
        "external_ids": {},
        "created_at": created_at,
        "updated_at": created_at,
        "deleted_at": None,
    }
    value.update(overrides)
    return value


def write_catalog(path: Path, records: list[dict[str, Any]]) -> None:
    payload = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
