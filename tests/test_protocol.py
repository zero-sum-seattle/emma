"""Tests for AppServerConnection.request() / next_event() message dispatch.

Covers the _pending buffering that lets an out-of-order response arrive
after unrelated notifications, and the A3 fix where a message carrying both
"id" and "method" (a server-initiated request) gets a JSON-RPC
"method not found" reply instead of deadlocking the event loop.
"""

from __future__ import annotations

import json
import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Reuse test_framing's already-loaded emma module (and its make_connection
# helper) rather than loading a second copy: load_emma() re-execs the
# source into a fresh module object each time, so a separately loaded
# module's EmmaError class would not match exceptions raised by an
# AppServerConnection built from a different loaded copy.
from test_framing import emma, encode_frame, make_connection


def send_message(sock: socket.socket, message: dict) -> None:
    payload = json.dumps(message).encode("utf-8")
    sock.sendall(encode_frame(0x1, payload))


class RequestPendingBufferTest(unittest.TestCase):
    def setUp(self):
        self.client, self.peer = socket.socketpair()
        self.addCleanup(self.client.close)
        self.addCleanup(self.peer.close)
        self.conn = make_connection(self.client)

    def test_response_after_notifications_still_resolves(self):
        send_message(self.peer, {"method": "item/agentMessage/delta", "params": {"delta": "a"}})
        send_message(self.peer, {"method": "item/agentMessage/delta", "params": {"delta": "b"}})
        send_message(self.peer, {"id": 1, "result": {"ok": True}})

        # request() sends its own frame first; drain it so the socketpair
        # buffer doesn't matter, then read the result.
        result = self.conn.request("thread/start", {})
        self.assertEqual(result, {"ok": True})

        # The two notifications should have been buffered in order and
        # come back out via next_event() in the same order.
        first = self.conn.next_event()
        second = self.conn.next_event()
        self.assertEqual(first["params"]["delta"], "a")
        self.assertEqual(second["params"]["delta"], "b")

    def test_request_raises_on_error_response(self):
        send_message(self.peer, {"id": 1, "error": {"message": "boom"}})
        with self.assertRaises(emma.EmmaError):
            self.conn.request("thread/start", {})


class InboundRequestReplyTest(unittest.TestCase):
    def setUp(self):
        self.client, self.peer = socket.socketpair()
        self.addCleanup(self.client.close)
        self.addCleanup(self.peer.close)
        self.conn = make_connection(self.client)

    def _recv_json_message(self) -> dict:
        first, second = self.peer.recv(2)
        length = second & 0x7F
        mask = b""
        masked = bool(second & 0x80)
        if masked:
            mask = self.peer.recv(4)
        payload = b""
        while len(payload) < length:
            payload += self.peer.recv(length - len(payload))
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return json.loads(payload.decode("utf-8"))

    def test_next_event_replies_to_inbound_request_and_skips_it(self):
        send_message(
            self.peer,
            {"id": 99, "method": "approvalPrompt", "params": {}},
        )
        send_message(self.peer, {"method": "item/agentMessage/delta", "params": {"delta": "x"}})

        event = self.conn.next_event()

        reply = self._recv_json_message()
        self.assertEqual(reply["id"], 99)
        self.assertEqual(reply["error"]["code"], -32601)

        self.assertEqual(event["method"], "item/agentMessage/delta")

    def test_request_replies_to_inbound_request_while_waiting(self):
        send_message(
            self.peer,
            {"id": 7, "method": "authRefresh", "params": {}},
        )
        send_message(self.peer, {"id": 1, "result": {"done": True}})

        result = self.conn.request("thread/start", {})

        # request() writes its own outgoing "thread/start" call before
        # anything else, so it's first on the wire; drain it before
        # checking the inbound-request reply that follows it.
        outgoing = self._recv_json_message()
        self.assertEqual(outgoing["method"], "thread/start")

        reply = self._recv_json_message()
        self.assertEqual(reply["id"], 7)
        self.assertEqual(reply["error"]["code"], -32601)
        self.assertEqual(result, {"done": True})


if __name__ == "__main__":
    unittest.main()
