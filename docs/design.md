# Application design

## Product shape

This is a personal pocket catalog, not a general-purpose media database. The primary object is a **journey unit** chosen by the user: for example, a television season, a split cour, a special finale, a game, or a meaningful live-service segment. Unit titles stay deliberately flexible and editable.

The interface is mobile-first and uses the same application on a phone and desktop. It emphasizes quick browsing and lifecycle progress while keeping database mechanics out of the normal flow.

## Information architecture

The catalog screen has four layers:

1. **At-a-glance summary** — total active journeys plus ongoing, paused, planned, and investigate counts.
2. **Find and narrow** — full-text search, lifecycle filters, media filters, and sorting.
3. **Journey cards** — work title, optional unit, classification, status, relevant date, year, and rating.
4. **Detail sheet** — notes/review, full lifecycle timeline, progress actions, corrections, and technical record information.

On a phone, details open as a bottom sheet. On a larger screen, the same sheet becomes a right-side panel, preserving a single interaction model across devices.

## Browser control and server lifecycle

One page controls the whole app, including browsing. The first page claims control; another page explicitly chooses **Take Control**. Each accepted claim gets a random server token. Every data request supplies `X-Page-Id` and `X-Controller-Token`. Unauthorized reads and writes are rejected; public assets and `/health` remain available for startup.

`POST /api/controller/claim` and `/takeover` accept `{page_id}`; `/release` accepts `{page_id, token}`. Control never expires. No heartbeat or ownership polling is used. A single operation guard serializes takeover with complete protected requests, so a save finishes before control transfers. Ownership is separate from the safeguards for persisted data:

- **ETag / `If-Match`** rejects stale record edits;
- **cross-process file lock** serializes writers;
- **disk digest check + atomic replace** detects external changes and prevents partial JSONL writes;
- **SQLite transactions and snapshots** protect local Work records;
- **lifetime process guards** reserve both Journey and Work paths for one running server, even across different ports.

The operating system releases server guards after normal exit or a crash. Scheduled startup uses console-free Python; foreground startup remains available for troubleshooting. Application and startup errors go to `logs/server.log`, rotating at 5 MiB with three backups.

Equivalent operating concerns use the same small modules as the sibling repositories: `app.py`, `config.py`, `controller.py`, `env.py`, `errors.py`, `logging_setup.py`, `health.py`, and `process_lock.py`. HTTP remains standard-library based; this app needs no web framework dependency.

## Configuration and module roles

`__main__.py` delegates to `app.py`, which configures logging, loads `Settings` through `load_settings(env_file=None, *, environ=None)`, and starts `server.py`. `api.py` handles HTTP policy and serialization; `service.py` and `work_service.py` coordinate domain operations; `storage.py` and `work_storage.py` preserve the independent Journey and Work stores. Browser assets live in `static/` and use `/static/` URLs.

The current settings are in [`.env.example`](../.env.example), using the `POCKET_ENTERTAINMENT_CATALOG_` prefix. `.env` values are literal UTF-8, with an optional BOM and matching quotes; real environment values override the file. Duplicate assignments and malformed quoting are rejected. Relative data paths start at the repository root, independent of the launcher's current directory. Journey and Work cannot point to the same file.

`/health` returns the package name and current process identity. Installation and restart scripts require a fresh instance, preventing an old or unrelated listener from being accepted as a successful restart.

## Lifecycle behavior

The normal path is intentionally strict:

```text
Investigate → Planned → Ongoing ⇄ Paused
                         ├→ Completed
                         └→ Discontinued
```

Each dated transition accepts a calendar date, not a timestamp. Pausing and resuming preserve the original Started date and do not add a lifecycle milestone. A Paused journey must return to Ongoing before it can be completed or discontinued. Terminal entries may receive an integer rating from 1 to 10.

Untouched historical imports may retain four unknown lifecycle dates. Once any lifecycle date is known, every milestone through the current status is required and chronological. If a normal transition is backdated before an earlier milestone—or follows a legacy record with missing prerequisites—the interface asks for confirmation before atomically aligning those affected earlier milestones to the selected date.

“Correct history” is a separate, less prominent action for fixing old or mistaken records without weakening the everyday sequential workflow.

## Creation defaults

Only `work_title` must be typed when adding an entry. The application supplies:

- a collision-resistant ID made from a readable title slug and a ULID derived from creation time;
- initial `investigate` status;
- the local calendar date as `investigated_on`;
- exact UTC technical timestamps;
- null optional fields;
- media type from the active media filter, the last-used value, or Anime on a first visit.

All supplied values except server-owned technical fields remain editable.

## Persistence and Git

The JSONL is the source of truth. Records retain stable file order, which also preserves the old-to-new ordering of the historical import; newly created records append to the end.

After each safe local save, the server commits just the catalog file and queues a background push. The controlling page sees the saved local state without waiting for the network. Failed pushes remain explicit and retryable. The server never automatically merges or force-pushes divergent history.

## Trust boundary

The server is designed for one trusted private network:

- private or loopback client addresses only;
- validated `Host` header;
- same-origin state-changing requests;
- strict JSON request bodies and field allowlists;
- no public-internet exposure.

There is intentionally no account or password layer in this version. Adding remote hosting later would require real authentication, TLS, authorization, and a different persistence strategy.

## Deliberately deferred

- A finer model for live-service game segments such as Wuthering Waves, Goddess of Victory: Nikke, and Neverness to Everness.
- Cover art and third-party metadata providers.
- Rewatch/replay history, because it is not part of the stated use case.
- Manual list ranking for Planned and Investigate.
- Remote multi-writer merging. The chosen model is one canonical running server with one controlling browser page.

The visual design uses warm dark surfaces, subtle category colors, large readable titles, and restrained controls. It is meant to be iterated from actual phone and desktop use rather than treated as final.
