# Emma

Emma is a small Ubuntu terminal helper backed by the persistent Codex app-server.
It connects to Codex's managed Unix socket, asks a concise question using
`gpt-5.6-luna`, streams the answer, and exits while the app-server stays running.

## Requirements

- Linux with Python 3.10 or newer
- Codex CLI 0.150.1 or a compatible release
- An existing authenticated Codex login

The app-server protocol is experimental. A future Codex update may require a
small compatibility change.

## Install

```bash
install -Dm755 emma ~/.local/bin/emma
```

Ensure `~/.local/bin` is on `PATH`. If an older zsh function named `emma` is
still loaded in the current terminal, clear it once:

```bash
unfunction emma
```

## Usage

```bash
emma how do I see disk usage
emma what does chmod 755 mean
emma --timing how do I list failed systemd services
```

Emma defaults to Luna with low reasoning. It starts Codex's managed daemon
automatically when the socket is unavailable. Every question gets a fresh,
ephemeral, read-only thread with approvals disabled and instructions not to run
tools or access external services.

## Configuration

Environment variables provide lightweight overrides:

- `EMMA_MODEL` — model name; defaults to `gpt-5.6-luna`
- `EMMA_TIMEOUT` — response timeout in seconds; defaults to `120`
- `EMMA_CWD` — thread working directory; defaults to `/tmp`
- `EMMA_CODEX` — path to the Codex executable
- `EMMA_SOCKET` — path to the managed app-server Unix socket
- `EMMA_DEBUG` — print raw app-server messages when set

## How it works

1. Connect to `~/.codex/app-server-control/app-server-control.sock`.
2. Perform a WebSocket upgrade over the Unix socket.
3. Send `initialize`, `thread/start`, and `turn/start` app-server messages.
4. Stream `item/agentMessage/delta` events to the terminal.
5. Close the client connection while leaving Codex's daemon running.

There is no custom daemon, systemd service, API key, or third-party Python
dependency.

## Benchmark snapshot

Measured on the original Ubuntu machine on 2026-08-29 with a one-word response:

- Direct `codex exec`: 6.14 seconds
- Emma persistent path: 4.09 seconds median across three runs
- Daemon connection: 0.30 seconds cold, 0.02 seconds warm

Model and network latency remain the largest part of the total response time.

## Safety note

Emma's output can still be incorrect. Read unfamiliar commands before running
them, especially commands using `sudo`, deletion, disks, permissions, or package
management.
