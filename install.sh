#!/usr/bin/env bash
# Installs emma to ~/.local/bin.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
prefix="${EMMA_INSTALL_PREFIX:-$HOME/.local/bin}"

install -Dm755 "$script_dir/emma" "$prefix/emma"
echo "Installed emma to $prefix/emma"

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
