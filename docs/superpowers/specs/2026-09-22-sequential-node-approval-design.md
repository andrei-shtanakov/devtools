# Последовательное одобрение узлов бандла — человеческий гейт внутри авторинга

Дата: 2026-09-22. Статус: **accepted** — spec converged (локальный цикл Codex, 4 круга),
уточнение D1 подписано владельцем 2026-09-22 (§2). План —
`docs/superpowers/plans/2026-09-22-sequential-node-approval.md`. Эпик:
`eco.dark-factory`. Исполняемый пункт — `TODO.md` `@id:sequential-node-approval`.

Основания:
- решение владельца 2026-09-20, `prograph-vault/authored/notes/2026-09-20-pipeline-and-polygon-decisions.md`,
  решения 4 и 5;
- решения владельца 2026-09-22 (§2 D1–D4);
- механика §I12 — `docs/superpowers/specs/2026-09-09-tasks-supersede-contract-design.md`
  (в т.ч. «зависимые уровни в одном candidate-PR запрещены», «по два PR на
  уровень», волна = проход по DAG с intent-отпечатком состава);
- код: `governance/approve_node.py` (`approve_node` → `check_bundle_composition`,
  `_propose`, `_base_text`, `_carried_text`, `_publish_candidate`,
  `_sync_branch_to_snapshot`, `_cascade_stale`, `_invalidate_downstream`,
  `_require_upstream_ready`, `_reconcile_candidate`, `_finalize`,
  `_verify_nodes_in_base`, `_missing_approving_review`, `read_dag_state`),
  `governance/approval_ledger.py` (`open_wave_record`, `open_wave`,
  `_wave_for`, `live_request_for_step`, `reconcile_wave_after_approved_dag`),
  `governance/node_approval.py` (`node_debt`, условие 3 — пин по `blob_sha1`
  всего файла upstream, включая конверт), `governance/bundle_dag.py`
  (`BUNDLE_DAG`, `dag_for`, `check_bundle_composition`), `governance/runner.py`
  (`advance`, `_step_branch` — ветка `spec/<ws>-behaviour` в том же
  `target_dir`, `_step_authoring`, `_step_gate`, `_step_pr`, `resume`,
  `_reconcile_pr_merged_out_of_band` по `state.pr`, `_STOPPED_RESET_OPS`),
  `governance/ops.py` (`gate_check_candidate` — `--profile <путь>`; авторский
  агент пинует upstream по `git hash-object` в worktree), `governance/spec_loop.py`
  (восстановление из фактов GitHub по MERGED бандл-PR), `governance/edge_check/`
  (пакет; CLI — `edge_check.py`), `contracts/review-scope/v1/prose-paths.env`;
- steward (только чтение): `cli.py` `_resolve_profile_path` принимает любой
  путь к YAML, но `roles.yaml`, `gate-catalog.yaml`, `approval-policy.yaml`,
  `arch-policy.yaml` анкорятся к каталогу профиля; `check_completeness`
  (REQ-202) — обязательный узел без артефакта = ошибка;
- edge-check — `docs/superpowers/specs/2026-09-21-edge-check-design.md`
  (D12 леджер доверенного процесса, D14–D16 публикация, §8 коды возврата);
- контрольный прогон S7 — `docs/evidence/2026-09-21-s7-control-run.md`
  (находка 6: tasks-PR кодовый из-за `evidence/*.jsonl`).

## 1. Цель и граница

### 1.1. Что наблюдается сегодня

Все шесть узлов пишутся одним заходом до первого одобрения, затем один
бандл-PR, человеческий мерж (`waiting_human_merge`) и шесть актов §I12 над
байтами, уже лежащими в base. Авторинг последователен по данным, человеческий
гейт — один и в конце. На бандле ~6300 строк это стоило восьми кругов ревью
и четырнадцати на доработку: пока писались нижние узлы, ни один верхний не
был заморожен.

### 1.2. Что вводится

