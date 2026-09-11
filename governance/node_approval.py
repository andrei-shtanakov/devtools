"""Одобренность узла бандла: статусы, self-hash и предикат (§I12).

Одобрение узла — человеческий акт: единственная дорога в `approved` идёт
через `--approve-node` и мерж candidate-PR, а любая доставка одобренность
только ПРОВЕРЯЕТ. Этот модуль держит проверяемую половину контракта —
величины и предикат, — и не совершает ни одного эффекта: он ничего не
штампует, не пинует и не пишет.

ТРИ ДОЛГОВЫХ СТАТУСА, и смысл у них разный (§I12): `draft` — «не
одобрялось»; `stale` — «одобрялось, но upstream сдвинулся»;
`approval_pending` — «вынесено на одобрение». Различать их обязана
диагностика: первые два адресуют оператора к `--approve-node`, третий —
либо к человеку с правом мержа, либо снова к `--approve-node`. Статус узла
на этот вопрос не отвечает; отвечает статус ЗАЯВКИ в леджере
(`approval_ledger`), и потому в диагностике обязаны быть оба.

Статуса `invalidated` у УЗЛА не бывает. `invalidated` — терминальный
статус заявки (§I12, «Восстановление заявки»): заявку сделали неисполнимой
факты, а узел при этом остаётся в `approval_pending`, и выход из него —
новый candidate. Приписать `invalidated` файлу узла значило бы завести
вторую величину на тот же вопрос.
"""

from __future__ import annotations

from dataclasses import dataclass

from governance.frontmatter import join_frontmatter, split_frontmatter
from governance.stale_adapter import blob_sha1

STATUS_DRAFT = "draft"
STATUS_STALE = "stale"
STATUS_APPROVAL_PENDING = "approval_pending"
STATUS_APPROVED = "approved"

#: Статусы узла, известные контракту. Незнакомый — fail-closed долг, а не
#: «наверное, сойдёт»: молчаливый дефолт на неизвестном статусе и есть та
#: дыра, через которую в base попадает неодобренный узел.
KNOWN_STATUSES = (
    STATUS_DRAFT,
    STATUS_STALE,
    STATUS_APPROVAL_PENDING,
    STATUS_APPROVED,
)

#: Долговые статусы: ни один не является честно одобренным, и гейт любой
#: доставки обязан назвать каждый (§I12).
DEBT_STATUSES = (STATUS_DRAFT, STATUS_STALE, STATUS_APPROVAL_PENDING)

#: Смысл каждого долгового статуса — дословно по §I12. Словарь, а не
#: цепочка `if`: перечень закрыт `DEBT_STATUSES`, и расходиться этим двум
#: нельзя (тест лочит).
_DEBT_MEANING = {
    STATUS_DRAFT: "не одобрялось",
    STATUS_STALE: "одобрялось, но upstream сдвинулся",
    STATUS_APPROVAL_PENDING: "вынесено на одобрение",
}

#: Статусы, которые каскад переводит в `stale`. `approval_pending` здесь
#: НЕ по недосмотру: первая редакция §I12 держала его среди
#: останавливающих («долг и так объявлен»), и это было ошибкой — долг у
#: него объявлен НЕ ПРО ТОТ upstream. Узел ждёт решения по предложению,
#: посчитанному против прежнего блоба; оставить его в этом статусе значило
#: бы оставить на столе предложение, которое уже неверно.
CASCADE_RESET_STATUSES = (STATUS_APPROVED, STATUS_APPROVAL_PENDING)

#: Статусы, на которых ветка каскада обрывается: от пометки долгом у них
#: не меняется файл, значит и пины их downstream не разъезжаются. Отсюда
#: же конечность рекурсии без счётчика.
CASCADE_STOP_STATUSES = (STATUS_DRAFT, STATUS_STALE)

#: Поле, в котором узел помнит СВОИ одобренные байты (§I12, условие 4).
SELF_HASH_KEY = "approved_content_hash"

#: Approval-контур узла: поля, которые проекция условия (4) вырезает.
#: `status`/`version` — потому что вопрос (4) про то, ЧТО одобряли, а не
#: про то, как одобрение записано; подпись и пины — по тому же поводу; сам
#: `approved_content_hash` — по механической причине: он лежит в том же
#: frontmatter и иначе хешировал бы сам себя.
#:
#: Отсюда свойство, на котором стоит каскад: перевод узла в `stale` меняет
#: только `status`, значит self-hash остаётся верным, и `stale`-узел
#: продолжает нести запись о том, что покрывала его подпись.
APPROVAL_ENVELOPE_KEYS = (
    "status",
    "version",
    "approved_by",
    "approved_at",
    "upstream_hashes",
    SELF_HASH_KEY,
)


