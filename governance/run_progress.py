"""Видимый прогресс фазы реализации dfl (devtools#765).

Пока `spec-runner run` идёт (блокирующий `Ops.spec_runner`), окно `dfl`
показывает строки событий задач и живой отсчёт текущей. Источник — файл
состояния spec-runner (`.executor-state.db`, стабильный контракт
`spec-runner/docs/state-schema.md`: `tasks`, `attempts`) только на чтение и
`tasks.md` прогона (названия, число задач). Наблюдатель никогда не валит
прогон: любая ошибка чтения — пропуск такта.
"""

from __future__ import annotations

import contextlib
import os
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from governance.task_bridge import _TASK_HEAD_RE

UNAVAILABLE = "прогресс недоступен: нет {path}"

_CONFIG = "spec-runner.config.yaml"
_LEGACY_CONFIG = "spec/executor.config.yaml"


def _paths_section(target: Path) -> dict:
    """`paths` из конфига spec-runner (как его `_resolve_config_path`)."""
    for rel, wrapped in ((_CONFIG, False), (_LEGACY_CONFIG, True)):
        path = target / rel
        if not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return {}
        if wrapped and isinstance(data, dict):
            data = data.get("executor") or {}
        paths = data.get("paths") if isinstance(data, dict) else None
        return paths if isinstance(paths, dict) else {}
    return {}


def state_db_path(target_dir: str, prefix: str) -> Path:
    """Путь БД состояния spec-runner для `--spec-prefix=<prefix>`.

    Как `spec_runner.config`: `paths.state` из конфига (тогда префикс не
    применяется) относительно `paths.root`; иначе
    `spec/.executor-{prefix}state.db`. Нечитаемый конфиг — умолчание.
    """
    target = Path(target_dir)
    paths = _paths_section(target)
    root = target / str(paths["root"]) if paths.get("root") else target
    state = paths.get("state")
    if state:
        state_path = Path(str(state))
        return state_path if state_path.is_absolute() else root / state_path
    return root / "spec" / f".executor-{prefix}state.db"


def read_tasks(text: str) -> list[tuple[str, str]]:
    """`(id, название)` задач `tasks.md` в порядке файла."""
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        if _TASK_HEAD_RE.match(line):
            head, title = line[4:].split(": ", 1)
            out.append((head.strip(), title.strip()))
    return out


@dataclass(frozen=True)
class TaskRow:
    """Строка задачи в снимке БД: статус, времена, число попыток, ошибка."""

    status: str
    started_at: str | None
    completed_at: str | None
    attempts: int
    last_error: str | None


_SNAPSHOT_SQL = """
SELECT t.task_id, t.status, t.started_at, t.completed_at,
       (SELECT COUNT(*) FROM attempts a WHERE a.task_id = t.task_id),
       (SELECT a.error FROM attempts a WHERE a.task_id = t.task_id
        ORDER BY a.timestamp DESC, a.id DESC LIMIT 1)
FROM tasks t
"""


def read_snapshot(db: Path) -> dict[str, TaskRow] | None:
    """Снимок задач БД только на чтение; нет файла или ошибка — `None`."""
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.5)
        try:
            rows = con.execute(_SNAPSHOT_SQL).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return {
        str(r[0]): TaskRow(str(r[1]), r[2], r[3], int(r[4] or 0), r[5]) for r in rows
    }


def fmt_duration(seconds: float) -> str:
    """`ММ:СС`, от часа — `Ч:ММ:СС`."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
#: длина текста из БД в строке события (ошибка агента бывает огромной)
_MAX_TEXT = 200


def _clean(text: str) -> str:
    """Текст из БД/tasks.md на экран: без управляющих символов, с потолком."""
    cleaned = _CONTROL.sub("", text)
    return cleaned if len(cleaned) <= _MAX_TEXT else cleaned[: _MAX_TEXT - 1] + "…"


def event_line(
    kind: str,
    task_id: str,
    title: str,
    *,
    seconds: float | None = None,
    attempt: int | None = None,
    error: str | None = None,
) -> str:
    """Строка события задачи (форма дизайна §3.1); текст из БД очищен от
    управляющих символов терминала."""
    task_id, title = _clean(task_id), _clean(title)
    if kind == "started":
        return f"▶ {task_id} «{title}» — начата"
    if kind == "done":
        return f"✓ {task_id} готова · {fmt_duration(seconds or 0)}"
    if kind == "retry":
        return f"↻ {task_id} «{title}» — попытка {attempt}"
    first = (error or "").strip().splitlines()
    reason = _clean(first[0]) if first else "причина не записана"
    return f"✗ {task_id} не прошла: {reason}"


def live_line(task_id: str, ordinal: int, total: int, seconds: float) -> str:
    """Живая строка: задача, «N из M» (если M известно), отсчёт."""
    count = f" · {ordinal} из {total}" if total else ""
    return f"  {_clean(task_id)}{count} · {fmt_duration(seconds)}"


def summary_line(done: int, total: int, seconds: float) -> str:
    """Итог фазы реализации (без стоимости — решение владельца)."""
    return f"реализация: сделано {done} из {total} · {fmt_duration(seconds)}"


#: статусы, которые считаются «сделано» (раннер их больше не делает)
_DONE = frozenset({"success", "skipped"})
#: столько секунд без БД — строка «прогресс недоступен»
UNAVAILABLE_AFTER = 30.0


def _epoch(iso: str | None) -> float | None:
    """Наивное ISO spec-runner (локальное время) → epoch; битое — `None`."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


