"""Protocol-level regression tests for ~/.config/emma/EMMA.md and
--instructions: verifies the actual thread/start and turn/start payloads a
real Codex app-server would receive, driven through main() end to end over
a fake Unix-socket app-server, rather than only asserting on what main()
hands to a mocked ask().

This is what pins down the precedence fix: user instructions must never
leak into thread/start's developerInstructions, and must reach turn/start's
input as their own ordered, leading items ahead of the unmodified question.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest_helper import load_emma
from fake_app_server import FakeAppServer

emma = load_emma()

NO_DEFAULT_INSTRUCTIONS = "/nonexistent/emma-test/EMMA.md"


def _recording_script(captured):
    """Fake-server script that records thread/start & turn/start params,
    then completes the turn immediately with no agent output."""

    def script(conn):
        req = conn.recv()
        assert req["method"] == "initialize"
        conn.reply(req, {"serverInfo": {"name": "codex-app-server", "version": "0.150.1"}})

        notification = conn.recv()
        assert notification.get("method") == "initialized"

        req = conn.recv()
        assert req["method"] == "thread/start"
        captured["thread_start_params"] = req["params"]
        conn.reply(req, {"thread": {"id": "thread-1"}})

        req = conn.recv()
        assert req["method"] == "turn/start"
        captured["turn_start_params"] = req["params"]
        conn.reply(req, {"turn": {"id": "turn-1"}})

        conn.send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            }
        )

    return script


class InstructionsProtocolTest(unittest.TestCase):
    def setUp(self):
        self.env_backup = dict(os.environ)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.socket_path = os.path.join(self.tmpdir.name, "app-server.sock")
        os.environ["EMMA_SOCKET"] = self.socket_path
        # Never let these tests see the real developer's own EMMA.md.
        os.environ["EMMA_USER_INSTRUCTIONS"] = NO_DEFAULT_INSTRUCTIONS

    def tearDown(self):
        self.tmpdir.cleanup()
        os.environ.clear()
        os.environ.update(self.env_backup)

    def _write(self, name, content):
        path = Path(self.tmpdir.name) / name
        path.write_text(content)
        return path

    def _run_main(self, argv):
        captured = {}
        server = FakeAppServer(self.socket_path, _recording_script(captured))
        server.start()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = emma.main(argv)
        server.join()
        self.assertIsNone(server.error)
        return rc, captured

    def test_developer_instructions_unchanged_and_excludes_user_content(self):
        default_path = self._write("EMMA.md", "GLOBAL_MARKER preferences\n")
        os.environ["EMMA_USER_INSTRUCTIONS"] = str(default_path)

        rc, captured = self._run_main(["what", "is", "chmod"])
        self.assertEqual(rc, 0)
        developer_instructions = captured["thread_start_params"]["developerInstructions"]
        self.assertEqual(developer_instructions, emma.DEVELOPER_INSTRUCTIONS)
        self.assertNotIn("GLOBAL_MARKER", developer_instructions)

    def test_no_instructions_files_sends_only_the_prompt(self):
        rc, captured = self._run_main(["what", "is", "chmod"])
        self.assertEqual(rc, 0)
        self.assertEqual(
            captured["turn_start_params"]["input"],
            [{"type": "text", "text": "what is chmod"}],
        )

    def test_default_instructions_reach_turn_input_at_user_level(self):
        default_path = self._write("EMMA.md", "GLOBAL_MARKER\n")
        os.environ["EMMA_USER_INSTRUCTIONS"] = str(default_path)

        rc, captured = self._run_main(["what", "is", "chmod"])
        self.assertEqual(rc, 0)
        input_items = captured["turn_start_params"]["input"]
        self.assertEqual(len(input_items), 2)
        self.assertIn("GLOBAL_MARKER", input_items[0]["text"])
        self.assertEqual(input_items[1]["text"], "what is chmod")
        # Not developer-level.
        self.assertNotIn("GLOBAL_MARKER", captured["thread_start_params"]["developerInstructions"])

    def test_default_and_explicit_reach_turn_input_in_order_with_prompt_last(self):
        default_path = self._write("EMMA.md", "GLOBAL_MARKER\n")
        explicit_path = self._write("notes.md", "EXPLICIT_MARKER\n")
        os.environ["EMMA_USER_INSTRUCTIONS"] = str(default_path)

        rc, captured = self._run_main(["--instructions", str(explicit_path), "review this code"])
        self.assertEqual(rc, 0)
        input_items = captured["turn_start_params"]["input"]
        self.assertEqual(len(input_items), 3)
        self.assertIn("GLOBAL_MARKER", input_items[0]["text"])
        self.assertIn("EXPLICIT_MARKER", input_items[1]["text"])
        # The actual question is untouched and standalone as the final,
        # most-specific item — never merged with instructions content.
        self.assertEqual(input_items[2]["text"], "review this code")
        self.assertNotIn("GLOBAL_MARKER", input_items[2]["text"])
        self.assertNotIn("EXPLICIT_MARKER", input_items[2]["text"])

    def test_no_user_instructions_skips_default_but_keeps_explicit(self):
        default_path = self._write("EMMA.md", "GLOBAL_MARKER\n")
        explicit_path = self._write("notes.md", "EXPLICIT_MARKER\n")
        os.environ["EMMA_USER_INSTRUCTIONS"] = str(default_path)

        rc, captured = self._run_main(
            ["--no-user-instructions", "--instructions", str(explicit_path), "review this"]
        )
        self.assertEqual(rc, 0)
        input_items = captured["turn_start_params"]["input"]
        self.assertEqual(len(input_items), 2)
        self.assertNotIn("GLOBAL_MARKER", input_items[0]["text"])
        self.assertIn("EXPLICIT_MARKER", input_items[0]["text"])
        self.assertEqual(input_items[1]["text"], "review this")

    def test_bad_explicit_instructions_path_never_connects(self):
        server = FakeAppServer(self.socket_path, _recording_script({}))
        server.start()

        rc = emma.main(["--instructions", "/nonexistent/emma-test/notes.md", "hi"])
        self.assertNotEqual(rc, 0)

        # A short join is enough: if main() had connected, the WebSocket
        # handshake + initialize round trip would complete near-instantly
        # over a local socket, and the background thread would finish.
        server.join(timeout=1.0)
        self.assertTrue(server._thread.is_alive(), "fake server accepted a connection")
        self.assertIsNone(server.error)


if __name__ == "__main__":
    unittest.main()
