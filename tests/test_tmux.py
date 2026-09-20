"""Real tmux command parsing on a private socket; no vendor harnesses run."""
import json
import errno
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import unittest

from mhproof.sessions import Tmux


class IsolatedTmux(Tmux):
    def __init__(self, socket):
        self.socket = socket

    def call(self, *args, **kwargs):
        return super().call("-S", self.socket, "-f", "/dev/null", *args, **kwargs)


@unittest.skipUnless(shutil.which("tmux"), "Real tmux is not installed")
class NativeTmuxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mhproof-tmux-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                path = str(self.root / "capability.sock")
                server.bind(path)
                server.listen(1)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(path)
        except OSError as error:
            if error.errno in {errno.EPERM, errno.EACCES, errno.EAFNOSUPPORT, errno.ENOSYS}:
                self.skipTest("Host does not permit private Unix sockets: " + str(error))
            raise
        self.tmux = IsolatedTmux(str(self.root / "server.sock"))
        self.record = {"session": "proof-test", "owner": "test-owner",
                       "directory": str(self.root), "panes": {}}

    def tearDown(self):
        # This server has a private test socket; never target the user's server.
        self.tmux.call("kill-server", check=False)

    def foreign_session(self):
        self.tmux.call("new-session", "-d", "-s", "proof-test-other",
                       sys.executable, "-c", "import time; time.sleep(60)")

    def test_owned_lifecycle_preserves_unrelated_session(self):
        self.foreign_session()
        dashboard = self.tmux.create(self.record)
        self.assertTrue(self.tmux.owned(self.record))
        self.assertTrue(self.tmux.pane_owned(self.record, dashboard))
        self.assertEqual(self.tmux.call("show-options", "-wv", "-t", dashboard,
                                       "remain-on-exit"), "on")
        pane = self.tmux.launch(self.record, "codex", [sys.executable, "-c", "import time; time.sleep(60)"])
        saved = json.loads((self.root / "session.json").read_text())
        self.assertEqual(saved["panes"]["codex"], pane)
        self.tmux.select(self.record, "codex")
        self.assertEqual(self.tmux.call("display-message", "-p", "-t", "=proof-test:",
                                       "#{window_name}"), "codex")
        outcome = self.tmux.finish_peer(self.record, "codex", pane, self.root)
        self.assertEqual(outcome["status"], "closed")
        self.assertTrue((self.root / "codex-terminal.log").exists())
        self.tmux.close(self.record)
        self.assertFalse(self.tmux.owned(self.record))
        self.assertEqual(self.tmux.call("list-sessions", "-F", "#{session_name}"), "proof-test-other")

    def test_prefix_match_and_wrong_owner_do_not_allow_cleanup(self):
        self.foreign_session()
        # A prefix match with the same token still must not qualify as our session.
        self.tmux.call("set-option", "-t", "=proof-test-other:", "@mhproof_owner", self.record["owner"])
        self.assertFalse(self.tmux.owned(self.record))
        pane = self.tmux.create(self.record)
        self.tmux.call("set-option", "-t", pane, "@mhproof_owner", "different-owner")
        self.assertFalse(self.tmux.owned(self.record))
        self.tmux.close(self.record)
        result = self.tmux.finish_peer(self.record, "codex", pane, self.root)
        self.assertEqual(result["status"], "already-closed-or-unowned")
        self.assertEqual(set(self.tmux.call("list-sessions", "-F", "#{session_name}").splitlines()),
                         {"proof-test", "proof-test-other"})


if __name__ == "__main__":
    unittest.main()
