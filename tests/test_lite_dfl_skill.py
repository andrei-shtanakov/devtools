"""Сторож скила `darkfactory` (#766 срез 5): скил не дрейфует от `dfl`.

Скил говорит с человеком и зовёт только `dfl`; каждая названная в нём команда
и флаг должны существовать у парсера, а каждый вид ждущего вопроса — быть
описан в справке.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from lite import dfl, pending

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "darkfactory"
SKILL = SKILL_DIR / "SKILL.md"
REFERENCE = SKILL_DIR / "reference.md"

#: виды ждущего вопроса, на которые скил обязан знать ответ
KINDS = pending.INTERVIEW_KINDS | {
    pending.REVIEW,
    pending.MANUAL,
    pending.REMARKS_CONFIRM,
    pending.EDGE,
    pending.INTERVIEW_PERSONAL,
    pending.BRIEF_CONFIRM,
}
#: флаги главного парсера, допустимые в любой команде
GLOBAL_FLAGS = frozenset({"--cwd", "--help"})

_SPAN = re.compile(r"`([^`\n]+)`")
_FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.DOTALL | re.MULTILINE)
_COMMAND = re.compile(r"\bdfl\s+([a-z][a-z-]*)(.*?)(?=\bdfl\s+[a-z]|$)")
_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z-]*")


def _subcommands() -> dict[str, argparse.ArgumentParser]:
    parser = dfl._parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    raise AssertionError("у dfl нет подкоманд")


def _code_lines(text: str) -> list[str]:
    """Строки кода: содержимое `…` и строки блоков ```."""
    fenced = [line for block in _FENCE.findall(text) for line in block.splitlines()]
    outside = _FENCE.sub("", text)
    return fenced + _SPAN.findall(outside)


def _commands(text: str) -> list[tuple[str, list[str]]]:
    """(подкоманда, флаги) каждого `dfl <sub> …` в коде текста."""
    found = []
    for line in _code_lines(text):
        for sub, rest in _COMMAND.findall(line):
            found.append((sub, _FLAG.findall(rest)))
    return found


def test_skill_frontmatter() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    head = text.split("---", 2)[1]
    assert "name: darkfactory" in head
    match = re.search(r"^description:\s*(\S.*)$", head, re.MULTILINE)
    assert match is not None, "нет непустого description"


def test_every_pending_kind_documented() -> None:
    text = REFERENCE.read_text(encoding="utf-8")
    missing = sorted(k for k in KINDS if f"`{k}`" not in text)
    assert not missing, f"в reference.md не описаны виды: {missing}"


def test_skill_commands_exist() -> None:
    subs = _subcommands()
    seen = 0
    for path in (SKILL, REFERENCE):
        for sub, flags in _commands(path.read_text(encoding="utf-8")):
            seen += 1
            assert sub in subs, f"{path.name}: нет подкоманды dfl {sub}"
            known = set(subs[sub]._option_string_actions) | GLOBAL_FLAGS
            for flag in flags:
                assert flag in known, f"dfl {sub}: нет флага {flag}"
    assert seen > 10, "скил не называет команд dfl — сторож ничего не проверил"


def test_skill_has_prohibitions() -> None:
    text = SKILL.read_text(encoding="utf-8")
    for needle in ("не мержи", "make behaviour-run", "принято предложение:"):
        assert needle in text, f"в SKILL.md нет «{needle}»"


def test_command_scanner_catches_unknown_flag() -> None:
    """Сам сторож: несуществующий флаг и подкоманда видны сканеру."""
    found = _commands("`dfl status --json` и\n```sh\ndfl nope --x\n```\n")
    assert ("status", ["--json"]) in found
    assert ("nope", ["--x"]) in found
    assert "--json" not in _subcommands()["status"]._option_string_actions


def test_command_scanner_ignores_placeholders() -> None:
    found = _commands("`dfl reply --id <id> --text '<тема> --нет'`")
    assert found == [("reply", ["--id", "--text"])]
