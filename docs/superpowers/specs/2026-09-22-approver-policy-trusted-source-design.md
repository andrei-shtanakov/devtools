# Политика подписи §I12 из доверенного источника — `approval-policy`

Дата: 2026-09-22. Статус: draft, **spec converged** (локальное ревью Codex, 2 круга:
13 находок с одним блокером → 7 minor/nit; все отработаны). Ждёт вычитки
владельца (§9). Эпик:
`eco.dark-factory`. Исполняемый пункт — `TODO.md`
`@id:approver-policy-trusted-source`.

Основания:
- решение владельца 2026-09-21 (в пункте TODO): доверенный источник,
  недоступный на запись стороне, чью работу санкционируют; три границы;
- решение владельца 2026-09-22 (§2 D1–D5): отдельный репозиторий политики,
  пин по commit SHA у candidate, повторное чтение у finalize, отказ с
  сохранением заявки и явной причиной на недоступность/пустоту/смену
  версии, переход на новую версию — повторное установление авторизации,
  CLI и окружение список не переопределяют;
- правило `prograph-vault/authored/rules/approver-policy.md` (значение
  политики; после этой работы правило меняет способ доставки — заявка
  prograph-vault#147 расширяется, см. §8);
- механика §I12 — `docs/superpowers/specs/2026-09-09-tasks-supersede-contract-design.md`;
- код: `governance/approval_facts.py` (`approver_allowlist`,
  `policy_fingerprint`, `authorized_signature`, `Authorization`),
  `governance/approve_node.py` (`_propose` — фаза 1, включая присоединение к
  живой заявке через `extend_request`; `_reconcile_candidate` — фаза 2;
  `_finalize` — фаза 3), `governance/approval_ledger.py` (`start_request`,
  `extend_request`, `record_merge`, `next_step`, `TERMINAL_STATUSES`),
  `governance/ops.py` (`remote_branch_head_fact` — GraphQL, FOUND/ABSENT/
  UNAVAILABLE; `show_repo_file_bytes` — REST contents), `governance/ssot_env.py`
  (`read_key`), `governance/authority_root.py` +
  `contracts/authority-root/v1/paths.env`, `human-merge.sh`.

## 1. Цель и граница

### 1.1. Что наблюдается сегодня

Allowlist учёток, чей мерж создаёт подпись узла, читается из переменной
окружения `AUTHORIZED_APPROVER_ACCOUNTS` того процесса, который устанавливает
факт мержа (`approval_facts.approver_allowlist`). Это та же сторона, чью
работу подпись санкционирует: запусти финализацию агент — политику подаёт он
сам. `Authorization.source` записывает имя переменной, а не место, которое
кто-то контролирует. Пустая политика уже отказывает до candidate (#332) и не
хоронит заявку на фазе 2 (#338) — обе правки про передачу значения, не про
его источник. `human-merge.sh` сверяет логин против той же переменной.

### 1.2. Что вводится

Канонический список живёт в **отдельном репозитории политики**, куда
исполнитель (`ai-prosto` и любой агент) писать не может, а изменения вносит
владелец. Механика §I12 читает список из этого репозитория по
**закреплённому commit SHA**: candidate закрепляет версию, finalize читает ту
же версию и сверяет, что актуальная не сменилась. Окружение и аргументы
запуска список не задают и не переопределяют.

Независимость, которую это даёт, — **от окружения и CLI исполнителя и от
самого списка**. От проверяемого дерева devtools она ровно в меру
authority-root: координаты источника лежат в SSOT-файле под authority-root
(§3.3), и PR, который их меняет, агент не ревьюит и не мержит (`accept_pr`,
`merge-pr.sh`). Полной независимости от дерева у devtools нет ни у одного
гварда, и эта спека её не обещает.

### 1.3. Чем эта работа не является

- Не смена значения политики: список остаётся `andrei-shtanakov`.
- Не security boundary на стороне форджи: настоящая граница — правила
  репозиториев.
- Не изменение фазы 3 (`_finalize`): она сверяет целостность записанного
  решения и allowlist заново не применяет.

## 2. Решения владельца

| # | Решение | Дата |
|---|---|---|
| D1 | Источник — отдельный репозиторий политики; исполнитель без права записи; изменения утверждает владелец. Путь authority-root внутри devtools независимости не обеспечивает | 2026-09-22 |
| D2 | Candidate закрепляет версию политики по commit SHA; finalize повторно получает её из доверенного источника | 2026-09-22 |
| D3 | Недоступность, пустота или изменение актуальной версии относительно закреплённой — отказ с сохранением заявки и явной причиной | 2026-09-22 |
| D4 | Переход на новую версию требует повторного установления авторизации | 2026-09-22 |
| D5 | CLI и окружение исполнителя список не переопределяют | 2026-09-22 |
| D6 | Три границы реализации: источник вне проверяемого дерева с независимым управлением доступом; окружение и аргументы его не переопределяют; candidate и finalize берут одну закреплённую версию, недоступная или изменившаяся — отказ с явной причиной | 2026-09-21 |

**Уточнение D3, требующее подтверждения владельца (S6).** Буквальное
«отказ с сохранением заявки» на смену версии запирает узел навсегда: заявка с
`candidate_pr` и без `merged_by` остаётся живой (`next_step` =
`AWAIT_CANDIDATE_MERGE`), каждый вызов по узлу идёт в `_reconcile_candidate`
и получает тот же отказ, а новый candidate (`_propose`) при живой заявке не
заводится и закрыть уже вмерженный candidate-PR нельзя. Поэтому спека
разводит два случая: **неустановленный** факт (сеть, недоступный источник,
подмена на пути чтения) — отказ с сохранением, как велит D3; **установленный**
факт смены версии (обе версии прочитаны) — терминализация заявки с причиной,
после которой новый candidate штатно доступен, — что и есть «повторное
установление авторизации» D4.

Решения этой спеки (внесены по поручению «имя репозитория и настройку
доступа внеси в секцию дизайна»):

| # | Решение |
|---|---|
| S1 | Репозиторий `andrei-shtanakov/approval-policy`, **публичный**. Список не секрет — он опубликован в правиле волта; доверие даёт ограничение записи, не скрытность. Приватный добавил бы вторую зависимость доступа (`pull` для `ai-prosto`) — в личных репозиториях ступеней прав три (`pull`/`push`/`admin`, замер 2026-09-19), и лишняя выдача есть лишняя поверхность |
| S2 | Соавторов нет: `ai-prosto` не добавляется ни в какой роли; писать может только владелец по факту владения |
| S3 | Ruleset на `main`: запрет удаления и force-push, линейная история, изменения только через PR, bypass-акторов нет, обязательных ревью нет (единственный пишущий — владелец, форджа не даёт одобрить свой PR; требование ревью сделало бы ветку неизменяемой — класс «зелёное для админа ≠ проходимое для не-админа») |
| S4 | Раскладка: `policy/approvers.env` с единственным ключом `AUTHORIZED_APPROVER_ACCOUNTS=<login>[,<login>…]` и `README.md`. Формат `.env` — потому что у devtools есть fail-closed читатель этой формы (`ssot_env.read_key`: отказ на отсутствии ключа, дубле, пустом значении), и тот же ключ читает правило волта |
| S5 | **Версия = последний коммит `main`, тронувший `policy/approvers.env`** (commit SHA; D2 соблюдено). Не HEAD `main`: правка README хоронила бы все живые заявки (ревью, находка 5). Файл читается по этому SHA |
| S6 | Смена версии при живой заявке: на фазе 2 — установленный факт о паре версий → заявка `invalidated` с причиной «политика сменилась: закреплена X, актуальная Y; повторное установление авторизации — новый candidate над теми же узлами». Команды перепиновки живой заявки нет: она превращала бы акт под одной политикой в акт под другой |
| S7 | `AUTHORIZED_APPROVER_ACCOUNTS` в окружении исполнителя **не читается**; выставленная переменная — именованный отказ до любого эффекта и на любой фазе («переменная больше не источник политики; источник — approval-policy@…»), с сохранением заявки: оператор, выставивший её по старому правилу, обязан услышать, что намерение не исполняется |
| S8 | Координаты источника — SSOT-файл `contracts/approval-policy-source/v1/source.env` (`APPROVAL_POLICY_REPO`, `APPROVAL_POLICY_REF`, `APPROVAL_POLICY_PATH`), префикс добавляется в `AUTHORITY_ROOT_PREFIXES`; читают `ssot_env.read_key` (python) и `ssot_key` — функция сегодня живёт только в `merge-pr.sh`; в `human-merge.sh` она **выносится в подключаемый файл** `ssot_env.sh`, который читают оба скрипта (третья копия формата дала бы дрейф, ради которого `ssot_env` заводился). Константы в `governance/` агент мог бы перенаправить своим PR под агентским мержем (ревью, находка 2) |
| S9 | Чтение политики идёт под **дефолтным профилем оператора** `gh` — тем же, под которым устанавливаются факты форджи (`pr_facts`, `find_pr`, `remote_branch_head_fact`); профиль `~/.config/review` (`ai-prosto`) остаётся только для актов публикации (`merge`, `close_pr`, ревью). Репозиторий публичный, поэтому любой аутентифицированный профиль читает его; выбор дефолтного — потому что политика есть факт форджи, а не акт |

## 3. Источник

### 3.1. Репозиторий `andrei-shtanakov/approval-policy`

```
README.md
policy/approvers.env      # AUTHORIZED_APPROVER_ACCOUNTS=andrei-shtanakov
```

Bootstrap — шаг плана с явным подтверждением владельца (создание
репозитория есть внешний эффект):

1. `gh repo create andrei-shtanakov/approval-policy --public` с начальным
   коммитом двух файлов;
2. ruleset `main` по S3 (`deletion`, `non_fast_forward`,
   `required_linear_history`, `pull_request` без обязательных ревью;
   `bypass_actors: []`);
3. соавторов не добавлять (S2); проверка —
   `gh api repos/andrei-shtanakov/approval-policy/collaborators` даёт
   только владельца;
4. проверка чтения под дефолтным профилем оператора:
   `gh api graphql` с запросами §4.1 возвращает SHA и содержимое.

Репозитория на 2026-09-22 не существует (`gh repo view` — 404); bootstrap
идёт первым шагом плана, до кода.

### 3.2. Что считается версией

Версия политики — SHA последнего коммита `main`, изменившего
`policy/approvers.env` (S5). Содержимое читается **по этому SHA**: два чтения
одной версии обязаны дать одни байты.

Отпечаток (`policy_fingerprint`) продолжает считаться по составу списка
(`v1:` + sha1 отсортированного перечня; для `andrei-shtanakov` —
`v1:31bf1658…`, пересчитан) — он отвечает «та же ли политика по содержанию» и
совместим с прошлыми ледгерами. SHA отвечает «та ли версия источника» и
пишется рядом.

### 3.3. Координаты источника (S8)

`contracts/approval-policy-source/v1/source.env`:

```
APPROVAL_POLICY_REPO=andrei-shtanakov/approval-policy
APPROVAL_POLICY_REF=main
APPROVAL_POLICY_PATH=policy/approvers.env
```

`contracts/approval-policy-source/` входит в `AUTHORITY_ROOT_PREFIXES`
(`contracts/authority-root/v1/paths.env`). В `_HARNESS_PREFIXES` (`accept_pr`)
файл не нужен — `accept_pr` его не читает; тест
`test_every_harness_input_is_authority_root_but_the_kit` проверяет разность
`harness − authority` и от добавления префикса только в authority не
меняется — правки теста нет, добавляется отдельный тест на присутствие
префикса. PR,
трогающий этот файл, агент не мержит (`merge-pr.sh` — четвёртый
категорический отказ; `accept_pr` — стоп до ревью).

## 4. Контракт механики

### 4.1. Факты форджи

Два запроса GraphQL (по образцу `remote_branch_head_fact`: положительное
отсутствие отличимо от недоступности, чего REST-404 не даёт):

- **версия**: `repository(owner,name){ ref(qualifiedName){ target { ...
  history(first:1, path:$path){ nodes { oid } } } } }` → `FOUND(sha)`;
  `repository` есть, `ref: null` — `ABSENT` (ветки нет); история пуста
  (файла никогда не было) — `ABSENT`; иное — `UNAVAILABLE`;
- **содержимое**: `repository{ object(oid:$sha){ ... on Commit { file(path:$path){
  object { ... on Blob { text isBinary isTruncated } } } } } }` → `FOUND(text)`;
  `object: null` — коммита нет (`ABSENT` о версии); `file: null` — пути нет
  в этом коммите (`ABSENT` о файле); `text: null`, `isBinary` или
  `isTruncated` — `UNAVAILABLE` (прочитать не удалось, не «нет»). Форма
  `object(expression:"<sha>:<path>")` не используется: она отдаёт `null` и
  на несуществующий коммит, и на отсутствующий путь, сливая два случая.

`Ops` получает `policy_version_fact(repo, ref, path) -> Fact[str]` и
`repo_file_fact(repo, sha, path) -> Fact[str]`. `show_repo_file_bytes`
(REST, `None` на любой сбой) для политики не используется: он сворачивает
отсутствие и недоступность в одно. `remote_branch_head_fact` не
переиспользуется напрямую, потому что нужен не HEAD ветки, а последний
коммит по пути (S5); форма и граница исходов — те же.

### 4.2. Снимок политики

```
PolicySnapshot(repo, ref, path, sha, accounts: frozenset[str], fingerprint)
```

`policy_snapshot(ops, *, pinned_sha: str | None) -> Fact[PolicySnapshot]`:

| Шаг | Исход |
|---|---|
| `AUTHORIZED_APPROVER_ACCOUNTS` выставлена в окружении | `FORBIDDEN(kind=env)` — S7, до обращения к фордже |
| координаты (`source.env`) не читаются | `FORBIDDEN(kind=source)` — конфигурация devtools, не форджа |
| версия `UNAVAILABLE` | `UNAVAILABLE` |
| версия `ABSENT` (нет ветки / файла никогда не было; коммит удаления файла `history(path:)` включает — тогда версия есть, отсутствие ловится шагом содержимого) | `FORBIDDEN(kind=absent)` — установленный факт об источнике |
| `pinned_sha` задан и версия ≠ `pinned_sha` | `FORBIDDEN(kind=superseded, pinned, current)` |
| содержимое `UNAVAILABLE` | `UNAVAILABLE` |
| содержимое `ABSENT` при известном SHA | `FORBIDDEN(kind=absent)` |
| `read_key` отказывает (нет ключа, дубль, пусто) **или значение после разбора не содержит ни одного логина** (`= , ,` проходит `read_key`, но даёт пустое множество) | `FORBIDDEN(kind=empty, detail)` — «подписать не может никто»; `FOUND` с пустым `accounts` невозможен по построению |
| всё прочитано | `FOUND(snapshot)` |

`kind` — машинный различитель, по которому фазы решают, сохранять заявку
или терминализировать (§4.4). Ни одно сообщение не упоминает учётку мержера.

### 4.3. Фаза 1 — предложение

`_propose` читает снимок **до** первой записи, и путь зависит от того,
заводится ли новая заявка или узел присоединяется к живой
(`live_request_for_step` + `_still_accumulating`):

- **новая заявка**: `policy_snapshot(ops, pinned_sha=None)`;
  `UNAVAILABLE` → `_unresolved`; любой `FORBIDDEN` → `RuntimeError` с
  причиной, ничего не создано; `FOUND` → `start_request(..., policy=…)` пишет
  `policy: {repo, ref, path, sha, fingerprint}` тем же write-ahead;
- **присоединение** (`extend_request`): `policy_snapshot(ops,
  pinned_sha=op["policy"]["sha"])` — узел присоединяется только под пином
  заявки; `superseded` → отказ **без записей** с текстом: «заявка K закреплена
  на X, актуальная Y; завершите или дождитесь терминализации K, затем новый
  candidate»; заявка без `policy` (старый формат, §4.6) — тот же отказ;
  `FOUND` — отпечаток прочитанной закреплённой версии сверяется с
  `op["policy"]["fingerprint"]` (та же проверка «прочитано не то», что на
  фазе 2), расхождение — отказ без записей.

Ветки no-op и долга по пинам остаются выше чтения (как у #332).

### 4.4. Фаза 2 — установление факта мержа

`_reconcile_candidate` после `merge_event` и до `authorized_signature`
читает `policy_snapshot(ops, pinned_sha=op["policy"]["sha"])`:

| Исход | Действие |
|---|---|
| `UNAVAILABLE` | `_unresolved` — заявка жива, ничего не записано |
| `FORBIDDEN(env)` | `RuntimeError` S7, заявка жива (фазы 1 и 2 — разные процессы, урок #338) |
| `FORBIDDEN(source)` | `RuntimeError` «конфигурация источника не читается», заявка жива — инструмент, не работа |
| `FORBIDDEN(superseded)` | **`invalidate_request`** с причиной «политика сменилась: закреплена X, актуальная Y» и `RuntimeError`, называющий переход: новый candidate над теми же узлами (S6, уточнение D3) |
| `FORBIDDEN(absent)` / `FORBIDDEN(empty)` для закреплённого SHA | невозможно для версии, прочитанной фазой 1 (SHA неизменяем); если случилось — `RuntimeError` с причиной, заявка жива: это подмена или сбой пути чтения, не факт о версии |
| `FOUND` | сверка `snapshot.fingerprint == op["policy"]["fingerprint"]`; расхождение при том же SHA — `RuntimeError` «прочитано не то, что закреплялось», заявка жива; совпало — `authorized_signature(merged, snapshot)` |

`authorized_signature(event, snapshot)` — `FOUND(Authorization)` при логине
в `snapshot.accounts`, `FORBIDDEN` иначе (как сегодня, `invalidated`);
ветка `UNAVAILABLE` при пустом списке (#338) уходит: пустой список в
закреплённой версии не достижим (фаза 1 его отказала).

`Authorization.source` =
`github:andrei-shtanakov/approval-policy@<sha>:policy/approvers.env`;
`Authorization.policy` — отпечаток снимка. Фаза 3 не меняется.

Сообщения оператору (`_candidate_body`, тексты `_propose`,
`_reconcile_candidate`, `ApprovalOutcome`) называют
`approval-policy@<sha>` вместо переменной; тело candidate-PR несёт строку
`policy: andrei-shtanakov/approval-policy@<sha>` — по ней `human-merge.sh`
сверяет версию **до** мержа (§4.5), чтобы смена политики не сжигала
человеческий акт.

### 4.5. `human-merge.sh`

Скрипт — authority-root. Сверка логина против `policy/approvers.env`
репозитория политики, прочитанного `gh api graphql` под дефолтным профилем
по SHA из тела PR (`policy: <repo>@<sha>`); до мержа скрипт читает
актуальную версию (S5) и отказывает кодом 3, если она ≠ пину: «политика
сменилась после candidate — мерж не создаст подписи; новый candidate».
Выставленная `AUTHORIZED_APPROVER_ACCOUNTS` — отказ S7 (код 3) до всего.
Тело без строки `policy:` — код 2: это состояние PR (candidate старого
формата), не авторизация актора.

### 4.6. Заявки старого формата

Заявка без поля `policy` на фазе 2 — `invalidated` с причиной «заявка не
закрепила версию политики» и переходом «новый candidate»: считать её
закреплённой на текущую версию значило бы переавторизовать по конфигурации
момента повтора. Живых заявок такого формата на 2026-09-22 нет.

### 4.7. Что больше не существует

`approver_allowlist()` и чтение переменной из окружения удаляются;
константа имени остаётся ради отказа S7 и ключа в `approvers.env`.
Локальной или вендоренной копии политики в devtools нет.

### 4.8. Ледгер

```
"policy": {"repo": "...", "ref": "main", "path": "policy/approvers.env",
           "sha": "<40 hex>", "fingerprint": "v1:<sha1>"}
"authorization": {"login": "...", "policy": "v1:<sha1>",
                  "source": "github:<repo>@<sha>:policy/approvers.env"}
```

## 5. Изменения по модулям

| Файл | Правка |
|---|---|
| `contracts/approval-policy-source/v1/source.env` (новый), `contracts/authority-root/v1/paths.env` | координаты источника; префикс под authority-root; `test_every_harness_input_is_authority_root_but_the_kit` |
| `governance/ops.py` | `policy_version_fact`, `repo_file_fact` (GraphQL, §4.1) в протоколе и `RealOps`; фейки в тестах |
| `governance/approval_facts.py` | `PolicySnapshot`, `policy_snapshot`, `authorized_signature(event, snapshot)`, `policy_fingerprint(accounts)`; удаление `approver_allowlist`; чтение координат через `ssot_env` |
| `governance/approval_ledger.py` | `start_request(..., policy)`; `extend_request` без изменений формы, но вызывается только под пином (§4.3) |
| `governance/approve_node.py` | фаза 1 (новая/присоединение), фаза 2 (§4.4), §4.6; тексты сообщений и `_candidate_body` |
| `human-merge.sh`, `ssot_env.sh` (новый, общий с `merge-pr.sh`), `tests/test_human_merge.py` | §4.5; стенд уже подменяет `gh` стабом в PATH — уходит только env, стаб получает ответы GraphQL политики |
| `tests/test_governance_approve_node.py` | фикстура `world` без `setenv`; `Forge` получает фейк источника политики (версия, содержимое, `mute` для `UNAVAILABLE`, подмена содержимого для «прочитано не то»); тесты 2433–2510 переписываются под §4 |
| `tests/test_governance_approval_facts.py`, `tests/test_governance_approval_ledger.py` | литералы `source`/env → снимок |
| `docs/superpowers/specs/2026-09-09-tasks-supersede-contract-design.md` | таблица исходов: строки про пустой дефолт, смену политики; строки 2069 и 2130–2131 («повтор классифицирует по allowlist, актуальному на момент повтора») → «по закреплённой версии» |
| `docs/superpowers/specs/2026-09-15-need-stage-design.md` | D6 упоминает env — сноска на эту спеку |
| `CLAUDE.md`, `README.md` | секция мержа: источник — репозиторий; оговорка defense-in-depth уточняется до «в меру authority-root» |
| `TODO.md` | пункт закрывается PR пары; **PR мержит человек** — `human-merge.sh` и `contracts/authority-root/` в дифе |

## 6. Тесты (красные без правки)

- фаза 1 без сети — `_unresolved`, заявка не создана; с выставленной
  переменной — отказ до обращения к `ops`; с нечитаемым `source.env` —
  отказ с именем файла;
- фаза 1 пишет `policy.sha` = версия, прочитанная тем же вызовом; правка
  README в репозитории политики версию не меняет (S5);
- присоединение под чужим пином при сменившейся версии — отказ без записей;
- фаза 2 при смене версии — `invalidated` с обеими версиями; новый
  candidate заводится и завершается под новым пином;
- фаза 2 при том же SHA и совпавшем отпечатке — мерж учётки из списка
  завершает заявку, `authorization.source` содержит `@<sha>:`;
- фаза 2 при подменённом содержимом под тем же SHA — отказ, заявка жива;
- фаза 2 с выставленной переменной — отказ S7, заявка жива;
- заявка без `policy` — `invalidated` §4.6;
- пустой/битый `approvers.env` в актуальной версии — фаза 1 отказывает до
  candidate, текст называет ключ и репозиторий, не учётку;
- `human-merge.sh`: логин вне списка — код 3; версия сменилась — код 3 до
  мержа; тело без `policy:` — код 2; выставленная переменная — код 3;
- AST-страж: `os.environ.get(APPROVER_ALLOWLIST_ENV` в `governance/` — только
  в проверке S7;
- authority-root: `contracts/approval-policy-source/` в перечне (отдельный
  тест присутствия префикса);
- `approvers.env` со значением `, ,` — `FORBIDDEN(empty)`, не `FOUND`.

## 7. Вне объёма

- Автоматизация bootstrap репозитория политики.
- Перенос других политик (steward actor-policy, `merge_policy`) в тот же
  репозиторий.
- Кэш снимка между вызовами: два чтения на заявку.

## 8. Порядок раскатки

1. Bootstrap репозитория политики (владелец).
2. PR пары (спека + план + код) — мержит человек (authority-root).
3. **Не позже** мержа — правка правила волта (prograph-vault#147 расширяется):
   команда `AUTHORIZED_APPROVER_ACCOUNTS=… make human-merge` после S7 есть
   команда на отказ; правило называет репозиторий и `make human-merge` без
   переменной.

## 9. Открытые вопросы

Один, для владельца: подтвердить уточнение D3 (§2) — смена версии при живой
заявке терминализирует её, а не сохраняет. Остальное — S1–S9 — решения
спеки, ждут вычитки.
