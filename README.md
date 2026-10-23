# Pocket Entertainment Catalog

A small, self-hosted catalog for tracking entertainment as journey units: a season, a movie, a game, a manga series, or any other continuous segment that makes sense at the time.

Use the **Journey** profile to track watching, reading, and gaming, and the **Work** profile to plan and track personal editing projects and tasks. Switch profiles using the tabs below the header. Both use the same mobile-friendly cards and editing sheets, with independent records and progress.

Journey's database is [`data/catalog.jsonl`](data/catalog.jsonl), with automatic Git synchronization. Work uses a separate local SQLite database, created automatically on first startup. The application uses Python 3.11 or newer and only the standard library. Windows setup creates a repository-local `.venv`, matching the sibling apps.

## Work profile

Open **Work** to add a project or task. Only the reserved **Uncategorized** category exists initially; no sample works or media categories are seeded. A work has a required work title and an optional unit title, so a season, trailer, or individual creator video can be grouped under its broader title or account. Repeated editing efforts can share titles and units; each gets a distinct, stable title-slug-plus-ULID ID.

Works follow `Investigate → Planned → Ongoing ⇄ Paused → Completed / Discontinued`. A paused work must resume before finishing. Moving to Planned requires an explicit **High** or **Low** priority. Investigate has no priority; every later status retains a required, editable priority. Works have optional release years and notes, without ratings.

For a project whose manga, anime, or other source material is still being released, enable **Source Ongoing** in **Add work** or **Edit details**. It defaults to off and remains editable in every status, including Completed and Discontinued. Enabled projects show a subtle card indicator. The **Source** filter finds projects with or without the flag alongside the other Work filters. Tasks do not use this flag; changing a project to a task clears it. This setting does not change lifecycle status, priority, or tasks.

Use **Categories** to create, rename, or delete categories. Names are unique after Unicode, whitespace, and case normalization. Work forms only select existing categories. Uncategorized is the default and cannot be renamed or deleted. Deleting another category atomically moves its works, including Trash, to Uncategorized.

Lifecycle dates use Journey's chronology and backdating confirmation rules. **Correct dates** adjusts milestones for the current status without bypassing sequential transitions. Normal records cannot erase their entire lifecycle history.

To enter historical work yourself, open **Add work → Adding old work? Add legacy work**. Select its existing status and a High/Low priority (except Investigate, which has no priority). All four lifecycle dates are saved as unknown, with the record explicitly marked as legacy. You can fill dates later using **Correct dates**. Ordinary new works still start in Investigate with today's date and progress sequentially; a legacy work's subsequent transitions also follow that progression. Bulk spreadsheet import is not provided.

Work searches, filters (status, category, kind, priority, source), sorting, and 48-record pagination run on the server. Work shares the single controlling page and stale-record safeguards with Journey.

Work data lives in `data/editing.sqlite3` by default and is excluded from Git. It never creates catalog commits or pushes. Open the **Local storage** button in the header for **Download backup**, which produces a consistent SQLite snapshot, or **Export JSON**, which downloads all categories and works, including Trash. The first successful write each local day also creates a snapshot under `data/work-backups/`, retaining the latest seven daily snapshots for that database. Backup failures are logged separately from successful primary saves. These files are local: include them in your computer's backup plan.

On startup, existing Work databases upgrade automatically to schema version 2. The migration explicitly sets `source_ongoing = false` for every existing entry, including Tasks and Trash, preserving all records and other field values. No database recreation or manual migration is needed.

To restore a downloaded SQLite backup, stop the server, copy the backup over the configured Work database, and restart. Keep a copy of the database you are replacing. See [the Work data contract](docs/work-profile.md) for storage and API details.

## First-time Windows setup

Use Windows PowerShell 5.1+ and Python 3.11 or newer. From the repository folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap-windows.ps1
```

Bootstrap creates the repository-local `.venv`, prepares its requirements, and creates `.env` from [`.env.example`](.env.example) when missing. It preserves existing configuration and data. The defaults work without editing `.env`; see [Configuration](#configuration) for optional changes.

Confirm foreground operation from ordinary PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-dev.ps1
```

Open `http://127.0.0.1:8766/` on the PC or the printed LAN address, such as `http://192.168.1.20:8766/`, on your phone. Both devices must use the same private network. Keep the foreground terminal open; stop with `Ctrl+C`. Restart the foreground server after Python code changes.

## Start automatically at your logon

Stop any foreground instance, then install from ordinary PowerShell in the repository:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-startup-task.ps1
```

This installs and immediately starts **Pocket Entertainment Catalog** at your Windows user logon, with limited privileges and `.venv\Scripts\pythonw.exe`. It runs without a terminal, ignores duplicate task starts, has no runtime limit, and retries errors up to three times at one-minute intervals. The same Windows account supplies folder permissions, Git author configuration, and GitHub credentials. The PC must remain signed in and awake.

For first-time phone access or after changing the port, run from **PowerShell as Administrator using the same Windows account**:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-startup-task.ps1 -ConfigureFirewall
```

The optional firewall rule allows the configured TCP port only from `LocalSubnet` on the **Private** profile. Normal installation leaves firewall rules untouched. Keep the Windows network marked Private and use the PC's LAN address on the phone.

After updating application code, rerun bootstrap to apply any dependency changes, then double-click **`restart-server.cmd`** in the repository root. It stops the registered task, waits for it to stop, starts it, and verifies a fresh `/health` response from this app. It requests UAC elevation only if Windows denies task-management permission. Success, cancellation, and failures stay visible until you press Enter. Finish active saves first. Reinstall the task after changing startup scripts or moving the repository.