Авторинг ведётся **волнами**: узлы волны k пишутся против узлов волн < k,
уже одобренных (`approved`) в base. Гейт волны = механическая проверка steward
по проекции профиля + обязательный edge-check + заявка §I12, чей
**candidate-PR несёт авторские байты узла** вместе с конвертом
`approval_pending`; мерж candidate человеком вносит байты в base и есть акт
одобрения; finalize — агентом. Отдельного бандл-PR нет: последний candidate
закрывает бандл.

Переоткрытие верхнего узла — явный акт оператора (§3.4): нижние узлы
остаются в base, их одобрения помечаются `stale`, продвижение заблокировано,
переодобрение — по уровням, каждое после edge-check против новой версии.

### 1.3. Чем эта работа не является

- Не нарезка предмета прогона (снята решением 4).
- Не новая подпись: акт — мерж candidate учёткой из политики (§I12;
  источник политики — спека `2026-09-22-approver-policy-trusted-source-design.md`).
- Не смена контракта пина §I12 (условие 3 `node_debt`: пин = blob всего
  файла upstream, включая конверт) — см. S1.
- Не оракул продукта (`bundle-docs-as-oracle`, после этой спеки).
- Не экономия платных вызовов авторинга.

## 2. Решения владельца

| # | Решение | Дата |
|---|---|---|
| D1 | Три гейта внутри авторинга: после `10-requirements`, после `15-behaviour-spec`, после пары `20-design` + `25-acceptance`; `30-decomposition` закрывается вместе с бандлом | 2026-09-20 / 2026-09-22 |
| D2 | Переоткрытие верхнего узла сохраняет нижние артефакты, делает их одобрения устаревшими и блокирует продвижение; ничего не удаляется и не переписывается автоматически | 2026-09-22 |
| D3 | Повторное одобрение нижних — после проверки относительно новой одобренной версии верхнего | 2026-09-22 |
| D4 | Edge-check — обязательная проверка гейта; результат предъявляется и как evidence; ошибка или невозможность блокирует переход; успех не заменяет человеческое одобрение | 2026-09-22 |

**Уточнение D1 — подписано владельцем 2026-09-22:** «принимаю шесть
человеческих актов. Три логических гейта сохраняются; charter и requirements
проходят отдельными candidate последовательно. Контракт пина §I12 и гейт
steward ради сокращения числа актов не меняем». Соответствие актов гейтам —
таблица ниже. Довод: гейт 1 «после requirements» механически состоит из
**двух** человеческих актов: charter и
requirements не могут ехать одним candidate. Пин requirements на charter
считается по байтам всего файла charter, включая конверт подписи, а конверт
charter появляется только после его finalize — пин, посчитанный в общем
candidate, никогда не совпал бы с base (`_verify_nodes_in_base` →
`invalidated`; §I12 прямо запрещает зависимые уровни в одном candidate).
Альтернатива — сменить определение пина на хэш без конверта — меняет
контракт §I12 (условие 3), GC-STALE steward и урок spec-runner#410; в этой
спеке не выбрана. Число актов на бандл поэтому **6** (S8), не 5.

**Шесть актов ↔ три гейта D1:**

| Акт | Что мержит человек | Логический гейт D1 | Что заморожено после акта |
|---|---|---|---|
| 1 | candidate W1 — `00-charter` | гейт 1 «после requirements» (первая половина: charter — основание requirements) | charter `approved`, запинован |
| 2 | candidate W2 — `10-requirements` | гейт 1 «после requirements» (вторая половина, закрывает гейт) | requirements `approved`; behaviour-spec пишется против него |
| 3 | candidate W3 — `15-behaviour-spec` | гейт 2 «после behaviour-spec» | behaviour-spec `approved`; design и acceptance пишутся против него |
| 4 | candidate W4 — `20-design` + `25-acceptance` (одна заявка уровня) | гейт 3 «после пары design + acceptance» | пара `approved`; decomposition пишется против неё |
| 5 | candidate W5 — `30-decomposition` | терминальное одобрение: «закрывается вместе с бандлом» (D1) | бандл целиком `approved`; S8 и доставка tasks |
| 6 | approve tasks-спеки (мост, как сегодня) | вне бандла — граница доставки | tasks-спека `approved`, исполнение spec-runner |

