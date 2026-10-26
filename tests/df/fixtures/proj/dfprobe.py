"""Entrypoint of the df stage-1 acceptance fixture: `dfprobe [--help]`."""

from __future__ import annotations

import sys


def main() -> int:
    """Print usage for `--help`; print the Python version otherwise."""
    if "--help" in sys.argv[1:]:
        print("usage: dfprobe [--help]")
        return 0
    import six  # the locked dependency is importable

    print(f"dfprobe: python {sys.version.split()[0]}, six {six.__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
