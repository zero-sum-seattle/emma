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

### Personalization

Emma automatically picks up persistent, personal preferences from:

```
~/.config/emma/EMMA.md
```

If that file exists, its contents are sent as **user-level** context ahead
of every question — not developer-level policy, and not the question
itself. If the file doesn't exist, Emma runs exactly as before — nothing is
required.

Example `~/.config/emma/EMMA.md`:

```markdown
# Emma Instructions
Keep answers concise.
Assume I use Ubuntu and bash unless told otherwise.
For shell commands:
- explain destructive operations
- prefer simple solutions
For programming:
- favor maintainable code over unnecessary abstractions
- explain important architectural tradeoffs
- challenge questionable assumptions
```

It's capped at 128 KiB (`MAX_INSTRUCTIONS_FILE_BYTES` in the script) — Emma
is meant to stay fast, and an instructions file is meant to hold a page of
preferences, not a log dump. A file over the limit is a clean error that
exits non-zero before anything is sent to Codex.

Two flags adjust this per invocation:

```bash
emma "why is this systemd service failing?"
emma --instructions ./instructions.md "review this code"
emma --no-user-instructions "why is this systemd service failing?"
```

- `--instructions <file>` adds an extra, more specific file of user-level
  instructions on top of `~/.config/emma/EMMA.md` for just that call. It's
  subject to the same 128 KiB limit.
- `--no-user-instructions` skips `~/.config/emma/EMMA.md` for that call
  (an explicitly passed `--instructions` file is still used).

Layered from least to most specific:

1. Emma's own built-in developer instructions (`DEVELOPER_INSTRUCTIONS` in
   the script) — unaffected by any of this, always sent as
   `thread/start`'s `developerInstructions`.
2. Codex's own project-context handling, including `AGENTS.md` — entirely
   Codex's responsibility. Emma never reads, parses, or otherwise touches
   `AGENTS.md`. Whether Codex finds one at all, and which one, depends on
   the thread's working directory (`cwd`), which Emma sets from `EMMA_CWD`
   — **and `EMMA_CWD` defaults to `/tmp`**, so a plain `emma "..."` run
   from inside a project does *not* by itself put that project's
   `AGENTS.md` in play. Set `EMMA_CWD` to the project directory when you
   want Codex to operate relative to it.
3. `~/.config/emma/EMMA.md`, as user-level context.
4. `--instructions <file>`, layered after it, also user-level.
5. The command-line question itself — always last, always the most
   specific, and never rewritten by any of the above.

A missing `~/.config/emma/EMMA.md` is silent and normal; a missing,
oversized, non-UTF-8, or unreadable file passed to `--instructions` (or an
oversized/unreadable `EMMA.md` that does exist) is a clean error that
exits non-zero before anything is sent to Codex.

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
- `EMMA_CWD` — thread working directory; defaults to `/tmp`. This is also
  what governs whether Codex's own `AGENTS.md` discovery has a project to
  find (see Personalization).
- `EMMA_CODEX` — path to the Codex executable
- `EMMA_SOCKET` — path to the managed app-server Unix socket
- `EMMA_USER_INSTRUCTIONS` — overrides the default personal instructions
  file path; defaults to `~/.config/emma/EMMA.md` (see Personalization)
- `EMMA_DEBUG` — print raw app-server messages when set

An invalid (non-numeric) value for any of the timeout variables prints a
clean `emma: invalid EMMA_...=...` error and exits 2 instead of a Python
traceback.

## How it works

1. Connect to `~/.codex/app-server-control/app-server-control.sock`.
2. Perform a WebSocket upgrade over the Unix socket.
3. Send `initialize`, `thread/start`, and `turn/start` app-server messages.
   `thread/start`'s `developerInstructions` carries only Emma's own base
   instructions — user preferences never go there. Instead, `turn/start`'s
   `input` is a list of `{"type": "text", ...}` items: any content from
   `~/.config/emma/EMMA.md` and/or `--instructions <file>` becomes its own
   leading item, in least-to-most-specific order, and the question itself
   is always the final item, unmodified. (This list shape is inferred from
   the app-server's existing single-item usage, not confirmed against a
   live server — Codex isn't installed in this environment.)
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
