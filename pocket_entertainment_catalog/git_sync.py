"""Local Git commits with a non-blocking, retryable push worker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import threading


@dataclass(frozen=True)
class SyncSnapshot:
    state: str
    message: str
    last_attempt_at: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "state": self.state,
            "message": self.message,
            "last_attempt_at": self.last_attempt_at,
        }


class GitSync:
    """Commit only the catalog file and push committed changes in the background."""

    def __init__(self, repository: str | Path, catalog_path: str | Path) -> None:
        self.repository = Path(repository).resolve()
        self.catalog_path = Path(catalog_path).resolve()
        try:
            self.relative_catalog = self.catalog_path.relative_to(self.repository)
        except ValueError as error:
            raise ValueError("Catalog must live inside its Git repository.") from error
        self._state_lock = threading.RLock()
        self._commit_lock = threading.Lock()
        self._push_event = threading.Event()
        self._generation = 0
        self._thread: threading.Thread | None = None
        self._snapshot = self._initial_snapshot()
        if self._snapshot.state == "pending":
            self._generation = 1
            self._ensure_worker()
            self._push_event.set()

    def snapshot(self) -> SyncSnapshot:
        with self._state_lock:
            return self._snapshot

    def record_change(self, message: str) -> SyncSnapshot:
        if self.snapshot().state == "unavailable":
            return self.snapshot()
        with self._commit_lock:
            try:
                self._run("add", "--", str(self.relative_catalog))
                changed = self._run(
                    "diff",
                    "--cached",
                    "--quiet",
                    "--",
                    str(self.relative_catalog),
                    check=False,
                )
                if changed.returncode not in {0, 1}:
                    raise RuntimeError(self._detail(changed, "Could not inspect staged catalog changes."))
                if changed.returncode == 0:
                    return self.snapshot()
                self._run(
                    "commit",
                    "-m",
                    message[:180],
                    "--",
                    str(self.relative_catalog),
                )
            except (OSError, subprocess.SubprocessError, RuntimeError) as error:
                return self._set("error", f"Saved locally; Git commit failed: {error}")

        with self._state_lock:
            self._generation += 1
            self._snapshot = SyncSnapshot(
                "pending",
                "Saved locally; GitHub push pending.",
                self._now(),
            )
        self._ensure_worker()
        self._push_event.set()
        return self.snapshot()

    def retry(self) -> SyncSnapshot:
        if self.snapshot().state == "unavailable":
            return self.snapshot()
        self._set("pending", "Retrying GitHub synchronization.")
        self._ensure_worker()
        self._push_event.set()
        return self.snapshot()

    def _initial_snapshot(self) -> SyncSnapshot:
        if not (self.repository / ".git").exists():
            return SyncSnapshot(
                "unavailable",
                "This folder is not initialized as a Git repository.",
                None,
            )
        try:
            inside = self._run("rev-parse", "--is-inside-work-tree")
            if inside.stdout.strip() != "true":
                raise RuntimeError("Git did not recognize the repository.")
            remote = self._run("remote", "get-url", "origin", check=False)
            if remote.returncode != 0:
                return SyncSnapshot(
                    "unavailable",
                    "Git repository has no origin remote.",
                    self._now(),
                )
        except (OSError, subprocess.SubprocessError, RuntimeError) as error:
            return SyncSnapshot("unavailable", f"Git is unavailable: {error}", self._now())
        if self._local_commits_pending():
            return SyncSnapshot(
                "pending",
                "Local commits are waiting to be pushed to GitHub.",
                self._now(),
            )
        return SyncSnapshot("synced", "No GitHub synchronization is pending.", None)

    def _local_commits_pending(self) -> bool:
        upstream = self._run(
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
            check=False,
        )
        if upstream.returncode != 0:
            return False
        ahead = self._run(
            "rev-list",
            "--count",
            "@{upstream}..HEAD",
            check=False,
        )
        if ahead.returncode != 0:
            return False
        try:
            return int(ahead.stdout.strip() or "0") > 0
        except ValueError:
            return False

    def _ensure_worker(self) -> None:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._push_loop,
                name="PocketCatalogGitPush",
                daemon=True,
            )
            self._thread.start()

    def _push_loop(self) -> None:
        while True:
            self._push_event.wait()
            self._push_event.clear()
            with self._state_lock:
                generation = self._generation
            try:
                result = self._run("push", "origin", "HEAD", check=False, timeout=60)
                if result.returncode != 0:
                    raise RuntimeError(self._detail(result, "GitHub rejected the push."))
            except (OSError, subprocess.SubprocessError, RuntimeError) as error:
                self._set("error", f"Saved and committed locally; push failed: {error}")
                return
            with self._state_lock:
                if generation == self._generation:
                    self._snapshot = SyncSnapshot(
                        "synced",
                        "Catalog is synchronized with GitHub.",
                        self._now(),
                    )
                    return
                self._push_event.set()

    def _run(
        self,
        *arguments: str,
        check: bool = True,
        timeout: int = 30,
    ) -> subprocess.CompletedProcess[str]:
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=creationflags,
        )
        if check and result.returncode != 0:
            raise RuntimeError(self._detail(result, f"git {' '.join(arguments)} failed."))
        return result

    @staticmethod
    def _detail(result: subprocess.CompletedProcess[str], fallback: str) -> str:
        text = (result.stderr or result.stdout).strip().splitlines()
        return (text[-1][:300] if text else fallback)

    def _set(self, state: str, message: str) -> SyncSnapshot:
        with self._state_lock:
            self._snapshot = SyncSnapshot(state, message, self._now())
            return self._snapshot

    @staticmethod
    def _now() -> str:
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
