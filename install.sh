#!/usr/bin/env bash
# Installs emma to ~/.local/bin.
set -euo pipefail
 
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
prefix="${EMMA_INSTALL_PREFIX:-$HOME/.local/bin}"
 
# `install -D` is a GNU coreutils extension; macOS ships BSD install, which
# rejects it. mkdir -p + `install -m` is portable across both.
mkdir -p "$prefix"
install -m 755 "$script_dir/emma" "$prefix/emma"
echo "Installed emma to $prefix/emma"
 
python_bin="$(command -v python3 || true)"
if [ -z "$python_bin" ]; then
    echo "Warning: python3 was not found on your PATH." >&2
elif ! "$python_bin" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "Warning: $python_bin is $("$python_bin" -c 'import platform; print(platform.python_version())'), but emma expects 3.10 or newer." >&2
    echo "On macOS the system python3 is often 3.9; install a newer one (e.g. brew install python)." >&2
fi
 
case ":$PATH:" in
    *":$prefix:"*) ;;
    *)
        echo "Warning: $prefix is not on your PATH." >&2
        echo "Add this to your shell profile:" >&2
        echo "  export PATH=\"$prefix:\$PATH\"" >&2
        ;;
esac
 
if ! command -v codex >/dev/null 2>&1; then
    echo "Warning: codex was not found on your PATH." >&2
    echo "Emma requires the Codex CLI to be installed and authenticated." >&2
fi
 
if command -v emma >/dev/null 2>&1 && [ "$(command -v emma)" != "$prefix/emma" ]; then
    echo "Warning: another 'emma' is shadowing $prefix/emma on your PATH ($(command -v emma))." >&2
fi
 
