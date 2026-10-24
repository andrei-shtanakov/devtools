"""Bootstrap of the administrative command (spec 3.9, F10).

Run by a human before the first activation, or to recover when
`darkfactory` refuses:

    sudo <python >= 3.12> -I -B -S <devtools checkout>/df/bootstrap.py init|install ...

The checkout root is the directory above this file's `df/`, found from the
file's own path, never from `cwd` or `PATH`. Trust in that checkout and in the
interpreter is the human's (they hold `sudo`). Only `init` and `install` are
accepted; `df.cli.main(mode="bootstrap")` takes the writer lock, settles and
reads the snapshot itself, and refuses when the installed command is healthy.

This file must parse under the oldest `python3` a host ships (macOS 3.9):
the version check below has to run there and say what is needed.
"""

from __future__ import annotations

import os
import sys

MIN_PYTHON = (3, 12)
COMMANDS = ("init", "install")
EXIT_REFUSED = 2


def checkout_root(path: str) -> str:
    """The directory above `df/` of this file (links resolved)."""
    return os.path.dirname(os.path.dirname(os.path.realpath(path)))


def refusal(argv: list[str]) -> str | None:
    """Why bootstrap refuses to run, or None."""
    if sys.version_info < MIN_PYTHON:
        return "нужен Python >= 3.12"
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        return "запустите как: sudo <python> -I -B -S <чекаут>/df/bootstrap.py ..."
    if not argv or argv[0] not in COMMANDS:
        return "bootstrap принимает только init или install"
    return None


def main(argv: list[str]) -> int:
    """Check, put the checkout first on `sys.path`, run `df.cli.main`."""
    problem = refusal(argv)
    if problem is not None:
        sys.stderr.write("darkfactory bootstrap: " + problem + "\n")
        return EXIT_REFUSED
    sys.path.insert(0, checkout_root(__file__))
    from df.cli import main as cli_main

    return cli_main(argv, mode="bootstrap", read=None, lock_fd=None)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
