"""Типизированные исходы фактов, от которых зависит терминализация (§I12).

Слой `ops` местами сознательно не различает «факта нет» и «узнать не
удалось»: `show_file` отдаёт `None` и когда ревизии нет, и когда файла в ней
нет; `delete_remote_branch` отдаёт `False` и при отсутствии прав, и когда
ветки уже нет (issue #177). Контракт (§I12, «Требование к наблюдаемости»)
обязывает реализацию либо ввести типизированный результат —
`FOUND`/`ABSENT`/`UNAVAILABLE`/`FORBIDDEN`, — либо честно оставить ветку
терминализации недостижимой. Общий тип живёт в `governance.facts`. Этот
модуль классифицирует по нему факты терминализации заявки; fact-методы ops
классифицируют у subprocess-вызова те ответы, где свёртка в примитив уже
необратимо смешала бы `ABSENT` и `UNAVAILABLE`.

ГЛАВНОЕ ПРАВИЛО, и оно же единственное место, где реализация способна
незаметно обнулить весь §I12: **в `invalidated` переводит только
положительно установленный факт**. `None`, `False`, `rc != 0`, исключение
сети и любой иной свёрнутый исход дают `UNAVAILABLE` и оставляют заявку
ЖИВОЙ. Эвристики («здесь очевидно, что файла просто нет») запрещены прямым
текстом контракта: снаружи такая эвристика выглядит как работающая
терминализация, а на деле хоронит живые заявки при первом же сбое сети.

Асимметрия цены прямая: ошибка в сторону «временно» стоит лишний круг
(оператор повторяет вызов), ошибка в сторону «постоянно» требует заново
пройти весь путь одобрения — самый дорогой шаг схемы. Fail-closed здесь
означает «не хоронить», а не «отказать».

Что НЕ типизируется и почему: `delete_remote_branch`. §I12 выводит вывод
ветки из обращения из блокирующих шагов («оставшаяся ветка — неубранный
мусор, а не опасность»), значит терминализация на нём не стоит вовсе, а
типизировать примитив ради единообразия — расширять поверхность без
предмета. В cleanup замены его `False` не считается фактом: task-bridge
отдельно перечитывает типизированный branch-head и уже по нему различает
отсутствие, неизвестность и оставшуюся ссылку. Каскад approve-node вывод
ветки сознательно оставляет best-effort без такой гарантии: уникальное имя
не позволяет старой ветке стать новым предложением.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Iterable
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

from governance import ssot_env
from governance.facts import Fact, Outcome, unavailable
from governance.ops import Ops


# --- Факт: есть ли PR на ветке заявки -----------------------------------


def search_pr(
    ops: Ops, repo_slug: str, branch: str, *, any_state: bool = False
) -> Fact[int]:
    """PR на ветке `branch`: `FOUND` номер / `ABSENT` / `UNAVAILABLE`.

    `ABSENT` здесь ЗАКОННЫЙ, и это не оценка «по контексту», а гарантия
    самого примитива: `ops.find_pr` отдаёт `None` ТОЛЬКО когда таких PR
    нет, а сбой запроса (rc != 0, битый JSON, неожиданная форма) поднимает
    `RuntimeError` — различие заведено там же и ровно за этим (финальное
    ревью #156). Классификация лишь переносит его в тип.

    Крэш-окно «коммит есть, PR ещё нет» (§I12) читает этот факт первым, но
    решение принимает НЕ по нему: имя ветки говорит, где смотреть, а
    идентичность устанавливает записанный заявкой `head_sha`.
    """
    try:
        found = ops.find_pr(repo_slug, branch, any_state=any_state)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return unavailable(f"PR на ветке {branch}: запрос не удался ({exc})")
    if found is None:
        return Fact(Outcome.ABSENT, None, f"PR на ветке {branch} нет")
    return Fact(Outcome.FOUND, found, f"PR на ветке {branch}: #{found}")


# --- Факт: состояние PR и обстоятельства его мержа -----------------------


class Disposition(Enum):
    """Состояние PR — прочитанное, а не выведенное."""

    OPEN = "open"
    MERGED = "merged"
    CLOSED_UNMERGED = "closed_unmerged"


@dataclass(frozen=True)
class MergeEvent:
    """Forge-событие мержа: учётка, время, коммит.

    Ровно то, что §I12 называет источником подписи, и ровно столько:
    `approved_by` — учётка, которой форджа атрибутировала мерж,
    `approved_at` — время этого события. Что подпись НЕ доказывает —
    физическое присутствие человека — записано в контракте остатком, а не
    замаскировано, и этот тип не пытается доказать больше.
    """

    login: str
    merged_at: str
    commit: str


def read_pr(ops: Ops, repo_slug: str, pr: int) -> Fact[dict]:
    """Сырые факты PR: `FOUND` словарь либо `UNAVAILABLE`.

    `ABSENT` здесь не бывает намеренно. `gh pr view` отдаёт ненулевой rc и
    на несуществующий PR, и на сетевой сбой, и на отозванный токен —
    различить нечем, а «PR не существует» как постоянный отказ похоронило
    бы заявку по сетевой ошибке.
    """
    try:
        facts = ops.pr_facts(repo_slug, pr)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        return unavailable(f"факты PR #{pr}: запрос не удался ({exc})")
    if not isinstance(facts, dict) or not facts:
        return unavailable(f"факты PR #{pr}: пустой либо неожиданный ответ")
    return Fact(Outcome.FOUND, facts, f"факты PR #{pr} прочитаны")


def disposition(facts: dict) -> Fact[Disposition]:
    """Состояние PR из его фактов: `FOUND` одно из трёх либо `UNAVAILABLE`.

    Величина `state` читается как есть; `MERGED` от `CLOSED` форджа
    различает сама, и выводить одно из другого (скажем, по непустому
    `mergedAt`) значило бы завести второе определение того же факта.
    Незнакомое значение — `UNAVAILABLE`, а не «наверное открыт»: молчаливый
    дефолт здесь и есть эвристика, которую §I12 запрещает.
    """
    state = facts.get("state")
    if state == "MERGED":
        return Fact(Outcome.FOUND, Disposition.MERGED, "PR вмержен")
    if state == "CLOSED":
        return Fact(
            Outcome.FOUND, Disposition.CLOSED_UNMERGED, "PR закрыт без мержа"
        )
    if state == "OPEN":
        return Fact(Outcome.FOUND, Disposition.OPEN, "PR открыт")
    return unavailable(f"состояние PR не прочитано: state={state!r}")


def merge_event(facts: dict) -> Fact[MergeEvent]:
    """Акт мержа: `FOUND` событие / `ABSENT` / `UNAVAILABLE`.

    - `FOUND` — PR вмержен И все три величины события непусты. Это тот
      самый акт одобрения узла (§I12): подпись берётся отсюда;
    - `ABSENT` — PR ЗАКРЫТ без мержа. Факт установлен и однозначен:
      одобрения не было и уже не будет, заявка терминализуется в
      `abandoned` (человек решил не одобрять);
    - `UNAVAILABLE` — PR ОТКРЫТ (акта ещё не было — ждём человека, §I9),
      состояние не прочитано, либо вмержен, но какой-то величины события в
      ответе нет. Последний случай нарочно не `FOUND` с дырой: неполное
      событие не может ни подписать узел, ни быть сверенным фазой 3.

    Открытый PR даёт `UNAVAILABLE`, а не `ABSENT`, потому что вопрос здесь
    один — «состоялся ли акт», — и «ещё нет» ответом на него не является:
    прими его за установленное отсутствие, и живая заявка, ждущая
    человека, была бы похоронена своим же ожиданием. Кому нужно отличить
    «ждём» от «не удалось прочитать», спрашивает `disposition` — там это
    `FOUND(OPEN)`.
    """
    where = disposition(facts)
    if where.value is Disposition.CLOSED_UNMERGED:
        return Fact(Outcome.ABSENT, None, "PR закрыт без мержа — акта не было")
    if where.value is not Disposition.MERGED:
        return unavailable(f"акт мержа не установлен: {where.detail}")
    merged_by = facts.get("mergedBy")
    login = merged_by.get("login") if isinstance(merged_by, dict) else None
    merged_at = facts.get("mergedAt")
    commit = facts.get("mergeCommit")
    oid = commit.get("oid") if isinstance(commit, dict) else None
    missing = [
        name
        for name, value in (
            ("mergedBy.login", login),
            ("mergedAt", merged_at),
            ("mergeCommit.oid", oid),
        )
        if not value
    ]
    if missing:
        return unavailable(
            f"PR вмержен, но факты события неполны: нет {', '.join(missing)}"
        )
    return Fact(
        Outcome.FOUND,
        MergeEvent(str(login), str(merged_at), str(oid)),
        f"мерж от {login} в {merged_at}",
    )


# --- Факт: создаёт ли этот мерж подпись ----------------------------------

#: Ключ политики в `policy/approvers.env` репозитория `approval-policy` — И имя
#: переменной окружения, выставление которой теперь есть ОТКАЗ (спека
#: approval-policy S7): переменная больше не источник, а молчаливое
#: игнорирование оставило бы оператора, действующего по старому правилу, в
#: уверенности, что его намерение исполняется. Одно имя в двух местах —
#: намеренно: правило волта и файл политики читаются одним словарём.
APPROVER_ALLOWLIST_ENV = "AUTHORIZED_APPROVER_ACCOUNTS"

#: Координаты источника политики — SSOT под authority-root (S8): константу
#: в этом модуле агент перенаправил бы своим PR под агентским мержем.
POLICY_SOURCE_FILE = (
    Path(__file__).resolve().parent.parent
    / "contracts"
    / "approval-policy-source"
    / "v1"
    / "source.env"
)

#: Версия схемы отпечатка политики. Отпечаток обязан меняться, когда
#: меняется СПОСОБ его вычисления, а не только состав списка, — иначе две
#: разные политики однажды дадут одинаковую строку и запись перестанет
#: отвечать на свой вопрос.
POLICY_SCHEME = "v1"

#: Машинные различители отказа снимка (§4.2): по ним фазы решают, сохранять
#: заявку или терминализировать. Ни один не про учётку мержера.
POLICY_REFUSAL_ENV = "env"
POLICY_REFUSAL_SOURCE = "source"
POLICY_REFUSAL_ABSENT = "absent"
POLICY_REFUSAL_SUPERSEDED = "superseded"
POLICY_REFUSAL_EMPTY = "empty"
#: Префикс причины `invalidated` при смене версии политики (решение
#: владельца 2026-09-22): отличим от прочих причин, запись заявки сохраняется.
INVALIDATION_POLICY_CHANGED = "policy_changed"


def policy_source() -> tuple[str, str, str]:
    """(repo, ref, path) из SSOT под authority-root; RuntimeError на битом файле."""
    what = "SSOT источника политики подписи"
    return (
        ssot_env.read_key(POLICY_SOURCE_FILE, "APPROVAL_POLICY_REPO", what),
        ssot_env.read_key(POLICY_SOURCE_FILE, "APPROVAL_POLICY_REF", what),
        ssot_env.read_key(POLICY_SOURCE_FILE, "APPROVAL_POLICY_PATH", what),
    )


def policy_fingerprint(accounts: Iterable[str]) -> str:
    """Отпечаток политики по составу списка — `v1:` + sha1 отсортированного
    перечня через запятую. Совместим с ледгерами прошлых прогонов; отвечает
    «та же ли политика по содержанию», версию источника не кодирует."""
    payload = ",".join(sorted(accounts)).encode("utf-8")
    return f"{POLICY_SCHEME}:{hashlib.sha1(payload).hexdigest()}"


@dataclass(frozen=True)
class PolicySnapshot:
    """Прочитанная версия политики: откуда, какой SHA, какой состав.

    `sha` — последний коммит `ref`, тронувший `path` (S5): правка README
    рядом версию не меняет. `fingerprint` — по составу (совместим с
    прошлыми записями). `source` — то, что пишется в `Authorization`.
    """

    repo: str
    ref: str
    path: str
    sha: str
    accounts: frozenset[str]
    fingerprint: str

    @property
    def source(self) -> str:
        return f"github:{self.repo}@{self.sha}:{self.path}"

    def as_record(self) -> dict[str, str]:
        """Поле `policy` заявки: без состава — состав восстанавливается по SHA."""
        return {
            "repo": self.repo,
            "ref": self.ref,
            "path": self.path,
            "sha": self.sha,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class PolicyRefusal:
    """Установленный отказ по ПОЛИТИКЕ (не по мержеру): `kind` из POLICY_REFUSAL_*."""

    kind: str
    detail: str
    pinned: str | None = None
    current: str | None = None


PolicyFact = Fact[PolicySnapshot | PolicyRefusal]


def _forbidden(kind: str, detail: str, **extra: str) -> PolicyFact:
    return Fact(Outcome.FORBIDDEN, PolicyRefusal(kind, detail, **extra), detail)


def policy_snapshot(ops: Ops, *, pinned_sha: str | None) -> PolicyFact:
    """Снимок политики из репозитория `approval-policy` (спека §4.2).

    Порядок — таблица §4.2: выставленная переменная → отказ до форджи;
    координаты не читаются → отказ о конфигурации devtools; версия
    `UNAVAILABLE` → неустановленный факт; версия `ABSENT` → отказ об
    источнике; `pinned_sha` задан и версия ≠ ему → `superseded` с обеими
    версиями; содержимое — тем же порядком; значение без единого логина
    (`= , ,` проходит `read_key`) → `empty`. `FOUND` с пустым `accounts`
    невозможен по построению. Ни одно сообщение не упоминает учётку мержера.
    """
    if os.environ.get(APPROVER_ALLOWLIST_ENV) is not None:
        return _forbidden(
            POLICY_REFUSAL_ENV,
            f"{APPROVER_ALLOWLIST_ENV} выставлена в окружении, но переменная "
            "больше не источник политики подписи — источник репозиторий "
            "approval-policy; снимите переменную и повторите",
        )
    try:
        repo, ref, path = policy_source()
    except RuntimeError as exc:
        return _forbidden(
            POLICY_REFUSAL_SOURCE,
            f"конфигурация источника политики не читается: {exc}",
        )
    version = ops.policy_version_fact(repo, ref, path)
    if version.outcome is Outcome.UNAVAILABLE:
        return unavailable(
            f"версия политики {repo}:{path}@{ref} не установлена: {version.detail}"
        )
    if version.outcome is Outcome.ABSENT or not isinstance(version.value, str):
        return _forbidden(
            POLICY_REFUSAL_ABSENT, f"источник политики пуст: {version.detail}"
        )
    sha = version.value
    if pinned_sha is not None and sha != pinned_sha:
        return _forbidden(
            POLICY_REFUSAL_SUPERSEDED,
            f"политика сменилась: закреплена {pinned_sha}, актуальная {sha}",
            pinned=pinned_sha,
            current=sha,
        )
    content = ops.repo_file_fact(repo, sha, path)
    if content.outcome is Outcome.UNAVAILABLE:
        return unavailable(
            f"содержимое политики {repo}@{sha}:{path} не прочитано: "
            f"{content.detail}"
        )
    if content.outcome is Outcome.ABSENT or not isinstance(content.value, str):
        return _forbidden(POLICY_REFUSAL_ABSENT, f"в версии {sha} нет {path}")
    lines = ssot_env.definition_lines(content.value, APPROVER_ALLOWLIST_ENV)
    if len(lines) != 1 or not lines[0]:
        reason = (
            "ключ отсутствует" if not lines
            else "дубль ключа" if len(lines) > 1
            else "пустое значение"
        )
        return _forbidden(
            POLICY_REFUSAL_EMPTY,
            f"{path}@{sha}: {reason} {APPROVER_ALLOWLIST_ENV} — подписать не "
            "может никто",
        )
    accounts = frozenset(p.strip() for p in lines[0].split(",") if p.strip())
    if not accounts:
        return _forbidden(
            POLICY_REFUSAL_EMPTY,
            f"{path}@{sha}: {APPROVER_ALLOWLIST_ENV} без единого логина — "
            "подписать не может никто",
        )
    snapshot = PolicySnapshot(
        repo, ref, path, sha, accounts, policy_fingerprint(accounts)
    )
    return Fact(
        Outcome.FOUND,
        snapshot,
        f"политика {snapshot.source}, отпечаток {snapshot.fingerprint}",
    )


@dataclass(frozen=True)
class Authorization:
    """Записанное решение об авторизации мержа: кого, по какой политике.

    `login` — учётка, о которой решение принято; `policy` — отпечаток
    политики на тот момент; `source` — версия источника
    (`github:<repo>@<sha>:<path>`). Втроём они делают решение проверяемым
    фактом: фаза 3 сверяет его ЦЕЛОСТНОСТЬ (то ли это решение и о том ли
    мерже), а не применяет allowlist заново.
    """

    login: str
    policy: str
    source: str

    def as_record(self) -> dict[str, str]:
        """Форма для леджера — плоская, потому что `run.json` это JSON."""
        return {
            "login": self.login,
            "policy": self.policy,
            "source": self.source,
        }


def authorized_signature(
    event: MergeEvent, snapshot: PolicySnapshot
) -> Fact[Authorization]:
    """Создаёт ли этот мерж подпись по закреплённому снимку: FOUND / FORBIDDEN.

    `FOUND` несёт РЕШЕНИЕ: учётка плюс отпечаток и версия политики, по
    которой она признана авторизованной. Записывается один раз и при
    возобновлении не пересматривается.

    `FORBIDDEN` — положительно установленный факт: учётка прочитана, её нет
    в закреплённой версии политики, подписи этот мерж не создаёт. Законная
    дорога в `invalidated` (§I12). Пустого снимка здесь не бывает —
    `policy_snapshot` отказывает на нём раньше.
    """
    if event.login in snapshot.accounts:
        return Fact(
            Outcome.FOUND,
            Authorization(event.login, snapshot.fingerprint, snapshot.source),
            f"{event.login} авторизован политикой {snapshot.source}",
        )
    return Fact(
        Outcome.FORBIDDEN,
        None,
        f"мерж от {event.login}: учётки нет в политике {snapshot.source} — "
        "подписи этот мерж не создаёт",
    )


# --- Факт: байты узла в base --------------------------------------------


def read_blob_text(
    ops: Ops, target_dir: str, ref: str, path: str
) -> Fact[str]:
    """Текст файла в ревизии: `FOUND` либо `UNAVAILABLE` — и НИКОГДА `ABSENT`.

    `ops.show_file` отдаёт `None` и когда ревизии нет, и когда файла в ней
    нет (issue #177). Прочитать из этого «файла нет» — ровно та эвристика,
    которую §I12 запрещает прямым текстом: ветка терминализации стала бы
    достижимой на вид, а на деле хоронила бы заявки при каждом сбое
    выборки. Пока #177 не закрыт, отсутствие файла в `base` остаётся
    неклассифицируемым, и это законный исход, а не недоделка.

    Пустой файл — `FOUND` с пустой строкой, а не «нет»: `show_file`
    возвращает `""` и `None` РАЗНЫМИ величинами, и сворачивать их в одну
    значило бы завести ту же эвристику через ложность строки.

    Сверки фазы 3 при этом достижимы полностью: они сравнивают
    ПРОЧИТАННЫЕ байты (`FOUND`) с записанным в заявке, а расхождение —
    положительно установленный семантический факт.
    """
    try:
        text = ops.show_file(target_dir, ref, path)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return unavailable(f"{ref}:{path}: чтение не удалось ({exc})")
    if text is None:
        return unavailable(
            f"{ref}:{path}: вывод не удался — «нет ревизии» и «нет файла» "
            "слой ops не различает (issue #177)"
        )
    return Fact(Outcome.FOUND, text, f"{ref}:{path} прочитан")


def read_blob_bytes(
    ops: Ops, target_dir: str, ref: str, path: str
) -> Fact[bytes]:
    """Exact bytes in a revision, with the same fail-closed outcome model."""
    try:
        data = ops.show_file_bytes(target_dir, ref, path)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return unavailable(f"{ref}:{path}: чтение байтов не удалось ({exc})")
    if data is None:
        return unavailable(
            f"{ref}:{path}: вывод байтов не удался — «нет ревизии» и "
            "«нет файла» слой ops не различает (issue #177)"
        )
    return Fact(Outcome.FOUND, data, f"{ref}:{path} прочитан побайтово")


# --- Факт: закрытие PR подтверждено --------------------------------------


def confirm_closed(
    ops: Ops, repo_slug: str, pr: int, comment: str
) -> Fact[int]:
    """Закрытие PR: `FOUND` подтверждено либо `UNAVAILABLE`.

    `ops.close_pr` отдаёт `False` и при отсутствии прав, и когда PR уже
    закрыт — различать нечем (#177). Значит неподтверждённое закрытие
    остаётся неустановленным фактом, и §I12 говорит, что делать: операция
    над upstream остаётся возобновляемой и mergeable candidate НЕ
    публикует. Порядок «сначала инвалидация, потом публикация» — не
    предпочтение, а единственный способ закрыть окно, в котором mergeable
    предложение ниже переживает своё основание.
    """
    try:
        closed = ops.close_pr(repo_slug, pr, comment)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return unavailable(f"закрытие PR #{pr} не удалось ({exc})")
    if not closed:
        return unavailable(
            f"закрытие PR #{pr} не подтверждено — «нет прав» и «уже закрыт» "
            "слой ops не различает (issue #177)"
        )
    return Fact(Outcome.FOUND, pr, f"PR #{pr} закрыт")
