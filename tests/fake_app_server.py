"""A minimal fake Codex app-server for offline end-to-end testing.

Speaks just enough of the WebSocket + JSON-RPC-ish protocol to drive
ask() through a real Unix socket: accepts one connection, performs the
WebSocket handshake, answers initialize/thread/start/turn/start, and lets
the test script the rest of the exchange (deltas, completion, inbound
requests, ...) via `expect_request` / `send` / `send_notification`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading

WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _recv_exact(conn: socket.socket, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = conn.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("fake app-server: peer closed early")
        buf.extend(chunk)
    return bytes(buf)


def _encode_frame(opcode: int, payload: bytes) -> bytes:
    first = 0x80 | opcode
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", first, length)
    elif length <= 0xFFFF:
        header = struct.pack("!BBH", first, 126, length)
    else:
        header = struct.pack("!BBQ", first, 127, length)
    return header + payload


class FakeAppServer:
    """Runs the protocol handling in a background thread.

    Usage: construct with a socket path, call start(), then use the
    `script` callable to drive a scenario against `conn`-level send/recv
    helpers passed into it.
    """

    def __init__(self, socket_path: str, script) -> None:
        self.socket_path = socket_path
        self._script = script
        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(socket_path)
        self._server_sock.listen(1)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.error: BaseException | None = None

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float = 5.0) -> None:
        self._thread.join(timeout)

    def _run(self) -> None:
        try:
            conn, _ = self._server_sock.accept()
        except OSError:
            return
        try:
            self._handshake(conn)
            self._script(_ServerConn(conn))
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test
            self.error = exc
        finally:
            conn.close()
            self._server_sock.close()

    def _handshake(self, conn: socket.socket) -> None:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                raise ConnectionError("fake app-server: client closed during handshake")
            data += chunk
        headers, _, _rest = data.partition(b"\r\n\r\n")
        key = ""
        for line in headers.decode("latin-1").split("\r\n")[1:]:
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
        accept = base64.b64encode(
            hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()
        ).decode("ascii")
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        )
        conn.sendall(response.encode("ascii"))


class _ServerConn:
    """Server-side JSON message helpers for use inside a script callback."""

    def __init__(self, conn: socket.socket) -> None:
        self._conn = conn

    def recv(self) -> dict:
        first, second = _recv_exact(self._conn, 2)
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", _recv_exact(self._conn, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _recv_exact(self._conn, 8))[0]
        mask = _recv_exact(self._conn, 4) if masked else b""
        payload = _recv_exact(self._conn, length)
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return json.loads(payload.decode("utf-8"))

    def send(self, message: dict) -> None:
        payload = json.dumps(message).encode("utf-8")
        self._conn.sendall(_encode_frame(0x1, payload))

    def reply(self, request: dict, result: dict) -> None:
        self.send({"id": request["id"], "result": result})