Человеческих актов **на один больше**, чем гейтов плюс терминальные акты (5), потому что гейт 1 распадается на два candidate; по сравнению с сегодняшним минимумом (7) — на один меньше.

Решения спеки (вычитаны владельцем вместе с уточнением D1):

| # | Решение |
|---|---|
| S1 | **Волны авторинга**: W1 = {charter}, W2 = {requirements}, W3 = {behaviour-spec}, W4 = {design, acceptance}, W5 = {decomposition}. Три гейта D1 — это границы после W2, W3, W4; W1 — технический уровень (charter одобряется своим candidate до авторинга requirements); W5 — терминальное одобрение, закрывающее бандл. Волны соответствуют уровням DAG (`_levels`) один к одному; отдельного разбиения не вводится: уровни 0-based (индекс DAG), волны 1-based — `wave_of(node) = level(node) + 1` |
| S2 | **Candidate волны несёт авторские байты.** Источник байтов узла для заявки — **коммит ветки прогона** (SHA записывается в заявку полем `source_sha`), не рабочий каталог и не base. `_propose`/`_carried_text`/`_candidate_text` читают узел из этого коммита (`ops.show_file(target_dir, source_sha, path)`), self-hash считается по нему; `_verify_nodes_in_base` после мержа сверяет base с записанным — как сегодня. **W1-candidate несёт и discovery source-слой** (`00-discovery/*`, `state.brief.source_paths`): это прямой вход charter, который `direct_blobs` читает по ref в `_propose`, `_snapshot_is_published`, `_verify_nodes_in_base`, `read_dag_state`; `_sync_branch_to_snapshot` пишет его из `source_sha` вместе с файлами узлов, а поверхность D16 для W1 включает `00-discovery/*`. Тело candidate несёт `run-id` (сегодня — только у бандл-PR), иначе восстановление S13 не найдёт прогон |
| S3 | **Ледгер §I12 не меняется**: `wave` остаётся проходом по DAG с intent-отпечатком **полного** состава, `step` — уровнем DAG, `attempt` — попыткой; имена веток по `patterns.env` как сегодня. **`approve_node` получает полный DAG; усечение — только в проверке состава base** (S4а). Номер авторской волны живёт в **состоянии прогона** (`state.wave`, 1-based) и равен `уровень + 1` (уровень `K` в имени ветки заявки — 0-based, как сегодня); design+acceptance объединяются через `live_request_for_step(W, K)`; `--reopen` внутри незавершённого прохода даёт `approve-W-K-(A+1)`, после доставки (`complete_wave`) — новый W |
| S4 | **`approve_node` на неполном base**. Два независимых переключателя: **источник байтов** узла — поле заявки `source_sha` (есть → коммит ветки волны; нет → base, как сегодня; переодобрение `stale` уровней после `--reopen` идёт заявками без `source_sha`); **правило состава** — `state.authoring == waves` (не наличие `source_sha`: иначе переодобрение по base требовало бы полного DAG, а `--reopen requirements` после W4 ждал бы состава «≤ 0»). Правки: (а) `check_bundle_composition` в волновом режиме принимает base, чей состав — **префикс DAG по уровням** (уровни ≤ m для некоторого m; пустой состав при отсутствии каталога — сегодня это конфигурационная ошибка devtools#168, для W1 каталога нет по построению), и заявку над узлами уровня ≤ m+1; один предикат покрывает W1..W5, `--reopen` и переодобрение по base; (б) **только** `_cascade_stale` пропускает узлы, которых в base нет (`_invalidate_downstream` файлов не читает — не правится); (в) `read_dag_state` **не меняется**: отсутствующий узел остаётся `unresolved` — это единственный предикат «весь DAG одобрен» для гейта доставки, и пропуск сделал бы доставку проходимой на неполном бандле; (г) `_require_upstream_ready` без изменений (upstream обязан быть `approved` в base); (д) `_snapshot_is_published`/`_sync_branch_to_snapshot` приводят ветку заявки к байтам из `source_sha` (узлы + source-слой) + конверт; source-слой коммитится через `force_paths` — ignore-правило цели `workstreams/*/spec/*` + `!*.md` не пускает `00-discovery/` без `-f`, как в `_commit_bundle`; (е) поколение в `_carried_text` в режиме `source_sha` — `max(version в base, version в source) + 1`, иначе переавторенный файл с `version: 1` дал бы candidate `2` при `approved` `2` в base («один акт — одно поколение» §I12) |
| S5 | **Гейт steward по проекции профиля**: раннер копирует в каталог прогона `profiles/` target-репо целиком (профиль и его siblings `roles.yaml`, `gate-catalog.yaml`, `approval-policy.yaml`, `arch-policy.yaml` — они анкорятся к каталогу профиля), усекает копию профиля до уровней ≤ `state.wave` и передаёт её `--profile`. Компромисс назван: гейт судит по копии, которую пишет раннер; поэтому раннер записывает в леджер sha256 каждого скопированного файла и сверяет их с target перед вызовом — расхождение = `stopped_gate` «проекция не совпала с профилем». Целевое состояние — флаг `--upto <node>` у steward (заявка соседу по ADR-ECO-006 заводится планом; до неё — копия). Усечение по уровням даёт замкнутость upstream (`_validate_edges` steward) и выкидывает делегата `tasks` — `check_completeness` делегатов пропускает. Preflight `_step_authoring` (объявлен ли узел профилем) читает **полный** профиль target; локальный GC-COMPLETENESS раннера — **по уровням ≤ `state.wave`**, иначе он останавливал бы каждую волну до W4 |
| S6 | **Edge-check в гейте волны** (D4): для каждого узла волны — проверка против одобренных оснований в base по пинам; `PASS` обязателен; `FAIL`/`PENDING` → `stopped_review` с находками (код 1 edge-check §8 — имя стопа берётся у edge-check, второго не вводится), `ERROR`/невозможность (код 3) → `stopped_review` с `error_code`, обхода нет. Результаты `_step_edge` пишет в **каталог прогона** (CLI `--out`) и леджер (D12), не в worktree цели — иначе `approve_node` отказал бы на входе по `is_dirty`; в ветку заявки evidence `workstreams/<ws-id>/evidence/edge-check/<node>.json` кладёт только адаптер публикации поверх головы заявки (S7, D14–D16) |
| S7 | **Ревью candidate-PR = публикация edge-check** (edge-check D16, срез 3): evidence `.json` — код по `prose-paths.env`, scope-аттестации candidate не получит (S7-прогон, находка 6). Одобряющее ревью на candidate публикует edge-check своим маркером, когда изменённые пути ⊆ {файлы узлов заявки, **файлы каскада `stale` того же коммита**, **`00-discovery/*` для W1**, `evidence/edge-check/`}; иначе — отказ до PR. `_missing_approving_review` засчитывает последнее ревью любого логина = APPROVED — это и закрывает ruleset. **Порядок для многоузловой волны и повторов**: раннер собирает заявку по всем узлам волны (`start_request` + `extend_request`) **до** единственного push, затем один evidence-коммит адаптера поверх головы заявки, `head_sha` заявки **перезаписывается** на него (иначе повтор `_publish_candidate` встал бы на предка и `push -u` отказал non-ff), затем одно ревью на эту голову. **Предусловие учёток**: автор candidate/finalize (`gh pr create` под дефолтной учёткой процесса) ≠ `ai-prosto`, иначе ни edge-check, ни аттестация не дадут approving review — форджа не одобряет свой PR. Платного ревью моделью candidate не получает; экономии платных вызовов нет — edge-check и есть платный вызов на узел |
| S8 | **Finalize агентом на resume**: раннер после мержа candidate сам публикует scope-аттестацию на finalize-PR (`Ops.review` = `review-pr.sh`, prose-only, без модели) и повторяет `--approve-node` — четвёртой паузы на волну нет. Ненулевой код `Ops.review` на finalize (6 — stop rule/бюджет, 2 — прибор, доставленный CHANGES_REQUESTED) = «finalize остаётся человеку», не `stopped_review`; отказ форджи на мерже (код 4) — как сегодня, человеку |
| S9 | **Ветка на волну** `spec/<ws>-behaviour-w<k>`, создаётся от base на входе в волну k (`fetch` + `ops.switch_to(branch, base_ref)` — `git switch -C <branch> <start_point>`; не `ensure_branch`, который ветвит от текущего HEAD): в base лежат upstream с конвертами, пины авторского агента (`git hash-object` в worktree) совпадут с base (и всё равно перезаписываются из снимка заявки по base в `_carried_text`). Отдельная ветка на волну, а не пересоздание одной: `push_branch` — обычный `git push -u`, переписанная история одной ветки давала бы non-ff; ветка волны пушится (нужна `source_sha` для восстановления S13 на другой машине). Source-слой (`materialize-brief`) кладётся в worktree **каждой** волны заново из леджера (op `materialize-brief` становится per-wave), иначе гейт волны получил бы GC-BRIEF-SOURCE. Грязное дерево на входе — `stopped_dirty`, ничего не затирается молча |
| S10 | **Per-wave op'ы**: `branch-<w>`, `materialize-brief-<w>`, `author-<node>` (как сегодня), `commit-<w>`, `gate-<w>`, `edge-<w>`, `push-<w>`, `candidate-<w>`, `finalize-<w>`; `_STOPPED_RESET_OPS` расширяется диапазоном текущей волны. `state.pr` (бандл-PR) в волновом режиме пуст; реконсиляция мержа вне раннера ключуется на `candidate_pr` заявки текущей волны из ледгера, а не на `state.pr`, и ведёт к finalize волны, не в S8. S8 (authoritative gate) — после finalize W5 |
| S11 | **Переоткрытие** (D2/D3) — явная команда оператора `make behaviour-run ARGS='--run-id … --reopen <node>'`: `state.wave` = уровень узла + 1, создаётся ветка волны от base (S9); **файл узла удаляется из worktree и его `author-<node>` сбрасывается** — иначе `_step_authoring` пропустил бы существующий (approved) файл и candidate поехал бы над старыми байтами; узел авторится заново либо, при `--reopen … --manual`, прогон встаёт в `stopped_author` с подсказкой, и оператор правит файл сам; затем штатный гейт волны и candidate над ним (с `source_sha`); `_publish_candidate` каскадом ставит `stale` нижним узлам, уже лежащим в base (D2: их байты не трогаются). Дальше прогон идёт по уровням: каждый `stale` уровень — edge-check против новой версии верхнего + candidate над **теми же байтами** с пересчитанными пинами (переписывать — решение автора, снова `--reopen`). **Цена названа**: переоткрытие requirements = 4 повторных человеческих мержа (W2 сам + W3, W4, W5) |
| S12 | **Число человеческих актов** на бандл: 5 мержей candidate (W1–W5) + approve tasks-спеки = **6**. Сегодня минимум: 1 мерж бандл-PR + 5 candidate (design+acceptance одной заявкой, §I12 разрешает независимые узлы уровня) + 1 approve = 7; S7-прогон 21.09 — 9 (шесть одноузловых candidate, один потерян) |
| S13 | **Режим**: `authoring: waves` в run.json при старте; run.json без поля = прежний путь (один бандл-PR, `spec-loop` восстанавливает его по MERGED бандл-PR — в волнах бандл-PR нет, восстановление из фактов GitHub для волнового прогона идёт по candidate-PR последней волны, ветка `spec/<ws>-approve-*`). Дефолт после двух живых прогонов — волны; прежний путь удаляется отдельным пунктом |

## 3. Машина состояний

### 3.1. Прогон

```
running ─ W1: branch-1 (ветка волны от base) ▶ materialize-brief-1 ▶ author(charter) ▶ commit-1 ▶ gate-1 ▶ edge-1 ▶ push-1
        ▶ candidate-1 (edge-check публикует ревью) ▶ waiting_human_merge(wave=1)
        │ человек мержит candidate
        ▶ resume: finalize-1 (аттестация finalize-PR + approve-node агентом) ▶ approved в base
        ─ W2 … ▶ waiting_human_merge(wave=2) ▶ … ─ W5 … ▶ waiting_human_merge(wave=5)
        ▶ resume: finalize-5 ▶ S8 (authoritative gate) ▶ deliver tasks-PR
```

Стопы внутри волны — существующие (`stopped_author`, `stopped_gate`,
`stopped_review` — включая edge-check, `stopped_dirty`, `stopped_preflight`)
плюс `stopped_stale` (§3.4). Сброс op'ов при resume — по
`_STOPPED_RESET_OPS`, диапазон `commit-<w>`…`candidate-<w>` текущей волны.

`waiting_human_merge` несёт `wave`. Мерж candidate из `stopped_*` до шага
candidate (класс #522) ловится реконсиляцией по `candidate_pr` заявки волны
(S10).

### 3.2. Узел

Статусы — существующие (`draft` → `approval_pending` → `approved`, `stale`
каскадом). Ново только время попадания в base: со своим candidate.

### 3.3. Инварианты

1. Узел уровня k авторится, только когда все узлы уровней < k `approved` в
   base и ни один узел бандла в base не `stale`; узлы в `approval_pending`
   (открытый candidate) — «ещё не одобрен», прогон стоит в
   `waiting_human_merge`, не в `stopped_stale`.
2. Пины `upstream_hashes` считаются по байтам `approved` upstream в base
   (S9 делает worktree равным base по upstream).
3. Edge-check узла выполняется против тех же байтов оснований, что
   запинованы (edge-check D13).
4. Ни один шаг не удаляет и не переписывает узлы **автоматически** (D2);
   `stale` — метка; переписать может автор явным `--reopen`.
5. Candidate мержит только человек (`merge-pr.sh` отказывает на
   candidate-ветках по глобу `patterns.env`).

### 3.4. Переоткрытие и `stopped_stale`

`stopped_stale`: в base есть `stale` узел ниже верхнего `approved` уровня.
Достигается только через `--reopen` (S11) либо чужой candidate над верхним
узлом (например, оператор поднял его руками через `approve_node`). Выход —
по уровням: для каждого `stale` уровня edge-check + candidate + мерж +
finalize; авторинг новых уровней и доставка tasks отказывают, пока есть
`stale` (доставка уже отказывает сегодня: `read_dag_state` → `DEBT_STATUS`).

## 4. Изменения по модулям

| Файл | Правка |
|---|---|
| `governance/bundle_dag.py` | `levels(dag)` публично (сегодня `_levels` в `approve_node`); `dag_upto(dag, level)`; `check_bundle_composition(..., mode="waves")` — префикс по уровням, пустой состав при отсутствии каталога; тест: объединение уровней = состав |
| `governance/runner.py` | волновой режим: `state.wave`, `state.authoring`; per-wave op'ы (S10); `_step_branch` — ветка волны от base (S9), `materialize-brief` per-wave; `_step_authoring` — только узлы `state.wave`, преflight по полному профилю; `_step_gate` — проекция (S5), локальная полнота по уровням; `_step_edge` (S6); `_step_candidate` вместо `_step_pr` (через API `approve_node` с `source_sha`); `resume` — реконсиляция по `candidate_pr` волны, finalize на resume (S8); `--reopen` (S11); `stopped_stale` |
| `governance/approve_node.py` | `source_sha` в заявке и режим S4 (а–е); `_candidate_body` несёт `run-id`; перезапись `head_sha` после evidence-коммита (S7); `_missing_approving_review` и `read_dag_state` без изменений; `levels` переезжает в `bundle_dag` |
| `governance/approval_ledger.py` | `start_request(..., source_sha=None)`; форма записи иначе не меняется |
| `governance/policy_sources.py` | `wave_profile_dir(target_dir, profile, level, run_dir) -> Path` + сверка sha256 копий (S5) |
| `governance/edge_check/` | срез 2 (координатор: ключ D9, попытки D10, актуальность §6.3) и срез 3 (публикация D14–D16 + ревью candidate S7) — задачи плана этой спеки; `edge_check.py` — CLI как сегодня |
| `governance/task_bridge.py` | без изменений (отказ на `stale` уже есть — `DEBT_STATUS`) |
| `governance/spec_loop.py` | сообщения паузы называют волну и candidate-PR; восстановление из фактов GitHub для волнового прогона (S13); `_APPROVE_NODE_HINT`/docstring про три границы — правятся |
| `governance/console_model.py` | волна, статус edge-check по узлам |
| `CLAUDE.md`, `README.md`, `docs/superpowers/specs/2026-09-09-tasks-supersede-contract-design.md` | §I12: заявка волны над байтами ветки прогона (`source_sha`); таблица человеческих актов |

## 5. Отказы и их имена

| Ситуация | Исход |
|---|---|
| upstream уровня не `approved` в base (candidate открыт) | `waiting_human_merge` соответствующей волны |
| `stale` узел в base ниже верхнего approved-уровня | `stopped_stale` (§3.4); доставка tasks — отказ `DEBT_STATUS` как сегодня |
| edge-check `FAIL`/`PENDING` | `stopped_review`, находки в `edge-findings.txt` (файл снимается на входе в шаг — класс #338) |
| edge-check `ERROR` / нет ревьюера / неполный вход | `stopped_review` с `error_code`; обхода нет (D4) |
| проекция профиля не совпала с target (sha256) | `stopped_gate` «проекция не совпала» |
| гейт steward красный на проекции | `stopped_gate` |
| ветка прогона грязная на входе в волну | `stopped_dirty` |
| candidate волны смержен агентом | `invalidated` §I12 |
| candidate смержен человеком до шага candidate | реконсиляция по `candidate_pr` (S10) |
| пути candidate вне объявленной поверхности | отказ до PR (S7) |
| политика подписи недоступна / сменилась | по спеке approval-policy |

## 6. Приёмка

1. Юнит: уровни из DAG; проекция профиля = усечение исходного при
   побайтово равных siblings; инварианты 1–4 красные без правки; заявка с
   `source_sha` читает узел из коммита ветки, не из base; `read_dag_state`
   на неполном base по-прежнему `unresolved` (регрессия B1 круга 2);
   повтор `_publish_candidate` после evidence-коммита не даёт non-ff;
   правило состава принимает base-префикс уровней при `--reopen` и при
   переодобрении без `source_sha`, отказывает на «дыре» в уровнях.
2. Стенд §I12 (`tests/test_governance_approve_node.py`, настоящий git, base
   без нижних узлов): пять волн → пять человеческих мержей → tasks-PR;
   `--reopen requirements` после W4 → `stale` у behaviour-spec, design,
   acceptance → `stopped_stale` → переодобрение по уровням с edge-check →
   продолжение; переоткрытие не переписало ни одного нижнего файла (байты
   сравниваются).
3. Живая приёмка: `spec-loop --brief` на крошечном предмете в волновом
   режиме; evidence — число человеческих актов (ожидается 6), edge-check
   evidence и его ревью на каждом candidate, ноль платных ревью моделью на
   candidate/finalize, ноль правок узлов после одобрения без `--reopen`.

## 7. Риски и компромиссы

- **Проекция профиля копией** (S5): гейт судит по копии, которую пишет
  раннер; sha256-сверка удерживает копию равной target, но прибор всё же
  читает не target. Целевое состояние — флаг steward; до него компромисс
  назван и измерим.
- **Edge-check платный**: один вызов на узел на волну плюс повторы при
  переодобрении; ревью PR при этом моделью не оплачивается (S7).
- **Пять пауз** вместо одной; `spec-loop` их скрывает за повтором команды.
- **Переоткрытие дорого** (S11): каскад по уровням = до четырёх повторных
  мержей. Это цена D2/D3 при неизменном контракте пина.

## 8. Вне объёма

- Адресуемые критерии приёмки (`bundle-docs-as-oracle`).
- Интеграция edge-check с disputatio (D18).
- Флаг `--upto` у steward — заявка соседу планом; смена контракта пина §I12.
- Удаление прежнего пути — после двух живых прогонов (S13).

## 9. Открытые вопросы

Нет. Уточнение D1 подписано 2026-09-22 (шесть актов, контракт пина не
меняется); S1–S13 — решения спеки.
