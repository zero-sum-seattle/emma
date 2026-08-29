"""Tests for the WebSocket framing layer: _send_frame and _read_text_frame.

These bypass AppServerConnection.__init__ (which does a real socket connect
plus the WebSocket handshake) and instead construct a bare instance wired to
one end of a socketpair, so the framing code can be exercised directly
without a live app-server.
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest_helper import load_emma

emma = load_emma()


def make_connection(sock: socket.socket):
    conn = emma.AppServerConnection.__new__(emma.AppServerConnection)
    conn._next_id = 1
    conn._pending = []
    conn._incoming = bytearray()
    conn._socket = sock
    return conn


def encode_frame(opcode: int, payload: bytes, *, fin: bool = True, mask: bool = False) -> bytes:
    """Build a raw WebSocket frame, mirroring _send_frame's on-wire format.

    Used to construct server-style (unmasked) fixtures for the reader
    tests; kept intentionally separate from the code under test.
    """
    first = (0x80 if fin else 0x00) | opcode
    length = len(payload)
    mask_bit = 0x80 if mask else 0x00
    if length < 126:
        header = struct.pack("!BB", first, mask_bit | length)
    elif length <= 0xFFFF:
        header = struct.pack("!BBH", first, mask_bit | 126, length)
    else:
        header = struct.pack("!BBQ", first, mask_bit | 127, length)

    if mask:
        key = b"\x01\x02\x03\x04"
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
        return header + key + payload
    return header + payload


class SendFrameTest(unittest.TestCase):
    def setUp(self):
        self.client, self.peer = socket.socketpair()
        self.addCleanup(self.client.close)
        self.addCleanup(self.peer.close)
        self.conn = make_connection(self.client)

    def _read_raw_frame(self) -> tuple[int, int, int, bytes]:
        """Read one frame off the wire, returning (first, length_marker, length, payload)."""
        first, second = self.peer.recv(2)
        masked = bool(second & 0x80)
        length_marker = second & 0x7F
        length = length_marker
        if length_marker == 126:
            length = struct.unpack("!H", self.peer.recv(2))[0]
        elif length_marker == 127:
            length = struct.unpack("!Q", self.peer.recv(8))[0]
        self.assertTrue(masked, "client-to-server frames must be masked")
        mask_key = self.peer.recv(4)
        payload = b""
        while len(payload) < length:
            payload += self.peer.recv(length - len(payload))
        unmasked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        return first, length_marker, length, unmasked

    def test_small_payload_encoding(self):
        self.conn._send_frame(0x1, b"hello")
        first, _marker, length, payload = self._read_raw_frame()
        self.assertEqual(first, 0x80 | 0x1)  # FIN set, text opcode
        self.assertEqual(length, 5)
        self.assertEqual(payload, b"hello")

    def test_boundary_125_uses_base_length(self):
        data = b"x" * 125
        self.conn._send_frame(0x1, data)
        _first, marker, length, payload = self._read_raw_frame()
        self.assertEqual(marker, 125)
        self.assertEqual(length, 125)
        self.assertEqual(payload, data)

    def test_boundary_126_uses_16bit_length(self):
        data = b"y" * 126
        self.conn._send_frame(0x1, data)
        _first, marker, length, payload = self._read_raw_frame()
        self.assertEqual(marker, 126)
        self.assertEqual(length, 126)
        self.assertEqual(payload, data)

    def test_boundary_65535_uses_16bit_length(self):
        data = b"z" * 65535
        self.conn._send_frame(0x1, data)
        _first, marker, length, payload = self._read_raw_frame()
        self.assertEqual(marker, 126)
        self.assertEqual(length, 65535)
        self.assertEqual(payload, data)

    def test_boundary_65536_uses_64bit_length(self):
        data = b"w" * 65536
        self.conn._send_frame(0x1, data)
        _first, marker, length, payload = self._read_raw_frame()
        self.assertEqual(marker, 127)
        self.assertEqual(length, 65536)
        self.assertEqual(payload, data)


class ReadTextFrameTest(unittest.TestCase):
    def setUp(self):
        self.client, self.peer = socket.socketpair()
        self.addCleanup(self.client.close)
        self.addCleanup(self.peer.close)
        self.conn = make_connection(self.client)

    def test_single_frame(self):
        message = json.dumps({"hello": "world"})
        self.peer.sendall(encode_frame(0x1, message.encode("utf-8")))
        self.assertEqual(self.conn._read_text_frame(), message)

    def test_fragmented_message(self):
        part_a = "hello "
        part_b = "world"
        self.peer.sendall(encode_frame(0x1, part_a.encode("utf-8"), fin=False))
        self.peer.sendall(encode_frame(0x0, part_b.encode("utf-8"), fin=True))
        self.assertEqual(self.conn._read_text_frame(), part_a + part_b)

    def test_ping_interleaved_mid_stream_gets_pong(self):
        part_a = "hello "
        part_b = "world"
        self.peer.sendall(encode_frame(0x1, part_a.encode("utf-8"), fin=False))
        self.peer.sendall(encode_frame(0x9, b"ping-payload"))
        self.peer.sendall(encode_frame(0x0, part_b.encode("utf-8"), fin=True))

        result = self.conn._read_text_frame()
        self.assertEqual(result, part_a + part_b)

        # The pong should have been written back to the peer, masked, with
        # the same payload as the ping.
        first, second = self.peer.recv(2)
        self.assertEqual(first, 0x80 | 0xA)
        length = second & 0x7F
        masked = bool(second & 0x80)
        self.assertTrue(masked)
        mask_key = self.peer.recv(4)
        raw = self.peer.recv(length)
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(raw))
        self.assertEqual(payload, b"ping-payload")

    def test_close_frame_raises(self):
        self.peer.sendall(encode_frame(0x8, b""))
        with self.assertRaises(emma.EmmaError):
            self.conn._read_text_frame()

    def test_binary_frame_is_skipped(self):
        self.peer.sendall(encode_frame(0x2, b"\x00\x01\x02"))
        message = json.dumps({"after": "binary"})
        self.peer.sendall(encode_frame(0x1, message.encode("utf-8")))
        self.assertEqual(self.conn._read_text_frame(), message)

    def test_oversized_frame_is_rejected(self):
        oversized = emma.MAX_FRAME_BYTES + 1
        header = struct.pack("!BBQ", 0x80 | 0x1, 127, oversized)
        self.peer.sendall(header)
        with self.assertRaises(emma.EmmaError):
            self.conn._read_text_frame()


if __name__ == "__main__":
    unittest.main()
