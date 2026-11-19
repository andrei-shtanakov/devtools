"""Постоянное состояние личного прогона `dfl` (вне `RUNS_ROOT` wave-раннера)."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, date, datetime
from pathlib import Path

from governance import run_state as rs
from governance import spec_loop
from lite import i18n
from lite.output import say

#: Корень личных прогонов; `None` — `personal-runs` рядом с `rs.RUNS_ROOT`,
#: читается при каждом вызове: тест, изолировавший `RUNS_ROOT`, изолирует и
#: личные прогоны (настоящие прогоны разработчика тестам не видны).
PERSONAL_ROOT: Path | None = None

STEPS = (
    "interview",
    "design",
    "decomposition",
    "spec-pr",
    "code-run",
    "code-pr",
    "done",
)


@dataclass
class PersonalRun:
    """Состояние личного прогона; `step` — текущий шаг из `STEPS`."""

    run_id: str
    ws_id: str
    subject: str
    repo: str
    repo_slug: str
    target_dir: str
    bundle_dir: str
    step: str = "interview"
    #: база прогона; пусто — ещё не установлена (ветка по умолчанию цели,
    #: записывается, только когда факт форджи установлен — `flow._base`)
    base_ref: str = ""
    spec_pr: int | None = None
    code_pr: int | None = None
    stop_reason: str = ""
    #: `code-run`: открытые `spec-runner/run-*` PR до запуска раннера
    #: (`None` — запуск не начинался) и наблюдённый код выхода (write-ahead).
    before_prs: list[int] | None = None
    run_exit: int | None = None
    #: `spec-pr`: голова, которую запушил прогон; мерж — только по ней
    spec_head: str = ""
    #: `spec-pr` (write-ahead): коммит бандла в ветку прогона начат — ветка
    #: может нести коммит прогона; повтор не пересоздаёт её от базы
    spec_committed: bool = False
    #: этапы с паузой на просмотр владельцем (#768) — выбор при старте
    #: (`dfl run --review` / конфиг), дальше не меняется; в JSON — списком
    review_pause: tuple[str, ...] = ()
    #: ручные проверки в конце прогона (#771): `True` ставит `dfl` при
    #: создании; старые run.json — `False`, вопросов им нет. `manual` —
    #: запись ответов (форма `governance.manual_checks`).
    manual_checks: bool = False
    manual: dict = field(default_factory=dict)
    #: язык прогона (#769): задаётся при создании, дальше не меняется;
    #: старые run.json — `ru`. Недопустимое значение `load` заменяет на
    #: `ru` (`i18n.stored_language`): дальше читают только `ru`/`en`.
    language: str = "ru"
    #: недопустимый язык из run.json как был (для предупреждения `dfl`);
    #: `None` — значение было допустимым. Не сохраняется.
    invalid_language: object = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        self.review_pause = tuple(self.review_pause)


def _ws_id(subject: str, today: date) -> str:
    """ws-id из subject + случайный 4-hex суффикс; кириллица темы —
    транслитерацией (`spec_loop.slug_from_subject`, devtools#815).

    Суффикс у всех: перезапуск той же темы в тот же день (после
    `dfl abandon`) не попадёт в старый бандл — иначе `flow._node` пропустил
    бы авторинг, найдя прежний `20-design.md`."""
    return f"{spec_loop.ws_id_for(subject, today)}-{secrets.token_hex(2)}"


def new_personal_run(
    subject: str,
    repo: str,
    repo_slug: str,
    target_dir: str,
    today: date,
    review_pause: tuple[str, ...] = (),
    manual_checks: bool = False,
    language: str = "ru",
) -> PersonalRun:
    """Новый прогон: `run_id` = ws-id + случайный суффикс."""
    ws_id = _ws_id(subject, today)
    return PersonalRun(
        run_id=f"{ws_id}-{secrets.token_hex(3)}",
        ws_id=ws_id,
        subject=subject,
        repo=repo,
        repo_slug=repo_slug,
        target_dir=target_dir,
        bundle_dir=f"workstreams/{ws_id}/spec",
        review_pause=review_pause,
        manual_checks=manual_checks,
        language=language,
    )


def personal_root() -> Path:
    """`PERSONAL_ROOT` или `personal-runs` рядом с текущим `rs.RUNS_ROOT`."""
    if PERSONAL_ROOT is not None:
        return PERSONAL_ROOT
    return rs.RUNS_ROOT.parent / "personal-runs"


def run_dir(run_id: str) -> Path:
    """Каталог прогона под `personal_root()` (run_id валидируется)."""
    rs.validate_id_component(run_id, label="run_id")
    return personal_root() / run_id


def save(run: PersonalRun) -> None:
    """Атомарная запись `run.json`: temp в том же каталоге + `os.replace`."""
    target = run_dir(run.run_id)
    target.mkdir(parents=True, exist_ok=True)
    data = asdict(run)
    del data["invalid_language"]  # не состояние прогона: только для предупреждения
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(dir=target, prefix=".run.json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_name, target / "run.json")
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def load(run_id: str) -> PersonalRun:
    """Читает `run.json`; незнакомые ключи игнорируются. Недопустимый язык —
    `ru`, исходное значение — в `invalid_language` (#769)."""
    raw = json.loads((run_dir(run_id) / "run.json").read_text(encoding="utf-8"))
    known = {f.name for f in fields(PersonalRun)} - {"invalid_language"}
    run = PersonalRun(**{k: v for k, v in raw.items() if k in known})
    run.language, invalid = i18n.stored_language(raw.get("language"))
    if invalid:
        run.invalid_language = raw.get("language")
    return run


def active_for(repo_slug: str, subject: str | None = None) -> PersonalRun | None:
    """Последний по mtime незавершённый прогон репо (с `subject` — этой темы)
    или `None`: второй личный проект репо не прячет первый.

    Нечитаемый `run.json` (битый JSON, нет полей, негодное имя каталога) —
    одна строка предупреждения и пропуск: чужой сломанный прогон не валит
    `dfl run` этого репо."""
    root = personal_root()
    if not root.is_dir():
        return None
    runs: list[tuple[float, PersonalRun]] = []
    for path in root.glob("*/run.json"):
        try:
            runs.append((path.stat().st_mtime, load(path.parent.name)))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            say(f"личный прогон не читается, пропущен: {path} ({exc})")
    for _, run in sorted(runs, key=lambda item: item[0], reverse=True):
        if run.repo_slug != repo_slug or run.step == "done":
            continue
        if subject is None or run.subject == subject:
            return run
    return None


def finished_for(repo_slug: str) -> list[PersonalRun]:
    """Законченные (`done`) личные прогоны репо, новые первыми: `dfl status`
    показывает их ответы на ручные проверки (#771). Нечитаемые — пропуск."""
    root = personal_root()
    if not root.is_dir():
        return []
    runs: list[tuple[float, PersonalRun]] = []
    for path in root.glob("*/run.json"):
        try:
            run = load(path.parent.name)
            mtime = path.stat().st_mtime
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if run.repo_slug == repo_slug and run.step == "done":
            runs.append((mtime, run))
    return [run for _, run in sorted(runs, key=lambda item: item[0], reverse=True)]


def abandoned_root() -> Path:
    """Каталог брошенных личных прогонов (рядом с `personal_root()`)."""
    return personal_root().parent / "personal-runs-abandoned"


def abandon(run: PersonalRun) -> Path:
    """Перенести каталог прогона в `abandoned_root()/<время>/` (обратимо);
    возвращает новое место."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = abandoned_root() / stamp / run.run_id
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(run_dir(run.run_id)), str(dest))
    return dest
