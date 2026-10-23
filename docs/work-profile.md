# Work profile data contract

Work is a separate editing backlog, independent of Journey, Clip Selector folders, Premiere projects, and exported media filenames. Projects and tasks are peer work kinds, with the same lifecycle and shared metadata; Source Ongoing applies only to projects. SQLite is accessed through Python's standard library. It requires no additional service or installed dependency.

## Storage

`POCKET_ENTERTAINMENT_CATALOG_WORK_DATA` defaults to `data/editing.sqlite3`. SQLite `user_version` is 2; an unrecognized version is rejected. The initial database contains an empty `works` table and one reserved category, `uncategorized` / `Uncategorized`. Database initialization is transactional.

Version 1 databases migrate automatically on startup in a single write transaction: add the non-null `source_ongoing` column with a default of 0, explicitly backfill every existing work to 0, then advance `user_version` to 2. This includes every kind, lifecycle status, legacy record, and Trash entry. IDs, sequence/order, categories, dates, timestamps, and all other work fields are preserved. Revision triggers still observe the backfill. Failure rolls back the column, backfill, revision changes, and version together; restarting after success does not reset any flags. Existing databases and restored version 1 SQLite backups need no manual recreation.

Each request owns its SQLite connection. Foreign keys are enabled on every connection. Writes take a short `BEGIN IMMEDIATE` transaction with a five-second busy timeout. This serializes writes before checking the opened record's ETag, so stale versions cannot overwrite newer changes. SQLite handles journaled commits and rollback. The database stays on the server's local disk.

The `metadata` revision counter is advanced by triggers for all work/category inserts, updates, and deletes. `/api/work/state` lets readers notice changes without downloading the dataset. Category reassignment and deletion commit together; any failure rolls both back.

## Works

| Field | Contract |
| --- | --- |
| `id` | Server-owned work-title slug plus creation-time ULID with random entropy; immutable across renames. |
| `work_title` | Required trimmed single-line text, up to 300 characters. Can identify a title, creator, or account. |
| `unit_title` | Optional trimmed single-line scope, up to 200 characters. |
| `kind` | `project` or `task`; editable. |
| `source_ongoing` | Boolean, default false for normal and legacy creation. Indicates a project's source material is still being released, independently of lifecycle status. Always false for tasks; SQLite stores 0/1 with a non-null default and checks. |
| `category_id` | Required reference to an existing category; defaults to Uncategorized in the creation form. |
| `status` | `investigate`, `planned`, `ongoing`, `paused`, `completed`, or `discontinued`. |
| `priority` | Null for Investigate; `high` or `low` in all other statuses. |
| `release_year` | Optional integer from 1000 through 9999. |
| `notes` | Optional text, up to 20,000 characters. |
| `dates` | Four nullable `YYYY-MM-DD` milestones: `investigated_on`, `planned_on`, `started_on`, `ended_on`. Flattened into columns in SQLite. |
| `created_at`, `updated_at`, `deleted_at` | Server-owned UTC timestamps with milliseconds; deletion is soft. |
| `legacy_history` | Server-owned boolean. False for normal creations; true for the explicit legacy-creation route. Permits entirely unknown historical dates. Cannot be assigned or edited in request bodies. |

Title/category/unit combinations are deliberately **not unique**: another editing effort gets another ID. SQLite also maintains an internal monotonic `sequence` for stable work ordering.

Status changes follow the same sequential graph as Journey. Investigate → Planned must include a selected priority in the transition request. Pause/resume preserves Started. Completed and Discontinued are terminal. Dates must include every milestone through the current status, be chronological, and omit milestones beyond that status. Legacy records may instead have all four dates null; partially known legacy history obeys the normal rules. Backdating or filling missing prerequisites requires `adjust_prior_dates: true`.

The date-correction endpoint changes dates only, never status or legacy eligibility. The update endpoint cannot alter technical fields or bypass transitions. Unknown request fields are rejected. Model validation and SQL checks enforce priority/status, required milestones, chronology, timestamp ordering, kinds, and category references.

