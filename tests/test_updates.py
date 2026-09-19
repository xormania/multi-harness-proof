"""Run the updater against fake CLIs/downloads only; never update real software."""
import csv
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class UpdateScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bin = self.directory / "fake bins"
        self.bin.mkdir()
        self.calls = self.directory / "calls.txt"
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + "/usr/bin:/bin",
                    "MHPROOF_UPDATE_TEST_CALLS": str(self.calls), "MHPROOF_UPDATE_TEST_STATE": str(self.directory),
                    "MHPROOF_UPDATE_TEST_FAIL": "", "MHPROOF_UPDATE_TEST_DOWNLOAD_FAIL": ""}
        for peer in ("codex", "claude", "grok"):
            self.shim(peer, '''peer=${0##*/}
if [[ "$*" == *--version* ]]; then
    if [[ -f "$MHPROOF_UPDATE_TEST_STATE/$peer-updated" ]]; then printf '%s new\n' "$peer"; else printf '%s old\n' "$peer"; fi
    exit 0
fi
printf '%s update\n' "$peer" >> "$MHPROOF_UPDATE_TEST_CALLS"
[[ "$MHPROOF_UPDATE_TEST_FAIL" == "$peer" ]] && exit 7
touch "$MHPROOF_UPDATE_TEST_STATE/$peer-updated"
''')
        self.shim("curl", '''printf 'download\n' >> "$MHPROOF_UPDATE_TEST_CALLS"
while (($#)); do
    if [[ "$1" == --output ]]; then destination=$2; shift 2; else shift; fi
done
cat > "$destination" <<'INSTALL'
printf 'codex update\n' >> "$MHPROOF_UPDATE_TEST_CALLS"
touch "$MHPROOF_UPDATE_TEST_STATE/codex-updated"
INSTALL
[[ -n "$MHPROOF_UPDATE_TEST_DOWNLOAD_FAIL" ]] && exit 22
exit 0
''')
        self.shim("npm", 'exit 1\n')  # No real package-manager probes or updates.

    def shim(self, name, source):
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + source)
        path.chmod(0o700)

    def run_update(self, *extra, method="standalone"):
        log = self.directory / "update logs"
        result = subprocess.run(["bash", str(ROOT / "scripts/update-harnesses.sh"),
            "--codex-method", method, "--log-dir", str(log), *extra],
            env=self.env, capture_output=True, text=True, timeout=10)
        rows = list(csv.DictReader((log / "summary.tsv").read_text().splitlines(), delimiter="\t"))
        calls = self.calls.read_text().splitlines() if self.calls.exists() else []
        return result, {row["harness"]: row for row in rows}, calls

    def test_all_three_update_and_preserve_version_logs(self):
        result, rows, calls = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, ["download", "codex update", "claude update", "grok update"])
        self.assertTrue(all(row["status"] == "updated-or-current" and row["before"].endswith(" old") and
                            row["after"].endswith(" new") for row in rows.values()))
        self.assertTrue((self.directory / "update logs/codex-install.sh").is_file())

    def test_dry_run_never_downloads_or_updates(self):
        result, rows, calls = self.run_update("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(calls)
        self.assertTrue(all(row["status"] == "dry-run" and row["before"] == row["after"] for row in rows.values()))

    def test_failure_is_nonzero_but_other_harnesses_still_update(self):
        self.env["MHPROOF_UPDATE_TEST_FAIL"] = "claude"
        result, rows, calls = self.run_update()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(rows["claude"]["exit_code"], "7")
        self.assertEqual(rows["claude"]["status"], "failed")
        self.assertEqual(rows["grok"]["status"], "updated-or-current")
        self.assertIn("grok update", calls)

    def test_failed_download_never_executes_partial_installer(self):
        self.env["MHPROOF_UPDATE_TEST_DOWNLOAD_FAIL"] = "yes"
        result, rows, calls = self.run_update()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(rows["codex"]["exit_code"], "22")
        self.assertNotIn("codex update", calls)
        self.assertIn("grok update", calls)

    def test_unknown_installation_is_not_replaced(self):
        result, rows, calls = self.run_update(method="auto")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(rows["codex"]["status"], "unsupported")
        self.assertNotIn("download", calls)
        self.assertIn("claude update", calls)


if __name__ == "__main__":
    unittest.main()
