# Emma

<img src="assets/emma-mascot.png" alt="Emma mascot" width="240">

Emma is a tiny terminal assistant built on top of the persistent Codex app-server.

She connects to Codex's managed Unix socket, sends a focused question to gpt-5.6-luna, streams the answer straight back to your terminal, and then gets out of the way. The app-server keeps running, so Emma doesn't have to wake up an entirely new Codex process every time you ask something.

Think of her as the AI coworker living in your terminal: quick to answer, low on ceremony, and always one command away.

Emma is intentionally small and focused. She's great for quick questions, command help, explanations, sanity checks, and all the little things that don't need a full interactive Codex session.

## Requirements

- Linux with Python 3.10 or newer
- Codex CLI 0.150.1 or a compatible release
- An existing authenticated Codex login

The app-server protocol is experimental. A future Codex update may require a
small compatibility change.

## Install

```bash
./install.sh
```

This copies `emma` to `~/.local/bin/emma` and warns if that directory isn't on
`PATH` or if `codex` isn't installed. Set `EMMA_INSTALL_PREFIX` to install
somewhere else. If you'd rather install by hand:

```bash
install -Dm755 emma ~/.local/bin/emma
```

## Usage

```bash
emma how do I see disk usage
emma what does chmod 755 mean
emma --timing how do I list failed systemd services
git log | emma what changed here
emma -- --this-looks-like-a-flag-but-isnt
emma --help
emma --version
```

Emma defaults to Luna with low reasoning. It starts Codex's managed daemon
automatically when the socket is unavailable. Every question gets a fresh,
ephemeral, read-only thread with approvals disabled and instructions not to run
tools or access external services.

The question can come from argv, from piped stdin, or both. If stdin is piped
(a pipe or a redirected file — not just any non-terminal stdin) and argv is
also given, the piped input is appended to the argv question as clearly
delimited context, so `git log | emma what changed here` works as expected.
Use `--` to stop option parsing so a question can start with a dash. `--help`
and `--version` are handled directly and never touch the network or socket.

### Daemon lifecycle

You do not need to start the daemon yourself. On every invocation, Emma first
tries to connect to Codex's managed socket. If the socket is unavailable, Emma
runs `codex app-server daemon start`, reconnects, and submits the same question.

The daemon remains running after Emma exits and after the terminal is closed. A
reboot, crash, or manual `codex app-server daemon stop` ends it; the next Emma
call starts it again automatically. That first call may be slightly slower while
the daemon starts.

## Configuration

Environment variables provide lightweight overrides:

- `EMMA_MODEL` — model name; defaults to `gpt-5.6-luna`
- `EMMA_TIMEOUT` (alias `EMMA_IDLE_TIMEOUT`) — **idle** timeout in seconds:
  how long a single read from the app-server may block before giving up.
  It is not a cap on the whole response — a turn that keeps emitting tokens,
  even slowly, never trips it. Defaults to `120`.
- `EMMA_TURN_TIMEOUT` — wall-clock deadline in seconds for an entire turn,
  from `turn/start` to completion. This is the setting that bounds total
  response time. Defaults to `300`.
- `EMMA_CWD` — thread working directory; defaults to `/tmp`
- `EMMA_CODEX` — path to the Codex executable
- `EMMA_SOCKET` — path to the managed app-server Unix socket
- `EMMA_DEBUG` — print raw app-server messages when set

An invalid (non-numeric) value for any of the timeout variables prints a
clean `emma: invalid EMMA_...=...` error and exits 2 instead of a Python
traceback.

## How it works

1. Connect to `~/.codex/app-server-control/app-server-control.sock`.
2. Perform a WebSocket upgrade over the Unix socket.
3. Send `initialize`, `thread/start`, and `turn/start` app-server messages.
4. Stream `item/agentMessage/delta` events to the terminal.
5. Close the client connection while leaving Codex's daemon running.

There is no custom daemon, systemd service, API key, or third-party Python
dependency.

`--timing` prints cumulative phase timestamps to stderr, so a slow response
can be attributed to a specific phase instead of only a single opaque total:

```
emma timing: connect=0.021s initialize=0.045s thread_start=0.061s turn_start=0.078s first_output=1.203s total=4.091s
```

Each value is seconds since the start of the call, in the order the phases
complete: socket connect, the `initialize` handshake, `thread/start`,
`turn/start`, first streamed output, and the overall total.

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

## License

MIT — see [LICENSE](LICENSE).
