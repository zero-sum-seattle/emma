"""End-to-end test: drive ask() over a real Unix socket against a fake
Codex app-server, with no real Codex installed.

This is the highest-value test in the suite (it exercises the WebSocket
handshake, the JSON-RPC exchange, and the streaming event loop together)
but is kept to one straightforward happy-path scenario; the framing and
protocol edge cases are covered in isolation in test_framing.py and
test_protocol.py.
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


def happy_path_script(conn) -> None:
    req = conn.recv()
    assert req["method"] == "initialize"
    conn.reply(req, {"serverInfo": {"name": "codex-app-server", "version": "0.150.1"}})

    notification = conn.recv()
    assert notification.get("method") == "initialized"

    req = conn.recv()
    assert req["method"] == "thread/start"
    conn.reply(req, {"thread": {"id": "thread-1"}})

    req = conn.recv()
    assert req["method"] == "turn/start"
    conn.reply(req, {"turn": {"id": "turn-1"}})

    base_params = {"threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1"}
    conn.send(
        {
            "method": "item/agentMessage/delta",
            "params": {**base_params, "delta": "Use "},
        }
    )
    conn.send(
        {
            "method": "item/agentMessage/delta",
            "params": {**base_params, "delta": "df -h"},
        }
    )
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


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="emma-e2e-")
        self.socket_path = os.path.join(self.tmpdir, "app-server.sock")
        self.env_backup = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env_backup)
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass
        try:
            os.rmdir(self.tmpdir)
        except OSError:
            pass

    def test_ask_streams_output_over_fake_server(self):
        server = FakeAppServer(self.socket_path, happy_path_script)
        server.start()

        os.environ["EMMA_SOCKET"] = self.socket_path

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            emma.ask("how do I see disk usage", timing=False)

        server.join()
        self.assertIsNone(server.error)
        self.assertEqual(buf.getvalue(), "Use df -h\n")


if __name__ == "__main__":
    unittest.main()
