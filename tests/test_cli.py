"""Tests for CLI argument parsing, stdin handling, and env-var validation."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest_helper import EMMA_PATH, load_emma

emma = load_emma()

# A nonexistent default path so tests never depend on (or accidentally
# read) the real user's ~/.config/emma/EMMA.md.
NO_DEFAULT_INSTRUCTIONS = "/nonexistent/emma-test/EMMA.md"


class ParseArgsTest(unittest.TestCase):
    def test_plain_question(self):
        self.assertEqual(
            emma.parse_args(["how", "do", "I"]),
            ("run", ["how", "do", "I"], False, None, False),
        )

    def test_timing_anywhere(self):
        self.assertEqual(emma.parse_args(["--timing", "foo"]), ("run", ["foo"], True, None, False))
        self.assertEqual(emma.parse_args(["foo", "--timing"]), ("run", ["foo"], True, None, False))
        self.assertEqual(
            emma.parse_args(["foo", "--timing", "bar"]),
            ("run", ["foo", "bar"], True, None, False),
        )

    def test_help_flags(self):
        self.assertEqual(emma.parse_args(["--help"]), ("help", [], False, None, False))
        self.assertEqual(emma.parse_args(["-h"]), ("help", [], False, None, False))
        self.assertEqual(emma.parse_args(["foo", "--help"]), ("help", [], False, None, False))

    def test_version_flag(self):
        self.assertEqual(emma.parse_args(["--version"]), ("version", [], False, None, False))

    def test_double_dash_terminates_option_parsing(self):
        self.assertEqual(
            emma.parse_args(["--", "--help", "is", "not", "an", "option"]),
            ("run", ["--help", "is", "not", "an", "option"], False, None, False),
        )

    def test_double_dash_after_timing(self):
        self.assertEqual(
            emma.parse_args(["--timing", "--", "-x"]),
            ("run", ["-x"], True, None, False),
        )

    def test_empty_argv(self):
        self.assertEqual(emma.parse_args([]), ("run", [], False, None, False))

    def test_instructions_flag(self):
        self.assertEqual(
            emma.parse_args(["--instructions", "notes.md", "foo"]),
            ("run", ["foo"], False, "notes.md", False),
        )

    def test_instructions_flag_missing_value_is_error(self):
        action, message, *_rest = emma.parse_args(["--instructions"])
        self.assertEqual(action, "error")
        self.assertIn("--instructions", message[0])

    def test_no_user_instructions_flag(self):
        self.assertEqual(
            emma.parse_args(["--no-user-instructions", "foo"]),
            ("run", ["foo"], False, None, True),
        )

    def test_instructions_and_no_user_instructions_combined(self):
        self.assertEqual(
            emma.parse_args(["--no-user-instructions", "--instructions", "notes.md", "foo"]),
            ("run", ["foo"], False, "notes.md", True),
        )

    def test_double_dash_treats_instructions_flag_as_question_text(self):
        self.assertEqual(
            emma.parse_args(["--", "--instructions", "foo"]),
            ("run", ["--instructions", "foo"], False, None, False),
        )


class MainPromptAssemblyTest(unittest.TestCase):
    """Exercise main()'s prompt assembly by faking out ask() and stdin."""

    def setUp(self):
        # Never let these tests see the real user's EMMA.md.
        self.env_backup = dict(os.environ)
        os.environ["EMMA_USER_INSTRUCTIONS"] = NO_DEFAULT_INSTRUCTIONS

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env_backup)

    def _run(self, argv, stdin_text=None, stdin_isatty=True):
        captured = {}

        def fake_ask(prompt, *, timing, extra_instructions=None):
            captured["prompt"] = prompt
            captured["timing"] = timing
            captured["extra_instructions"] = extra_instructions

        real_ask = emma.ask
        real_stdin = sys.stdin
        emma.ask = fake_ask
        piped_stdin = None
        try:
            if stdin_text is not None:
                # emma's stdin-piped check needs a real fd to fstat; back
                # this with an actual pipe so S_ISFIFO is true.
                read_fd, write_fd = os.pipe()
                os.write(write_fd, stdin_text.encode("utf-8"))
                os.close(write_fd)
                piped_stdin = os.fdopen(read_fd)
                piped_stdin.isatty = lambda: stdin_isatty
                sys.stdin = piped_stdin
            else:
                sys.stdin = real_stdin
            rc = emma.main(argv)
        finally:
            emma.ask = real_ask
            sys.stdin = real_stdin
            if piped_stdin is not None:
                piped_stdin.close()
        return rc, captured

    def test_argv_only(self):
        rc, captured = self._run(["what", "is", "chmod"], stdin_text=None)
        self.assertEqual(rc, 0)
        self.assertEqual(captured["prompt"], "what is chmod")

    def test_argv_plus_piped_stdin_combined(self):
        rc, captured = self._run(
            ["what", "changed"], stdin_text="diff --git a b\n", stdin_isatty=False
        )
        self.assertEqual(rc, 0)
        self.assertIn("what changed", captured["prompt"])
        self.assertIn("diff --git a b", captured["prompt"])
        self.assertIn("---", captured["prompt"])

    def test_stdin_only(self):
        rc, captured = self._run([], stdin_text="what is disk usage\n", stdin_isatty=False)
        self.assertEqual(rc, 0)
        self.assertEqual(captured["prompt"], "what is disk usage")

    def test_no_argv_no_stdin_prints_usage(self):
        rc, captured = self._run([], stdin_text=None)
        self.assertEqual(rc, 2)
        self.assertNotIn("prompt", captured)

    def test_help_never_touches_ask(self):
        rc, captured = self._run(["--help"], stdin_text=None)
        self.assertEqual(rc, 0)
        self.assertNotIn("prompt", captured)

    def test_version_never_touches_ask(self):
        rc, captured = self._run(["--version"], stdin_text=None)
        self.assertEqual(rc, 0)
        self.assertNotIn("prompt", captured)

    def test_no_emma_md_continues_normally(self):
        # EMMA_USER_INSTRUCTIONS already points at a nonexistent path.
        rc, captured = self._run(["what", "is", "chmod"], stdin_text=None)
        self.assertEqual(rc, 0)
        self.assertEqual(captured["prompt"], "what is chmod")
        self.assertIsNone(captured["extra_instructions"])

    def test_default_instructions_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            instructions_path = Path(tmp) / "EMMA.md"
            instructions_path.write_text("Keep answers concise.\n")
            os.environ["EMMA_USER_INSTRUCTIONS"] = str(instructions_path)

            rc, captured = self._run(["what", "is", "chmod"], stdin_text=None)
            self.assertEqual(rc, 0)
            self.assertEqual(captured["prompt"], "what is chmod")
            self.assertIn("Keep answers concise.", captured["extra_instructions"])

    def test_no_user_instructions_flag_skips_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            instructions_path = Path(tmp) / "EMMA.md"
            instructions_path.write_text("Keep answers concise.\n")
            os.environ["EMMA_USER_INSTRUCTIONS"] = str(instructions_path)

            rc, captured = self._run(
                ["--no-user-instructions", "what", "is", "chmod"], stdin_text=None
            )
            self.assertEqual(rc, 0)
            self.assertEqual(captured["prompt"], "what is chmod")
            self.assertIsNone(captured["extra_instructions"])

    def test_instructions_flag_loads_explicit_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            explicit_path = Path(tmp) / "notes.md"
            explicit_path.write_text("Be terse.\n")

            rc, captured = self._run(
                ["--instructions", str(explicit_path), "review this"], stdin_text=None
            )
            self.assertEqual(rc, 0)
            self.assertEqual(captured["prompt"], "review this")
            self.assertIn("Be terse.", captured["extra_instructions"])

    def test_default_and_explicit_instructions_combined_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            default_path = Path(tmp) / "EMMA.md"
            default_path.write_text("GLOBAL_MARKER\n")
            explicit_path = Path(tmp) / "notes.md"
            explicit_path.write_text("EXPLICIT_MARKER\n")
            os.environ["EMMA_USER_INSTRUCTIONS"] = str(default_path)

            rc, captured = self._run(
                ["--instructions", str(explicit_path), "review this"], stdin_text=None
            )
            self.assertEqual(rc, 0)
            extra = captured["extra_instructions"]
            self.assertIn("GLOBAL_MARKER", extra)
            self.assertIn("EXPLICIT_MARKER", extra)
            # Least specific (default EMMA.md) first, most specific
            # (--instructions) last, per the documented precedence.
            self.assertLess(extra.index("GLOBAL_MARKER"), extra.index("EXPLICIT_MARKER"))

    def test_user_prompt_stays_intact_alongside_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            default_path = Path(tmp) / "EMMA.md"
            default_path.write_text("Assume Ubuntu.\n")
            os.environ["EMMA_USER_INSTRUCTIONS"] = str(default_path)

            rc, captured = self._run(
                ["why", "is", "this", "systemd", "service", "failing?"], stdin_text=None
            )
            self.assertEqual(rc, 0)
            self.assertEqual(captured["prompt"], "why is this systemd service failing?")
            self.assertNotIn("Assume Ubuntu.", captured["prompt"])

    def test_missing_explicit_instructions_file_returns_error(self):
        rc, captured = self._run(
            ["--instructions", "/nonexistent/emma-test/notes.md", "hi"], stdin_text=None
        )
        self.assertEqual(rc, 1)
        self.assertNotIn("prompt", captured)


