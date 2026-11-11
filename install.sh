#!/bin/sh
# Установка DarkFactory (лёгкий режим test) без sudo: macOS или Linux.
set -eu

case "$(uname -s)" in
    Darwin | Linux) ;;
    *)
        echo "install.sh: поддерживается только macOS (Darwin) и Linux" >&2
        exit 1
        ;;
esac

missing=""
for tool in git gh uv claude make jq; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        missing="$missing $tool"
    fi
done
if [ -n "$missing" ]; then
    echo "install.sh: не найдены программы:$missing" >&2
    exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
    echo "install.sh: gh не авторизован — выполните 'gh auth login'" >&2
    exit 1
fi

cd "$(dirname "$0")"
uv sync --frozen --group governance
exec uv run --frozen --group governance python -m lite.install "$@"
