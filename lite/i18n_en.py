"""Английский словарь `dfl`: русский шаблон -> английский шаблон.

Плейсхолдеры `{name}` в паре совпадают по именам (проверяет тест)."""

from __future__ import annotations

EN: dict[str, str] = {
    # баннер режима test (факты — `governance.df_config`, Task 2)
    "режим test: по сравнению со strict выключено": (
        "test mode: compared with strict, disabled:"
    ),
    "репо политики утверждения не используется: утверждающий — accounts.human": (
        "approval policy repo is not used: the approver is accounts.human"
    ),
    (
        "песочница авторинга: claude запускается обычным процессом"
        " пользователя (нет sandbox-exec, допуска и связки ключей)"
    ): (
        "authoring sandbox: claude runs as a regular user process"
        " (no sandbox-exec, admission or keychain)"
    ),
    (
        "белый список целей: цель — из targets.toml рядом с конфигом,"
        " манифест флота необязателен"
    ): (
        "target allowlist: the target comes from targets.toml next to the config,"
        " the fleet manifest is optional"
    ),
    (
        "одобрение ревью: вердикт публикуется комментарием"
        " (GitHub не даёт одобрить свой PR), вердикт обязателен"
    ): (
        "review approval: the verdict is posted as a comment"
        " (GitHub does not let you approve your own PR), a verdict is required"
    ),
    (
        "корень доверия df (/etc/df/trust.toml) не используется:"
        " акт — по мержу (режим до миграции)"
    ): (
        "df trust root (/etc/df/trust.toml) is not used:"
        " the act is the merge (pre-migration mode)"
    ),
    (
        "акт человека не требуется (test): candidate-PR мержит раннер после"
        " зелёных проверок"
    ): (
        "no human act required (test): the runner merges the candidate PR"
        " after green checks"
    ),
    (
        "класс актора не применяется (test): finalize не сверяет класс актора;"
        " kill switch действует"
    ): (
        "actor class is not enforced (test): finalize does not check the actor"
        " class; the kill switch still works"
    ),
    (
        "утверждение задач человеком не требуется (test): tasks-PR, spec approve"
        " и approve-PR проводит раннер"
    ): (
        "no human task approval required (test): the runner handles the tasks PR,"
        " spec approve and the approve PR"
    ),
    (
        "мерж PR с кодом — за вами (test): приёмка проверяет PR с кодом,"
        " но не мержит его"
    ): (
        "merging the code PR is up to you (test): acceptance checks the code PR"
        " but does not merge it"
    ),
    (
        "проверок нет (test): у PR нет .github/workflows и чеков — CI не ждём;"
        " чеки CI вне GitHub Actions, пришедшие позже 2 минут, не учитываются"
    ): (
        "no checks (test): the PR has no .github/workflows and no checks — CI is"
        " not awaited; CI checks outside GitHub Actions that arrive after"
        " 2 minutes are ignored"
    ),
    "одна учётка GitHub: accounts.human может совпадать с accounts.agent": (
        "one GitHub account: accounts.human may be the same as accounts.agent"
    ),
    "конфиг: {path}": "config: {path}",
    # вход: вопрос о типе проекта (lite/dfl.py)
    "Что это за проект?": "What kind of project is this?",
    "Личный — для себя (возможно, с близким кругом)": (
        "Personal — for yourself (maybe with a close circle)"
    ),
    "Enterprise — заказчик, команда, регламенты": (
        "Enterprise — a customer, a team, regulations"
    ),
    "ответьте номером: 1 или 2": "answer with a number: 1 or 2",
    "прогон не начат — повторите dfl run --subject '<тема>'": (
        "the run was not started — repeat dfl run --subject '<subject>'"
    ),
    (
        "тип проекта не спрошен (нет терминала) — enterprise; личный проект: "
        "dfl run --subject '…' --track personal"
    ): (
        "project type not asked (no terminal) — enterprise; a personal project: "
        "dfl run --subject '…' --track personal"
    ),
    "личный прогон {run_id}": "personal run {run_id}",
    (
        "прогон {run_id} ведётся на языке {stored} — {requested} из --lang/DFL_LANG "
        "не применён"
    ): (
        "run {run_id} is kept in language {stored} — {requested} from"
        " --lang/DFL_LANG is not applied"
    ),
    # dfl status
    "личный прогон {run_id}: шаг {step}": "personal run {run_id}: step {step}",
    "личный прогон {run_id}: закончен": "personal run {run_id}: finished",
    "ждёт ответа": "awaiting an answer",
    "закрыт — выполнено позже": "closed — done later",
    "открыт": "open",
    "состояние не прочитано": "state not read",
    # dfl abandon
    "брошен: {run_id} → {dest}": "abandoned: {run_id} → {dest}",
    (
        "открытые PR прогона (spec-PR, integration-PR), ветка "
        "spec/{ws_id}-personal и файлы {bundle_dir} в репо не тронуты — "
        "уберите их сами; вернуть прогон — перенести каталог обратно в {root}"
    ): (
        "the run's open PRs (spec PR, integration PR), the branch "
        "spec/{ws_id}-personal and the files {bundle_dir} in the repo are left "
        "untouched — remove them yourself; to restore the run, move the directory "
        "back to {root}"
    ),
    (
        "незаконченный enterprise-конвейер репо не тронут — бросить и его: "
        "повторите dfl abandon"
    ): (
        "the repo's unfinished enterprise pipeline is untouched — to abandon it "
        "too: repeat dfl abandon"
    ),
    (
        "enterprise-конвейеры репо не проверены — журнал прогона не читается "
        "(dfl status назовёт его)"
    ): (
        "the repo's enterprise pipelines were not checked — a run journal is "
        "unreadable (dfl status names it)"
    ),
}