class VersionConsistencyTest(unittest.TestCase):
    def test_version_used_in_client_info(self):
        source = EMMA_PATH.read_text()
        self.assertIn('"version": VERSION', source)


class CodexVersionWarningTest(unittest.TestCase):
    def _warning(self, initialize_result):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            emma._warn_if_codex_newer(initialize_result)
        return buf.getvalue()

    def test_newer_server_version_warns(self):
        newer = f"{emma.SUPPORTED_CODEX_VERSION[0]}.{emma.SUPPORTED_CODEX_VERSION[1] + 1}.0"
        output = self._warning({"serverInfo": {"version": newer}})
        self.assertIn("newer than emma expects", output)
        self.assertIn(newer, output)

    def test_matching_server_version_is_silent(self):
        matching = f"{emma.SUPPORTED_CODEX_VERSION[0]}.{emma.SUPPORTED_CODEX_VERSION[1]}.3"
        output = self._warning({"serverInfo": {"version": matching}})
        self.assertEqual(output, "")

    def test_missing_version_is_silent(self):
        self.assertEqual(self._warning({}), "")
        self.assertEqual(self._warning({"capabilities": {}}), "")


class UserInstructionsTest(unittest.TestCase):
    """Unit-level tests for resolve_user_instructions/default_instructions_path."""

    def setUp(self):
        self.env_backup = dict(os.environ)
        self.tmpdir = tempfile.TemporaryDirectory()
        os.environ["EMMA_USER_INSTRUCTIONS"] = NO_DEFAULT_INSTRUCTIONS

    def tearDown(self):
        self.tmpdir.cleanup()
        os.environ.clear()
        os.environ.update(self.env_backup)

    def _write(self, name, content):
        path = Path(self.tmpdir.name) / name
        path.write_text(content)
        return path

    def test_no_default_file_returns_none(self):
        result = emma.resolve_user_instructions(explicit_path=None, skip_default=False)
        self.assertIsNone(result)

    def test_default_file_is_loaded(self):
        default_path = self._write("EMMA.md", "Keep answers concise.\n")
        os.environ["EMMA_USER_INSTRUCTIONS"] = str(default_path)

        result = emma.resolve_user_instructions(explicit_path=None, skip_default=False)
        self.assertIn("Keep answers concise.", result)
        self.assertIn(str(default_path), result)

    def test_skip_default_skips_even_if_present(self):
        default_path = self._write("EMMA.md", "Keep answers concise.\n")
        os.environ["EMMA_USER_INSTRUCTIONS"] = str(default_path)

        result = emma.resolve_user_instructions(explicit_path=None, skip_default=True)
        self.assertIsNone(result)

    def test_explicit_file_is_loaded(self):
        explicit_path = self._write("notes.md", "Be terse.\n")

        result = emma.resolve_user_instructions(explicit_path=str(explicit_path), skip_default=True)
        self.assertIn("Be terse.", result)

    def test_default_and_explicit_combined_order(self):
        default_path = self._write("EMMA.md", "GLOBAL_MARKER\n")
        explicit_path = self._write("notes.md", "EXPLICIT_MARKER\n")
        os.environ["EMMA_USER_INSTRUCTIONS"] = str(default_path)

        result = emma.resolve_user_instructions(
            explicit_path=str(explicit_path), skip_default=False
        )
        self.assertLess(result.index("GLOBAL_MARKER"), result.index("EXPLICIT_MARKER"))

    def test_missing_explicit_file_raises(self):
        with self.assertRaises(emma.EmmaError):
            emma.resolve_user_instructions(
                explicit_path=str(Path(self.tmpdir.name) / "missing.md"),
                skip_default=True,
            )

    def test_missing_default_file_is_silent(self):
        # default_instructions_path() pointing at a file that just doesn't
        # exist must never raise — only real read errors should.
        try:
            result = emma.resolve_user_instructions(explicit_path=None, skip_default=False)
        except emma.EmmaError as exc:
            self.fail(f"missing default file raised: {exc}")
        self.assertIsNone(result)

    @unittest.skipIf(os.name != "posix" or os.geteuid() == 0, "requires non-root POSIX")
    def test_unreadable_default_file_raises(self):
        default_path = self._write("EMMA.md", "secret\n")
        default_path.chmod(0o000)
        os.environ["EMMA_USER_INSTRUCTIONS"] = str(default_path)
        try:
            with self.assertRaises(emma.EmmaError):
                emma.resolve_user_instructions(explicit_path=None, skip_default=False)
        finally:
            default_path.chmod(0o644)

    @unittest.skipIf(os.name != "posix" or os.geteuid() == 0, "requires non-root POSIX")
    def test_unreadable_explicit_file_raises(self):
        explicit_path = self._write("notes.md", "secret\n")
        explicit_path.chmod(0o000)
        try:
            with self.assertRaises(emma.EmmaError):
                emma.resolve_user_instructions(explicit_path=str(explicit_path), skip_default=True)
        finally:
            explicit_path.chmod(0o644)