To stop and remove automatic startup from ordinary PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\remove-startup-task.ps1
# Optional, from elevated PowerShell: also remove this app's firewall rule.
powershell -ExecutionPolicy Bypass -File .\scripts\remove-startup-task.ps1 -RemoveFirewall
```

Removal leaves firewall rules unless `-RemoveFirewall` is supplied. It keeps `.env`, `.venv`, logs, and all Journey and Work data.

Logs, including startup failures, go to `logs\server.log` beside the code. The file rotates at 5 MiB with three backups. If the PC URL fails, check this log, `.env`, task state, and whether another server already uses the data. If only phone access fails, check the active LAN address, network profile, and firewall rule.

## Features

- Use the same web app on your phone or desktop; explicitly take control when switching pages.
- Search, sort, and filter by lifecycle status or media type.
- Add a journey; only the work title needs to be typed. Media type defaults to the active filter or the last-used value.
- Move entries through `Investigate → Planned → Ongoing → Completed / Discontinued` with lifecycle dates, or pause an Ongoing journey and resume it later.
- Confirm automatic alignment of missing or later prerequisite dates when recording a backdated transition.
- Edit titles, flexible unit labels, media types, release years, notes/reviews, and terminal ratings.
- Correct backfilled lifecycle history when the normal sequential workflow is not appropriate.
- Soft-delete entries to Trash and restore them later.
- Preserve `id`, `created_at`, `updated_at`, and `deleted_at` as server-owned fields.
- Load valid external JSONL changes when returning to the page.

## One controlling page

The first page opened claims control before any catalog or Work data loads. Another tab or device shows **Take Control** and cannot browse or change data until you select it. Taking control revokes the previous page's requests; that page shows the same gate when it next contacts the server. A save already in progress finishes before control transfers.

There are no ownership heartbeats, timeouts, or polling. Closing an editing sheet keeps control. **Release Control** deliberately gives it up. Refreshing creates a new page, so you may need to take control again. Switching Journey/Work through the profile tabs releases the old page first. Restarting the server clears ownership.

If a connection drops during a save, the app reports that confirmation was lost and asks you to check the catalog before repeating it. Saves are never automatically retried.

One server may use each configured data location at a time, even across different ports or checkouts. An operating-system guard releases automatically when the server exits or crashes. ETags, file locks, disk-change detection, atomic JSONL replacement, SQLite transactions, and Work backups continue to protect saved data.

## GitHub synchronization

When this folder is a Git repository with an `origin` remote, every successful catalog mutation:

1. atomically saves the JSONL;
2. commits only `data/catalog.jsonl`; and
3. pushes committed changes to `origin` in the background.

The save remains successful if the network is offline or GitHub rejects the push. The UI then shows **Sync issue**, and its status button retries the push. A restart also retries commits that are locally ahead of the configured upstream.

The app deliberately does not fetch, pull, auto-merge, or force-push. If the remote branch is changed independently, resolve that divergence explicitly rather than risking an automatic catalog merge. Git credentials must already work for the configured remote.

The app remains usable without Git; the header says **Local only** until the repository and `origin` are configured, then the server is restarted.

## Configuration

Bootstrap creates `.env` from [`.env.example`](.env.example). Edit it only when a default needs changing:

```dotenv
POCKET_ENTERTAINMENT_CATALOG_HOST=0.0.0.0
POCKET_ENTERTAINMENT_CATALOG_PORT=8766
POCKET_ENTERTAINMENT_CATALOG_PUBLIC_HOST=
POCKET_ENTERTAINMENT_CATALOG_DATA=data/catalog.jsonl
POCKET_ENTERTAINMENT_CATALOG_WORK_DATA=data/editing.sqlite3
```

`0.0.0.0` allows private-network devices to connect. `POCKET_ENTERTAINMENT_CATALOG_PUBLIC_HOST` only changes the address printed at startup and is normally discovered automatically.

`POCKET_ENTERTAINMENT_CATALOG_WORK_DATA` accepts a local SQLite path, relative to the project folder or absolute. Its parent directory is created automatically. Keep custom database paths and backups out of Git as well. The database belongs on the server's local disk; phones access it through HTTP.

`.env` is read as literal UTF-8 text (an optional BOM and matching quotes are accepted). Windows backslashes and `$` are preserved; variables are not expanded. Duplicate assignments are rejected. Real environment variables override the file. Relative data paths start at the repository root.

This is a trusted-LAN application without user accounts. It rejects non-private peers and cross-origin mutations, but the port should never be exposed to the public internet.

## Verify

Install development dependencies, including Black 25.1.0, then check formatting and run the suite:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m black --check .
.\.venv\Scripts\python.exe -m unittest discover -v
```

Tests cover configuration, explicit ownership, safe storage, Work backups, frontend behavior, HTTP request checks, process guards, and Git synchronization. HTTP tests use only loopback with temporary data; they do not modify the checked-in catalog. Frontend checks use Node.js or JavaScriptCore when available.

Portable PowerShell tests exercise the real scripts with disposable OS adapters. They skip automatically if PowerShell is unavailable; `POCKET_TEST_POWERSHELL` can point to an installed executable. These checks do not register real tasks or change firewall rules. Actual Windows logon, console-free startup, UAC, firewall behavior, and phone access still require testing on the intended PC and device.

The architecture and UI rationale are in [docs/design.md](docs/design.md), the Journey JSONL contract is in [docs/data-model.md](docs/data-model.md), and local Work storage is in [docs/work-profile.md](docs/work-profile.md).
