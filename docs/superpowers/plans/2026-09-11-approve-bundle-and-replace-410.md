# Одобрение бандла и замена spec-runner#410

Операторский runbook, 2026-09-11. Разовый: описывает первое боевое применение
§I12 — одобрения узлов бандла как человеческого акта — на воркстриме
`verify-first-file-scope-group-targets-20260908`.

Контекст: §I12 доставлен целиком (devtools PR #188, #191, #194), карантин снят.
Доставка больше не имеет права одобрять узлы бандла: единственный переход
`draft|stale → approved` — команда `--approve-node`, а подпись берётся из
`mergedBy`/`mergedAt` мержа candidate-PR человеком. Контракт — SSOT
`docs/superpowers/specs/2026-09-09-tasks-supersede-contract-design.md`, §I12.

Проверено на момент написания: spec-runner чист и на `master`, заявок и волн в
леджере прогона нет, бандл полный (6 узлов), поэтому `--legacy-bundle` не нужен.

run-id: `verify-first-file-scope-group-targets-20260908-6d07d5`
Рабочая директория: `devtools/`. Флаг `--legacy-bundle` НЕ нужен — бандл полный, 6 узлов.

## Шаг 0 — обязательно, иначе первый же мерж пропадёт впустую

    export AUTHORIZED_APPROVER_ACCOUNTS=andrei-shtanakov

Переменная сейчас не задана, а пустой список означает «подписать не может никто»:
мерж будет классифицирован FORBIDDEN, заявка уйдёт в invalidated, PR придётся заводить заново.
Держи её экспортированной во всех вызовах ниже.

## Что одобряем

Все шесть узлов. charter и requirements выглядят `approved`, но подписаны до §I12
и не несут `approved_content_hash` — это миграционный долг, гейт их не пропустит.

| уровень | узлы | статус сейчас |
|---|---|---|
| 1 | charter | approved (миграционный долг) |
| 2 | requirements | approved (миграционный долг) |
| 3 | behaviour-spec | draft |
| 4 | design + acceptance | stale, stale |
| 5 | decomposition | draft |

Уровень 4 — ОДНА заявка на два узла: пока candidate-PR открыт, второй вызов
дописывает узел в ту же заявку. То есть мержей не 12, а 10.

## Цикл на каждый уровень

1. `make behaviour-tasks ARGS='--run-id verify-first-file-scope-group-targets-20260908-6d07d5 --approve-node <node-id>'`
   → заводит candidate-PR в spec-runner, печатает его номер.
2. **Мержишь candidate-PR своей учёткой.** Это и есть акт одобрения.
   Через веб-интерфейс или `gh pr merge <N> --repo andrei-shtanakov/spec-runner`
   из своего профиля. **`--repo` обязателен**: рабочая директория здесь —
   `devtools/`, и `gh` без него разрешит номер по remote текущего каталога,
   то есть в devtools. Это не просто «не найдётся»: при совпадении номеров
   мерж уйдёт в чужой PR, а devtools — authority-root-репо.
   НЕ через `merge-pr.sh` — он такие ветки отвергает намеренно.
3. Ту же команду шага 1 повторяешь → записывает подпись, заводит финализирующий PR.
4. **Мержишь финализирующий PR** той же учёткой.
5. **Ту же команду третий раз** — и этот вызов пропускать нельзя. Мерж
   финализирующего PR заявку не закрывает: она остаётся живой на шаге
   `AWAIT_FINALIZE_MERGE`, а до `completed` её доводит только третий вызов.
   Он же — единственное место, где сверяется **конверт**: `approved_by` и
   `approved_at` в base против записанных фактов мержа. Пины к этому моменту
   уже сверялись на втором вызове; эксклюзивна именно сверка конверта, потому
   что узел в `approval_pending` до мержа конверта до неё не доходит. Предикат
   гейта её не делает — он смотрит лишь на непустоту подписи. Пропустишь — сверка не выполнится ни разу, а
   заявки останутся `started` навсегда и при следующем проходе по тому же
   прогону дадут лишний непонятный круг.
6. Следующий уровень.

## Последовательность целиком

    export AUTHORIZED_APPROVER_ACCOUNTS=andrei-shtanakov
    R=verify-first-file-scope-group-targets-20260908-6d07d5

    # уровень 1
    make behaviour-tasks ARGS="--run-id $R --approve-node charter"        # → candidate-PR, мержишь
    make behaviour-tasks ARGS="--run-id $R --approve-node charter"        # → finalize-PR, мержишь
    make behaviour-tasks ARGS="--run-id $R --approve-node charter"        # → заявка completed + сверка конверта

    # уровень 2
    make behaviour-tasks ARGS="--run-id $R --approve-node requirements"   # → candidate-PR, мержишь
    make behaviour-tasks ARGS="--run-id $R --approve-node requirements"   # → finalize-PR, мержишь
    make behaviour-tasks ARGS="--run-id $R --approve-node requirements"   # → заявка completed + сверка конверта

    # уровень 3
    make behaviour-tasks ARGS="--run-id $R --approve-node behaviour-spec" # → candidate-PR, мержишь
    make behaviour-tasks ARGS="--run-id $R --approve-node behaviour-spec" # → finalize-PR, мержишь
    make behaviour-tasks ARGS="--run-id $R --approve-node behaviour-spec" # → заявка completed + сверка конверта

    # уровень 4 — два узла, ОДНА заявка
    make behaviour-tasks ARGS="--run-id $R --approve-node design"         # → candidate-PR
    make behaviour-tasks ARGS="--run-id $R --approve-node acceptance"     # → дописывает в ТОТ ЖЕ PR; мержишь
    make behaviour-tasks ARGS="--run-id $R --approve-node design"         # → finalize-PR, мержишь
    make behaviour-tasks ARGS="--run-id $R --approve-node design"         # → заявка completed + сверка конверта

    # уровень 5
    make behaviour-tasks ARGS="--run-id $R --approve-node decomposition"  # → candidate-PR, мержишь
    make behaviour-tasks ARGS="--run-id $R --approve-node decomposition"  # → finalize-PR, мержишь
    make behaviour-tasks ARGS="--run-id $R --approve-node decomposition"  # → заявка completed + сверка конверта

## Замена #410 — после того, как одобрены все шесть

    make behaviour-tasks ARGS="--run-id $R --supersede --replace-revision 5 \
      --reason 'переиздание v5 сбросило состояние исполнения задач; PR #410 отзывается'"

Ожидаемое в выводе: `comparison: unavailable` — это НЕ ошибка. Записанный anchor
принадлежит эпохе до катовера и несопоставим; эта доставка запишет baseline v2,
дальше сверка станет обычной.

## Если что-то пошло не так

- отказ называет долговые узлы и процедуру — читай его, он полный;
- «факт не установлен, повторите вызов» — **причину не достраивай**: механика её
  намеренно не называет, потому что названная наугад уводит чинить то, что не
  сломано. Повтор ничего не закрывает и не удаляет, но «записей нет» — не
  гарантия: durable-запись могла лечь раньше неподтверждённого сетевого шага.
  Сойдётся повтор тоже не всегда. **Если одно и то же сообщение приходит
  снова, причина не в сети, и состояние тупиковое**: пока над узлом жива
  заявка, новый candidate завести нечем — ветка предложения недостижима, а
  CLI-перехода, терминализующего заявку одобрения, нет (`--abandon-revision`
  относится к ревизиям переиздания, не к заявкам). Реальных выходов два, оба
  человеческие: правка записи заявки в `run.json` — исключение из правила ниже
  — либо новый прогон с другим `--run-id`. Типичный вход: мерж без
  `mergedBy.login`, то есть через интеграцию, а не учёткой;
- заявка ушла в `invalidated` — это терминально, выход один: повторить `--approve-node`
  по тому же узлу, заведётся новый candidate;
- ничего не удаляй и не правь в `run.json` руками — записи неприкосновенны после
  записи. **Единственное исключение** механика называет сама: повреждённый или
  неполный `intent` волны. Её отказ печатает ровно две процедуры — поправить
  запись волны в `run.json` либо начать новый прогон с другим `--run-id`;
  никакой третьей нет, механикой запись волны не чинится.
