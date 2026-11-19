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
    # слова и подсказки ответа (Task 3)
    "ждёт ответа: ": "awaiting an answer: ",
    "ответы на ручные проверки — потом: dfl run": (
        "answers to the manual checks — then: dfl run"
    ),
    "ждут ответа {n} ручных проверок — запустите dfl run в терминале": (
        "manual checks await an answer: {n} — run dfl run in a terminal"
    ),
    "ответ: «дальше» или замечания словами": (
        "answer: “next” or your remarks in words"
    ),
    "ответ: 1/выполнено, 2/не выполнено[: заметка], 3/с оговоркой: <текст>": (
        "answer: 1/passed, 2/failed[: note], 3/passed with caveat: <text>"
    ),
    "ответ: 1/{yes} или 2/оставить": "answer: 1/{yes} or 2/keep",
    "ответ: номер варианта 1–{n}, «своё: …» или «не согласен: <почему>»": (
        "answer: option number 1–{n}, “own: …” or “disagree: <why>”"
    ),
    (
        "номер варианта — отправится сразу · «своё: …» · «не согласен: <почему>» "
        "(пустая строка — отправить) · «пропустить» — остановиться"
    ): (
        "option number — sent at once · “own: …” · “disagree: <why>” "
        "(empty line — send) · “skip” — stop"
    ),
    "номер варианта · «своё: …» · «не согласен: <почему>»": (
        "option number · “own: …” · “disagree: <why>”"
    ),
    (
        "не понял ответ — номер варианта, «своё: …», «не согласен: …» или «пропустить»"
    ): "did not get that — an option number, “own: …”, “disagree: …” or “skip”",
    "напишите причину после «не согласен:»": "write the reason after “disagree:”",
    "«дальше»": "“next”",
    "«дальше» или замечания своими словами": "“next” or your remarks in your own words",
    "Верно? 1. да · 2. поправить: <что>": "Correct? 1. yes · 2. fix: <what>",
    # экран личного пути (Task 6): отказ, ошибки до прогона, подсказки
    "{command}: отказ (код {code})": "{command}: refusal (code {code})",
    "в теме нет ни букв, ни цифр — назовите тему: dfl run --subject '<тема>'": (
        "the subject has no letters or digits — name it: dfl run --subject '<subject>'"
    ),
    "незаконченного прогона нет — первый запуск: dfl run --subject '<тема>'": (
        "no unfinished run — first launch: dfl run --subject '<subject>'"
    ),
    "причина:": "reason:",
    "что сделать:": "what to do:",
    "отчёт разработчикам: dfl report": "report to the developers: dfl report",
    "подробно: dfl log": "details: dfl log",
    (
        "dfl работает только в режиме test (mode = {mode}) — "
        'поставьте mode = "test" в {path} '
        "(install.sh существующий конфиг не переписывает) или работайте "
        "через make"
    ): (
        "dfl works only in test mode (mode = {mode}) — "
        'set mode = "test" in {path} '
        "(install.sh does not overwrite an existing config) or work "
        "through make"
    ),
    "{cwd} не зарегистрирован — сначала dfl run в этом каталоге": (
        "{cwd} is not registered — first dfl run in this directory"
    ),
    "{exc} — дождитесь конца dfl run и повторите": (
        "{exc} — wait for dfl run to finish and repeat"
    ),
    # dfl pending / dfl reply
    "вопросов нет": "no questions",
    "ссылка: {link}": "link: {link}",
    "принимается: {accepts}": "accepted: {accepts}",
    "ответ: dfl reply --id {qid} --text '…'": "answer: dfl reply --id {qid} --text '…'",
    "нет вопроса, ждущего ответа — dfl pending": (
        "no question awaits an answer — dfl pending"
    ),
    "вопрос уже другой — dfl pending": "the question has changed — dfl pending",
    "ответ записан — продолжить: dfl run": "answer recorded — continue: dfl run",
    (
        "ждёт ответа: {first} — вопрос: dfl pending{pick}; "
        "ответ: dfl reply{pick} --id {qid} --text '…'"
    ): (
        "awaiting an answer: {first} — question: dfl pending{pick}; "
        "answer: dfl reply{pick} --id {qid} --text '…'"
    ),
    # личный путь без терминала (lite/personal/remote_interview.py, #766 срез 4)
    "ответ на этот вопрос — dfl note и dfl brief (бриф собирает скил)": (
        "the answer to this question is dfl note and dfl brief "
        "(the skill builds the brief)"
    ),
    "ответ: «да» или поправка словами": "answer: “yes” or a fix in words",
    (
        "нужен бриф по теме «{subject}»: разговор ведёт скил — "
        "dfl note, затем dfl brief"
    ): (
        "a brief is needed on the topic “{subject}”: the skill leads the "
        "conversation — dfl note, then dfl brief"
    ),
    "лимит вопросов исчерпан — соберите бриф: dfl brief": (
        "the question limit is reached — build the brief: dfl brief"
    ),
    (
        "строить пока нечего: нет ни одной функции с критерием приёмки — "
        "спросите человека, что должно работать (dfl note), и сдайте бриф снова"
    ): (
        "nothing to build yet: no function has an acceptance criterion — "
        "ask the person what should work (dfl note) and submit the brief again"
    ),
    "бриф не принят — поправьте и сдайте снова:": (
        "the brief is not accepted — fix it and submit again:"
    ),
    "пустая реплика не записывается": "an empty line is not recorded",
    "роль реплики: agent или human": "the line role: agent or human",
    "у реплики человека вариантов нет — --option только у agent": (
        "a person's line has no options — --option is for agent only"
    ),
    # ввод в терминале (lite/interview_chat.py)
    "(пустая строка — отправить)": "(empty line — send)",
    "/стоп": "/stop",
    (
        "строка не прочитана: в ней битый символ (часто — Backspace стёр полбуквы); "
        "введите эту строку ещё раз"
    ): (
        "the line was not read: it has a broken character (often Backspace erased"
        " half a letter); type this line again"
    ),
    "ввод не читается — интервью остановлено; продолжить: dfl run": (
        "input is unreadable — the interview is stopped; continue: dfl run"
    ),
    "ответ не принят — ответьте на вопрос ещё раз": (
        "the answer was not accepted — answer the question again"
    ),
    "расскажите об этом своими словами («пропустить» — оставить так)": (
        "tell about it in your own words (“skip” — leave it as is)"
    ),
    # интервьюер личного пути
    "Что поправить?": "What to fix?",
    (
        "агент не ответил по форме дважды подряд — интервью остановлено; "
        "продолжить: dfl run"
    ): (
        "the agent did not answer in the format twice in a row — the interview "
        "is stopped; continue: dfl run"
    ),
    "агент недоступен ({reason}) — интервью остановлено; продолжить: dfl run": (
        "the agent is unavailable ({reason}) — the interview is stopped;"
        " continue: dfl run"
    ),
    "платных вызовов агента: {n}": "paid agent calls: {n}",
    (
        "строить пока нечего: нет ни одной функции с критерием приёмки — "
        "расскажите, что должно работать (или /стоп)"
    ): (
        "nothing to build yet: no function has an acceptance criterion — "
        "tell what should work (or /stop)"
    ),
    "строка журнала интервью не прочитана — пропущена": (
        "an interview journal line was not read — skipped"
    ),
    # пауза на просмотр этапа (lite/review_pause.py)
    "этап «{label}» готов и прошёл проверку — посмотрите": (
        "stage “{label}” is ready and passed the check — take a look"
    ),
    "этап «{label}» готов и прошёл проверку — посмотрите:": (
        "stage “{label}” is ready and passed the check — take a look:"
    ),
    (
        "замечания своими словами (пустая строка — отправить); "
        "«дальше» — без замечаний; {stop} — прервать"
    ): (
        "remarks in your own words (empty line — send); "
        "“next” — no remarks; {stop} — interrupt"
    ),
    "неизвестный этап: {unknown}; допустимы: {stages}": (
        "unknown stage: {unknown}; allowed: {stages}"
    ),
    "не понял ответ — номер или слово варианта": (
        "did not get that — the option number or word"
    ),
    "Удалить {named}? 1. удалить · 2. оставить": "Remove {named}? 1. remove · 2. keep",
    "Снять Must с {named}? 1. снять · 2. оставить": (
        "Drop Must from {named}? 1. drop · 2. keep"
    ),
    "Принять правку {named}? 1. принять · 2. оставить как было": (
        "Accept the change to {named}? 1. accept · 2. keep as it was"
    ),
    "просмотр этапа «{label}» прерван — продолжить: dfl run": (
        "review of stage “{label}” interrupted — continue: dfl run"
    ),
    (
        "ждёт просмотра этапа «{label}» — откройте {links}; замечания — dfl run в "
        "терминале"
    ): (
        "awaiting review of stage “{label}” — open {links}; remarks — dfl run in "
        "a terminal"
    ),
    (
        "замечание не применено: правка удаляла или ослабляла {ids} — "
        "сформулируйте иначе (dfl run)"
    ): (
        "the remark was not applied: the revision removed or weakened {ids} — "
        "phrase it differently (dfl run)"
    ),
    (
        "бриф правится словами: опишите правку как замечание — файл возвращён "
        "к согласованной версии"
    ): (
        "the brief is changed with words: describe the change as a remark — the "
        "file is restored to the agreed version"
    ),
    (
        "платных вызовов: {head}сортировка {triage}, переписывание "
        "{revise}, проверка {check}"
    ): ("paid calls: {head}triage {triage}, revision {revise}, check {check}"),
    "авторинг {n}, ": "authoring {n}, ",
    # шаги личного пути (lite/personal/flow.py)
    "продолжить: dfl run": "continue: dfl run",
    "бросить прогон и начать заново — dfl abandon": (
        "abandon the run and start over — dfl abandon"
    ),
    "интервью прервано — продолжить: dfl run": "interview paused — continue: dfl run",
    "интервью не завершено — {retry}": "the interview is not finished — {retry}",
    (
        "ветка по умолчанию {slug} не установлена ({detail}) — "
        "база {base} (не закрепляется)"
    ): (
        "the default branch of {slug} is not established ({detail}) — "
        "base {base} (not pinned)"
    ),
    "декомпозиция нарушает инварианты — правка автором, круг {n}": (
        "the decomposition breaks invariants — revision by the author, round {n}"
    ),
    (
        "декомпозиция нарушает инварианты после {rounds} кругов правки: "
        "{problems} — поправьте {name} вручную и повторите dfl run ({abandon})"
    ): (
        "the decomposition breaks invariants after {rounds} revision rounds: "
        "{problems} — fix {name} by hand and repeat dfl run ({abandon})"
    ),
    "правка {name} не состоялась: {why}": "revision of {name} failed: {why}",
    "правка {name} отклонена — ослабляет узел: {ids}; файл возвращён": (
        "revision of {name} rejected — it weakens the node: {ids}; file restored"
    ),
    "бриф прогона не прочитан ({path}): {exc}": (
        "the run's brief was not read ({path}): {exc}"
    ),
    "автор {name} не написал узел: {why} — {retry}": (
        "the author did not write {name}: {why} — {retry}"
    ),
    "проверка ребра не состоялась: {why}": "the edge check did not run: {why}",
    (
        "замечания edge-check к {name} не сошлись за {rounds} "
        "круга — поправьте узел и повторите dfl run ({abandon})"
    ): (
        "edge-check findings on {name} did not converge in {rounds} "
        "rounds — fix the node and repeat dfl run ({abandon})"
    ),
    "разбор замечаний к {name} не завершён ({outcome}) — {retry}": (
        "resolution of findings on {name} is not finished ({outcome}) — {retry}"
    ),
    "замечания к этапу «{label}» — правка, круг {n}": (
        "remarks on stage “{label}” — revision, round {n}"
    ),
    (
        "в {target} незакоммиченные чужие изменения: {foreign} — "
        "уберите их и повторите dfl run"
    ): (
        "{target} has uncommitted foreign changes: {foreign} — "
        "remove them and repeat dfl run"
    ),
    (
        "{target} на ветке {branch}, ожидалась {base} (или ветка прогона "
        "{run_branch}) — переключитесь и повторите dfl run"
    ): (
        "{target} is on branch {branch}, expected {base} (or the run branch "
        "{run_branch}) — switch and repeat dfl run"
    ),
    "tasks.md не построен: {exc}": "tasks.md was not built: {exc}",
    (
        "ветка прогона {branch} несёт чужие изменения относительно "
        "origin/{base}: {foreign} — это коммит в ветке прогона или "
        "незапушенный коммит локальной {base}; уберите его из ветки "
        "прогона (или запушьте {base}) и повторите dfl run ({abandon})"
    ): (
        "the run branch {branch} carries foreign changes relative to "
        "origin/{base}: {foreign} — a commit on the run branch or an "
        "unpushed commit of the local {base}; remove it from the run "
        "branch (or push {base}) and repeat dfl run ({abandon})"
    ),
    "spec-PR не создан: {exc} — {retry}": "spec PR not created: {exc} — {retry}",
    (
        "ветка прогона {branch} с коммитом бандла пропала — верните её "
        "(git reflog) и повторите dfl run ({abandon})"
    ): (
        "the run branch {branch} with the bundle commit is gone — restore it "
        "(git reflog) and repeat dfl run ({abandon})"
    ),
    "ветка прогона {branch} не прочитана: {detail} — {retry}": (
        "the run branch {branch} was not read: {detail} — {retry}"
    ),
    (
        "ветка прогона {branch} уже есть и несёт коммиты, которых прогон не "
        "делал (или это не установлено), — разберите её вручную (удалите или "
        "переименуйте) и повторите dfl run ({abandon})"
    ): (
        "the run branch {branch} already exists and carries commits the run did "
        "not make (or this is not established) — sort it out by hand (delete or "
        "rename it) and repeat dfl run ({abandon})"
    ),
    "spec-PR #{pr} закрыт без мержа — решите вручную ({abandon})": (
        "spec PR #{pr} is closed without a merge — decide by hand ({abandon})"
    ),
    "spec-PR #{pr} не вмержен — {why}": "spec PR #{pr} is not merged — {why}",
    (
        "spec-PR #{pr} не вмержен — human-merge.sh вернул {rc}: "
        "проверки PR ещё идут или не прошли — повторите dfl run позже"
    ): (
        "spec PR #{pr} is not merged — human-merge.sh returned {rc}: "
        "the PR checks are still running or failed — repeat dfl run later"
    ),
    "spec-PR #{pr} вмержен": "spec PR #{pr} merged",
    "checkout/pull {base} не удался: {exc} — {retry}": (
        "checkout/pull {base} failed: {exc} — {retry}"
    ),
    "конфиг DarkFactory не прочитан: {exc}": (
        "the DarkFactory config was not read: {exc}"
    ),
    "мерж агентом только в режиме test, конфиг: {mode} — смержите сами": (
        "the agent merges only in test mode, config: {mode} — merge it yourself"
    ),
    "запушенная голова не записана — {retry}": (
        "the pushed head is not recorded — {retry}"
    ),
    (
        "голова PR {head} ≠ запушенной прогоном {pushed} — ветку меняли "
        "снаружи; разберите вручную"
    ): (
        "PR head {head} ≠ the head pushed by the run {pushed} — the branch was "
        "changed from outside; sort it out by hand"
    ),
    "(нет)": "(none)",
    "факты PR #{pr} не прочитаны: {exc} — {retry}": (
        "facts of PR #{pr} were not read: {exc} — {retry}"
    ),
    "факты PR #{pr} не прочитаны: ответ форджи не объект": (
        "facts of PR #{pr} were not read: the forge answer is not an object"
    ),
    "список PR {prefix}* не прочитан: {exc} — {retry}": (
        "the list of PRs {prefix}* was not read: {exc} — {retry}"
    ),
    (
        "исход spec-runner run не наблюдён, открыт integration-PR "
        "{partial} — он может быть частичным; закройте его и "
        "повторите dfl run (повтор перезапустит раннер; {abandon})"
    ): (
        "the outcome of spec-runner run was not observed, integration PR "
        "{partial} is open — it may be partial; close it and "
        "repeat dfl run (the repeat restarts the runner; {abandon})"
    ),
    "повтор покажет, что сделано, и не создаст новую ветку": (
        "the repeat shows what is done and does not create a new branch"
    ),
    "повтор перезапустит раннер": "the repeat restarts the runner",
    "spec-runner run вернул {rc} — разберите и повторите dfl run ({promise})": (
        "spec-runner run returned {rc} — sort it out and repeat dfl run ({promise})"
    ),
    "найдено {n}: {new}": "found {n}: {new}",
    "не найден": "none found",
    "spec-runner run: ожидался один новый integration-PR, {found} — {retry}": (
        "spec-runner run: one new integration PR was expected, {found} — {retry}"
    ),
    "integration-PR #{pr} открыт": "integration PR #{pr} opened",
    "integration-PR прогона не известен — повторите dfl run": (
        "the run's integration PR is unknown — repeat dfl run"
    ),
    "integration-PR #{pr} вмержен — конвейер закончен": (
        "integration PR #{pr} merged — the pipeline is finished"
    ),
    "смержите https://github.com/{slug}/pull/{pr} — затем dfl run": (
        "merge https://github.com/{slug}/pull/{pr} — then dfl run"
    ),
    # отказы `dfl run` на личном пути (lite/dfl.py)
    (
        "тема «{subject}» уже есть у enterprise-конвейера (идёт или закончен), "
        "--track personal не применим — продолжить её: dfl run --subject "
        "'{subject}' без --track; личный проект заведите под другой темой "
        "(или сначала dfl abandon)"
    ): (
        "the subject “{subject}” already has an enterprise pipeline (running or "
        "finished), --track personal does not apply — to continue it: dfl run "
        "--subject '{subject}' without --track; start the personal project under "
        "another subject (or dfl abandon first)"
    ),
    (
        "тема «{subject}» ведётся личным путём (прогон {run_id}), --track enterprise "
        "не применим — продолжить её: dfl run без --track; enterprise-конвейер для "
        "неё — сначала dfl abandon, затем dfl run --subject '<тема>' --track enterprise"
    ): (
        "the subject “{subject}” is on the personal path (run {run_id}), --track "
        "enterprise does not apply — to continue it: dfl run without --track; an "
        "enterprise pipeline for it — first dfl abandon, then dfl run --subject "
        "'<subject>' --track enterprise"
    ),
    (
        "--track требует --subject: активного личного прогона в репо нет — "
        "dfl run --subject '<тема>' --track personal"
    ): (
        "--track needs --subject: the repo has no active personal run — "
        "dfl run --subject '<subject>' --track personal"
    ),
    (
        "этапы с паузой на просмотр зафиксированы при старте прогона: {recorded} — "
        "продолжить: dfl run без --review; другой выбор — сначала dfl abandon, "
        "затем dfl run --subject '<тема>' --review <этапы>"
    ): (
        "the stages with a review pause were fixed at the start of the run: "
        "{recorded} — to continue: dfl run without --review; another choice — "
        "first dfl abandon, then dfl run --subject '<subject>' --review <stages>"
    ),
    "без пауз": "no pauses",
    (
        "--review: пауза на просмотр этапа работает только с автором claude "
        "(harness.author = claude) — поставьте его в конфиге или запустите без "
        "--review"
    ): (
        "--review: the stage review pause works only with the claude author "
        "(harness.author = claude) — set it in the config or run without --review"
    ),
    (
        "[review] pause_after не действует: пауза на просмотр этапа работает только "
        "с автором claude (harness.author = claude) — прогон идёт без пауз"
    ): (
        "[review] pause_after has no effect: the stage review pause works only "
        "with the claude author (harness.author = claude) — the run goes without "
        "pauses"
    ),
    (
        "неизвестный этап: {unknown}; допустимы: личный путь: "
        "{personal}; enterprise: {enterprise}"
    ): (
        "unknown stage: {unknown}; allowed: personal path: "
        "{personal}; enterprise: {enterprise}"
    ),
    # --- Task 7: edge-check, код, ручные проверки ---------------------------
    "исправлю сам: {n}; вопросов к вам: до {m}": (
        "I will fix myself: {n}; questions for you: up to {m}"
    ),
    "принять узел вопреки замечанию (запишется в журнал и в PR)": (
        "accept the node despite the finding (recorded in the journal and the PR)"
    ),
    (
        "принять узел вопреки всем {n} замечаниям правила {rule} "
        "(перекрывает ответы по ним; запишется в журнал и в PR)"
    ): (
        "accept the node despite all {n} findings of rule {rule} (overrides the "
        "answers on them; recorded in the journal and the PR)"
    ),
    "принятие вопреки не покрывает все замечания уровня — разбор остановлен": (
        "accepting despite does not cover all findings of the level — resolution "
        "stopped"
    ),
    "разбор остановлен — продолжить: dfl run": "resolution stopped — continue: dfl run",
    "разбор замечаний невозможен: {exc}": "findings cannot be resolved: {exc}",
    "разбор замечаний не состоялся: {exc}": "findings resolution failed: {exc}",
    "замечания приняты вопреки — записано в журнал; проверяю снова": (
        "findings accepted despite — recorded in the journal; checking again"
    ),
    "  то же правило, исправлю сам: {where}: {statement}": (
        "  same rule, I will fix myself: {where}: {statement}"
    ),
    "замечание {where}: {statement}": "finding {where}: {statement}",
    "удалит: {ids}": "removes: {ids}",
    "понизит: {ids}": "downgrades: {ids}",
    " (рекомендую)": " (recommended)",
    "  исправлено: {where}: {statement}": "  fixed: {where}: {statement}",
    "узел переписан ({names}): замечаний {n}; проверяю снова": (
        "node rewritten ({names}): findings {n}; checking again"
    ),
    "файлы не возвращены: {names} — копии в {path}": (
        "files not restored: {names} — copies in {path}"
    ),
    "переписывание {name} не состоялось: {why}": "rewriting {name} failed: {why}",
    "переписанный файл не читается: {exc}": "the rewritten file cannot be read: {exc}",
    (
        "переписывание {name} отклонено — ослабляет узел без вашего решения: "
        "{ids}; файл возвращён"
    ): (
        "rewriting {name} rejected — it weakens the node without your decision: "
        "{ids}; file restored"
    ),
    "критерии в записи не названы": "the record names no criteria",
    (
        "edge-check {name}: вердикт {verdict} без блокирующих находок — "
        "не пройдены критерии: {unmet}. Поправьте {name} по этим причинам и "
        "повторите dfl run (повтор платный; {abandon})"
    ): (
        "edge-check {name}: verdict {verdict} without blocking findings — "
        "criteria not met: {unmet}. Fix {name} for these reasons and repeat "
        "dfl run (the repeat is paid; {abandon})"
    ),
    "без файла": "no file",
    "если замечание на деле о {name} — поправьте его вручную": (
        "if the finding is really about {name} — fix it by hand"
    ),
    (
        "{path} можно поправить вручную (его собственное ребро не перепроверяется)"
    ): "{path} can be fixed by hand (its own edge is not rechecked)",
    "{brief} подтверждён в разговоре и меняется только новым интервью": (
        "{brief} was confirmed in the conversation and changes only by a new interview"
    ),
    (
        "edge-check {name}: замечания указывают не на {name}, а на "
        "{paths} — {shown}. Разбор правит только {name}: "
        "{advice}; затем повторите dfl run (повтор платный) — {abandon}"
    ): (
        "edge-check {name}: the findings point not at {name} but at {paths} — "
        "{shown}. Resolution edits only {name}: {advice}; then repeat dfl run "
        "(the repeat is paid) — {abandon}"
    ),
    (
        "spec-loop (test): реализация задач запущена · подробнее (необязательно), "
        "в каталоге проекта: spec-runner tui --spec-prefix={ws}-"
    ): (
        "spec-loop (test): task implementation started · details (optional), in "
        "the project directory: spec-runner tui --spec-prefix={ws}-"
    ),
    "прогон {run_id} исполняется другим процессом": (
        "run {run_id} is being executed by another process"
    ),
    "… думаю · {elapsed}": "… thinking · {elapsed}",
    "нет ответа за {timeout} с": "no answer within {timeout} s",
    "код {code}": "code {code}",
    "что видно на деле (Enter — без комментария):": (
        "what do you actually see (Enter — no comment):"
    ),
    "текст оговорки:": "caveat text:",
    "проверяйте код из PR #{pr}: gh pr checkout {pr} (назад: git switch -)": (
        "check the code from PR #{pr}: gh pr checkout {pr} (back: git switch -)"
    ),
    "PR #{pr} влит: проверяйте на обновлённой базе (git pull)": (
        "PR #{pr} is merged: check on the updated base (git pull)"
    ),
    (
        "⚠ в рабочем каталоге нет кода PR #{pr} — проверка пройдёт на старом коде; "
        "сначала: gh pr checkout {pr}"
    ): (
        "⚠ the working directory lacks the code of PR #{pr} — the check would "
        "run on old code; first: gh pr checkout {pr}"
    ),
    (
        "⚠ в рабочем каталоге нет кода влитого PR #{pr} — проверка пройдёт на "
        "старом коде; сначала: git pull"
    ): (
        "⚠ the working directory lacks the code of merged PR #{pr} — the check "
        "would run on old code; first: git pull"
    ),
    "бандл прогона не прочитан: {exc} — повторите dfl run": (
        "the run bundle was not read: {exc} — repeat dfl run"
    ),
    "публикация ответов не удалась: {exc} — повторите dfl run": (
        "publishing the answers failed: {exc} — repeat dfl run"
    ),
    'нет списка "items"': 'no "items" list',
    "находка {no} разобрана дважды": "finding {no} is sorted twice",
    "не разобраны находки: {missing}": "findings not sorted: {missing}",
    "элемент items не объект": "an items element is not an object",
    "неизвестная находка: {no!r}": "unknown finding: {no!r}",
    'находка {no}: kind должен быть "fix" или "decision"': (
        'finding {no}: kind must be "fix" or "decision"'
    ),
    "находка {no}: у decision нет вопроса": "finding {no}: the decision has no question",
    "находка {no}: нужно {low}–{high} варианта": (
        "finding {no}: {low}–{high} options needed"
    ),
    "находка {no}: рекомендован должен быть ровно один вариант": (
        "finding {no}: exactly one option must be recommended"
    ),
    "находка {no}: вариант не объект": "finding {no}: an option is not an object",
    "находка {no}: у варианта нет label": "finding {no}: an option has no label",
    "находка {no}: {field} должен быть списком строк": (
        "finding {no}: {field} must be a list of strings"
    ),
}
