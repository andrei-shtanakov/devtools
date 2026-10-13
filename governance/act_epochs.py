"""Журнал эпох доверия `approval-policy-source/v2/epochs.yaml` (спека §6.3).

Порядок эпох — позиция в списке; номер обязан ей равняться. Все эпохи,
кроме последней, закрыты (заполнены `last_confirmed`, `valid_until`,
`closed_reason`), последняя открыта. Единственная допустимая мутация —
атомарный переход: закрыть последнюю открытую и добавить следующую.
"""

from __future__ import annotations

import argparse
import datetime
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

if __package__ in (None, ""):  # запуск как `python governance/act_epochs.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from governance.act_source import SourceError, parse_source  # noqa: E402
from governance.act_time import parse_ts  # noqa: E402

SCHEMA = "approval-policy-epochs/v1"
_CLOSING = ("last_confirmed", "valid_until", "closed_reason")


class JournalError(ValueError):
    """Журнал не читается или переход недопустим → `unavailable`."""


@dataclass(frozen=True)
class Epoch:
    """Одна эпоха доверия."""

    epoch: int
    anchor: str
    last_confirmed: str | None = None
    valid_until: str | None = None
    closed_reason: str | None = None

    @property
    def closed(self) -> bool:
        """Эпоха закрыта человеком."""
        return self.last_confirmed is not None


def _closing_values(raw: dict[str, object], position: int) -> list[str | None]:
    values = [raw.get(key) for key in _CLOSING]
    for key, value in zip(_CLOSING, values, strict=True):
        if isinstance(value, (datetime.datetime, datetime.date)):
            raise JournalError(
                f"эпоха #{position}: {key} — YAML разобрал его как время; "
                "closing timestamps must be quoted ISO strings"
            )
    if any(v is not None for v in values) and not all(
        isinstance(v, str) and v for v in values
    ):
        raise JournalError(f"эпоха #{position}: закрыта частично")
    valid_until = values[_CLOSING.index("valid_until")]
    if isinstance(valid_until, str):
        try:
            parse_ts(valid_until)
        except ValueError as exc:
            raise JournalError(
                f"эпоха #{position}: valid_until {valid_until!r} — "
                "не ISO-метка с часовым поясом"
            ) from exc
    return [v if isinstance(v, str) else None for v in values]


def _epoch(raw: object, position: int) -> Epoch:
    if not isinstance(raw, dict) or set(raw) - {"epoch", "anchor", *_CLOSING}:
        raise JournalError(f"эпоха #{position}: неизвестная форма {raw!r}")
    number = raw.get("epoch")
    if type(number) is not int or number != position:
        raise JournalError(f"эпоха #{position}: номер {number!r} ≠ позиции (int)")
    anchor = raw.get("anchor")
    if not isinstance(anchor, str) or len(anchor) != 40:
        raise JournalError(f"эпоха #{position}: anchor обязан быть полным SHA")
    last_confirmed, valid_until, closed_reason = _closing_values(raw, position)
    return Epoch(position, anchor, last_confirmed, valid_until, closed_reason)


def parse_journal(text: str) -> tuple[Epoch, ...]:
    """Разбор и проверка формы журнала."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise JournalError(f"журнал не разобран как YAML: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise JournalError(f"схема журнала не {SCHEMA}")
    raw = data.get("epochs")
    if not isinstance(raw, list) or not raw:
        raise JournalError("журнал без эпох")
    epochs = tuple(_epoch(item, i) for i, item in enumerate(raw, start=1))
    if any(not e.closed for e in epochs[:-1]) or epochs[-1].closed:
        raise JournalError("открытой обязана быть ровно последняя эпоха")
    return epochs


def check_transition(prev: tuple[Epoch, ...], new: tuple[Epoch, ...]) -> None:
    """Сверка новой версии журнала с прежней (CI и каждый потребитель).

    Предусловие: обе версии — результат `parse_journal` (непусты, форма
    проверена); нарушение предусловия — `JournalError`, не падение.
    """
    if not prev or not new:
        raise JournalError("сверка перехода: журнал без эпох")
    if new == prev:
        return
    if len(new) != len(prev) + 1 or new[: len(prev) - 1] != prev[:-1]:
        raise JournalError("допустимо только закрытие последней эпохи + новая эпоха")
    closed, last = new[len(prev) - 1], prev[-1]
    reopened = replace(
        closed, last_confirmed=None, valid_until=None, closed_reason=None
    )
    if reopened != last or not closed.closed:
        raise JournalError("закрытие обязано менять только поля закрытия")
    if new[-1].closed:
        raise JournalError("добавленная эпоха обязана быть открытой")


class _BaseUnavailable(Exception):
    """BASE_SHA не разрешается в коммит."""


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False)


def _show_base(base: str, file: str) -> str | None:
    """Содержимое файла в базовом коммите; `None` — файла там нет."""
    if _git("rev-parse", "--git-dir").returncode != 0:
        raise JournalError("не git-чекаут")
    if _git("cat-file", "-e", f"{base}^{{commit}}").returncode != 0:
        raise _BaseUnavailable(base)
    listing = _git("ls-tree", base, "--", file)
    if listing.returncode != 0:
        raise JournalError(f"git ls-tree {base}: {listing.stderr.strip()}")
    if not listing.stdout.strip():
        return None
    shown = _git("show", f"{base}:{file}")
    if shown.returncode != 0:
        raise JournalError(f"git show {base}:{file}: {shown.stderr.strip()}")
    return shown.stdout


def _check_bootstrap(new: tuple[Epoch, ...], source_text: str) -> None:
    """Файла в базе нет: допустима одна открытая эпоха 1 с якорем bootstrap."""
    try:
        source = parse_source(source_text)
    except SourceError as exc:
        raise JournalError(f"source.env при bootstrap: {exc}") from exc
    if len(new) != 1 or new[0].closed:
        raise JournalError("bootstrap: нужна ровно одна открытая эпоха 1")
    if new[0].anchor != source.bootstrap_activation:
        raise JournalError("bootstrap: anchor ≠ BOOTSTRAP_ACTIVATION из source.env")


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise JournalError(f"{path}: не читается: {exc}") from exc


def check_head_against_base(base: str, file: str, source_env: str) -> None:
    """Допустимость журнала рабочего дерева относительно базового коммита."""
    prev_text = _show_base(base, file)
    new = parse_journal(_read(file))
    if prev_text is None:
        _check_bootstrap(new, _read(source_env))
        return
    check_transition(parse_journal(prev_text), new)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: 0 допустимо, 1 недопустимо, 2 нет BASE_SHA / плохие аргументы."""
    parser = argparse.ArgumentParser(prog="act_epochs")
    parser.add_argument("--check-transition", metavar="BASE_SHA", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--source-env", required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        check_head_against_base(args.check_transition, args.file, args.source_env)
    except _BaseUnavailable:
        print(f"BASE_SHA {args.check_transition!r} не коммит", file=sys.stderr)
        return 2
    except JournalError as exc:
        print(f"недопустимо: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
