"""Run asynchronous browser-logic regression checks without frontend dependencies."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc")


class FrontendTests(unittest.TestCase):
    def test_work_requests_keep_versions_and_editor_ownership_ordered(self):
        runtime = shutil.which("node") or (str(JSC) if JSC.exists() else None)
        if runtime is None:
            self.skipTest("Node.js or JavaScriptCore is needed for frontend checks.")
        parts = [
            ROOT / "tests" / "work_frontend_harness.js",
            ROOT / "pocket_entertainment_catalog" / "static" / "work.js",
            ROOT / "pocket_entertainment_catalog" / "static" / "app.js",
            ROOT / "tests" / "work_frontend_checks.js",
        ]
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "checks.js"
            script.write_text("\n".join(path.read_text() for path in parts), encoding="utf-8")
            result = subprocess.run(
                [runtime, str(script)], capture_output=True, text=True, timeout=15
            )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("Frontend checks passed", result.stdout)