def self_hash(text: str) -> str:
    """`approved_content_hash` узла: хеш его КАНОНИЧЕСКОЙ СОБСТВЕННОЙ части.

    Определение — часть контракта, а не деталь реализации: текст файла, из
    frontmatter которого удалён весь approval-контур
    (`APPROVAL_ENVELOPE_KEYS`); остальной frontmatter и тело входят целиком.

    Это НЕ канонизация §I2, и путать их нельзя. Та отвечает на вопрос
    «менялось ли содержание апстрима» по всему DAG: `status` она
    ОСТАВЛЯЕТ (иначе бесследный no-op §I5 скрыл бы откат узла в долг —
    он стоит перед гейтом), `version` вырезает (правка 2026-09-11:
    поколение акта содержанием не является). Эта отвечает «те ли
    собственные байты, что одобрял человек» по одному узлу и режет ВЕСЬ
    конверт, включая `status`: иначе одобрение собственных байтов не
    отличить от перезаписи конверта. Совпадение по `version` у двух
    проекций случайно — они режут его по разным причинам, и сливать их в
    одну процедуру всё так же нельзя (§7).

    Ровно эта проекция и развязывает курицу с яйцом двухфазной схемы: хеш,
    посчитанный фазой 1 до подписи, остаётся верным после того, как фаза 3
    впишет конверт.
    """
    meta, body = split_frontmatter(text)
    for key in APPROVAL_ENVELOPE_KEYS:
        meta.pop(key, None)
    return blob_sha1(join_frontmatter(meta, body))


def cascade_marks_stale(status: object) -> bool:
    """Каскад переводит узел с этим статусом в `stale` (§I12)."""
    return status in CASCADE_RESET_STATUSES


def cascade_stops_at(status: object) -> bool:
    """На узле с этим статусом ветка каскада обрывается (§I12)."""
    return status in CASCADE_STOP_STATUSES


#: Виды долга — по КАКОМУ условию предиката узел не прошёл. Механика
#: одобрения ветвится по ним (§I12 различает исходы: пункт 6 — fail-closed,
#: пункт 5 — переодобрение), и ветвиться она обязана по величине, а не по
#: подстроке в тексте отказа: текст пишется человеку и меняется свободно.
DEBT_UNKNOWN_STATUS = "unknown_status"
DEBT_STATUS = "debt_status"
DEBT_UNSIGNED = "unsigned"
DEBT_PINS = "pins"
DEBT_MIGRATION = "migration"
DEBT_SELF_HASH = "self_hash"


@dataclass(frozen=True)
class NodeDebt:
    """Почему узел не проходит предикат и что с этим делать оператору.

    Отказ без процедуры бесполезен (§I10, §I12): диагностика гейта обязана
    назвать КАЖДЫЙ непроходящий узел с его статусом, а для разошедшихся
    пинов — обе величины.
    """

    node_id: str
    status: object
    kind: str
    reason: str
    procedure: str

    def render(self) -> str:
        """Одна строка диагностики: узел, статус, причина, процедура."""
        return (
            f"{self.node_id}: status={self.status!r} — {self.reason}. "
            f"Процедура: {self.procedure}"
        )


#: Процедура для узла, который ждёт человеческого одобрения. Топологический
#: порядок назван не для красоты: approve узла меняет его байты, поэтому
#: downstream обязан выноситься на одобрение ПОСЛЕ того, как окончательные
#: байты upstream окажутся в base.
APPROVE_PROCEDURE = "--approve-node <node-id> в топологическом порядке"

#: Процедура для узла с миграционным долгом. Особого режима, отдельного
#: флага и массовой миграции по флоту контракт не заводит: долг
#: предъявляется тем же гейтом и гасится теми же вызовами.
MIGRATION_PROCEDURE = (
    f"{APPROVE_PROCEDURE} — переодобрение погасит миграционный долг"
)