class EnvValidationTest(unittest.TestCase):
    def _run_emma(self, env_overrides, argv):
        env = dict(os.environ)
        env.setdefault("EMMA_USER_INSTRUCTIONS", NO_DEFAULT_INSTRUCTIONS)
        env.update(env_overrides)
        return subprocess.run(
            [sys.executable, str(EMMA_PATH), *argv],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )

    def test_invalid_timeout_prints_clean_error_and_exits_2(self):
        result = self._run_emma({"EMMA_TIMEOUT": "abc"}, ["hi"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("emma: invalid EMMA_TIMEOUT", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_invalid_turn_timeout_prints_clean_error_and_exits_2(self):
        result = self._run_emma({"EMMA_TURN_TIMEOUT": "nope"}, ["hi"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("emma: invalid EMMA_TURN_TIMEOUT", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_help_and_version_touch_no_network(self):
        # --help/--version must exit before any socket/subprocess work.
        result = self._run_emma({}, ["--help"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage: emma", result.stdout)

        result = self._run_emma({}, ["--version"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), emma.VERSION)

    def test_daemon_start_failure_is_clean_and_fast(self):
        result = self._run_emma(
            {
                "EMMA_SOCKET": "/tmp/emma-test-nonexistent.sock",
                "EMMA_CODEX": "/bin/false",
            },
            ["hi"],
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("emma:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_instructions_file_is_clean_and_never_touches_network(self):
        # Even with no socket/codex configured, a bad --instructions path
        # must fail before any connection attempt (no partial request).
        result = self._run_emma(
            {
                "EMMA_SOCKET": "/tmp/emma-test-nonexistent.sock",
                "EMMA_CODEX": "/bin/false",
            },
            ["--instructions", "/nonexistent/emma-test/notes.md", "hi"],
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("emma:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_instructions_flag_missing_value_is_usage_error(self):
        result = self._run_emma({}, ["--instructions"])
        self.assertEqual(result.returncode, 2)
        self.assertIn("emma: --instructions", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
