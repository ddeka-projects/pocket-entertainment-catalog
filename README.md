# Pocket Entertainment Catalog

A small, self-hosted catalog for tracking entertainment as journey units: a season, a movie, a game, a manga series, or any other continuous segment that makes sense at the time.

Use the **Journey** profile to track watching, reading, and gaming, and the **Work** profile to plan and track personal editing projects and tasks. Switch profiles using the tabs below the header. Both use the same mobile-friendly cards and editing sheets, with independent records and progress.

Journey's database is [`data/catalog.jsonl`](data/catalog.jsonl), with automatic Git synchronization. Work uses a separate local SQLite database, created automatically on first startup. The application uses only Python's standard library, so there is no package-install step.

## Work profile

Open **Work** to add a project or task. Only the reserved **Uncategorized** category exists initially; no sample works or media categories are seeded. A work has a required work title and an optional unit title, so a season, trailer, or individual creator video can be grouped under its broader title or account. Repeated editing efforts can share titles and units; each gets a distinct, stable title-slug-plus-ULID ID.

Works follow `Investigate → Planned → Ongoing ⇄ Paused → Completed / Discontinued`. A paused work must resume before finishing. Moving to Planned requires an explicit **High** or **Low** priority. Investigate has no priority; every later status retains a required, editable priority. Works have optional release years and notes, without ratings.

Use **Categories** to create, rename, or delete categories. Names are unique after Unicode, whitespace, and case normalization. Work forms only select existing categories. Uncategorized is the default and cannot be renamed or deleted. Deleting another category atomically moves its works, including Trash, to Uncategorized.

Lifecycle dates use Journey's chronology and backdating confirmation rules. **Correct dates** adjusts milestones for the current status without bypassing sequential transitions. Normal records cannot erase their entire lifecycle history.

To enter historical work yourself, open **Add work → Adding old work? Add legacy work**. Select its existing status and a High/Low priority (except Investigate, which has no priority). All four lifecycle dates are saved as unknown, with the record explicitly marked as legacy. You can fill dates later using **Correct dates**. Ordinary new works still start in Investigate with today's date and progress sequentially; a legacy work's subsequent transitions also follow that progression. Bulk spreadsheet import is not provided.

Work searches, filters (status, category, kind, priority), sorting, and 48-record pagination run on the server. Work shares the single-editor lease and stale-record safeguards with Journey. Reads remain available to other devices.

Work data lives in `data/editing.sqlite3` by default and is excluded from Git. It never creates catalog commits or pushes. Open the **Local storage** button in the header for **Download backup**, which produces a consistent SQLite snapshot, or **Export JSON**, which downloads all categories and works, including Trash. The first successful write each local day also creates a snapshot under `data/work-backups/`, retaining the latest seven daily snapshots for that database. Backup failures are logged separately from successful primary saves. These files are local: include them in your computer's backup plan.

To restore a downloaded SQLite backup, stop the server, copy the backup over the configured Work database, and restart. Keep a copy of the database you are replacing. See [the Work data contract](docs/work-profile.md) for storage and API details.

## Start the app

From this folder, run:

```sh
python3 run.py
```

The server prints an address such as `http://192.168.1.20:8766/`. Open it on the computer running the server or on another device connected to the same private network. Keep the terminal open while using the app.

The default port is `8766`. If macOS asks whether Python may accept incoming connections, allow it for private networks so a phone can connect.

## Windows scheduled startup

The Windows launcher and Task Scheduler scripts live under `scripts`. The server starts when your Windows user signs in, under that account with limited privileges. This lets the application use the same Git author configuration and GitHub credentials that work in your normal terminal. The PC must be signed in and awake for other devices to connect.

First, confirm the launcher works from PowerShell in the project folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-server.ps1
```

Stop it with `Ctrl+C`, then install and immediately start the scheduled task:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-startup-task.ps1
```

The task is named `Pocket Entertainment Catalog`. It starts at user logon without opening a terminal, ignores duplicate starts, has no runtime limit, and retries up to three times when the server exits with an error. Logs are appended to:

```text
.tmp\server.log
```

If Python is not discovered automatically, add its full path to `.env`:

```dotenv
POCKET_CATALOG_PYTHON=C:\Python313\python.exe
```

Use a stable Python installation path; the scripts require Python 3.10 or newer. The scheduled task uses the console-free `pythonw.exe` installed beside the configured `python.exe`.

After updating the code or `.env`, finish active saves and rerun the installer to restart the server.

To stop and remove automatic startup:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\remove-startup-task.ps1
```

If the task exists but the phone cannot connect, allow the `pythonw.exe` path printed by the installer through Windows Defender Firewall on **Private** networks and confirm that the phone and PC are on the same network. Use the PC's LAN address, such as `http://192.168.1.20:8766/`, rather than `localhost` on the phone.

## What the first version supports

- Browse from any number of devices at the same time.
- Search, sort, and filter by lifecycle status or media type.
- Add a journey; only the work title needs to be typed. Media type defaults to the active filter or the last-used value.
- Move entries through `Investigate → Planned → Ongoing → Completed / Discontinued` with lifecycle dates, or pause an Ongoing journey and resume it later.
- Confirm automatic alignment of missing or later prerequisite dates when recording a backdated transition.
- Edit titles, flexible unit labels, media types, release years, notes/reviews, and terminal ratings.
- Correct backfilled lifecycle history when the normal sequential workflow is not appropriate.
- Soft-delete entries to Trash and restore them later.
- Preserve `id`, `created_at`, `updated_at`, and `deleted_at` as server-owned fields.
- Notice valid external changes to the JSONL and refresh readers automatically.

## One editor, many readers

Reading never claims a lock. The app claims its single editing lease only when a page opens an add/edit action or performs a lifecycle mutation.

The page renews the lease every five seconds. Closing the editor releases it; an abandoned lease expires after fifteen seconds. Every save also carries the record version that was originally opened, so a stale browser cannot silently overwrite a newer change.

Run only one catalog server instance. A filesystem lock and atomic replacement protect the JSONL against accidental concurrent processes, but the visible editing lease belongs to one running server.

## GitHub synchronization

When this folder is a Git repository with an `origin` remote, every successful catalog mutation:

1. atomically saves the JSONL;
2. commits only `data/catalog.jsonl`; and
3. pushes committed changes to `origin` in the background.

The save remains successful if the network is offline or GitHub rejects the push. The UI then shows **Sync issue**, and its status button retries the push. A restart also retries commits that are locally ahead of the configured upstream.

The app deliberately does not fetch, pull, auto-merge, or force-push. If the remote branch is changed independently, resolve that divergence explicitly rather than risking an automatic catalog merge. Git credentials must already work for the configured remote.

The app remains usable without Git; the header says **Local only** until the repository and `origin` are configured, then the server is restarted.

## Configuration

Copy [`.env.example`](.env.example) to `.env` only when a default needs changing:

```dotenv
POCKET_CATALOG_HOST=0.0.0.0
POCKET_CATALOG_PORT=8766
POCKET_CATALOG_PUBLIC_HOST=
POCKET_CATALOG_DATA=data/catalog.jsonl
POCKET_CATALOG_WORK_DATA=data/editing.sqlite3
POCKET_CATALOG_PYTHON=
```

`0.0.0.0` allows private-network devices to connect. `POCKET_CATALOG_PUBLIC_HOST` only changes the address printed at startup and is normally discovered automatically.

`POCKET_CATALOG_WORK_DATA` accepts a local SQLite path, relative to the project folder or absolute. Its parent directory is created automatically. Keep custom database paths and backups out of Git as well. The database belongs on the server's local disk; phones access it through HTTP.

This is a trusted-LAN application without user accounts. It rejects non-private peers and cross-origin mutations, but the port should never be exposed to the public internet.

## Verify

Run all schema, storage, lease, HTTP, and Git synchronization tests with:

```sh
python3 -m unittest discover -v
```

The HTTP tests bind only to `127.0.0.1` on a temporary port and all mutation tests use disposable catalog files. They do not modify the checked-in database.

The architecture and UI rationale are recorded in [`docs/design.md`](docs/design.md), and the exact JSONL contract is in [`docs/data-model.md`](docs/data-model.md).