`source_ongoing` is editable through creation and detail updates for projects in any lifecycle status, including Completed and Discontinued. It does not change status, priority, dates, or other records, and transitions preserve it. Tasks reject `source_ongoing: true`; changing kind to task clears an existing flag when the request omits it. Non-boolean values are rejected. Trash retains the flag and must be restored before editing, following the existing detail-edit rules.

## Categories

Categories contain `id`, `name`, and an internal unique `name_key`. Names are normalized with Unicode NFKC, collapsed whitespace, and case folding for uniqueness. Display names preserve the chosen capitalization. Maximum length is 100 characters. Category responses include an ETag and a count of all referencing works, including Trash.

Reserved Uncategorized is protected against both rename and deletion, including by SQLite triggers. Category deletion explicitly reassigns every referencing work to its ID and advances each affected work's `updated_at`, invalidating stale work edits. Renaming a category preserves its ID and work references. Rename conflicts are rejected; categories are never implicitly merged.

## HTTP

All routes use the existing trusted-LAN, Host, same-origin, and JSON-body policies. All data routes, including reads and backup downloads, require page control through `X-Page-Id` / `X-Controller-Token` headers. Updates/deletes/transitions require the current `If-Match` ETag. Category creation and work creation need page control but no prior ETag.

| Route | Behavior |
| --- | --- |
| `GET /api/work/catalog` | Filtered, sorted page plus counts, categories, and revision. |
| `GET /api/work/state` | Lightweight revision. |
| `GET /api/work/entries/{id}` | One work and ETag, including soft-deleted works. |
| `POST /api/work/entries` | Create an Investigate work with today's local calendar date. |
| `POST /api/work/legacy` | Explicit historical creation: normal creation fields plus required `status` and valid `priority`. The server marks the record as legacy and sets all four dates null. Uses the same page controller. |
| `PATCH /api/work/entries/{id}` | Edit details and valid priority. |
| `POST /api/work/entries/{id}/transition` | Sequential status change, date, optional notes, and required priority when planning. |
| `PUT /api/work/entries/{id}/history` | Correct `dates` for the existing status. |
| `DELETE /api/work/entries/{id}` | Move to Trash. |
| `POST /api/work/entries/{id}/restore` | Restore with an empty JSON body. |
| `GET /api/work/categories` | Category list. |
| `POST /api/work/categories` | Create from `{name}`. |
| `PATCH /api/work/categories/{id}` | Rename from `{name}`, with category ETag. |
| `DELETE /api/work/categories/{id}` | Reassign works and remove category, with category ETag. |
| `GET /api/work/backup` | Download a consistent SQLite backup using the online backup API. |
| `GET /api/work/export` | Download a versioned JSON snapshot of all categories and works, including Trash. |

Catalog query parameters are `status` (`all`/`trash`/lifecycle), `category` (ID or `all`), `kind`, `priority` (`high`/`low`/`none`/`all`), `source_ongoing` (`all`/`true`/`false`), `search`, `sort` (`journey` for insertion order / `title` / `updated` / `priority`), `direction` (`asc`/`desc`), `page`, and `page_size` (1–100; UI uses 48). Both explicit source filters return projects only, combined with all other filters; `all` includes projects and tasks. Combining a source filter with `kind=task` returns no entries. Unknown or repeated parameters are rejected. Status summary counts cover the full dataset, independently of active filters. Pagination clamps a now-empty last page after mutations.

Normal and legacy creation accept optional `source_ongoing`; omission means false. `PATCH /api/work/entries/{id}` accepts the boolean independently of other detail fields. Entry, catalog, and JSON export responses always include it as a JSON boolean for every work, including tasks and Trash. JSON export `schema_version` is 2.

## Backups

Database writes do not invoke Git. Once per local day, after a successful write, a consistent snapshot is saved beside the configured database under `work-backups/<database-stem>-YYYY-MM-DD.sqlite3`; the latest seven snapshots for that stem are retained. Further writes that day leave that snapshot intact. A failed automatic backup is logged without misreporting an already committed work save. Download backup creates a fresh snapshot on demand. Export is for inspection and future backfill tooling. Legacy records can be added individually through the dedicated route; there is no bulk import endpoint.
