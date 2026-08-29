"""Tests for CLI argument parsing, stdin handling, and env-var validation."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest_helper import EMMA_PATH, load_emma

emma = load_emma()


class ParseArgsTest(unittest.TestCase):
    def test_plain_question(self):
        self.assertEqual(emma.parse_args(["how", "do", "I"]), ("run", ["how", "do", "I"], False))

    def test_timing_anywhere(self):
        self.assertEqual(emma.parse_args(["--timing", "foo"]), ("run", ["foo"], True))
        self.assertEqual(emma.parse_args(["foo", "--timing"]), ("run", ["foo"], True))
        self.assertEqual(emma.parse_args(["foo", "--timing", "bar"]), ("run", ["foo", "bar"], True))

    def test_help_flags(self):
        self.assertEqual(emma.parse_args(["--help"]), ("help", [], False))
        self.assertEqual(emma.parse_args(["-h"]), ("help", [], False))
        self.assertEqual(emma.parse_args(["foo", "--help"]), ("help", [], False))

    def test_version_flag(self):
        self.assertEqual(emma.parse_args(["--version"]), ("version", [], False))

    def test_double_dash_terminates_option_parsing(self):
        self.assertEqual(
            emma.parse_args(["--", "--help", "is", "not", "an", "option"]),
            ("run", ["--help", "is", "not", "an", "option"], False),
        )

    def test_double_dash_after_timing(self):
        self.assertEqual(
            emma.parse_args(["--timing", "--", "-x"]),
            ("run", ["-x"], True),
        )

    def test_empty_argv(self):
        self.assertEqual(emma.parse_args([]), ("run", [], False))


class MainPromptAssemblyTest(unittest.TestCase):
    """Exercise main()'s prompt assembly by faking out ask() and stdin."""

    def _run(self, argv, stdin_text=None, stdin_isatty=True):
        captured = {}

        def fake_ask(prompt, *, timing):
            captured["prompt"] = prompt
            captured["timing"] = timing

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


class EnvValidationTest(unittest.TestCase):
    def _run_emma(self, env_overrides, argv):
        env = dict(os.environ)
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


if __name__ == "__main__":
    unittest.main()