def debt_procedure(
    status: object, *, awaiting_merge_pr: int | None = None
) -> str:
    """Что делать оператору с узлом в долговом статусе.

    `awaiting_merge_pr` приходит ИЗ ЛЕДЖЕРА и перебивает статус, а не
    уточняет его. Статус — про файл в base, а вопрос здесь про то, чего
    система ЖДЁТ, и эти два расходятся штатно: узел лежит `draft`, пока
    его candidate открыт, — и правильное действие всё равно мерж, а не
    повторный `--approve-node`, который ответит «PR открыт, ждём мержа».
    Тем более статус не отличает «ждём candidate» от «candidate вмержен,
    ждём конверта»: PR в этих двух случаях разные.

    `None` — ни один PR не ждёт (живой заявки нет либо работа за
    механикой): процедура та же, что у `draft`.
    """
    if awaiting_merge_pr is not None:
        return (
            f"мерж PR #{awaiting_merge_pr} учёткой из "
            "authorized_approver_accounts"
        )
    return APPROVE_PROCEDURE


def node_debt(
    node_id: str,
    text: str,
    upstream_blobs: dict[str, str],
    *,
    awaiting_merge_pr: int | None = None,
) -> NodeDebt | None:
    """Долг узла по предикату честной одобренности; `None` — долга нет.

    Предикат — ОДИН на все проверки, и условий у него четыре сразу (§I12):

    1. `status: approved`;
    2. `approved_by` и `approved_at` непусты (шаблон бандла заводит
       `approved_by: ""` до всякого approve — пустая строка есть «не
       подписано», а не подпись);
    3. для КАЖДОГО прямого upstream пин равен фактическому блобу файла
       этого upstream в том же дереве;
    4. `approved_content_hash` равен `self_hash` собственных байтов узла.

    Условия (3) и (4) отвечают на разные половины одного вопроса «что
    именно покрывает эта подпись»: (3) помнит ЧУЖИЕ байты, (4) — СВОИ. Ни
    одно другое не заменяет, и цену отсутствия каждого предъявил бой —
    (3) дефектом spec-runner#410, (4) находкой ревью §I12.

    `upstream_blobs` — фактические блобы ПРЯМЫХ upstream'ов узла в том же
    дереве (`node-id -> blob`). Пустой словарь — корневой узел: условие (3)
    выполнено пусто, и это не послабление, а отсутствие предмета.

    Расхождение пинов даёт fail-closed с обеими величинами, а НЕ молчаливую
    перепиновку: ровно она в spec-runner#410 сохранила прежнюю подпись и
    сделала ложное утверждение истинным на вид. Порядок работы — снизу
    вверх по причине: сперва одобрить изменившийся upstream, чей каскад
    объявит долг этому узлу.
    """
    meta, _ = split_frontmatter(text)
    status = meta.get("status")
    if status not in KNOWN_STATUSES:
        return NodeDebt(
            node_id,
            status,
            DEBT_UNKNOWN_STATUS,
            "статус не известен контракту "
            f"(допустимы {', '.join(KNOWN_STATUSES)})",
            APPROVE_PROCEDURE,
        )
    if status != STATUS_APPROVED:
        return NodeDebt(
            node_id,
            status,
            DEBT_STATUS,
            _DEBT_MEANING[status],
            debt_procedure(status, awaiting_merge_pr=awaiting_merge_pr),
        )
    if not meta.get("approved_by") or not meta.get("approved_at"):
        return NodeDebt(
            node_id,
            status,
            DEBT_UNSIGNED,
            "approved без подписи (approved_by/approved_at пусты)",
            APPROVE_PROCEDURE,
        )
    pins = meta.get("upstream_hashes")
    pins = dict(pins) if isinstance(pins, dict) else {}
    diverged = [
        f"{upstream}: ожидался {pins.get(upstream) or 'нет пина'}, "
        f"фактически {blob}"
        for upstream, blob in sorted(upstream_blobs.items())
        if pins.get(upstream) != blob
    ]
    if diverged:
        return NodeDebt(
            node_id,
            status,
            DEBT_PINS,
            "approved с разошедшимися пинами — " + "; ".join(diverged),
            "сперва одобрите изменившийся upstream — его каскад объявит "
            f"долг этому узлу; затем {APPROVE_PROCEDURE}",
        )
    recorded = meta.get(SELF_HASH_KEY)
    if not recorded:
        return NodeDebt(
            node_id,
            status,
            DEBT_MIGRATION,
            f"нет {SELF_HASH_KEY}: собственные одобренные байты узла "
            "непроверяемы — миграционный долг",
            MIGRATION_PROCEDURE,
        )
    actual = self_hash(text)
    if recorded != actual:
        return NodeDebt(
            node_id,
            status,
            DEBT_SELF_HASH,
            f"{SELF_HASH_KEY} разошёлся: подписано {recorded}, фактически "
            f"{actual} — собственные байты узла изменились с момента подписи",
            f"{APPROVE_PROCEDURE} — это явное ПЕРЕОДОБРЕНИЕ",
        )
    return None
