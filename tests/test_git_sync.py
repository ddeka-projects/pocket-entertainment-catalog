from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from pocket_entertainment_catalog.git_sync import GitSync


def git(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(directory), *arguments],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class GitSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.remote = root / "remote.git"
        self.repository = root / "catalog"
        self.repository.mkdir()
        subprocess.run(
            ["git", "init", "--bare", str(self.remote)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        git(self.repository, "init")
        git(self.repository, "config", "user.name", "Catalog Test")
        git(self.repository, "config", "user.email", "catalog-test@example.invalid")
        git(self.repository, "checkout", "-b", "main")
        self.catalog = self.repository / "data" / "catalog.jsonl"
        self.catalog.parent.mkdir()
        self.catalog.write_text('{"version":1}\n', encoding="utf-8")
        git(self.repository, "add", "data/catalog.jsonl")
        git(self.repository, "commit", "-m", "initial catalog")
        git(self.repository, "remote", "add", "origin", str(self.remote))
        git(self.repository, "push", "-u", "origin", "main")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_catalog_change_is_committed_and_pushed(self) -> None:
        sync = GitSync(self.repository, self.catalog)
        self.catalog.write_text('{"version":2}\n', encoding="utf-8")

        snapshot = sync.record_change("catalog: test update")
        self.assertEqual("pending", snapshot.state)

        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and sync.snapshot().state == "pending":
            time.sleep(0.02)
        self.assertEqual("synced", sync.snapshot().state, sync.snapshot().message)
        remote_value = subprocess.run(
            ["git", "--git-dir", str(self.remote), "show", "refs/heads/main:data/catalog.jsonl"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
        self.assertEqual('{"version":2}\n', remote_value)

    def test_restart_pushes_a_commit_left_ahead_of_origin(self) -> None:
        self.catalog.write_text('{"version":2}\n', encoding="utf-8")
        git(self.repository, "add", "data/catalog.jsonl")
        git(self.repository, "commit", "-m", "left pending")

        sync = GitSync(self.repository, self.catalog)
        self.assertIn(sync.snapshot().state, {"pending", "synced"})
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and sync.snapshot().state == "pending":
            time.sleep(0.02)
        self.assertEqual("synced", sync.snapshot().state, sync.snapshot().message)

    def test_unchanged_catalog_does_not_queue_a_push(self) -> None:
        sync = GitSync(self.repository, self.catalog)
        before = git(self.repository, "rev-parse", "HEAD").stdout.strip()

        snapshot = sync.record_change("catalog: no-op")

        self.assertEqual("synced", snapshot.state)
        self.assertEqual(before, git(self.repository, "rev-parse", "HEAD").stdout.strip())


if __name__ == "__main__":
    unittest.main()