class Watcher:
    """Разница снимков БД → строки событий; текущая задача → живая строка.

    `tick(t)` детерминирован (время — аргумент): поток лишь зовёт его раз в
    секунду. События до `begin` не печатаются; любое исключение внутри
    `tick` проглатывается — прогресс не валит прогон.
    """

    def __init__(
        self,
        db: Path,
        tasks: list[tuple[str, str]],
        *,
        emit: Callable[[str], None],
        live: Callable[[str], None] | None,
    ) -> None:
        self.db, self.emit, self.live = db, emit, live
        self.titles = dict(tasks)
        self.total = len(tasks)
        self.prev: dict[str, TaskRow] = {}
        self.first_start: dict[str, float] = {}
        self.began = 0.0
        self.seen_db = False
        self.warned = False
        #: `running` из базового снимка — хвост убитого прошлого запуска:
        #: его «восстановление» раннером в `failed` — не событие этого запуска
        self.stale: dict[str, str | None] = {}

    def begin(self, t: float) -> None:
        """Базовый снимок без событий: прошлое этого прогона не печатается."""
        self.began = t
        snap = read_snapshot(self.db)
        if snap is not None:
            self.seen_db = True
            self.prev = snap
            self.stale = {
                k: r.started_at for k, r in snap.items() if r.status == "running"
            }

    def tick(self, t: float) -> None:
        """Один такт: события переходов и живая строка."""
        try:
            self._tick(t)
        except Exception:  # noqa: BLE001 - прогресс — украшение, прогон важнее
            return

    def _tick(self, t: float) -> None:
        snap = read_snapshot(self.db)
        if snap is None:
            if (
                not self.seen_db
                and not self.warned
                and t - self.began >= (UNAVAILABLE_AFTER)
            ):
                self.warned = True
                self.emit(UNAVAILABLE.format(path=self.db))
            return
        self.seen_db = True
        for task_id, row in snap.items():
            if task_id in self.stale:
                if row.started_at == self.stale[task_id]:
                    continue  # хвост прошлого запуска — молчим
                del self.stale[task_id]  # новый старт: обычная задача
                self._events(task_id, None, row, t)
                continue
            self._events(task_id, self.prev.get(task_id), row, t)
        self.prev = snap
        self._live(snap, t)

    def _events(self, task_id: str, prev: TaskRow | None, row: TaskRow, t: float):
        title = self.titles.get(task_id, "")
        was = prev.status if prev else None
        if row.status == "running":
            start = _epoch(row.started_at)
            if start is not None:
                self.first_start.setdefault(task_id, start)
            if was != "running":
                self.emit(event_line("started", task_id, title))
            elif prev is not None and row.started_at != prev.started_at:
                self.emit(event_line("retry", task_id, title, attempt=row.attempts + 1))
        elif row.status == "success" and was != "success":
            end = _epoch(row.completed_at) or t
            start = self.first_start.get(task_id) or _epoch(row.started_at) or end
            self.emit(event_line("done", task_id, title, seconds=end - start))
        elif row.status == "failed" and was != "failed":
            self.emit(event_line("failed", task_id, title, error=row.last_error))

    def _live(self, snap: dict[str, TaskRow], t: float) -> None:
        if self.live is None:
            return
        running = [
            (_epoch(r.started_at) or t, task_id)
            for task_id, r in snap.items()
            if r.status == "running" and task_id not in self.stale
        ]
        if not running:
            # между задачами — отсчёт от конца предыдущей, без номера (§3.2)
            ends = [_epoch(r.completed_at) for r in snap.values()]
            since = max([e for e in ends if e and e >= self.began] or [self.began])
            self.live(f"  … · {fmt_duration(t - since)}")
            return
        start, task_id = max(running)
        done = sum(1 for r in snap.values() if r.status in _DONE)
        self.live(live_line(task_id, done + 1, self.total, t - start))

    def summary(self, t: float) -> str:
        """Итог: сделано (включая до старта) из всех задач, время запуска."""
        done = sum(1 for r in self.prev.values() if r.status in _DONE)
        total = self.total or len(self.prev)  # tasks.md не прочитался — из БД
        return summary_line(done, total, t - self.began)


