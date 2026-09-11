# Catalog data model

`data/catalog.jsonl` contains one JSON object per journey unit. JSONL keeps changes reviewable in Git and lets the server replace the file atomically without a separate database service.

## Record shape

```json
{
  "id": "example-work--01K4R7Y8M0ABCDEF1234567890",
  "work_title": "Example Work",
  "unit_title": "Season 2",
  "media_type": "animation",
  "status": "ongoing",
  "rating": null,
  "release_year": 2026,
  "dates": {
    "investigated_on": "2026-01-10",
    "planned_on": "2026-02-02",
    "started_on": "2026-09-09",
    "ended_on": null
  },
  "notes": "Current thoughts about the journey.",
  "external_ids": {},
  "created_at": "2026-09-09T19:42:15.231Z",
  "updated_at": "2026-09-09T19:42:15.231Z",
  "deleted_at": null
}
```

Initial imported records also have:

```json
"import": { "source": "completed", "position": 17 }
```

Completed-sheet positions preserve the known old-to-new legacy order. Planned imports retain only their source because their sheet positions had no semantic meaning. New web-app records have no `import` field.

## Fields

| Field | Type | Ownership and meaning |
| --- | --- | --- |
| `id` | string | Server-owned stable ID: title slug plus a creation-time ULID. Renaming a work never changes it. |
| `work_title` | string | The only value the user must type for a new entry. |
| `unit_title` | string or null | Flexible journey label such as `Season 1`, `Part 2`, or `Finale`. |
| `media_type` | enum | `anime`, `animation`, `game`, `live_action_movie`, `live_action_series`, `manga`, or `visual_novel`. |
| `status` | enum | `investigate`, `planned`, `ongoing`, `paused`, `completed`, or `discontinued`. |
| `rating` | integer or null | Whole number from 1 through 10; permitted only for terminal statuses. |
| `release_year` | integer or null | Four-digit year. |
| `dates` | object | Nullable lifecycle calendar dates in `YYYY-MM-DD` form. |
| `notes` | string or null | Mutable notes during a journey or a final impression/review afterward. |
| `external_ids` | object | Reserved string-to-string identifiers for future metadata integrations. |
| `import` | object, optional | Immutable provenance for the one-time Sheets migration. |
| `created_at` | timestamp | Server-owned exact UTC creation time with millisecond precision. |
| `updated_at` | timestamp | Server-owned exact UTC time of the most recent material change. |
| `deleted_at` | timestamp or null | Server-owned soft-deletion time; records are never hard-deleted through the app. |

## Invariants

- IDs are unique and immutable.
- The combination of media type, case-insensitive work title, and case-insensitive unit title identifies one journey unit.
- Untouched legacy imports may have all four lifecycle dates null. Otherwise every date through the current status is required.
- Known lifecycle dates must be chronological; equal milestone dates are permitted.
- Investigate entries cannot contain later-stage dates.
- Planned entries cannot contain started or ended dates.
- Ongoing and Paused entries require Investigated, Planned, and Started dates and cannot contain an Ended date.
- Paused entries can transition only back to Ongoing; pausing and resuming preserve the original Started date.
- Only Completed and Discontinued entries may have an ended date or rating.
- Technical timestamps are exact UTC instants; lifecycle values are calendar dates and are used for user-facing analysis.
- `updated_at` never precedes `created_at`; a non-null `deleted_at` falls between them.
- Unknown fields are rejected rather than silently stored.

The server validates the complete catalog before every replacement, so a mutation cannot leave one valid changed record beside an invalid pre-existing record.
