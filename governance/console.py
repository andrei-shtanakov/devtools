"""Behaviour console (Task 4): textual TUI поверх `governance.console_model`
(Task 3) — таблица прогонов, деталь по Enter (ops-журнал, findings, verdict
reason, срез бандла).

Read-only: сам модуль не запускает пайплайн и не пишет `run.json` — все
изменения состояния идут через `tmux new-session` с `make behaviour-run`
(`governance.runner` CLI, тот же паттерн, что и resume/verify руками).

`textual` — тяжёлая опциональная зависимость (группа uv `governance`):
import лениво, только внутри интерактивной TUI-ветки. `--json` и non-TTY
plain-путь обязаны работать и без установленного textual.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from governance import run_state as rs

if TYPE_CHECKING:  # только для аннотаций — в рантайме не исполняется
    from governance import console_model as cm

DEVTOOLS_ROOT = Path(__file__).resolve().parent.parent

# `console_model` (Task 3) тянет `steward` транзитивно через
# `bundle_state.py` (модульный `import`, безусловно) — той же группой uv
# `governance`, что и `textual`. Импортируем его лениво здесь же, той же
# дисциплиной, что и textual ниже: иначе сам `import governance.console`
# падает без установленной группы, и `--json`/plain-путь, которому textual
# не нужен, всё равно требовал бы steward только на этапе импорта модуля.
# Аннотации `cm.RunRow` живут только в `TYPE_CHECKING`-импорте выше:
# `from __future__ import annotations` не вычисляет их в рантайме (лень
# сохраняется), но делает их видимыми `ruff`/типчекерам (F821, финальное
# ревью C-2) — без этого блока `cm` был undefined name везде, где он
# встречался только в сигнатурах, а не в теле функции.


def _session_name(run_id: str) -> str:
    return f"beh-{run_id}"


def _verify_session_name(parent_run_id: str) -> str:
    return f"beh-verify-{parent_run_id}"


def _tmux_launch(session: str, root: Path, make_args: str) -> str:
    """Одна tmux-сессия на `session`; повторный запуск — подсказка attach.

    `=`-таргет в `has-session` — точное совпадение имени (урок
    issue_console.launch): без него tmux матчит по префиксу, и
    `beh-r-1` ложно "находит" `beh-r-10`.
    """
    target = f"={session}"
    exists = subprocess.run(
        ["tmux", "has-session", "-t", target], capture_output=True, text=True
    )
    if exists.returncode == 0:
        return f"exists: tmux attach -t {target}"
    shell_cmd = (
        f"make behaviour-run ARGS={shlex.quote(make_args)}; exec $SHELL"
    )
    done = subprocess.run(
        ["tmux", "new-session", "-d", "-s", session, "-c", str(root), shell_cmd],
        capture_output=True, text=True,
    )
    if done.returncode:
        raise RuntimeError(done.stderr.strip() or "tmux failed")
    return f"started {session}"


def launch_resume(run_id: str, root: Path = DEVTOOLS_ROOT) -> str:
    """`make behaviour-run ARGS='resume --run-id <id>'` в новой tmux-сессии."""
    session = _session_name(run_id)
    return _tmux_launch(session, root, f"resume --run-id {run_id}")


def launch_verify(
    parent_run_id: str, run_id: str, root: Path = DEVTOOLS_ROOT
) -> str:
    """`make behaviour-run ARGS='verify --parent <parent> --run-id <id>'`.

    Сессия именуется от РОДИТЕЛЯ (`beh-verify-<parent_run_id>`), не от
    `run_id` потомка (финальное ревью I-7): `verify_plan` генерирует свежий
    `run_id` на КАЖДЫЙ вызов (`os.urandom`), поэтому дедуп по `=`-таргету с
    именем от потомка никогда не совпадал бы — второе нажатие `v` на том же
    ряду поднимало бы вторую tmux-сессию и создавало ВТОРОЙ
    remediation-issue у уже помеченного родителя (`runner.verify()` не
    проверяет, есть ли у родителя потомок). Имя от parent делает `=`-гвард
    рабочим: повторный вызов находит существующую сессию раньше, чем
    успевает стартовать новый `verify`.
    """
    session = _verify_session_name(parent_run_id)
    return _tmux_launch(
        session, root, f"verify --parent {parent_run_id} --run-id {run_id}"
    )


def verify_plan(row: cm.RunRow) -> tuple[str, str] | str:
    """`(parent_run_id, новый child run_id)` для verify выбранного ряда.

    Семантика `runner.verify(parent_run_id, ops, run_id)` (спека §5): parent
    — сам `merged_unverified`-прогон (`remediated_by` у родителя всегда
    `None` — это поле потомка, указывающее на родителя, не наоборот; брать
    `row.remediated_by` как parent значило бы искать родителя у родителя),
    `run_id` — НОВЫЙ id ребёнка, который создаёт verify (`_reserve_run_id`
    внутри `verify()` требует ещё не занятый id). Любой статус, кроме
    `merged_unverified`, -> строка-ошибка, не молчаливый отказ.
    """
    if row.status != "merged_unverified":
        return f"verify: run {row.run_id} не в merged_unverified"
    child = f"{row.run_id}-v{os.urandom(2).hex()}"
    return row.run_id, child


def _safe_run_detail(run_id: str) -> cm.RunDetail | str:
    """`cm.run_detail(run_id)` либо строка-ошибка на битом `run.json`.

    `list_runs()` уже показывает битый прогон как `status="corrupt"`
    (`console_model.py`), но `run_detail()` не защищён тем же
    `try/except` — Enter на таком ряду раньше выбрасывал
    `ValueError`/`OSError`/`TypeError`/`KeyError` прямо в message pump
    textual и ронял всё приложение (финальное ревью I-4). Function-level:
    не трогает textual, тестируется напрямую.
    """
    from governance import console_model as cm

    try:
        return cm.run_detail(run_id)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return f"run {run_id}: битый run.json ({exc})"


def _bundle_summary_for(run_id: str) -> tuple[tuple[str, str], ...]:
    """Срез бандла текущего прогона; битый/нечитаемый `run.json` -> пусто."""
    from governance import console_model as cm

    try:
        state = rs.load(run_id)
    except (OSError, ValueError, TypeError, KeyError):
        return ()
    return cm.bundle_summary(state.target_dir, state.profile, state.bundle_dir)


def _print_plain(rows: tuple[cm.RunRow, ...]) -> None:
    if not rows:
        print("no runs")
        return
    for row in rows:
        print(
            f"{row.run_id:<24} {row.status:<10} {row.step:<20} "
            f"ws={row.ws_id} repo={row.repo} pr={row.pr}"
        )


def _build_app(rows: tuple[cm.RunRow, ...], root: Path):
    """Собирает `BehaviourConsoleApp`; textual импортируется здесь и только

    здесь (не на верхнем уровне модуля) — `--json`/non-TTY plain-путь не
    должен требовать установленного textual (ленивый импорт).
    """
    from textual.app import App, ComposeResult
    from textual.containers import Vertical
    from textual.screen import Screen
    from textual.widgets import DataTable, Footer, Header, Static

    class DetailScreen(Screen):
        BINDINGS = [
            ("q", "app.pop_screen", "back"),
            ("escape", "app.pop_screen", "back"),
        ]

        def __init__(self, run_id: str) -> None:
            super().__init__()
            self._run_id = run_id

        def compose(self) -> ComposeResult:
            yield Header()
            yield Static(id="detail-body")
            yield Footer()

        def on_mount(self) -> None:
            detail = _safe_run_detail(self._run_id)
            if isinstance(detail, str):
                self.query_one("#detail-body", Static).update(detail)
                return
            bundle = _bundle_summary_for(self._run_id)
            lines = [f"run_id: {detail.row.run_id}  status: {detail.row.status}"]
            lines.append("")
            lines.append("ops:")
            lines.extend(f"  {key:<20} {status}" for key, status in detail.ops)
            lines.append("")
            lines.append(f"verdict_reason: {detail.verdict_reason or '—'}")
            lines.append("")
            lines.append("bundle:")
            lines.extend(f"  {node_id:<24} {status}" for node_id, status in bundle)
            if detail.findings:
                lines.append("")
                lines.append("findings:")
                lines.append(detail.findings)
            self.query_one("#detail-body", Static).update("\n".join(lines))

    class BehaviourConsoleApp(App):
        BINDINGS = [
            ("q", "quit", "quit"),
            ("r", "resume_selected", "resume"),
            ("v", "verify_selected", "verify"),
        ]

        # Имена биндингов должны совпадать с action_<name> дословно — textual
        # резолвит их по строке, несовпадение молча не срабатывает (нашлось
        # только ручным pilot-смоуком, см. task-4-report).

        def __init__(self, rows: tuple[cm.RunRow, ...], root: Path) -> None:
            super().__init__()
            self._rows = rows
            self._root = root
            self._status = ""

        def compose(self) -> ComposeResult:
            yield Header()
            with Vertical():
                yield DataTable(id="runs")
                yield Static(id="status")
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one("#runs", DataTable)
            # cell — дефолт cursor_type, но Enter в нём не шлёт RowSelected.
            table.cursor_type = "row"
            table.add_columns("run_id", "ws_id", "repo", "status", "step", "pr")
            for row in self._rows:
                table.add_row(
                    row.run_id, row.ws_id, row.repo, row.status, row.step,
                    "" if row.pr is None else str(row.pr),
                    key=row.run_id,
                )

        def _selected_run_id(self) -> str | None:
            table = self.query_one("#runs", DataTable)
            if table.row_count == 0:
                return None
            key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            return key.value if key is not None else None

        def _selected_row(self) -> cm.RunRow | None:
            run_id = self._selected_run_id()
            if run_id is None:
                return None
            return next((r for r in self._rows if r.run_id == run_id), None)

        def _set_status(self, text: str) -> None:
            self._status = text
            self.query_one("#status", Static).update(text)

        def on_data_table_row_selected(self, event: object) -> None:
            row = self._selected_row()
            if row is None:
                return
            # Битый run.json уже виден в таблице как status="corrupt"
            # (console_model.list_runs) — не пытаемся открыть деталь, она
            # всё равно её не построит (I-4): статус-строка вместо падения.
            if row.status == "corrupt":
                self._set_status(f"run {row.run_id}: битый run.json")
                return
            self.push_screen(DetailScreen(row.run_id))

        def action_resume_selected(self) -> None:
            run_id = self._selected_run_id()
            if run_id is None:
                return
            try:
                self._set_status(launch_resume(run_id, self._root))
            except (RuntimeError, FileNotFoundError) as exc:
                self._set_status(f"error: {exc}")

        def action_verify_selected(self) -> None:
            row = self._selected_row()
            if row is None:
                return
            plan = verify_plan(row)
            if isinstance(plan, str):
                self._set_status(plan)
                return
            parent_run_id, child_run_id = plan
            try:
                self._set_status(
                    launch_verify(parent_run_id, child_run_id, self._root)
                )
            except (RuntimeError, FileNotFoundError) as exc:
                self._set_status(f"error: {exc}")

    return BehaviourConsoleApp(rows, root)


def _run_tui(rows: tuple[cm.RunRow, ...], root: Path) -> None:
    """Интерактивная ветка: делегирует сборку `_build_app` (лень импорта)."""
    _build_app(rows, root).run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true",
        help="печать list_runs как JSON, без TUI (без импорта textual)",
    )
    parser.add_argument(
        "--run-id", default=None,
        help=(
            "деталь одного прогона (detail_to_json(run_detail(<id>))), "
            "без TUI; сочетается с --json ради единообразия вызова"
        ),
    )
    parser.add_argument(
        "--root", type=Path, default=DEVTOOLS_ROOT,
        help="корень devtools для tmux -c (cwd resume/verify)",
    )
    args = parser.parse_args(argv)

    from governance import console_model as cm

    if args.run_id is not None:
        print(cm.detail_to_json(cm.run_detail(args.run_id)))
        return 0

    rows = cm.list_runs()
    if args.json:
        print(cm.rows_to_json(rows))
        return 0
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        _print_plain(rows)
        return 0
    _run_tui(rows, args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