#: экранный fd под перехватом вывода `dfl` (то же имя, что
#: `auto_tasks.SCREEN_FD_ENV` и `lite.runlog.SCREEN_FD_ENV`; равенство — тест)
SCREEN_FD_ENV = "DFL_SCREEN_FD"
THREAD_NAME = "dfl-run-progress"


class _LiveScreen:
    """Живая строка в экранный fd: только под перехватом (fd в окружении —
    вывод раннера уходит в лог) и только в терминал. fd читается при каждой
    записи: перехват включается внутри вызова раннера."""

    def __init__(self, fallback_fd: int = 1, *, uncaptured: bool = False) -> None:
        self.drawn = False
        self.fallback_fd = fallback_fd
        self.uncaptured = uncaptured  # без перехвата — рисовать в `fallback_fd`

    @staticmethod
    def _tty(fd: int) -> int | None:
        try:
            return fd if os.isatty(fd) else None
        except OSError:
            return None

    def _fd(self) -> int | None:
        raw = os.environ.get(SCREEN_FD_ENV, "")
        if raw.isdigit():
            return self._tty(int(raw))
        return self._tty(self.fallback_fd) if self.uncaptured else None

    def __call__(self, text: str) -> None:
        fd = self._fd()
        if fd is None:
            return
        with contextlib.suppress(OSError):
            os.write(fd, f"\r{text}\x1b[K".encode())
            self.drawn = True

    def clear(self) -> None:
        """Стереть нарисованную строку. Перехват мог уже кончиться (личный
        путь: он живёт только внутри вызова раннера) — тогда экран снова на
        `fallback_fd` (stdout)."""
        if not self.drawn:
            return
        fd = self._fd()
        if fd is None:
            fd = self._tty(self.fallback_fd)
        if fd is None:
            return
        with contextlib.suppress(OSError):
            os.write(fd, b"\r\x1b[K")
        self.drawn = False


def watched(
    call: Callable[[], int],
    *,
    target_dir: str,
    prefix: str,
    tasks_rel: str,
    emit: Callable[[str], None],
    period: float = 1.0,
) -> int:
    """Вызвать раннер (`call`) под наблюдателем прогресса.

    Код возврата и исключение `call` — как есть; поток наблюдателя
    останавливается в любом исходе, живая строка стирается, затем итог.
    """
    try:
        text = (Path(target_dir) / tasks_rel).read_text(encoding="utf-8")
    except (OSError, ValueError):  # нет файла, права, битая кодировка
        text = ""
    live = _LiveScreen()

    def emit_line(line: str) -> None:
        live.clear()  # строка события не дописывается в хвост живой
        emit(line)

    try:
        watcher = Watcher(
            state_db_path(target_dir, prefix),
            read_tasks(text),
            emit=emit_line,
            live=live,
        )
        watcher.begin(time.time())
    except Exception:  # noqa: BLE001 - прогресс не мешает прогону: без него
        return call()
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(period):
            watcher.tick(time.time())

    thread = threading.Thread(target=loop, name=THREAD_NAME, daemon=True)
    thread.start()
    try:
        return call()
    finally:
        stop.set()
        thread.join(timeout=2)
        watcher.tick(time.time())
        live.clear()
        with contextlib.suppress(Exception):
            emit(watcher.summary(time.time()))


THINKING = "… думаю · {elapsed}"
THINKING_THREAD = "dfl-thinking"


def thinking[R](call: Callable[[], R], *, period: float = 1.0) -> R:
    """Вызвать агента (`call`) под живой строкой `… думаю · ММ:СС`.

    Строка — только в терминал (экранный fd перехвата или stdout), без
    терминала её нет; стирается в любом исходе, результат и исключение
    `call` — как есть (devtools#799)."""
    live = _LiveScreen(uncaptured=True)
    began = time.monotonic()

    def draw() -> None:
        live(THINKING.format(elapsed=fmt_duration(time.monotonic() - began)))

    draw()
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(period):
            draw()

    thread = threading.Thread(target=loop, name=THINKING_THREAD, daemon=True)
    thread.start()
    try:
        return call()
    finally:
        stop.set()
        thread.join(timeout=2)
        live.clear()
