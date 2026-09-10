# Pocket Entertainment Catalog

A small, self-hosted catalog for tracking entertainment as journey units: a season, a movie, a game, a manga series, or any other continuous segment that makes sense at the time.

The database is [`data/catalog.jsonl`](data/catalog.jsonl). The application uses only Python's standard library, so there is no package-install step.

## Start the app

From this folder, run:

```sh
python3 run.py
```

The server prints an address such as `http://192.168.1.20:8766/`. Open it on the computer running the server or on another device connected to the same private network. Keep the terminal open while using the app.

The default port is `8766`. If macOS asks whether Python may accept incoming connections, allow it for private networks so a phone can connect.

## Windows scheduled startup

The Windows launcher and Task Scheduler scripts live under `scripts`. The recommended mode starts the server when your Windows user signs in. This lets the application use the same Git author configuration and GitHub credentials that work in your normal terminal.

First, confirm the launcher works from PowerShell in the project folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-server.ps1
```

Stop it with `Ctrl+C`, then install and immediately start the scheduled task:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-startup-task.ps1
```

The task is named `Pocket Entertainment Catalog`. It starts at user logon without opening a terminal, ignores duplicate starts, and retries up to three times when the server exits with an error. Logs are appended to:

```text
.tmp\server.log
```

If Python is not discovered automatically, add its full path to `.env`:

```dotenv
POCKET_CATALOG_PYTHON=C:\Python313\python.exe
```

Use a stable Python installation path; the scripts require Python 3.10 or newer. The scheduled task uses the console-free `pythonw.exe` installed beside the configured `python.exe`.

For a true machine-boot task that runs before anyone signs in, open PowerShell as Administrator and install the alternative mode:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-startup-task.ps1 -Mode SystemStartup
```

`SystemStartup` runs as Windows `SYSTEM`. It can serve and save the local catalog, but it normally cannot access your personal Git author configuration or GitHub credentials. Automatic commits or pushes will therefore report a sync issue unless Git is separately configured for that machine account. `UserLogon` is the recommended mode when GitHub synchronization matters.

Changing modes replaces the existing task. To stop and remove either version:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\remove-startup-task.ps1
```

If the task exists but the phone cannot connect, allow the `pythonw.exe` path printed by the installer through Windows Defender Firewall on **Private** networks and confirm that the phone and PC are on the same network. Use the PC's LAN address, such as `http://192.168.1.20:8766/`, rather than `localhost` on the phone.

## What the first version supports

- Browse from any number of devices at the same time.
- Search, sort, and filter by lifecycle status or media type.
- Add a journey; only the work title needs to be typed. Media type defaults to the active filter or the last-used value.
- Move entries through `Investigate → Planned → Ongoing → Completed / Discontinued` with lifecycle dates.
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
POCKET_CATALOG_PYTHON=
```

`0.0.0.0` allows private-network devices to connect. `POCKET_CATALOG_PUBLIC_HOST` only changes the address printed at startup and is normally discovered automatically.

This is a trusted-LAN application without user accounts. It rejects non-private peers and cross-origin mutations, but the port should never be exposed to the public internet.

## Verify

Run all schema, storage, lease, HTTP, and Git synchronization tests with:

```sh
python3 -m unittest discover -v
```

The HTTP tests bind only to `127.0.0.1` on a temporary port and all mutation tests use disposable catalog files. They do not modify the checked-in database.

The architecture and UI rationale are recorded in [`docs/design.md`](docs/design.md), and the exact JSONL contract is in [`docs/data-model.md`](docs/data-model.md).
