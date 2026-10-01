# conductor срез 1, часть B — действия и приёмка. Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** пять действий среза 1 (`owner_queue`, `notify_satisfied`, `nudge`, `pr_nudge`, `close_shipped`) планируются из результата ядра и исполняются исполнителем части A; ступени включения измеримы, приёмка в песочнице подготовлена.

**Architecture:** часть B опирается на часть A (`2026-10-01-conductor-slice-1-plan-a.md`) — она должна быть влита первой. Дополнительные чтения (комментарии с `id`/`updated_at`, timeline и `closedByPullRequestsReferences` открытых issues, активность PR, факты по first-parent истории git, очередь владельца) включаются только флагом `--config`: прогоны среза 0 и их тесты не меняются. Планировщики — чистые функции в `conductor/actions/` над `Result` ядра и `Inputs`; повторная проверка основания перед каждым шагом — замыкания `revalidate(m) -> str | None` по действиям через `conductor/fresh.py` (App-клиент и свежий git): ревизия, доказательство, период, ответ, движение (ревью P1-1); уровень позиции — `authority` по свежему роадмапу (P1-2). До планирования CLI ищет эффекты незавершённых попыток (`conductor/reconcile.py`, P2-3). В профиле `acceptance` зонтик — песочница: мини-флот не читает настоящий зонтик (P2-4). Показатели ступеней — команда `stage-report` над снимками прогонов.

**Tech Stack:** как в части A; git ≥ 2.31 (`--first-parent` подразумевает `--diff-merges=first-parent`).

**Spec:** `docs/superpowers/specs/2026-09-30-conductor-slice-1-design.md` (rev 17) и общая `2026-09-29-conductor-design.md` (rev 12). «§N» — спека среза, «О §N» — общая.

## Global Constraints

- Всё из Global Constraints части A.
- Новые чтения выполняются только при `collect(..., slice1=True)` (CLI передаёт его при `--config`); без него `Inputs` и поведение — как в срезе 0.
- Собственные записи conductor (автор = `ctx.bot_login`) никогда не считаются движением; `updated_at` issue/PR движением не считается (§7.1, §7.3).
- Граф `partial` → планировщики не выдают записей (только заметку `partial`) (О §2.4).
- В ключи маркеров и `effect_key` входит `fact_id`, а не SHA снимка: пункт — первый коммит first-parent, где он стал `[x]`; issue — id события `closed` из timeline (`ClosedEvent.id` = REST `node_id`; решение владельца 2, 2026-10-01: `closedAt` — только для отображения, время не обещает уникальности последовательных закрытий); PR — merge SHA; `date>=D` — `date:D`; `exists` — коммит последнего появления пути (§7.1).
- Шаг 8 каждой записи — `revalidate` своего действия по свежим чтениям; сбой чтения — снятие, не запись (ревью P1-1).
- Адрес доставки — только открытый тред; тредов нет — «канал доставки — срез 1b» (TODO-only), все закрыты — «нет открытого адреса доставки» (решение 5); нумерация и интервал пинка — по маркерам во всех тредах продюсера (решение 4); отредактированный маркер — находка `MK-EDITED` в выдаче, без вопроса владельцу (решение 3).
- Значения маркеров — только через `markers.h1` (часть A).
- Пинок и вопрос — только по позициям с рангом (`rank.rank_of` не `None`); уведомление — без требования ранга (§7.2–7.3, О §5.7).
- `n` маркера пинка — следующий после наибольшего действительного `n` того же `wait`/`p` (или `pr`) (§7.3–7.4).

## Review Focus

1. **Комментарий с маркером, отредактированный человеком** — не считается доставкой: планировщик снова предлагает запись (новая доставка), находка `MK-EDITED` в заметках. Тест — в Task B6 (`test_edited_marker_is_not_delivery`).
2. **Ответ владельца, данный номером варианта, а не именем** (`Q-ab12cd34: 2`) — переводится в имя по порядку вариантов; номер вне диапазона — ответ не учитывается. Тест — Task B4.
3. **Пункт, ставший `[x]`, затем снятый и снова отмеченный** — новый `fact_id`, новое уведомление; повторный прогон без изменений — без дубля. Тест — Task B2 и B6.
4. **PR снова переведён в драфт после пинка** — пинков нет, пока драфт. Тест — Task B7 (`test_draft_and_fresh_red_pr_are_not_nudged`).
5. **Очередь владельца, закрытая самим владельцем** — не переоткрывается, вопросов в неё не пишется, заметка `GR-QUEUE-CLOSED`. Тест — Task B5.
6. **Основание изменилось между планированием и шагом или между двумя шагами** (удалён/конфликтует ответ, переоткрытие, снятая `[x]`, снятый тег, новое движение) — мутаций после изменения нет. Тесты — `test_revalidate_*.py` (B5–B8).
7. **Обрыв после отправки** — следующий процесс находит эффект и закрывает попытку `ok`, без повторной мутации. Тесты — `test_reconcile.py` (B3), `test_cli_slice1.py` (B9).

## Ревью владельца 2026-10-01: где закрыто в части B

| Пункт | Что изменено | Тест, краснеющий на прежнем коде плана |
|---|---|---|
| P1-1 | `revalidate` по действиям: `close_check` (открыт, период, основание (а) — влит и закрывает, (б) — ответ И пересчитанное основание вопроса; комментарий — маркера нет, закрытие — подтверждение есть), `notify_check` (адрес, маркер, тег, факт), `nudge_check` (pending, период `P`, движение), `pr_nudge_check` (свежая потребность PR и упавшие проверки), `queue_check` (очередь, свежие ответы, маркер). Повторное ревью: F1 — `question_basis`, F2 — `marker_check` перед закрытием, F3 — `pr_facts`/`pr_need` | `test_revalidate_close.py` (в т. ч. `test_legacy_basis_rechecked_*`, `test_close_needs_valid_confirmation`, `test_close_completion_from_previous_run_needs_confirmation` — оба воспроизведения владельца), `test_revalidate_notify.py`, `test_revalidate_nudge.py` (в т. ч. `test_pr_nudge_removed_when_need_changes`), `test_revalidate_queue.py` — изменение до первого шага и между шагами, с двойниками |
| P1-2 | `authority(...)` у каждой записи (`ranked` у пинков и закрытия по основанию (а)), уровень субъекта у вопроса очереди | `test_writer.py` (A) + `revalidate`-миры через настоящий `Writer` |
| P2-2 | очередь — одна запись: тело/создание → закрепление (`optional`) → вопросы | `test_owner_queue.py::test_create_pin_and_question_comments`; механизм — `test_writer.py` (A) |
| P2-3 | `reconcile.py` (поиск по `op`/`expected`), вызов в CLI до планирования | `test_reconcile.py`, `test_cli_slice1.py::test_run_settles_found_effects` |
| P2-4 | `umbrella_dir` в `fleet_repos`/`read_manifest`/`read_epics`/`_roadmap`/`collect`, `umbrella_name(cfg)` в CLI; песочница — один клон | `test_collect_slice1.py::test_acceptance_collect_reads_only_sandbox` |
| Решение 2 | `closed_event` (B1), `Inputs.closed_events` (B2), `fact_of`/`fresh_fact` (B3) | `test_sources_gh_slice1.py::test_closed_event_is_last_closed_node_id`, `test_collect_slice1.py::test_closed_issue_records_get_closed_event_ids`, `test_notify.py::test_issue_prerequisite_fact_is_closed_event_id` |
| Решение 3 | `edited_findings` во всех действиях | `test_notify.py::test_edited_marker_is_not_delivery` |
| Решение 4 | `_prior` по всем тредам продюсера | `test_nudge.py::test_prior_nudge_in_closed_address_keeps_interval`, `test_prior_nudge_numbering_continues_in_new_address` |
| Решение 5 | `threads`/`addresses`/`no_address_reason` | `test_notify.py::test_closed_addresses_are_not_channel_1b`, `test_nudge.py::test_all_addresses_closed_is_named` |
| п. 4 ревью | мутационная проверка удаляет настоящие проверки | Task B9 шаг 6, `tools/mutate_check.py`: 47 условий, 0 выживших |

---

## Файлы части B

| Файл | Ответственность |
|---|---|
| `conductor/sources_gh.py` (изм.) | `id`/`updated_at` комментариев, `closedAt` (отображение), merge SHA, драфт, активность PR, упавшие проверки; `issue_extras`, `queue_records`, `closed_event` |
| `conductor/facts.py` | факты и периоды по first-parent истории |
| `conductor/inputs.py` (изм.) | поля `done_facts`, `edge_periods`, `path_added`, `issue_extras`, `closed_events`, `queue_records`, `umbrella_full` |
| `conductor/collect.py`, `conductor/manifest.py` (изм.) | `slice1=True`: факты, extras, id событий закрытия, очередь; `umbrella_dir` — зонтик профиля |
| `conductor/fresh.py` | свежие чтения через App и git для `revalidate` и поиска эффектов |
| `conductor/reconcile.py` | поиск результата незавершённых попыток до планирования (§5.3) |
| `conductor/actions/common.py` | треды и адреса, ключ ожидания, `fact_id` и свежий факт, уровень позиции, движение, маркеры треда |
| `conductor/actions/answers.py` | ответы владельца, канонический набор, производный вопрос |
| `conductor/actions/shipped.py` | основание `close_shipped`, период открытия |
| `conductor/actions/owner_queue.py` | вопросы очереди и записи очереди |
| `conductor/actions/notify.py` | `notify_satisfied` |
| `conductor/actions/nudge.py` | `nudge`, `pr_nudge` |
| `conductor/actions/close.py` | `close_shipped` |
| `conductor/actions/__init__.py` (изм.) | `PlanContext` с клиентом и заметками, сборка плана |
| `conductor/stage_report.py` | показатели ступени |
| `conductor/__main__.py` (изм.) | `--trigger`, `slice1` и зонтик профиля при `--config`, свежий git для шага 8, поиск эффектов до плана, `stage-report`, заметки в снимок |
| `deploy/conductor/*` | примеры конфигов, drop-in тени, `acceptance.sh`, `acceptance_dump.py`, README |
| `docs/conductor/acceptance/*` | песочница и шаблон квитанции |
| `tests/conductor/slice1_fixtures.py` | общие фикстуры части B |
| `tests/conductor/slice1_world.py` | мир тестов последовательностей: планировщик → `Writer` → фейк GitHub и git |

---
### Task B1: дочитывания GitHub для среза 1

**Files:**
- Modify: `conductor/sources_gh.py`
- Test: `tests/conductor/test_sources_gh_slice1.py`

**Interfaces:**
- Produces (в записях `gh_records`): у комментариев — `id: int`, `updated_at: str`; у issue — `closed_at: str | None` (только для отображения); у PR — `merge_sha: str | None`, `is_draft: bool`, `created_at: str`, `red_checks: list[{"name", "url"}]`, `ready_at: str | None`, `last_commit_at: str | None`, `reviews: list[{"author", "submitted_at"}]`. Новые функции: `red_checks(rollup) -> list[dict[str, str]]`; `issue_extras(owner: str, name: str, runner: Runner) -> dict[int, dict] | None` (на номер открытого issue: `created_at`, `period` — id последнего `ReopenedEvent` или `"0"`, `period_start`, `closed_by: list[{"repo", "number", "merged", "merged_at", "merge_sha", "base_is_default"}]`); `queue_records(owner: str, name: str, runner: Runner) -> list[dict] | None` (записи issue с меткой `owner-queue` в любом состоянии, с полями `pinned: bool`, `repo_full: str`); `closed_event(owner: str, name: str, number: int, runner: Runner) -> str | None` — `node_id` последнего события `closed` timeline (= GraphQL `ClosedEvent.id`): `fact_id` закрытия issue (решение владельца 2, 2026-10-01: не `closedAt`); нет события — `""`, сбой — `None`.

- [ ] **Step 1: Write the failing test**

```python
"""Дочитывания GitHub для среза 1 (спека среза 1, §6.1, §7.3–7.5)."""

import json

from conductor.sources_gh import (
    closed_event,
    fetch_record,
    issue_extras,
    queue_records,
    red_checks,
)


def fake(responses: dict[str, list[tuple[int, str, str]]]):
    """Ответы по префиксу команды; список расходуется по порядку."""
    calls: list[str] = []

    def run(args: list[str]) -> tuple[int, str, str]:
        key = " ".join(args)
        calls.append(key)
        for prefix, answers in responses.items():
            if key.startswith(prefix):
                return answers.pop(0) if len(answers) > 1 else answers[0]
        return 1, "", f"unexpected: {key}"

    run.calls = calls  # type: ignore[attr-defined]
    return run


ROLLUP = [
    {
        "__typename": "CheckRun",
        "name": "test",
        "conclusion": "FAILURE",
        "detailsUrl": "https://ci/1",
    },
    {
        "__typename": "StatusContext",
        "context": "lint",
        "state": "ERROR",
        "targetUrl": "https://ci/2",
    },
    {
        "__typename": "CheckRun",
        "name": "ok",
        "conclusion": "SUCCESS",
        "detailsUrl": "https://ci/3",
    },
]
PR_VIEW = json.dumps(
    {
        "title": "p",
        "body": "",
        "state": "OPEN",
        "mergedAt": None,
        "author": {"login": "u"},
        "labels": [],
        "updatedAt": "2026-09-30T00:00:00Z",
        "url": "https://x/pull/7",
        "closingIssuesReferences": [],
        "headRefOid": "abc",
        "reviewDecision": None,
        "statusCheckRollup": ROLLUP,
        "mergeCommit": None,
        "isDraft": False,
        "createdAt": "2026-09-20T00:00:00Z",
    }
)
PR_EXTRA = json.dumps(
    {
        "data": {
            "repository": {
                "pullRequest": {
                    "headRefOid": "abc",
                    "latestReviews": {
                        "totalCount": 1,
                        "nodes": [
                            {
                                "state": "COMMENTED",
                                "submittedAt": "2026-09-25T00:00:00Z",
                                "author": {"login": "rev"},
                                "commit": {"oid": "abc"},
                            }
                        ],
                    },
                    "files": {"totalCount": 0, "nodes": []},
                    "commits": {
                        "nodes": [{"commit": {"committedDate": "2026-09-24T00:00:00Z"}}]
                    },
                    "timelineItems": {"nodes": [{"createdAt": "2026-09-22T00:00:00Z"}]},
                }
            }
        }
    }
)
COMMENTS = json.dumps(
    [
        [
            {
                "id": 5,
                "user": {"login": "u"},
                "body": "c",
                "created_at": "t1",
                "updated_at": "t2",
            }
        ]
    ]
)


def test_red_checks_names_and_links() -> None:
    assert red_checks(ROLLUP) == [
        {"name": "test", "url": "https://ci/1"},
        {"name": "lint", "url": "https://ci/2"},
    ]
    assert red_checks(None) == []


def test_pr_record_carries_activity_and_ids() -> None:
    run = fake(
        {
            "pr view 7": [(0, PR_VIEW, "")],
            "api --paginate --slurp repos/own/a/issues/7/comments": [(0, COMMENTS, "")],
            "api graphql": [(0, PR_EXTRA, "")],
        }
    )
    rec = fetch_record("own", "a", 7, True, run)
    assert rec is not None
    assert rec["comments"] == [
        {"id": 5, "author": "u", "body": "c", "created_at": "t1", "updated_at": "t2"}
    ]
    assert rec["is_draft"] is False and rec["created_at"] == "2026-09-20T00:00:00Z"
    assert rec["ready_at"] == "2026-09-22T00:00:00Z"
    assert rec["last_commit_at"] == "2026-09-24T00:00:00Z"
    assert rec["reviews"] == [{"author": "rev", "submitted_at": "2026-09-25T00:00:00Z"}]
    assert [c["name"] for c in rec["red_checks"]] == ["test", "lint"]
    assert rec["merge_sha"] is None


def _extras_page(numbers: list[int], has_next: bool, total: int = 1) -> str:
    return json.dumps(
        {
            "data": {
                "repository": {
                    "defaultBranchRef": {"name": "master"},
                    "issues": {
                        "pageInfo": {"hasNextPage": has_next, "endCursor": "CUR"},
                        "nodes": [
                            {
                                "number": n,
                                "createdAt": "2026-09-01T00:00:00Z",
                                "closedByPullRequestsReferences": {
                                    "totalCount": total,
                                    "nodes": [
                                        {
                                            "number": 50 + n,
                                            "merged": True,
                                            "mergedAt": "2026-09-10T00:00:00Z",
                                            "mergeCommit": {"oid": f"m{n}"},
                                            "baseRefName": "master",
                                            "baseRepository": {
                                                "defaultBranchRef": {"name": "master"}
                                            },
                                            "repository": {"name": "a"},
                                        }
                                    ],
                                },
                                "timelineItems": {
                                    "nodes": [
                                        {
                                            "id": "RE_1",
                                            "createdAt": "2026-09-15T00:00:00Z",
                                        }
                                    ]
                                    if n == 2
                                    else []
                                },
                            }
                            for n in numbers
                        ],
                    },
                }
            }
        }
    )


def test_issue_extras_pages_and_periods() -> None:
    run = fake(
        {
            "api graphql -f query=query($o:String!,$n:String!,$c:String)": [
                (0, _extras_page([1], True), ""),
                (0, _extras_page([2], False), ""),
            ]
        }
    )
    extras = issue_extras("own", "a", run)
    assert extras is not None and set(extras) == {1, 2}
    assert extras[1]["period"] == "0"
    assert extras[1]["period_start"] == "2026-09-01T00:00:00Z"
    assert extras[2]["period"] == "RE_1"
    assert extras[2]["period_start"] == "2026-09-15T00:00:00Z"
    assert extras[1]["closed_by"][0] == {
        "repo": "a",
        "number": 51,
        "merged": True,
        "merged_at": "2026-09-10T00:00:00Z",
        "merge_sha": "m1",
        "base_is_default": True,
    }
    assert any("c=CUR" in c for c in run.calls)  # type: ignore[attr-defined]


def test_issue_extras_truncated_is_unread() -> None:
    run = fake(
        {
            "api graphql -f query=query($o:String!,$n:String!,$c:String)": [
                (0, _extras_page([1], False, total=21), "")
            ]
        }
    )
    assert issue_extras("own", "a", run) is None


ISSUE_VIEW = json.dumps(
    {
        "title": "Очередь",
        "body": "b",
        "state": "CLOSED",
        "stateReason": "COMPLETED",
        "author": {"login": "conductor[bot]"},
        "labels": [{"name": "owner-queue"}],
        "updatedAt": "u",
        "url": "https://x/issues/3",
        "closedAt": "2026-09-30T00:00:00Z",
    }
)


def test_queue_records_any_state_with_pinned() -> None:
    search = json.dumps(
        [{"total_count": 1, "incomplete_results": False, "items": [{"number": 3}]}]
    )
    pinned = json.dumps({"data": {"repository": {"issue": {"isPinned": True}}}})
    run = fake(
        {
            "api -X GET search/issues -f q=user:own repo:own/ws label:owner-queue": [
                (0, search, "")
            ],
            "issue view 3 -R own/ws": [(0, ISSUE_VIEW, "")],
            "api --paginate --slurp repos/own/ws/issues/3/comments": [(0, "[[]]", "")],
            "api graphql": [(0, pinned, "")],
        }
    )
    [rec] = queue_records("own", "ws", run) or [None]
    assert rec is not None and rec["pinned"] is True and rec["repo_full"] == "own/ws"
    assert rec["state"] == "closed" and rec["closed_at"] == "2026-09-30T00:00:00Z"


def test_closed_event_is_last_closed_node_id() -> None:
    """fact_id закрытия — id события closed (решение владельца 2), не closedAt."""
    timeline = json.dumps(
        [
            [
                {"event": "closed", "node_id": "CE_1"},
                {"event": "reopened", "node_id": "RE_1"},
                {"event": "closed", "node_id": "CE_2"},
            ]
        ]
    )
    path = "api --paginate --slurp repos/own/a/issues/7/timeline?per_page=100"
    assert closed_event("own", "a", 7, fake({path: [(0, timeline, "")]})) == "CE_2"
    assert closed_event("own", "a", 7, fake({path: [(0, "[[]]", "")]})) == ""
    assert closed_event("own", "a", 7, fake({path: [(1, "", "boom")]})) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_sources_gh_slice1.py -q`
Expected: FAIL — `ImportError: cannot import name 'issue_extras'`.

- [ ] **Step 3: Write minimal implementation**

Применить к `conductor/sources_gh.py` (поля `closedAt`/`mergeCommit`/`isDraft`/`createdAt`, расширенный `PR_EXTRA`, `id`/`updated_at` комментариев, `red_checks`, активность PR, `issue_extras`, `queue_records`, `closed_event`):

```diff
diff --git a/conductor/sources_gh.py b/conductor/sources_gh.py
index fc3a710..3e725a7 100644
--- a/conductor/sources_gh.py
+++ b/conductor/sources_gh.py
@@ -20,16 +20,34 @@ from conductor.model import SourceState
 
 GH_TIMEOUT = 120
 Runner = Callable[[list[str]], tuple[int, str, str]]
-ISSUE_FIELDS = "title,body,state,stateReason,author,labels,updatedAt,url"
+ISSUE_FIELDS = "title,body,state,stateReason,author,labels,updatedAt,url,closedAt"
 PR_FIELDS = (
     "title,body,state,mergedAt,author,labels,updatedAt,url,"
-    "closingIssuesReferences,headRefOid,reviewDecision,statusCheckRollup"
+    "closingIssuesReferences,headRefOid,reviewDecision,statusCheckRollup,"
+    "mergeCommit,isDraft,createdAt"
 )
 PR_EXTRA = (
     "query($o:String!,$n:String!,$k:Int!){repository(owner:$o,name:$n)"
     "{pullRequest(number:$k){headRefOid latestReviews(first:50)"
-    "{totalCount nodes{state commit{oid}}}"
-    " files(first:100){totalCount nodes{path}}}}}"
+    "{totalCount nodes{state submittedAt author{login} commit{oid}}}"
+    " files(first:100){totalCount nodes{path}}"
+    " commits(last:1){nodes{commit{committedDate}}}"
+    " timelineItems(itemTypes:[READY_FOR_REVIEW_EVENT],last:1)"
+    "{nodes{... on ReadyForReviewEvent{createdAt}}}}}}"
+)
+ISSUE_EXTRAS = (
+    "query($o:String!,$n:String!,$c:String){repository(owner:$o,name:$n)"
+    "{defaultBranchRef{name} issues(states:OPEN,first:50,after:$c)"
+    "{pageInfo{hasNextPage endCursor} nodes{number createdAt"
+    " closedByPullRequestsReferences(first:20,includeClosedPrs:true)"
+    "{totalCount nodes{number merged mergedAt mergeCommit{oid} baseRefName"
+    " baseRepository{defaultBranchRef{name}} repository{name}}}"
+    " timelineItems(itemTypes:[REOPENED_EVENT],last:1)"
+    "{nodes{... on ReopenedEvent{id createdAt}}}}}}}"
+)
+PINNED = (
+    "query($o:String!,$n:String!,$k:Int!){repository(owner:$o,name:$n)"
+    "{issue(number:$k){isPinned}}}"
 )
 RED = {
     "FAILURE",
@@ -86,6 +104,26 @@ def ci_state(rollup: list[dict[str, Any]] | None) -> str:
     return "green" if all(s in GREEN for s in states) else "pending"
 
 
+def red_checks(rollup: list[dict[str, Any]] | None) -> list[dict[str, str]]:
+    """Упавшие проверки head SHA: имя и ссылка (лог не читается, §7.4)."""
+    out = []
+    for c in rollup or []:
+        state = (
+            (
+                c.get("state")
+                if c.get("__typename") == "StatusContext"
+                else c.get("conclusion") or c.get("status")
+            )
+            or ""
+        ).upper()
+        if state in RED:
+            name = c.get("name") or c.get("context") or "?"
+            out.append(
+                {"name": name, "url": c.get("detailsUrl") or c.get("targetUrl") or ""}
+            )
+    return out
+
+
 def _json(out: str | None, default: str = "null") -> Any:
     """JSON ответа gh; битый ответ при коде 0 — None (сбой чтения, не падение)."""
     try:
@@ -156,9 +194,11 @@ def _comments(
         return None
     return [
         {
+            "id": c.get("id"),
             "author": (c.get("user") or {}).get("login", ""),
             "body": c.get("body") or "",
             "created_at": c.get("created_at", ""),
+            "updated_at": c.get("updated_at", ""),
         }
         for page in pages
         for c in page
@@ -196,12 +236,23 @@ def _pr_extra(
         for r in reviews
     ) and not any(r["state"] == "CHANGES_REQUESTED" for r in reviews)
     files = [f["path"] for f in pr["files"]["nodes"]]
+    commits = (pr.get("commits") or {}).get("nodes") or []
+    ready = (pr.get("timelineItems") or {}).get("nodes") or []
     return {
         "extra_head": head,
         "approved_at_head": approved,
         "files": files,
         "complete": pr["files"]["totalCount"] <= len(files)
         and pr["latestReviews"]["totalCount"] <= len(reviews),
+        "last_commit_at": commits[-1]["commit"]["committedDate"] if commits else None,
+        "ready_at": ready[-1].get("createdAt") if ready else None,
+        "reviews": [
+            {
+                "author": (r.get("author") or {}).get("login", ""),
+                "submitted_at": r.get("submittedAt") or "",
+            }
+            for r in reviews
+        ],
     }
 
 
@@ -233,6 +284,7 @@ def fetch_record(
         "labels": [lab["name"] for lab in raw.get("labels", [])],
         "updated_at": raw.get("updatedAt", ""),
         "url": raw.get("url", ""),
+        "closed_at": raw.get("closedAt"),
         "comments": comments,
         "closing_refs": [
             f"{r['repository']['name']}#{r['number']}"
@@ -249,6 +301,10 @@ def fetch_record(
             head_sha=raw.get("headRefOid"),
             review_decision=raw.get("reviewDecision"),
             ci=ci_state(raw.get("statusCheckRollup")),
+            merge_sha=(raw.get("mergeCommit") or {}).get("oid"),
+            is_draft=bool(raw.get("isDraft")),
+            created_at=raw.get("createdAt", ""),
+            red_checks=red_checks(raw.get("statusCheckRollup")),
             **extra,
         )
     return record
@@ -332,3 +388,105 @@ def collect_gh(
     return GhResult(
         list(records.values()), "error", f"ссылки не сошлись за {max_hops} шага"
     )
+
+
+def _extras_node(node: dict[str, Any]) -> dict[str, Any] | None:
+    refs = node["closedByPullRequestsReferences"]
+    if refs["totalCount"] > len(refs["nodes"]):
+        return None  # усечено — основание не доказано (§7.5)
+    reopened = node["timelineItems"]["nodes"]
+    return {
+        "created_at": node["createdAt"],
+        "period": reopened[-1]["id"] if reopened else "0",
+        "period_start": reopened[-1]["createdAt"] if reopened else node["createdAt"],
+        "closed_by": [
+            {
+                "repo": r["repository"]["name"],
+                "number": r["number"],
+                "merged": bool(r["merged"]),
+                "merged_at": r.get("mergedAt"),
+                "merge_sha": (r.get("mergeCommit") or {}).get("oid"),
+                "base_is_default": r["baseRefName"]
+                == ((r.get("baseRepository") or {}).get("defaultBranchRef") or {}).get(
+                    "name"
+                ),
+            }
+            for r in refs["nodes"]
+        ],
+    }
+
+
+def issue_extras(
+    owner: str, name: str, runner: Runner
+) -> dict[int, dict[str, Any]] | None:
+    """Открытые issues репо: период открытия и закрывающие PR (§7.5); сбой — None."""
+    extras: dict[int, dict[str, Any]] = {}
+    cursor: str | None = None
+    for _ in range(50):
+        args = ["api", "graphql", "-f", f"query={ISSUE_EXTRAS}", "-f", f"o={owner}"]
+        args += ["-f", f"n={name}"] + (["-f", f"c={cursor}"] if cursor else [])
+        code, out, _ = runner(args)
+        try:
+            page = (_json(out) if code == 0 else None)["data"]["repository"]["issues"]
+        except (KeyError, TypeError):
+            return None
+        for node in page["nodes"]:
+            extra = _extras_node(node)
+            if extra is None:
+                return None
+            extras[node["number"]] = extra
+        if not page["pageInfo"]["hasNextPage"]:
+            return extras
+        cursor = page["pageInfo"]["endCursor"]
+    return None
+
+
+def queue_records(owner: str, name: str, runner: Runner) -> list[dict[str, Any]] | None:
+    """Issues очереди владельца в любом состоянии (§6.1); сбой — None."""
+    items, _ = _search(owner, f"repo:{owner}/{name} label:owner-queue is:issue", runner)
+    if items is None:
+        return None
+    out = []
+    for item in items:
+        rec = fetch_record(owner, name, item["number"], False, runner)
+        if rec is None:
+            return None
+        code, raw, _ = runner(
+            ["api", "graphql", "-f", f"query={PINNED}", "-f", f"o={owner}"]
+            + ["-f", f"n={name}", "-F", f"k={item['number']}"]
+        )
+        try:
+            rec["pinned"] = bool(
+                (_json(raw) if code == 0 else None)["data"]["repository"]["issue"][
+                    "isPinned"
+                ]
+            )
+        except (KeyError, TypeError):
+            return None
+        rec["repo_full"] = f"{owner}/{name}"
+        out.append(rec)
+    return out
+
+
+def closed_event(owner: str, name: str, number: int, runner: Runner) -> str | None:
+    """id (node_id) последнего события `closed` timeline issue — fact_id
+    закрытия (§7.1, решение владельца 2026-10-01: не `closedAt`); нет
+    события — "", сбой — None."""
+    code, out, _ = runner(
+        [
+            "api",
+            "--paginate",
+            "--slurp",
+            f"repos/{owner}/{name}/issues/{number}/timeline?per_page=100",
+        ]
+    )
+    pages = _json(out, "[]") if code == 0 else None
+    if not isinstance(pages, list):
+        return None
+    ids = [
+        e.get("node_id") or ""
+        for page in pages
+        for e in page
+        if isinstance(e, dict) and e.get("event") == "closed"
+    ]
+    return ids[-1] if ids else ""
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/conductor/test_sources_gh_slice1.py tests/conductor/test_sources_gh.py tests/conductor/test_collect.py -q`
Expected: PASS (новые и прежние тесты источника).

- [ ] **Step 5: Commit**

```bash
git add conductor/sources_gh.py tests/conductor/test_sources_gh_slice1.py
git commit -m "conductor: дочитывания GitHub среза 1 — активность PR, периоды, очередь (§6.1, §7.3–7.5)"
```

---

### Task B2: факты по first-parent истории и сбор `slice1`

**Files:**
- Create: `conductor/facts.py`
- Modify: `conductor/inputs.py`, `conductor/collect.py`, `conductor/manifest.py`
- Test: `tests/conductor/test_facts.py`, `tests/conductor/test_collect_slice1.py`

**Interfaces:**
- Consumes: `sources_git.git`, `sources_git.GitError`, `sources_git._bounded`; `issue_extras`, `queue_records` (B1).
- Produces: `facts.first_done_commit(repo_dir: Path, ref: str, repo: str, item: str) -> str | None`; `facts.edge_period(repo_dir: Path, ref: str, repo: str, item: str, raw: str) -> tuple[str, str] | None` (sha, ISO-дата коммита); `facts.path_added(repo_dir: Path, ref: str, path: str) -> str | None`; все при сбое git — `GitError`; `facts.ItemState(done, blocked_by: frozenset[str], trigger)` и `facts.item_state(text, repo, item) -> ItemState | None` — пункт `todo://<repo>/<item>` ТЕМ ЖЕ каноническим парсером `plan_fields`, что строит узлы ядра (раунд 3 ревью: `@id` в бэктиках или прозе — не пункт); история фактов и все свежие перепроверки пункта идут только через него, regex-разбора строки пункта нет; дубликат `@id` (`PF-ID-DUPLICATE`) — `facts.AmbiguousItem`: ни выполненным, ни ребром не считается, свежая проверка отказывает (раунд 4, R4-1); история перебирает ВСЕ версии TODO.md first-parent (`_log`/`_transitions`), без отбора коммитов по тексту `@id` — отбор не полон относительно грамматики (`@id:"…"`, раунд 4, R4-2); читает лениво до найденного перехода, голова фиксируется по SHA, журнал и тексты версий кэшируются по SHA, разбор — по тексту (раунд 5, R5-1: 10 запросов к 41 версии — 13 вызовов git); `facts.TODO`. Новые поля `Inputs` (по умолчанию пустые): `done_facts: dict[str, str]` (node_id пункта → sha), `edge_periods: dict[str, list[str]]` (`"<src>|<raw>"` → `[sha, date]`), `path_added: dict[str, str]` (текст `@trigger` → sha), `issue_extras: dict[str, dict]` (`"<repo-key>#<N>"` → extras), `closed_events: dict[str, str]` (`"<repo-key>#<N>"` закрытого issue → id события `closed`), `queue_records: list[dict]`, `umbrella_full: str`. `collect(..., slice1: bool = False, umbrella_dir: str = UMBRELLA)`; зонтик профиля (ревью P2-4) — параметр `umbrella_dir` у `manifest.fleet_repos(text, umbrella)`, `collect.read_manifest`, `read_epics`, `_roadmap`, `umbrella_full`: в `acceptance` мини-флот читает только песочницу, настоящий зонтик не добавляется и не читается.

- [ ] **Step 1: Write the failing test**

```python
"""Факты по first-parent истории (спека среза 1, §7.1, §7.3)."""

import subprocess
from pathlib import Path

import pytest

from conductor import facts
from conductor.facts import (
    AmbiguousItem,
    edge_period,
    first_done_commit,
    item_state,
    path_added,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "master")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def commit(repo: Path, todo: str, msg: str, extra: dict[str, str] | None = None) -> str:
    (repo / "TODO.md").write_text(todo, encoding="utf-8")
    for path, text in (extra or {}).items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def test_first_done_commit_is_latest_transition(repo: Path) -> None:
    commit(repo, "- [ ] x @id:x\n", "open")
    done1 = commit(repo, "- [x] x @id:x\n", "done")
    commit(repo, "- [x] x правка заголовка @id:x\n", "title")
    assert first_done_commit(repo, "master", "a", "x") == done1
    commit(repo, "- [ ] x @id:x\n", "reopen")
    done2 = commit(repo, "- [x] x @id:x\n", "done again")
    assert first_done_commit(repo, "master", "a", "x") == done2


def test_first_done_ignores_other_items(repo: Path) -> None:
    commit(repo, "- [ ] x @id:x\n- [ ] x2 @id:x2\n", "open")
    done = commit(repo, "- [x] x @id:x\n- [ ] x2 @id:x2\n", "x done")
    commit(repo, "- [x] x @id:x\n- [x] x2 @id:x2\n", "x2 done")
    assert first_done_commit(repo, "master", "a", "x") == done


def test_edge_period_survives_title_edit_and_resets_on_readd(repo: Path) -> None:
    commit(repo, "- [ ] c @id:c\n", "no edge")
    added = commit(repo, "- [ ] c @id:c @blocked_by:todo://p/y\n", "edge")
    commit(repo, "- [ ] c новый заголовок @id:c @blocked_by:todo://p/y\n", "title")
    period = edge_period(repo, "master", "a", "c", "todo://p/y")
    assert period is not None and period[0] == added
    commit(repo, "- [ ] c @id:c\n", "drop")
    readded = commit(repo, "- [ ] c @id:c @blocked_by:todo://p/y\n", "readd")
    period = edge_period(repo, "master", "a", "c", "todo://p/y")
    assert period is not None and period[0] == readded


def test_edge_period_ignores_prefix_collision(repo: Path) -> None:
    commit(repo, "- [ ] c @id:c @blocked_by:todo://p/yy\n", "other edge")
    added = commit(
        repo, "- [ ] c @id:c @blocked_by:todo://p/yy @blocked_by:todo://p/y\n", "edge"
    )
    period = edge_period(repo, "master", "a", "c", "todo://p/y")
    assert period is not None and period[0] == added


def test_path_added_last_appearance(repo: Path) -> None:
    commit(repo, "x\n", "base")
    first = commit(repo, "x\n", "add", {"docs/путь.md": "1"})
    assert path_added(repo, "master", "docs/путь.md") == first
    (repo / "docs" / "путь.md").unlink()
    _git(repo, "commit", "-qam", "rm")
    again = commit(repo, "x\n", "readd", {"docs/путь.md": "2"})
    assert path_added(repo, "master", "docs/путь.md") == again
    assert path_added(repo, "master", "absent.md") is None


def test_backticked_id_is_not_an_item(repo: Path) -> None:
    """Канонический разбор (как узлы ядра): `@id` в бэктиках — не пункт,
    ни в истории фактов, ни в свежей перепроверке (раунд 3 ревью, F1)."""
    commit(repo, "- [ ] x @id:x\n", "open")
    commit(repo, "- [x] x `@id:x`\n", "quoted, done")
    assert first_done_commit(repo, "master", "a", "x") is None
    assert item_state("- [x] x `@id:x`\n", "a", "x") is None
    state = item_state("- [x] x @id:x @blocked_by:todo://p/y\n", "a", "x")
    assert state is not None and state.done and state.blocked_by == {"todo://p/y"}


def test_history_covers_quoted_id_and_refuses_duplicates(repo: Path) -> None:
    """Раунд 4 (R4-2, R4-1): история перебирает ВСЕ версии TODO (не отбор по
    `@id:x`): квотированный id находит новый факт; дубликат id — не факт."""
    commit(repo, "- [ ] x @id:x\n", "open")
    commit(repo, "- [x] x @id:x\n", "done")
    commit(repo, '- [x] x @id:"x"\n', "quote")
    commit(repo, '- [ ] x @id:"x"\n', "reopen")
    latest = commit(repo, '- [x] x @id:"x"\n', "done again")
    assert first_done_commit(repo, "master", "a", "x") == latest
    commit(repo, '- [x] x @id:"x"\n- [ ] x @id:x\n', "duplicate")
    assert first_done_commit(repo, "master", "a", "x") == latest  # не новый факт
    with pytest.raises(AmbiguousItem):
        item_state("- [x] x @id:x\n- [ ] x @id:x\n", "a", "x")


def test_history_reads_are_lazy_and_cached(repo: Path, monkeypatch) -> None:
    """Раунд 5 (Codex, R5-1): полнота истории без повторного чтения всех
    версий — голова фиксируется по SHA, журнал и тексты кэшируются, версии
    читаются до найденного перехода. 40 версий, 10 запросов к той же голове."""
    for k in range(40):
        commit(repo, f"# заголовок {k}\n", f"title {k}")
    done = commit(repo, "# заголовок\n- [x] done @id:goal\n", "done")
    calls: list[tuple[str, ...]] = []
    real = facts.git

    def counted(repo_dir: Path, *args: str) -> tuple[int, str, str]:
        calls.append(args)
        return real(repo_dir, *args)

    monkeypatch.setattr(facts, "git", counted)
    for _ in range(10):
        assert first_done_commit(repo, "master", "a", "goal") == done
    assert sum(a[0] == "log" for a in calls) == 1  # журнал — один раз
    assert sum(a[0] == "show" for a in calls) <= 2  # HEAD и его родитель
    assert len(calls) <= 13
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_facts.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'conductor.facts'`.

- [ ] **Step 3: Write `conductor/facts.py`**

```python
"""Идентичность фактов и периодов по first-parent истории (спека среза 1, §7.1, §7.3).

Факт выполнения пункта — последний коммит first-parent истории, в котором
пункт стал `[x]`; период ожидания — последний коммит, в котором ребро
появилось. Правка заголовка их не меняет. Требует git ≥ 2.31.

Пункт и его теги читаются ТЕМ ЖЕ каноническим парсером `plan-fields`, что
строит узлы ядра (`item_state`): упоминание `@id` в бэктиках или прозе — не
пункт, ни в истории, ни в свежей перепроверке перед записью.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import plan_fields as pf

from conductor.manifest import manifest_index
from conductor.sources_git import GitError, git

TODO = "TODO.md"


@dataclass(frozen=True)
class ItemState:
    """Пункт TODO по каноническому разбору: выполнен, рёбра, триггер."""

    done: bool
    blocked_by: frozenset[str]
    trigger: str | None


def _checked(repo_dir: Path, *args: str) -> str:
    code, out, err = git(repo_dir, *args)
    if code != 0:
        raise GitError(f"{repo_dir.name}: git {args[0]}: {err.strip() or code}")
    return out


@cache
def _index(repo: str) -> Any:
    """Индекс манифеста из одного репо: разбору пунктов этого TODO хватает."""
    return manifest_index(
        f'[cores.{repo}]\nrepo_url = "git@github.com:x/{repo}.git"\n'
        f'git_dir = "{repo}"\n'
    )


class AmbiguousItem(Exception):
    """`@id` пункта встречается в TODO больше одного раза (PF-ID-DUPLICATE):
    состояние пункта не доказано — ни выполненным, ни ребром его не считать."""


AMBIGUOUS = object()


@cache
def _items(text: str, repo: str) -> dict[str, Any]:
    """Все пункты версии TODO каноническим разбором: node_id → ItemState или
    AMBIGUOUS (дубликат id). Кэш по тексту: одна версия разбирается один раз."""
    snapshot = pf.parse_fleet(
        [pf.RepoInput(repo, todo_text=text, commit="fresh", available=True)],
        _index(repo),
    )
    refs: dict[str, set[str]] = {}
    for r in snapshot["references"]:
        if r["kind"] == "blocked_by":
            refs.setdefault(r["source_node_id"], set()).add(r.get("raw_ref") or "")
    out: dict[str, Any] = {}
    for n in snapshot["nodes"]:
        nid = n["node_id"]
        state = ItemState(
            n["declared_status"] != "open",
            frozenset(refs.get(nid, ())),
            n.get("trigger"),
        )
        out[nid] = AMBIGUOUS if nid in out else state
    return out


def item_state(text: str | None, repo: str, item: str) -> ItemState | None:
    """Пункт `todo://<repo>/<item>` в тексте TODO.md — каноническим разбором
    `plan_fields` (как узлы ядра); нет такого пункта — None; дубликат id —
    AmbiguousItem (ядро взяло бы последний узел молча — писать по нему нельзя)."""
    state = _items(text or "", repo).get(f"todo://{repo}/{item}")
    if state is AMBIGUOUS:
        raise AmbiguousItem(f"todo://{repo}/{item}: @id встречается дважды")
    return state


def _state(text: str | None, repo: str, item: str) -> ItemState | None:
    """Для истории: неоднозначный пункт — как отсутствующий (то же правило,
    что у свежей проверки: ни выполненным, ни ребром не считается)."""
    try:
        return item_state(text, repo, item)
    except AmbiguousItem:
        return None


def _file_at(repo_dir: Path, rev: str) -> str | None:
    """TODO.md в ревизии; ревизии нет (корневой коммит) или файла нет — None."""
    if git(repo_dir, "rev-parse", "-q", "--verify", f"{rev}^{{commit}}")[0] != 0:
        return None
    code, out, _ = git(repo_dir, "show", f"{rev}:{TODO}")
    return out if code == 0 else None


@cache
def _blob(repo_dir: Path, sha: str) -> str | None:
    """TODO.md в коммите sha (неизменяем — кэш по SHA); файла нет — None."""
    code, out, _ = git(repo_dir, "show", f"{sha}:{TODO}")
    return out if code == 0 else None


@cache
def _log(repo_dir: Path, head: str) -> tuple[tuple[str, str], ...]:
    """(sha, дата) всех first-parent коммитов, менявших TODO.md, от head
    (SHA — неизменяемый ключ кэша), новые первыми. Без отбора по тексту
    `@id`: отбор не полон относительно грамматики парсера (кавычки и т. п.)."""
    out = _checked(
        repo_dir, "log", "--first-parent", "--format=%H %cI", head, "--", TODO
    )
    return tuple(
        (sha, date) for sha, date in (ln.split(" ", 1) for ln in out.splitlines() if ln)
    )


def _transitions(
    repo_dir: Path, ref: str, holds: Callable[[str | None], bool]
) -> tuple[str, str] | None:
    """Последний коммит, в котором holds(текст TODO.md) стало истинным (в его
    первом родителе — ложно): (sha, дата) или None. Перебираются ВСЕ версии
    файла, но лениво — до найденного перехода; ref фиксируется в SHA, журнал и
    тексты версий кэшируются по SHA (повторный запрос к той же голове не
    повторяет чтений git)."""
    head = _checked(repo_dir, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()
    rows = _log(repo_dir, head)
    for k, (sha, date) in enumerate(rows):
        if k + 1 < len(rows):
            before = _blob(repo_dir, rows[k + 1][0])  # TODO.md первого родителя
        else:  # самая старая версия: в родителе файла не было (или корень)
            before = _file_at(repo_dir, f"{sha}^")
        if holds(_blob(repo_dir, sha)) and not holds(before):
            return sha, date
    return None


def first_done_commit(repo_dir: Path, ref: str, repo: str, item: str) -> str | None:
    """Последний коммит, где пункт стал [x]; None — переход не найден."""

    def done(text: str | None) -> bool:
        state = _state(text, repo, item)
        return state is not None and state.done

    found = _transitions(repo_dir, ref, done)
    return found[0] if found else None


def edge_period(
    repo_dir: Path, ref: str, repo: str, item: str, raw: str
) -> tuple[str, str] | None:
    """Период ожидания: последнее появление ребра (sha, дата коммита)."""

    def edge(text: str | None) -> bool:
        state = _state(text, repo, item)
        return state is not None and raw in state.blocked_by

    return _transitions(repo_dir, ref, edge)


def path_added(repo_dir: Path, ref: str, path: str) -> str | None:
    """Последний коммит first-parent истории, добавивший путь; нет — None."""
    out = _checked(
        repo_dir,
        "log",
        "--first-parent",
        "--diff-filter=A",
        "--format=%H",
        "-1",
        ref,
        "--",
        path,
    )
    return out.strip() or None
```

- [ ] **Step 4: Run the facts test**

Run: `uv run pytest tests/conductor/test_facts.py -q`
Expected: PASS.

- [ ] **Step 5: Extend `Inputs` and `collect`**

Применить к `conductor/inputs.py`, `conductor/collect.py`, `conductor/manifest.py` (поля `Inputs`, `_slice1` с id событий закрытия, параметр `umbrella_dir` по цепочке чтения зонтика; локальная `facts` в `collect` переименована в `trigger_facts`, чтобы не затенять модуль):

```diff
diff --git a/conductor/collect.py b/conductor/collect.py
index b2bbe5a..78aa2b4 100644
--- a/conductor/collect.py
+++ b/conductor/collect.py
@@ -14,9 +14,11 @@ from typing import Any
 
 import plan_fields as pf
 
+from conductor import facts
 from conductor.graph import local_refs, referenced_issues
 from conductor.inputs import Inputs, RepoTodo
 from conductor.manifest import (
+    _URL_RE,
     UMBRELLA,
     FleetRepo,
     fleet_repos,
@@ -24,12 +26,19 @@ from conductor.manifest import (
     manifest_index,
 )
 from conductor.model import SourceState
-from conductor.sources_gh import Runner, collect_gh
+from conductor.sources_gh import (
+    Runner,
+    closed_event,
+    collect_gh,
+    issue_extras,
+    queue_records,
+)
 from conductor.sources_git import (
     GitError,
     default_ref,
     ever_had,
     fetch,
+    git,
     last_commit_mentioning,
     line_since,
     path_fact,
@@ -53,10 +62,10 @@ AUTHORITY_ROOT_ENV = (
 
 
 def read_epics(
-    root: Path,
+    root: Path, umbrella_dir: str = UMBRELLA
 ) -> tuple[dict[str, dict[str, Any]], SourceState, str, str | None]:
     """epics.toml зонтика с origin/<default>; ошибки реестра → error."""
-    umbrella = root / UMBRELLA
+    umbrella = root / umbrella_dir
     if not (umbrella / ".git").exists():
         return {}, "error", f"нет клона {umbrella}", None
     text, sha, state, detail = read_file_at_origin(umbrella, "epics.toml")
@@ -75,21 +84,21 @@ def read_epics(
 
 
 def read_manifest(
-    root: Path, do_fetch: bool
+    root: Path, do_fetch: bool, umbrella_dir: str = UMBRELLA
 ) -> tuple[str | None, tuple[str, str | None], list[str]]:
     """Манифест с origin зонтика; fetch — ДО чтения, иначе состав флота устарел.
 
     Сбой fetch — деградация (ошибка в источнике history), а не отказ: читается
     последний известный origin, и граф будет partial.
     """
-    umbrella = root / UMBRELLA
+    umbrella = root / umbrella_dir
     errors: list[str] = []
     if (
         do_fetch
         and (umbrella / ".git").exists()
         and (problem := fetch(umbrella)) is not None
     ):
-        errors.append(f"{UMBRELLA}: fetch: {problem}")
+        errors.append(f"{umbrella_dir}: fetch: {problem}")
     text, sha, state, detail = read_file_at_origin(umbrella, "workspace-manifest.toml")
     if state != "read":
         return None, ("origin", sha), [f"манифест не прочитан: {detail}"]
@@ -206,7 +215,7 @@ def _human_merge(
 
 
 def _roadmap(
-    root: Path, roadmap_path: Path | None
+    root: Path, roadmap_path: Path | None, umbrella_dir: str = UMBRELLA
 ) -> tuple[str | None, str | None, SourceState, str]:
     if roadmap_path is not None:
         source = f"file:{roadmap_path}"
@@ -214,7 +223,7 @@ def _roadmap(
             return roadmap_path.read_text(encoding="utf-8"), None, "read", source
         except OSError as exc:  # нет файла, каталог, права — состояние, не падение
             return None, None, "error", f"{source}: {exc.strerror or exc}"
-    umbrella = root / UMBRELLA
+    umbrella = root / umbrella_dir
     if not (umbrella / ".git").exists():
         return None, None, "error", "origin"
     text, sha, state, _ = read_file_at_origin(umbrella, "roadmap.toml")
@@ -230,6 +239,90 @@ def truncated_prs(records: list[dict[str, Any]]) -> list[str]:
     ]
 
 
+def umbrella_full(root: Path, owner: str, umbrella_dir: str = UMBRELLA) -> str:
+    """owner/name зонтика по remote клона (в acceptance зонтик — песочница)."""
+    code, url, _ = git(root / umbrella_dir, "remote", "get-url", "origin")
+    match = _URL_RE.search(url.strip()) if code == 0 else None
+    if match:
+        return f"{match.group(1)}/{match.group(2)}"
+    return f"{owner}/{umbrella_dir}"
+
+
+def _slice1(
+    root: Path,
+    repos: dict[str, FleetRepo],
+    snapshot: dict[str, Any],
+    readable: set[str],
+    owner: str,
+    runner: Runner,
+    errors: list[str],
+    umbrella_dir: str = UMBRELLA,
+    records: list[dict[str, Any]] | None = None,
+) -> dict[str, Any]:
+    """Чтения среза 1: периоды рёбер, факты выполнения, появления путей,
+    extras открытых issues, очередь владельца. Сбой — ошибка источника."""
+    out: dict[str, Any] = {
+        "done_facts": {},
+        "edge_periods": {},
+        "path_added": {},
+        "issue_extras": {},
+        "closed_events": {},
+        "queue_records": [],
+        "umbrella_full": umbrella_full(root, owner, umbrella_dir),
+    }
+    nodes = {n["node_id"]: n for n in snapshot["nodes"]}
+    for ref in snapshot["references"]:
+        repo = ref["provenance"]["repo"]
+        if ref["kind"] != "blocked_by" or repo not in readable:
+            continue
+        repo_dir = root / repos[repo].git_dir
+        item = ref["source_node_id"].rsplit("/", 1)[-1]
+        raw = ref.get("raw_ref") or ""
+        try:
+            period = facts.edge_period(
+                repo_dir, default_ref(repo_dir) or "", repo, item, raw
+            )
+            if period is not None:
+                out["edge_periods"][f"{ref['source_node_id']}|{raw}"] = list(period)
+            target = nodes.get(ref.get("resolved_target") or "")
+            if target is not None and target["declared_status"] != "open":
+                t_repo = target["repo"]
+                t_dir = root / repos[t_repo].git_dir
+                sha = facts.first_done_commit(
+                    t_dir,
+                    default_ref(t_dir) or "",
+                    t_repo,
+                    target["node_id"].rsplit("/", 1)[-1],
+                )
+                if sha is not None:
+                    out["done_facts"][target["node_id"]] = sha
+        except GitError as exc:
+            errors.append(str(exc))
+    for name, key in {r.github_name: r.key for r in repos.values()}.items():
+        extras = issue_extras(owner, name, runner)
+        if extras is None:
+            errors.append(f"{name}: extras открытых issues не прочитаны")
+            continue
+        out["issue_extras"].update({f"{key}#{n}": v for n, v in extras.items()})
+    names = {r.key: r.github_name for r in repos.values()}
+    for rec in records or []:
+        if rec["is_pr"] or rec["state"] != "closed":
+            continue
+        name = names.get(rec["repo"], rec["repo"])
+        event = closed_event(owner, name, rec["number"], runner)
+        if event is None:
+            errors.append(f"{name}#{rec['number']}: timeline закрытия не прочитан")
+        elif event:
+            out["closed_events"][f"{rec['repo']}#{rec['number']}"] = event
+    umbrella_owner, _, umbrella_name = out["umbrella_full"].partition("/")
+    queue = queue_records(umbrella_owner, umbrella_name, runner)
+    if queue is None:
+        errors.append("очередь владельца не прочитана")
+    else:
+        out["queue_records"] = queue
+    return out
+
+
 def collect(
     root: Path,
     manifest_text: str,
@@ -240,9 +333,14 @@ def collect(
     host: str,
     now: str,
     prior_errors: list[str] | None = None,
+    slice1: bool = False,
+    umbrella_dir: str = UMBRELLA,
 ) -> Inputs:
-    """Прочитать флот; сбои — состояния источников, не исключения."""
-    repos = {r.key: r for r in fleet_repos(manifest_text)}
+    """Прочитать флот; сбои — состояния источников, не исключения.
+
+    umbrella_dir — зонтик профиля: в acceptance — песочница (§9.1).
+    """
+    repos = {r.key: r for r in fleet_repos(manifest_text, umbrella_dir)}
     todos = [read_todo(r, root, do_fetch) for r in repos.values()]
     names = {r.github_name: r.key for r in repos.values()}
     norm = {**{k: k for k in repos}, **names}
@@ -254,8 +352,8 @@ def collect(
         runner,
         weak_refs=lambda recs: local_refs(recs, norm),
     )
-    rm_text, rm_sha, rm_state, rm_source = _roadmap(root, roadmap_path)
-    epics, epics_state, epics_detail, epics_sha = read_epics(root)
+    rm_text, rm_sha, rm_state, rm_source = _roadmap(root, roadmap_path, umbrella_dir)
+    epics, epics_state, epics_detail, epics_sha = read_epics(root, umbrella_dir)
     snapshot = pf.parse_fleet(
         [
             pf.RepoInput(
@@ -271,7 +369,36 @@ def collect(
     hist = _History(root, repos)
     hist.errors += prior_errors or []
     hist.collect(snapshot, {t.repo for t in todos if t.state == "read"})
-    facts = _trigger_facts(root, repos, todos, hist.errors)
+    trigger_facts = _trigger_facts(root, repos, todos, hist.errors)
+    extra = (
+        _slice1(
+            root,
+            repos,
+            snapshot,
+            {t.repo for t in todos if t.state == "read"},
+            owner,
+            runner,
+            hist.errors,
+            umbrella_dir,
+            gh.records,
+        )
+        if slice1
+        else {}
+    )
+    if slice1:
+        for text, fact in trigger_facts.items():
+            m = EXISTS_RE.match(text)
+            if m and fact.get("exists"):
+                repo_dir = root / repos[m.group(1)].git_dir
+                try:
+                    sha = facts.path_added(
+                        repo_dir, default_ref(repo_dir) or "", m.group(2)
+                    )
+                except GitError as exc:
+                    hist.errors.append(str(exc))
+                    continue
+                if sha is not None:
+                    extra.setdefault("path_added", {})[text] = sha
     human = _human_merge(root, repos, hist.errors)
     prefixes = read_authority_prefixes()
     if prefixes is None:
@@ -297,7 +424,7 @@ def collect(
         movement=hist.movement,
         wait_since=hist.since,
         history=hist.history,
-        trigger_facts=facts,
+        trigger_facts=trigger_facts,
         epics_sha=epics_sha,
         aux_state="error" if hist.errors else "read",
         aux_detail="; ".join(hist.errors[:5]),
@@ -305,4 +432,5 @@ def collect(
         authority_prefixes=prefixes or [],
         manifest_source=manifest_origin[0],
         manifest_sha=manifest_origin[1],
+        **extra,
     )
diff --git a/conductor/inputs.py b/conductor/inputs.py
index 460f204..0ba2c73 100644
--- a/conductor/inputs.py
+++ b/conductor/inputs.py
@@ -54,6 +54,13 @@ class Inputs:
     authority_prefixes: list[str] = field(default_factory=list)
     manifest_source: str = "file"
     manifest_sha: str | None = None
+    done_facts: dict[str, str] = field(default_factory=dict)
+    edge_periods: dict[str, list[str]] = field(default_factory=dict)
+    path_added: dict[str, str] = field(default_factory=dict)
+    issue_extras: dict[str, dict[str, Any]] = field(default_factory=dict)
+    closed_events: dict[str, str] = field(default_factory=dict)
+    queue_records: list[dict[str, Any]] = field(default_factory=list)
+    umbrella_full: str = ""
 
 
 def save_inputs(inputs: Inputs, path: Path) -> None:
diff --git a/conductor/manifest.py b/conductor/manifest.py
index b43059c..35c88be 100644
--- a/conductor/manifest.py
+++ b/conductor/manifest.py
@@ -39,8 +39,8 @@ def _entries(text: str) -> list[tuple[str, dict[str, Any]]]:
     ]
 
 
-def fleet_repos(text: str) -> list[FleetRepo]:
-    """Не-member записи с repo_url, уникальные по git_dir, + зонтик."""
+def fleet_repos(text: str, umbrella: str = UMBRELLA) -> list[FleetRepo]:
+    """Не-member записи с repo_url, уникальные по git_dir, + зонтик профиля."""
     repos: dict[str, FleetRepo] = {}
     for key, entry in _entries(text):
         url, git_dir = entry.get("repo_url"), entry.get("git_dir")
@@ -48,7 +48,7 @@ def fleet_repos(text: str) -> list[FleetRepo]:
             continue
         if match := _URL_RE.search(url):
             repos.setdefault(git_dir, FleetRepo(key, git_dir, match.group(2)))
-    repos.setdefault(UMBRELLA, FleetRepo(UMBRELLA, UMBRELLA, UMBRELLA))
+    repos.setdefault(umbrella, FleetRepo(umbrella, umbrella, umbrella))
     return sorted(repos.values(), key=lambda r: r.key)
 
 
```

Создать `tests/conductor/test_collect_slice1.py` (регрессия P2-4: рядом лежит настоящий зонтик — ни одного чтения и вызова о нём; решение 2: id событий закрытия только для закрытых issues, сбой — ошибка источника):

```python
"""Чтения среза 1 в collect: зонтик профиля (§9.1; ревью P2-4) и id событий
закрытия (§7.1; решение владельца 2)."""

from pathlib import Path

from conductor.collect import _slice1, collect, read_manifest
from conductor.manifest import UMBRELLA, fleet_repos
from tests.conductor.fixtures import ROADMAP
from tests.conductor.test_collect import _repo

SANDBOX = "conductor-sandbox"
MANIFEST = (
    f'[cores.{SANDBOX}]\nrepo_url = "https://github.com/own/{SANDBOX}.git"\n'
    f'git_dir = "{SANDBOX}"\n'
    f"[cores.{SANDBOX}-outside]\n"
    f'repo_url = "https://github.com/own/{SANDBOX}-outside.git"\n'
    f'git_dir = "{SANDBOX}-outside"\n'
)


def test_fleet_repos_add_profile_umbrella_only() -> None:
    keys = {r.key for r in fleet_repos(MANIFEST, SANDBOX)}
    assert keys == {SANDBOX, f"{SANDBOX}-outside"}
    assert UMBRELLA in {r.key for r in fleet_repos(MANIFEST)}


def test_acceptance_collect_reads_only_sandbox(tmp_path: Path) -> None:
    _repo(
        tmp_path / SANDBOX,
        {
            "TODO.md": "- [ ] x @owner:TBD @id:x\n",
            "roadmap.toml": ROADMAP,
            "workspace-manifest.toml": MANIFEST,
        },
    )
    _repo(tmp_path / f"{SANDBOX}-outside", {"TODO.md": ""})
    # настоящий зонтик рядом есть, и его чтение было бы заметно
    _repo(tmp_path / UMBRELLA, {"TODO.md": "- [ ] real @owner:TBD @id:real\n"})
    calls: list[str] = []

    def runner(args: list[str]) -> tuple[int, str, str]:
        calls.append(" ".join(args))
        return 1, "", "offline"

    text, origin, errors = read_manifest(tmp_path, False, SANDBOX)
    assert text == MANIFEST
    inp = collect(
        tmp_path,
        text,
        origin,
        None,
        False,
        runner,
        "h",
        "2026-10-01T12:00:00Z",
        errors,
        slice1=True,
        umbrella_dir=SANDBOX,
    )
    assert {t.repo for t in inp.todos} == {SANDBOX, f"{SANDBOX}-outside"}
    assert inp.roadmap_text == ROADMAP and inp.roadmap_source == "origin"
    assert inp.umbrella_full == f"own/{SANDBOX}"
    assert calls and not any(UMBRELLA in c for c in calls)
    assert not any("real" in (t.text or "") for t in inp.todos)


def test_closed_issue_records_get_closed_event_ids(tmp_path: Path) -> None:
    repos = {r.key: r for r in fleet_repos(MANIFEST, SANDBOX)}
    timeline = '[[{"event": "closed", "node_id": "CE_9"}]]'

    def runner(args: list[str]) -> tuple[int, str, str]:
        if any("issues/7/timeline" in a for a in args):
            return 0, timeline, ""
        if any("issues/8/timeline" in a for a in args):
            return 1, "", "boom"
        return 1, "", "offline"

    records = [
        {"repo": SANDBOX, "number": 7, "is_pr": False, "state": "closed"},
        {"repo": SANDBOX, "number": 8, "is_pr": False, "state": "closed"},
        {"repo": SANDBOX, "number": 9, "is_pr": True, "state": "closed"},
        {"repo": SANDBOX, "number": 10, "is_pr": False, "state": "open"},
    ]
    errors: list[str] = []
    snapshot = {"nodes": [], "references": []}
    out = _slice1(
        tmp_path, repos, snapshot, set(), "own", runner, errors, SANDBOX, records
    )
    assert out["closed_events"] == {f"{SANDBOX}#7": "CE_9"}
    assert any("#8: timeline закрытия не прочитан" in e for e in errors)
```

Создать `tests/conductor/test_item_forms.py` — таблица форм записи пункта (раунды 3–4 ревью: класс «свежая проекция пункта расходится с ядром» закрыт механизмом): для каждой формы (`[x]`/`[X]`, `*`, вложенность, `@id:"…"`, бэктики, прилипший `x@id:`, хвостовая точка, блок кода, HTML-комментарий, переименование, удаление, дубликат `@id` в обоих порядках) `item_state` совпадает с узлом ядра (дубликат — `AmbiguousItem`), а история находит факт выполнения ровно тогда, когда ядро видит выполненный однозначный пункт:

```python
"""Таблица форм записи пункта: проекция `facts.item_state` и история фактов
совпадают с узлом ядра для любой формы (раунды 3–4 ревью: класс «свежая
проекция пункта расходится с ядром» закрывается механизмом, не случаем).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conductor.facts import AmbiguousItem, first_done_commit, item_state
from tests.conductor.slice1_fixtures import world

OPEN = "- [ ] g @owner:TBD @id:goal\n"
FORMS = {
    "bare-done": "- [x] g @owner:TBD @id:goal\n",
    "upper-X": "- [X] g @owner:TBD @id:goal\n",
    "star-bullet": "* [x] g @owner:TBD @id:goal\n",
    "nested": "- [ ] parent @owner:TBD @id:p\n  - [x] g @owner:TBD @id:goal\n",
    "quoted-id": '- [x] g @owner:TBD @id:"goal"\n',
    "backtick-id": "- [x] g @owner:TBD `@id:goal`\n",
    "glued-id": "- [x] g @owner:TBD x@id:goal\n",
    "trailing-dot": "- [x] g @owner:TBD @id:goal.\n",
    "code-fence": "```\n- [x] g @owner:TBD @id:goal\n```\n",
    "html-comment": "<!-- - [x] g @owner:TBD @id:goal -->\n",
    "renamed": "- [x] g @owner:TBD @id:other\n",
    "deleted": "# пунктов нет\n",
    "dup-done-open": "- [x] g @owner:TBD @id:goal\n- [ ] g @owner:TBD @id:goal\n",
    "dup-open-done": "- [ ] g @owner:TBD @id:goal\n- [x] g @owner:TBD @id:goal\n",
}
NODE = "todo://a/goal"


def _core_node(text: str):
    result, _ = world({"a": text}, [])
    return result.graph.nodes.get(NODE)


def _duplicate(text: str) -> bool:
    return any(f.code == "PF-ID-DUPLICATE" for f in world({"a": text}, [])[0].findings)


@pytest.mark.parametrize("form", sorted(FORMS))
def test_projection_matches_core(form: str) -> None:
    """Есть ли пункт и выполнен ли он — как у ядра; дубликат — отказ."""
    text = FORMS[form]
    node = _core_node(text)
    if form.startswith("dup-"):
        with pytest.raises(AmbiguousItem):
            item_state(text, "a", "goal")
        return
    state = item_state(text, "a", "goal")
    assert (state is None) == (node is None), form
    if node is not None and state is not None:
        assert state.done == (not node.is_open), form


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.parametrize("form", sorted(FORMS))
def test_history_sees_exactly_core_transitions(tmp_path: Path, form: str) -> None:
    """Переход open → форма: факт выполнения находится тогда и только тогда,
    когда ядро на новой версии видит выполненный однозначный пункт; правка
    формы без смены состояния не меняет факт."""
    _git(tmp_path, "init", "-q", "-b", "master")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    for msg, text in (("open", OPEN), ("form", FORMS[form])):
        (tmp_path / "TODO.md").write_text(text, encoding="utf-8")
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-q", "--allow-empty", "-m", msg)
    head = _git(tmp_path, "rev-parse", "HEAD")
    node = _core_node(FORMS[form])
    sees_done = node is not None and not node.is_open and not form.startswith("dup-")
    expected = head if sees_done else None
    assert first_done_commit(tmp_path, "master", "a", "goal") == expected, form
    if sees_done:  # переписать форму обратно в bare — тот же факт, нового нет
        (tmp_path / "TODO.md").write_text(FORMS["bare-done"], encoding="utf-8")
        _git(tmp_path, "commit", "-q", "--allow-empty", "-am", "respell")
        assert first_done_commit(tmp_path, "master", "a", "goal") == head, form
```

- [ ] **Step 6: Run collect tests**

Run: `uv run pytest tests/conductor -q`
Expected: PASS — `slice1` и `umbrella_dir` по умолчанию — поведение среза 0, прежние тесты сбора не меняются.

- [ ] **Step 7: Commit**

```bash
git add conductor/facts.py conductor/inputs.py conductor/collect.py conductor/manifest.py \
    tests/conductor/test_facts.py tests/conductor/test_collect_slice1.py \
    tests/conductor/test_item_forms.py
git commit -m "conductor: факты и периоды по first-parent истории, сбор slice1 (§7.1, §7.3)"
```

---
### Task B3: свежие чтения, поиск эффектов и общие помощники планировщиков

**Files:**
- Create: `conductor/fresh.py`, `conductor/reconcile.py`, `conductor/actions/common.py`, `tests/conductor/slice1_fixtures.py`, `tests/conductor/slice1_world.py`
- Test: `tests/conductor/test_fresh.py`, `tests/conductor/test_actions_common.py`, `tests/conductor/test_reconcile.py`

**Interfaces:**
- Consumes: `markers.{Marker, classify, h1, parse_body, render}` (A3), `opstate.{parse_ts, OpState}` (A5), `gh_app.{Blocked, JournalLost}` (A8), `gh_write.{Mutation, PINNED_QUERY}` (A10), `writer.*` (A11), `graph.{Graph, FROM_ORIGIN}`, `waits.{Wait, DATE_RE, EXISTS_RE}`, `policy.position_level`, `rank._entry`, `rank._reverse`, `analysis.dependency_adjacency`, `facts.*` (B2), фейк `FakeClient` (A10).
- Produces (`conductor/fresh.py`): `GitRepo = Callable[[str], tuple[Path, str] | None]` (ключ репо → свежий клон после `fetch` и ref по умолчанию); `FreshReader(client, git_repo=None)` с `.comments(repo, number)`, `.issue(repo, number)`, `.pull(repo, number)`, `.last_event(repo, number, kind) -> str | None` (`node_id` последнего события timeline; нет — `""`), `.pinned(repo, number) -> bool | None`, `.pr_facts(repo, number) -> dict | None` (одно GraphQL-чтение: открыт/влит/драфт, head, merge SHA, проверки head → `ci` и `red`, одобрение head, закрывающие ссылки; усечение или сбой — None), `.marker_found(repo, number, rendered, bot) -> bool | None`, `.marker_absent(...) -> bool`, `.queue_found(repo, bot) -> bool | None`, `.git(repo_key)`; `valid(m) -> None`; `pr_need(facts) -> str` (ветви `policy._pr_need`, от которых зависит текст пинка); `comment_check(fresh, m, bot) -> str | None`; `marker_check(fresh, m, rendered, bot) -> str | None` (действительный неотредактированный маркер App с полным ключом есть); `open_check(fresh, repo, number) -> str | None`; `first_reason(*checks) -> str | None`. Сбой чтения в проверке — причина снятия («при сомнении шаг снимается»).
- Produces (`conductor/reconcile.py`, ревью P2-3): `found(fresh, attempt, bot) -> bool | None` и `reconcile(state, fresh, bot, now) -> list[dict]` — до планирования каждая незавершённая попытка (`OpState.open_attempts()`) ищется в GitHub по идентичности эффекта (`op`/`expected` попытки: маркер комментария, sha256 тела, `closed`+`completed`, `isPinned`, найденная очередь); найдена — `OpState.settle(effect_key)` (`ok`), задержка эффекта снята; не найдена или чтение не удалось — без изменений; запрет по лимиту — поиск прекращается. Найденный эффект не даёт права писать (I7).
- Produces (`conductor/actions/common.py`): `WaitRef(src, raw, consumer, prereq)`; `wait_refs(graph, waits)`; `wait_id(ref)`; `fact_of(graph, inputs, ref) -> str | None` (issue — `closed:<id события closed>` из `inputs.closed_events`, решение 2); `threads(graph, node_id) -> list[str]` (все треды, открытые и закрытые); `addresses(graph, node_id) -> list[str]` (только открытые, решение 5); `no_address_reason(graph, node_id) -> str` («канал доставки — срез 1b» без тредов / «нет открытого адреса доставки» — все закрыты); `edited_findings(graph, threads, bot, action) -> list[dict]` (`MK-EDITED` — находка, решение 3); `authority(result, inputs, node_id, ranked=False) -> Callable[[Roadmap, int], int]` (уровень позиции по свежему роадмапу, О §2.4; `ranked` — без ранга 0); `node_repo_item(node_id)`; `fresh_item(fresh, node_id) -> tuple[ItemState | None, clone, problem]` (пункт на свежем origin каноническим разбором; `None` — пункта нет; неоднозначный `@id` — причина отказа «идентичность пункта неоднозначна»); `fresh_accepted(fresh, graph, inputs, subject) -> tuple[str | None, str | None]` (куда склеена заявка по свежей записи — меткам и телу — ТЕМИ ЖЕ правилами `accepted_as`, что у ядра: `graph._gh_edges`, метка `inbox` или распознанная шапка протокола; одна реализация на ядро и перепроверку); `fresh_fact(fresh, graph, inputs, ref) -> tuple[str | None, str | None]` (факт по свежим чтениям, как `fact_of`; закрыт не `completed` — `closed-not-completed`); `thread_events`, `pr_activity`, `movement`, `target`, `entry_rank`, `due` — как прежде.
- `tests/conductor/slice1_fixtures.py`: `BOT`, `NOW`, `TODOS`, `REQUESTS`, `world(**overrides)`, `comment(...)`.
- `tests/conductor/slice1_world.py`: `World(tmp)` — `FakeClient`, состояние, `FreshReader` с локальными git-репо `tmp/<ключ>` и `.run(records, action, between=None)` через настоящий `Writer` (`between` — изменение мира перед ВТОРЫМ шагом); `commit(repo, todo, msg)`, `roadmap(*actions)`, `outcomes(reports)`, константы `GOAL`, `GOAL_FREE`, `B_OPEN`, `B_DONE`, `UMB`.

- [ ] **Step 1: Write the shared fixtures**

`tests/conductor/slice1_fixtures.py`:

```python
"""Мир для планировщиков среза 1: цель фокуса ждёт фоновый пункт."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from conductor.inputs import Inputs
from conductor.snapshot import Result, evaluate
from tests.conductor.fixtures import inputs, record

BOT = "conductor[bot]"
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
TODOS = {
    "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 @blocked_by:todo://b/b\n",
    "b": "- [ ] b @owner:TBD @id:b @epic:eco.bg\n",
}
# заявка a#3 склеена с целью (адрес потребителя), b#4 — с предпосылкой (адрес продюсера)
REQUESTS = [
    record("a", 3, labels=["inbox"], body="slug: goal\nfrom: devtools\n"),
    record("b", 4, labels=["inbox"], body="slug: b\nfrom: devtools\n"),
]


def comment(
    author: str, body: str, created: str, cid: int, updated: str | None = None
) -> dict[str, Any]:
    """Комментарий в форме записи gh_records."""
    return {
        "id": cid,
        "author": author,
        "body": body,
        "created_at": created,
        "updated_at": updated or created,
    }


def world(
    todos: dict[str, str] | None = None,
    records: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> tuple[Result, Inputs]:
    """Result ядра и Inputs с полями среза 1 (extra)."""
    inp = inputs(todos or TODOS, records if records is not None else REQUESTS, **extra)
    return evaluate(inp, 0), inp
```

`tests/conductor/slice1_world.py` (мир тестов последовательностей §11: планировщик → `Writer` → фейк GitHub и git):

```python
"""Мир для тестов последовательностей среза 1: планировщик → настоящий
Writer → фейковый GitHub и локальные git-репо (спека среза 1, §11).

Повторная проверка основания (§5.1 шаг 8, регрессия P1-1 ревью планов):
между планированием и отправкой, а также между двумя шагами меняется
ответ, период, факт, тег или движение — мутаций после изменения нет.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from conductor.fresh import FreshReader
from conductor.host_config import HostConfig
from conductor.journal import MutationLog, RunJournal
from conductor.opstate import init_state, open_state
from conductor.roadmap import parse_roadmap
from conductor.writer import PlanRecord, StepReport, Writer
from tests.conductor.fake_app import FakeClient
from tests.conductor.fixtures import EPICS, ROADMAP
from tests.conductor.slice1_fixtures import NOW

CFG = HostConfig(
    app_id=1,
    installation_id=2,
    private_key=Path("/k"),
    profile="fleet",
    shadow=False,
    state_dir=Path("/s"),
)
UMB = "own/ai-orchestrators-workspace"
FENCE = frozenset({"own/a", "own/b", UMB})
GOAL = "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 @blocked_by:todo://b/b\n"
GOAL_FREE = "- [ ] g @owner:github:own @id:goal @epic:eco.focus1\n"
B_OPEN = "- [ ] b @owner:TBD @id:b @epic:eco.bg\n"
B_DONE = "- [x] b @owner:TBD @id:b @epic:eco.bg\n"


def git(repo: Path, *args: str) -> str:
    """git в репо теста; ошибка — исключение."""
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo: Path, todo: str, msg: str = "c") -> str:
    """Коммит TODO.md (репо создаётся при первом вызове); вернуть SHA."""
    if not (repo / ".git").exists():
        repo.mkdir(parents=True, exist_ok=True)
        git(repo, "init", "-q", "-b", "master")
        git(repo, "config", "user.email", "t@t")
        git(repo, "config", "user.name", "t")
    (repo / "TODO.md").write_text(todo, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


def roadmap(*actions: str):
    """Роадмап autonomy=1 с включёнными actions."""
    text = ROADMAP.replace(
        "autonomy = 0", f"autonomy = 1\nenabled_actions = {json.dumps(list(actions))}"
    )
    rm = parse_roadmap(text, EPICS)
    assert rm.valid, rm.findings
    return rm


class World:
    """FakeClient, git-репо a и b, состояние и исполнитель."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.client = FakeClient()
        init_state(tmp / "state", NOW - timedelta(hours=3))
        opened = open_state(tmp / "state", NOW)
        assert opened.state is not None
        self.state = opened.state
        self.fresh = FreshReader(self.client, self.clone)

    def clone(self, key: str) -> tuple[Path, str] | None:
        repo = self.tmp / key
        return (repo, "master") if (repo / ".git").exists() else None

    def run(
        self,
        records: list[PlanRecord],
        action: str,
        between: Callable[[], None] | None = None,
    ) -> list[StepReport]:
        """Исполнить; between — изменение мира перед ВТОРЫМ шагом."""
        calls = {"n": 0}

        def load():
            calls["n"] += 1
            if calls["n"] == 2 and between is not None:
                between()
            return roadmap(action)

        writer = Writer(
            cfg=CFG,
            client=self.client,
            state=self.state,
            log=MutationLog(self.tmp / "run"),
            journal=RunJournal(self.tmp / "run"),
            fence=FENCE,
            load_roadmap=load,
            hostname="vps",
            run_id="r1",
            level_cap=3,
            clock=lambda: NOW,
        )
        return writer.execute(records)


def outcomes(reports: list[StepReport]) -> list[tuple[str, str]]:
    """(исход, причина) по шагам."""
    return [(r.outcome, r.reason) for r in reports]
```

- [ ] **Step 2: Write the failing tests**

`tests/conductor/test_fresh.py`:

```python
"""Свежие чтения (спека среза 1, §5.1 шаг 8, §5.3)."""

from conductor.fresh import FreshReader, comment_check, open_check, pr_need
from conductor.gh_write import Mutation
from conductor.markers import h1, make, render, with_marker
from tests.conductor.fake_app import FakeClient, ok

BOT = "conductor[bot]"
M = make("q", id=h1("q", "a"))
COMMENT = Mutation("comment", "o/r", 1, text=with_marker("x", M))


def test_marker_found_absent_and_unreadable() -> None:
    client = FakeClient()
    fresh = FreshReader(client)
    assert fresh.marker_found("o/r", 1, render(M), BOT) is False
    assert comment_check(fresh, COMMENT, BOT) is None
    client.add_comment("o/r", 1, BOT, with_marker("x", M))
    assert fresh.marker_found("o/r", 1, render(M), BOT) is True
    assert comment_check(fresh, COMMENT, BOT) == "маркер уже есть"
    client.override[("GET", "/repos/o/r/issues/1/comments?per_page=100&page=1")] = ok(
        {"x": 1}
    )
    assert fresh.marker_found("o/r", 1, render(M), BOT) is None
    assert comment_check(fresh, COMMENT, BOT) == "тред не прочитан"


def test_foreign_or_edited_marker_is_not_found() -> None:
    client = FakeClient()
    client.add_comment("o/r", 1, "own", with_marker("x", M))
    client.add_comment("o/r", 1, BOT, with_marker("x", M), updated_at="2027-01-01")
    assert FreshReader(client).marker_found("o/r", 1, render(M), BOT) is False


def test_open_check_and_last_event_and_pinned() -> None:
    client = FakeClient()
    fresh = FreshReader(client)
    assert open_check(fresh, "o/r", 1) is None
    client.issue("o/r", 1, state="closed")
    assert open_check(fresh, "o/r", 1) == "субъект закрыт"
    assert fresh.last_event("o/r", 1, "reopened") == ""
    client.issue("o/r", 1, timeline=[{"event": "reopened", "node_id": "RE_1"}])
    assert fresh.last_event("o/r", 1, "reopened") == "RE_1"
    assert fresh.pinned("o/r", 1) is False
    client.issue("o/r", 1, pinned=True)
    assert fresh.pinned("o/r", 1) is True
    assert client.sent == []  # чтения GraphQL — не мутации


def test_queue_found_and_git_without_reader() -> None:
    client = FakeClient()
    fresh = FreshReader(client)
    assert fresh.queue_found("o/ws", BOT) is False
    client.issue("o/ws", 7, labels=["owner-queue"], user=BOT, state="closed")
    assert fresh.queue_found("o/ws", BOT) is True
    assert fresh.git("a") is None


def test_pr_facts_and_need() -> None:
    client = FakeClient()
    red = {
        "__typename": "CheckRun",
        "name": "t",
        "conclusion": "FAILURE",
        "detailsUrl": "u",
    }
    client.pull("o/r", 7, head={"sha": "h"}, checks=[red], closing=["o/r#1"])
    fresh = FreshReader(client)
    facts = fresh.pr_facts("o/r", 7)
    assert facts is not None and pr_need(facts) == "fix_pr"
    assert facts["red"] == [{"name": "t", "url": "u"}] and facts["closing"] == {"o/r#1"}
    client.pull("o/r", 7, checks=[{**red, "conclusion": "SUCCESS"}])
    assert pr_need(fresh.pr_facts("o/r", 7) or {}) == "review"
    client.pull("o/r", 7, reviews=[{"state": "APPROVED", "commit": {"oid": "h"}}])
    assert pr_need(fresh.pr_facts("o/r", 7) or {}) == "merge"
    client.pull("o/r", 7, checks=[{**red, "conclusion": None, "status": "IN_PROGRESS"}])
    assert pr_need(fresh.pr_facts("o/r", 7) or {}) == "wait_ci"
    assert fresh.pr_facts("o/r", 8) is None  # PR нет
    assert client.sent == []


def test_pr_facts_truncated_contexts_unread() -> None:
    client = FakeClient()
    client.pull("o/r", 7)
    result = client._pr_graphql({"o": "o", "n": "r", "k": 7})
    data = result.response.json() if result.response else {}
    pr = data["data"]["repository"]["pullRequest"]
    contexts = pr["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]
    contexts["totalCount"] = 101
    client.override[("POST", "/graphql")] = ok(data)
    assert FreshReader(client).pr_facts("o/r", 7) is None
```

`tests/conductor/test_actions_common.py`:

```python
"""Общие помощники планировщиков (спека среза 1, §5.7, §7.1, §7.3)."""

from datetime import timedelta

from conductor.actions.common import (
    addresses,
    due,
    fact_of,
    movement,
    target,
    wait_id,
    wait_refs,
)
from tests.conductor.fixtures import record
from tests.conductor.slice1_fixtures import BOT, NOW, REQUESTS, TODOS, comment, world

D = timedelta(days=1)


def test_wait_refs_and_ids() -> None:
    result, _ = world()
    [(ref, wait)] = [
        (r, w)
        for r, w in wait_refs(result.graph, result.waits)
        if r.prereq == "todo://b/b"
    ]
    assert (ref.src, ref.raw, ref.consumer) == (
        "todo://a/goal",
        "todo://b/b",
        "todo://a/goal",
    )
    assert wait.verdict == "pending"
    assert wait_id(ref) == wait_id(ref) and wait_id(ref).startswith("h1-")


def test_addresses_glued_requests() -> None:
    result, _ = world()
    assert addresses(result.graph, "todo://a/goal") == ["a#3"]
    assert addresses(result.graph, "todo://b/b") == ["b#4"]


def test_fact_of_item_issue_trigger() -> None:
    todos = {**TODOS, "b": "- [x] b @owner:TBD @id:b @epic:eco.bg\n"}
    result, inp = world(todos, done_facts={"todo://b/b": "sha1"})
    [(ref, _)] = [
        (r, w)
        for r, w in wait_refs(result.graph, result.waits)
        if r.prereq == "todo://b/b"
    ]
    assert fact_of(result.graph, inp, ref) == "item:sha1"
    trig = {
        "a": (
            "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 "
            '@trigger:"date>=2026-01-01"\n'
        )
    }
    result, inp = world(trig, [])
    [(ref, _)] = wait_refs(result.graph, result.waits)
    assert ref.prereq is None and fact_of(result.graph, inp, ref) == "date:2026-01-01"


def test_movement_excludes_bot_and_updated_at() -> None:
    recs = [
        REQUESTS[0],
        record(
            "b",
            4,
            labels=["inbox"],
            body="slug: b\nfrom: devtools\n",
            updated_at="2026-10-09T00:00:00Z",
            comments=[
                comment(BOT, "пинок", "2026-10-08T00:00:00Z", 1),
                comment("dev", "работаю", "2026-10-05T00:00:00Z", 2),
            ],
        ),
    ]
    result, inp = world(records=recs)
    moved = movement(result.graph, inp, "todo://b/b", BOT)
    assert moved is not None and moved.isoformat().startswith("2026-10-05")


def test_target_maps_github_name() -> None:
    _, inp = world()
    assert target(inp, "ecosystem-kb#7") == ("own/prograph-vault", 7)
    assert target(inp, "a!9") == ("own/a", 9)


def test_due_branches() -> None:
    stale, renudge = 3 * D, 7 * D
    start = NOW - 10 * D
    assert due(NOW, start, NOW - 4 * D, None, [], stale, renudge) == 1
    assert (
        due(NOW, NOW - 2 * D, NOW - 2 * D, None, [], stale, renudge) is None
    )  # молодо
    assert (
        due(NOW, start, NOW - 1 * D, NOW - 1 * D, [], stale, renudge) is None
    )  # тишины мало
    prior = [(1, NOW - 8 * D)]
    assert (
        due(NOW, start, NOW - 9 * D, None, prior, stale, renudge) == 2
    )  # движения не было
    assert (
        due(NOW, start, NOW - 2 * D, NOW - 2 * D, prior, stale, renudge) is None
    )  # было, тишина < stale
    assert due(NOW, start, NOW - 4 * D, NOW - 4 * D, prior, stale, renudge) == 2
    assert (
        due(NOW, start, NOW - 9 * D, None, [(1, NOW - 6 * D)], stale, renudge) is None
    )
```

`tests/conductor/test_reconcile.py` (регрессия P2-3: обрыв `after_send` → новый процесс находит маркер → попытка `ok`, повторного комментария нет, закрытие идёт сразу; двойник без поиска — задержка):

```python
"""Поиск результата незавершённых попыток (спека среза 1, §5.3; регрессия P2-3).

Обрыв `after_send` → новый процесс находит эффект по идентичности →
попытке дописан `ok`, задержка эффекта снята, повторной мутации нет.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from conductor.fresh import FreshReader
from conductor.gh_app import Blocked
from conductor.gh_write import Mutation
from conductor.host_config import HostConfig
from conductor.journal import MutationLog, RunJournal
from conductor.markers import h1, make, render, with_marker
from conductor.opstate import OpState, init_state, open_state
from conductor.reconcile import reconcile
from conductor.roadmap import parse_roadmap
from conductor.writer import PlanRecord, Step, StopPoint, Writer, effect_key
from tests.conductor.fake_app import FakeClient
from tests.conductor.fixtures import EPICS, ROADMAP
from tests.conductor.slice1_fixtures import BOT, NOW

ACC = HostConfig(
    app_id=1,
    installation_id=2,
    private_key=Path("/k"),
    profile="acceptance",
    shadow=False,
    state_dir=Path("/s"),
    sandbox="own/a",
    stop_points=frozenset({"after_send"}),
)
FLEET = HostConfig(**{**ACC.__dict__, "profile": "fleet", "stop_points": frozenset()})
MARKER = make(
    "close", node=h1("node", "a#1"), period=h1("period", "0"), evidence=h1("e", "x")
)
COMMENT = Step(
    Mutation("comment", "own/a", 1, text=with_marker("ok", MARKER)), render(MARKER)
)
CLOSE = Step(Mutation("close", "own/a", 1), "close")
BODY = Step(Mutation("body", "own/a", 1, text="тело очереди"), "body")


def _roadmap():
    text = ROADMAP.replace(
        "autonomy = 0", 'autonomy = 1\nenabled_actions = ["close_shipped"]'
    )
    return parse_roadmap(text, EPICS)


def _state(tmp: Path) -> OpState:
    init_state(tmp / "state", NOW - timedelta(hours=3))
    state = open_state(tmp / "state", NOW).state
    assert state is not None
    return state


def _writer(tmp: Path, cfg: HostConfig, client: FakeClient, state: OpState, run: str):
    return Writer(
        cfg=cfg,
        client=client,
        state=state,
        log=MutationLog(tmp / run),
        journal=RunJournal(tmp / run),
        fence=frozenset({"own/a"}),
        load_roadmap=_roadmap,
        hostname="vps",
        run_id=run,
        level_cap=3,
        clock=lambda: NOW,
    )


def _interrupted(tmp: Path, steps: tuple[Step, ...]) -> tuple[FakeClient, OpState]:
    client, state = FakeClient(), _state(tmp)
    record = PlanRecord("close_shipped", "a#1", "r", 1, steps)
    with pytest.raises(StopPoint):
        _writer(tmp, ACC, client, state, "r1").execute([record])
    assert [a["status"] for a in state.attempts.values()] == ["in_flight"]
    return client, open_state(tmp / "state", NOW).state  # новый процесс


def test_after_send_comment_found_settled_and_close_proceeds(tmp_path: Path) -> None:
    client, state = _interrupted(tmp_path, (COMMENT, CLOSE))
    assert state is not None
    eff = effect_key(COMMENT, COMMENT.mutation)
    assert state.delayed(eff, NOW)
    notes = reconcile(state, FreshReader(client), BOT, NOW)
    assert notes == [{"settled": "own/a#1", "op": "comment"}]
    assert [a["status"] for a in state.attempts.values()] == ["ok"]
    assert not state.delayed(eff, NOW)
    # планировщик видит маркер — в плане только закрытие; оно идёт сразу
    record = PlanRecord("close_shipped", "a#1", "r", 1, (CLOSE,))
    reports = _writer(tmp_path, FLEET, client, state, "r2").execute([record])
    assert [r.outcome for r in reports] == ["success"]
    assert [s[0] for s in client.sent] == ["POST", "PATCH"]  # без второго POST


def test_without_reconcile_the_comment_stays_delayed(tmp_path: Path) -> None:
    """Двойник: без поиска эффекта повтор комментария задержан (in_flight)."""
    client, state = _interrupted(tmp_path, (COMMENT, CLOSE))
    assert state is not None
    record = PlanRecord("close_shipped", "a#1", "r", 1, (COMMENT, CLOSE))
    reports = _writer(tmp_path, FLEET, client, state, "r2").execute([record])
    assert [r.outcome for r in reports] == ["delay", "skipped_dependent"]


def test_invisible_effect_is_not_settled(tmp_path: Path) -> None:
    client = FakeClient()
    client.apply = False  # GitHub принял, но в чтении эффекта нет
    state = _state(tmp_path)
    record = PlanRecord("close_shipped", "a#1", "r", 1, (COMMENT,))
    with pytest.raises(StopPoint):
        _writer(tmp_path, ACC, client, state, "r1").execute([record])
    assert reconcile(state, FreshReader(client), BOT, NOW) == []
    assert state.delayed(effect_key(COMMENT, COMMENT.mutation), NOW)


def test_body_identity_and_close_state(tmp_path: Path) -> None:
    client, state = _interrupted(tmp_path, (BODY,))
    assert state is not None
    client.issue("own/a", 1, body="другая проекция")
    assert reconcile(state, FreshReader(client), BOT, NOW) == []  # не наше тело
    client.issue("own/a", 1, body="тело очереди")
    assert reconcile(state, FreshReader(client), BOT, NOW) == [
        {"settled": "own/a#1", "op": "body"}
    ]


def test_read_failure_and_ban_change_nothing(tmp_path: Path) -> None:
    client, state = _interrupted(tmp_path, (COMMENT,))
    assert state is not None
    client.blocked = True
    assert reconcile(state, FreshReader(client), BOT, NOW) == []
    assert [a["status"] for a in state.attempts.values()] == ["in_flight"]


def test_attempt_row_keeps_effect_identity(tmp_path: Path) -> None:
    _, state = _interrupted(tmp_path, (COMMENT,))
    assert state is not None
    rows = (tmp_path / "state" / "attempts.jsonl").read_text(encoding="utf-8")
    begin = [json.loads(r) for r in rows.splitlines() if '"begin"' in r][0]
    assert (begin["op"], begin["expected"]) == ("comment", render(MARKER))


def test_blocked_reader_stops(tmp_path: Path) -> None:
    class Boom(FakeClient):
        def call(self, *a, **k):
            raise Blocked("лимит")

    _, state = _interrupted(tmp_path, (COMMENT,))
    assert state is not None
    assert reconcile(state, FreshReader(Boom()), BOT, NOW) == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/conductor/test_fresh.py tests/conductor/test_actions_common.py tests/conductor/test_reconcile.py -q`
Expected: FAIL — `ImportError` (`FreshReader` без `pull`/`last_event`, нет `conductor.reconcile`).

- [ ] **Step 4: Write `conductor/fresh.py`**

```python
"""Свежие чтения: шаг 8 §5.1 и поиск эффектов §5.3 (спека среза 1).

App-чтения идут через клиента App (класс service); git — `fetch` и
`origin/<default>` учётными данными чтения хоста. Ошибка чтения при
повторной проверке — «недействительно» (шаг снимается, а не пишется); при
поиске эффекта — «не найден» (действует задержка §5.3).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from conductor.gh_write import PINNED_QUERY
from conductor.markers import classify, parse_body, render
from conductor.sources_gh import ci_state, red_checks

PAGES = 50
PR_FACTS = (
    "query($o:String!,$n:String!,$k:Int!){repository(owner:$o,name:$n)"
    "{pullRequest(number:$k){state merged isDraft headRefOid updatedAt"
    " mergeCommit{oid}"
    " closingIssuesReferences(first:50){totalCount"
    " nodes{number repository{nameWithOwner}}}"
    " latestReviews(first:50){totalCount nodes{state commit{oid}}}"
    " commits(last:1){nodes{commit{statusCheckRollup{contexts(first:100)"
    "{totalCount nodes{__typename ... on CheckRun{name conclusion status detailsUrl}"
    " ... on StatusContext{context state targetUrl}}}}}}}}}}"
)
GitRepo = Callable[[str], tuple[Path, str] | None]


def valid(m: Any) -> str | None:
    """revalidate без свежего чтения (тесты планировщиков без клиента)."""
    return None


def _comment(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": c.get("id"),
        "author": (c.get("user") or {}).get("login", ""),
        "body": c.get("body") or "",
        "created_at": c.get("created_at", ""),
        "updated_at": c.get("updated_at", ""),
    }


class FreshReader:
    """GET через клиента App; git-чтения — через git_repo(ключ репо).

    git_repo делает fetch и возвращает (каталог клона, ref по умолчанию);
    None — чтение не удалось. Без git_repo git-проверки недействительны.
    """

    def __init__(self, client: Any, git_repo: GitRepo | None = None) -> None:
        self._client = client
        self._git_repo = git_repo

    def _get(self, path: str) -> Any:
        result = self._client.call("service", "GET", path, auth="token")
        if result.outcome != "ok" or result.response is None:
            return None
        return result.response.json()

    def _pages(self, path: str) -> list[dict[str, Any]] | None:
        out: list[dict[str, Any]] = []
        for page in range(1, PAGES + 1):
            data = self._get(f"{path}?per_page=100&page={page}")
            if not isinstance(data, list):
                return None
            out += data
            if len(data) < 100:
                return out
        return None

    def comments(self, repo: str, number: int) -> list[dict[str, Any]] | None:
        """Все комментарии треда; сбой или усечение — None."""
        raw = self._pages(f"/repos/{repo}/issues/{number}/comments")
        return None if raw is None else [_comment(c) for c in raw]

    def issue(self, repo: str, number: int) -> dict[str, Any] | None:
        """Issue или PR как issue; сбой — None."""
        data = self._get(f"/repos/{repo}/issues/{number}")
        return data if isinstance(data, dict) else None

    def pull(self, repo: str, number: int) -> dict[str, Any] | None:
        """PR (REST pulls); сбой — None."""
        data = self._get(f"/repos/{repo}/pulls/{number}")
        return data if isinstance(data, dict) else None

    def pr_facts(self, repo: str, number: int) -> dict[str, Any] | None:
        """Свежие основания потребности PR (GraphQL-чтение): состояние, head,
        проверки head, одобрение head, закрывающие ссылки. Усечение или
        сбой — None (основание не прочитано — не писать)."""
        owner, _, name = repo.partition("/")
        result = self._client.call(
            "service",
            "POST",
            "/graphql",
            auth="token",
            body={
                "query": PR_FACTS,
                "variables": {"o": owner, "n": name, "k": number},
            },
            graphql=True,
        )
        data = result.response.json() if result.response is not None else None
        try:
            pr = data["data"]["repository"]["pullRequest"]  # type: ignore[index]
            return _pr_facts(pr) if result.outcome == "ok" else None
        except (KeyError, TypeError, IndexError):
            return None

    def last_event(self, repo: str, number: int, kind: str) -> str | None:
        """node_id последнего события kind timeline; нет — "", сбой — None."""
        events = self._pages(f"/repos/{repo}/issues/{number}/timeline")
        if events is None:
            return None
        found = [e.get("node_id") or "" for e in events if e.get("event") == kind]
        return found[-1] if found else ""

    def pinned(self, repo: str, number: int) -> bool | None:
        """Закреплён ли issue (GraphQL-чтение); сбой — None."""
        issue = self.issue(repo, number)
        if issue is None:
            return None
        result = self._client.call(
            "service",
            "POST",
            "/graphql",
            auth="token",
            body={"query": PINNED_QUERY, "variables": {"id": issue.get("node_id")}},
            graphql=True,
        )
        data = result.response.json() if result.response is not None else None
        if result.outcome != "ok" or not isinstance(data, dict):
            return None
        node = (data.get("data") or {}).get("node")
        return node.get("isPinned") is True if isinstance(node, dict) else None

    def marker_found(
        self, repo: str, number: int, rendered: str, bot: str
    ) -> bool | None:
        """Есть ли действительный маркер события rendered; сбой — None."""
        comments = self.comments(repo, number)
        if comments is None:
            return None
        return any(
            kind == "event" and marker is not None and render(marker) == rendered
            for kind, marker in (classify(c, bot) for c in comments)
        )

    def marker_absent(self, repo: str, number: int, rendered: str, bot: str) -> bool:
        """Действительного маркера в треде нет (сбой чтения — False)."""
        return self.marker_found(repo, number, rendered, bot) is False

    def queue_found(self, repo: str, bot: str) -> bool | None:
        """Есть ли очередь бота (открытая или закрытая); сбой — None."""
        data = self._get(f"/search/issues?q=repo:{repo}+label:owner-queue+is:issue")
        if not isinstance(data, dict) or data.get("incomplete_results"):
            return None
        return any(
            (i.get("user") or {}).get("login") == bot for i in data.get("items", [])
        )

    def git(self, repo_key: str) -> tuple[Path, str] | None:
        """Свежий клон репо флота (после fetch); нет или сбой — None."""
        return self._git_repo(repo_key) if self._git_repo is not None else None


def _pr_facts(pr: dict[str, Any]) -> dict[str, Any] | None:
    refs, reviews = pr["closingIssuesReferences"], pr["latestReviews"]
    nodes = pr["commits"]["nodes"]
    rollup = (nodes[-1]["commit"]["statusCheckRollup"] or {}) if nodes else {}
    contexts = rollup.get("contexts") or {"totalCount": 0, "nodes": []}
    if (
        refs["totalCount"] > len(refs["nodes"])
        or reviews["totalCount"] > len(reviews["nodes"])
        or contexts["totalCount"] > len(contexts["nodes"])
    ):
        return None  # усечено — основание не доказано
    head = pr["headRefOid"]
    approved = any(
        r["state"] == "APPROVED" and (r.get("commit") or {}).get("oid") == head
        for r in reviews["nodes"]
    ) and not any(r["state"] == "CHANGES_REQUESTED" for r in reviews["nodes"])
    return {
        "open": pr["state"] == "OPEN",
        "merged": bool(pr["merged"]),
        "merge_sha": (pr.get("mergeCommit") or {}).get("oid"),
        "draft": bool(pr["isDraft"]),
        "head": head,
        "updated_at": pr["updatedAt"],
        "ci": ci_state(contexts["nodes"]),
        "red": red_checks(contexts["nodes"]),
        "approved": approved,
        "closing": {
            f"{r['repository']['nameWithOwner']}#{r['number']}" for r in refs["nodes"]
        },
    }


def pr_need(facts: dict[str, Any]) -> str:
    """Потребность PR по свежим основаниям — ветви policy._pr_need, от
    которых зависит текст пинка (мерж — одна ветвь: текст один)."""
    if facts["ci"] == "red":
        return "fix_pr"
    if not facts["approved"]:
        return "review"
    if facts["ci"] != "green":
        return "wait_ci"
    return "merge"


def comment_check(fresh: FreshReader, m: Any, bot: str) -> str | None:
    """Комментарий: маркера этого события в треде ещё нет (свежее чтение)."""
    marker = parse_body(m.text)
    if marker is None:
        return "в тексте нет маркера"
    found = fresh.marker_found(m.repo, m.number, render(marker), bot)
    if found is None:
        return "тред не прочитан"
    return "маркер уже есть" if found else None


def marker_check(fresh: FreshReader, m: Any, rendered: str, bot: str) -> str | None:
    """Шаг, опирающийся на подтверждение: действительный маркер события App
    с полным ключом rendered в треде есть (свежее чтение, без правок)."""
    found = fresh.marker_found(m.repo, m.number, rendered, bot)
    if found is None:
        return "тред не прочитан"
    return None if found else "подтверждения в треде нет"


def open_check(fresh: FreshReader, repo: str, number: int) -> str | None:
    """Субъект всё ещё открыт (свежее чтение)."""
    data = fresh.issue(repo, number)
    if data is None:
        return "субъект не прочитан"
    return None if data.get("state") == "open" else "субъект закрыт"


def first_reason(*checks: Callable[[], str | None]) -> str | None:
    """Первая причина снятия среди проверок (ленивых: чтения по порядку)."""
    for check in checks:
        if (reason := check()) is not None:
            return reason
    return None
```

- [ ] **Step 5: Write `conductor/actions/common.py`**

```python
"""Общие помощники планировщиков среза 1 (§5.7, §7.1, §7.3)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from conductor import facts
from conductor.analysis import dependency_adjacency
from conductor.fresh import FreshReader
from conductor.graph import FROM_ORIGIN, Graph, _gh_edges, normalizer
from conductor.inputs import Inputs
from conductor.markers import Marker, classify, h1
from conductor.opstate import parse_ts
from conductor.policy import position_level
from conductor.rank import _entry, _reverse
from conductor.roadmap import Roadmap
from conductor.snapshot import Result
from conductor.sources_git import GitError
from conductor.waits import DATE_RE, EXISTS_RE, Wait


@dataclass(frozen=True)
class WaitRef:
    """Ребро ожидания: исходный src и ссылка (raw) — идентичность §7.3."""

    src: str
    raw: str
    consumer: str
    prereq: str | None


def wait_refs(graph: Graph, waits: list[Wait]) -> list[tuple[WaitRef, Wait]]:
    """Ожидания ядра с их рёбрами (та же канонизация, что evaluate_waits)."""
    by_pair = {(w.consumer, w.prereq): w for w in waits}
    out: list[tuple[WaitRef, Wait]] = []
    seen: set[tuple[str, str | None]] = set()
    for e in graph.edges:
        if e.type != "depends_on":
            continue
        raw = e.origin.removeprefix("todo:") if e.origin.startswith("todo:") else e.dst
        consumer = graph.resolve(e.src)
        prereq = e.dst if e.origin == FROM_ORIGIN else graph.resolve(e.dst)
        wait = by_pair.get((consumer, prereq))
        if wait is not None and (consumer, prereq) not in seen:
            seen.add((consumer, prereq))
            out.append((WaitRef(e.src, raw, consumer, prereq), wait))
    for w in waits:
        if w.prereq is None:
            text = graph.nodes[w.consumer].trigger or ""
            out.append((WaitRef(w.consumer, f"trigger:{text}", w.consumer, None), w))
    return out


def wait_id(ref: WaitRef) -> str:
    """Идентичность ожидания: (src, ссылка); правка заголовка её не меняет."""
    return h1("wait", ref.src, ref.raw)


def fact_of(graph: Graph, inputs: Inputs, ref: WaitRef) -> str | None:
    """Идентичность факта выполнения предпосылки (§7.1); неизвестно — None."""
    if ref.prereq is None:
        text = ref.raw.removeprefix("trigger:")
        if m := DATE_RE.match(text):
            return f"date:{m.group(1)}"
        if EXISTS_RE.match(text) and (sha := inputs.path_added.get(text)):
            return f"path:{sha}"
        return None
    node = graph.nodes.get(ref.prereq)
    if node is None:
        return None
    if node.kind == "item":
        sha = inputs.done_facts.get(node.node_id)
        return f"item:{sha}" if sha else None
    rec = graph.records.get(node.node_id) or {}
    if node.kind == "issue":
        # id события closed (решение владельца 2026-10-01), не closedAt:
        # время не обещает уникальности последовательных закрытий
        event = inputs.closed_events.get(node.node_id)
        return f"closed:{event}" if event else None
    return f"merged:{rec['merge_sha']}" if rec.get("merge_sha") else None


def threads(graph: Graph, node_id: str) -> list[str]:
    """Все треды узла (О §5.7), открытые и закрытые: issue/PR — свой;
    пункт — заявки, склеенные с ним или исходящие от него."""
    node = graph.nodes.get(node_id)
    if node is None:
        return []
    if node.kind in ("issue", "pr"):
        return [node_id]
    glued = {m for m in graph.members(node_id) if m != node_id}
    sent = {e.dst for e in graph.out(node_id, "depends_on") if e.origin == FROM_ORIGIN}
    return sorted(
        m
        for m in glued | sent
        if (n := graph.nodes.get(m)) is not None and n.kind == "issue"
    )


def addresses(graph: Graph, node_id: str) -> list[str]:
    """Адреса доставки — только открытые треды (решение владельца 5)."""
    return [m for m in threads(graph, node_id) if graph.nodes[m].is_open]


def no_address_reason(graph: Graph, node_id: str) -> str:
    """Почему адреса нет: тредов нет вовсе (TODO-only — канал среза 1b) или
    все закрыты («нет открытого адреса»: не доставка и не 1b)."""
    if threads(graph, node_id):
        return "нет открытого адреса доставки"
    return "канал доставки — срез 1b"


def edited_findings(
    graph: Graph, thread_ids: list[str], bot: str, action: str
) -> list[dict[str, Any]]:
    """MK-EDITED: отредактированный маркер — находка в выдаче (решение
    владельца 3), маркер недействителен, доставкой не считается."""
    return [
        {"finding": "MK-EDITED", "action": action, "thread": t}
        for t in thread_ids
        if thread_events(graph, t, bot)[1]
    ]


def thread_events(
    graph: Graph, node_id: str, bot: str
) -> tuple[list[tuple[Marker, dict[str, Any]]], int]:
    """Действительные маркеры треда и число отредактированных (MK-EDITED)."""
    events: list[tuple[Marker, dict[str, Any]]] = []
    edited = 0
    for c in (graph.records.get(node_id) or {}).get("comments", []):
        kind, marker = classify(c, bot)
        if kind == "event" and marker is not None:
            events.append((marker, c))
        elif kind == "edited":
            edited += 1
    return events, edited


def pr_activity(rec: dict[str, Any], bot: str) -> list[str]:
    """Движение PR не от App: коммиты, ready, ревью, комментарии (§7.4)."""
    stamps = [rec.get("last_commit_at") or "", rec.get("ready_at") or ""]
    stamps += [
        r["submitted_at"] for r in rec.get("reviews", []) if r.get("author") != bot
    ]
    stamps += [
        c["created_at"] for c in rec.get("comments", []) if c.get("author") != bot
    ]
    return [s for s in stamps if s]


def movement(graph: Graph, inputs: Inputs, node_id: str, bot: str) -> datetime | None:
    """Последнее движение по узлу работы не от App; updated_at — не движение."""
    stamps: list[str] = []
    for member in graph.members(node_id):
        stamps.append(inputs.movement.get(member) or "")
        rec = graph.records.get(member) or {}
        stamps += [
            c["created_at"] for c in rec.get("comments", []) if c.get("author") != bot
        ]
        for edge in graph.into(member, "implements"):
            pr = graph.nodes.get(edge.src)
            if pr is not None and pr.is_open:
                stamps += pr_activity(graph.records.get(edge.src) or {}, bot)
    values = [parse_ts(s) for s in stamps if s]
    return max(values) if values else None


def authority(
    result: Result, inputs: Inputs, node_id: str, ranked: bool = False
) -> Callable[[Roadmap, int], int]:
    """Уровень позиции по свежему роадмапу (О §2.4) для исполнителя.

    Граф — прогона; класс, ранг, фокус и `focus.autonomy` — из роадмапа,
    перечитанного перед шагом. ranked — действие требует ранга (§7.3–7.5):
    позиция без ранга по свежему роадмапу — уровень 0.
    """
    graph, waits = result.graph, result.waits
    rev = _reverse(dependency_adjacency(graph))

    def level(roadmap: Roadmap, run_level: int) -> int:
        if node_id not in graph.nodes:
            return 0
        entry = _entry(node_id, graph, waits, roadmap, rev, inputs.captured_at)
        if ranked and entry.rank is None:
            return 0
        return position_level(entry, roadmap, run_level)

    return level


def node_repo_item(node_id: str) -> tuple[str, str] | None:
    """todo://<repo>/<item> → (repo, item); иначе None."""
    if not node_id.startswith("todo://"):
        return None
    repo, _, item = node_id.removeprefix("todo://").partition("/")
    return repo, item


def fresh_item(
    fresh: FreshReader, node_id: str
) -> tuple[facts.ItemState | None, tuple[Any, str] | None, str | None]:
    """Пункт на свежем origin — каноническим разбором ядра (`facts.item_state`):
    (состояние | None — пункта нет, (каталог, ref), причина отказа: сбой
    чтения или неоднозначный `@id` — запись по нему не разрешается)."""
    parts = node_repo_item(node_id)
    if parts is None:
        return None, None, "не пункт TODO"
    clone = fresh.git(parts[0])
    if clone is None:
        return None, None, f"{parts[0]}: git не прочитан"
    repo_dir, ref = clone
    code, text, _ = facts.git(repo_dir, "show", f"{ref}:{facts.TODO}")
    if code != 0:
        return None, None, f"{parts[0]}: TODO.md не прочитан"
    try:
        return facts.item_state(text, parts[0], parts[1]), clone, None
    except facts.AmbiguousItem:
        return None, clone, "идентичность пункта неоднозначна (@id дважды)"


def fresh_fact(
    fresh: FreshReader, graph: Graph, inputs: Inputs, ref: WaitRef
) -> tuple[str | None, str | None]:
    """Факт выполнения предпосылки по свежим чтениям (как fact_of).

    (fact_id | None — не выполнена, причина сбоя чтения | None).
    """
    try:
        return _fresh_fact(fresh, graph, inputs, ref)
    except GitError as exc:
        return None, str(exc)


def _fresh_fact(
    fresh: FreshReader, graph: Graph, inputs: Inputs, ref: WaitRef
) -> tuple[str | None, str | None]:
    if ref.prereq is None:
        text = ref.raw.removeprefix("trigger:")
        if m := DATE_RE.match(text):
            return f"date:{m.group(1)}", None
        m = EXISTS_RE.match(text)
        clone = fresh.git(m.group(1)) if m else None
        if m is None or clone is None:
            return None, "условие не перепроверяется"
        sha = facts.path_added(clone[0], clone[1], m.group(2))
        return (f"path:{sha}" if sha else None), None
    node = graph.nodes.get(ref.prereq)
    if node is None:
        return None, "предпосылка вне графа"
    if node.kind == "item":
        state, clone, problem = fresh_item(fresh, node.node_id)
        if problem is not None or clone is None:
            return None, problem
        if state is None or not state.done:
            return None, None
        repo_key, item = node_repo_item(node.node_id) or ("", "")
        sha = facts.first_done_commit(clone[0], clone[1], repo_key, item)
        return (f"item:{sha}" if sha else None), None
    repo, number = target(inputs, node.node_id)
    if node.kind == "issue":
        issue = fresh.issue(repo, number)
        if issue is None:
            return None, "предпосылка не прочитана"
        if issue.get("state") != "closed":
            return None, None
        if issue.get("state_reason") != "completed":
            return "closed-not-completed", None
        event = fresh.last_event(repo, number, "closed")
        if event is None:
            return None, "timeline не прочитан"
        return (f"closed:{event}" if event else None), None
    pull = fresh.pull(repo, number)
    if pull is None:
        return None, "PR не прочитан"
    sha = pull.get("merge_commit_sha") if pull.get("merged") else None
    return (f"merged:{sha}" if sha else None), None


def fresh_accepted(
    fresh: FreshReader, graph: Graph, inputs: Inputs, subject: str
) -> tuple[str | None, str | None]:
    """Куда склеена заявка subject по СВЕЖЕЙ записи (метки, тело) — теми же
    правилами `accepted_as`, что у ядра (`graph._gh_edges`: метка inbox или
    распознанная шапка протокола). (пункт | None, причина сбоя чтения | None)."""
    repo, number = target(inputs, subject)
    issue = fresh.issue(repo, number)
    if issue is None:
        return None, "заявка не прочитана"
    rec = {
        "repo": subject.partition("#")[0],
        "number": number,
        "is_pr": False,
        "body": issue.get("body") or "",
        "labels": [lab.get("name") for lab in issue.get("labels") or []],
    }
    edges = _gh_edges(rec, graph.nodes, normalizer(inputs), [])
    accepted = [e.dst for e in edges if e.type == "accepted_as"]
    return (accepted[0] if accepted else None), None


def target(inputs: Inputs, node_id: str) -> tuple[str, int]:
    """repo#N / repo!N → (owner/github-name, N)."""
    sep = "#" if "#" in node_id else "!"
    key, _, number = node_id.partition(sep)
    names = {k: name for name, k in inputs.repo_names.items()}
    return f"{inputs.owner}/{names.get(key, key)}", int(number)


def entry_rank(result: Result, node_id: str) -> int | None:
    """Ранг позиции очереди (у PR — через узлы, которые он реализует)."""
    for e in result.queue + result.attention:
        if e.node_id == node_id:
            return e.rank
    return None


def due(
    now: datetime,
    age_start: datetime,
    silent_since: datetime,
    moved: datetime | None,
    prior: list[tuple[int, datetime]],
    stale: timedelta,
    renudge: timedelta,
) -> int | None:
    """Номер следующего пинка или None (§7.3: первый и две ветки повтора)."""
    if not prior:
        if now - age_start >= stale and now - silent_since >= stale:
            return 1
        return None
    last_n, last_at = max(prior)
    if now - last_at < renudge:
        return None
    if moved is not None and moved > last_at and now - moved < stale:
        return None
    return last_n + 1
```

- [ ] **Step 6: Write `conductor/reconcile.py`**

```python
"""Поиск результата незавершённых попыток по идентичности (спека среза 1, §5.3).

До планирования: каждая попытка `in_flight`/`uncertain` ищется в GitHub по
сохранённой идентичности эффекта (маркер комментария, sha256 тела, состояние
закрытия, закрепление, найденная очередь). Нашлась — попытке дописывается
`ok`, и задержка её эффекта снимается. Не нашлась или чтение не удалось —
ничего не меняется (действует задержка). Найденный эффект НЕ даёт права
писать (I7): планировщики заново читают удалённые объекты, исполнитель
проходит все проверки §5.1.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from conductor.fresh import FreshReader
from conductor.gh_app import Blocked, JournalLost
from conductor.opstate import OpState


def _split(target: str) -> tuple[str, int | None]:
    repo, _, number = target.partition("#")
    return repo, int(number) if number else None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def found(fresh: FreshReader, attempt: dict[str, Any], bot: str) -> bool | None:
    """Виден ли эффект попытки; None — чтение не удалось."""
    op = attempt.get("op")
    repo, number = _split(attempt["target"])
    if op == "create":
        return fresh.queue_found(repo, bot)
    if number is None:
        return None
    if op == "comment":
        return fresh.marker_found(repo, number, attempt.get("expected", ""), bot)
    if op == "pin":
        return fresh.pinned(repo, number)
    issue = fresh.issue(repo, number)
    if issue is None:
        return None
    if op == "close":
        closed = issue.get("state") == "closed"
        return closed and issue.get("state_reason") == "completed"
    if op == "body":
        return _sha(issue.get("body") or "") == attempt.get("expected")
    return None


def reconcile(
    state: OpState, fresh: FreshReader, bot: str, now: datetime
) -> list[dict[str, Any]]:
    """Сверить незавершённые попытки; вернуть заметки о найденных эффектах.

    Запрет по лимиту или потеря журнала установки прекращают поиск: без
    вызовов App состояние попыток не меняется.
    """
    notes: list[dict[str, Any]] = []
    for attempt in state.open_attempts():
        try:
            seen = found(fresh, attempt, bot)
        except (Blocked, JournalLost):
            break
        if seen:
            state.settle(attempt["effect_key"], now)
            notes.append({"settled": attempt["target"], "op": attempt.get("op")})
    return notes
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/conductor/test_fresh.py tests/conductor/test_actions_common.py tests/conductor/test_reconcile.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add conductor/fresh.py conductor/reconcile.py conductor/actions/common.py \
    tests/conductor/slice1_fixtures.py tests/conductor/slice1_world.py \
    tests/conductor/test_fresh.py tests/conductor/test_actions_common.py \
    tests/conductor/test_reconcile.py
git commit -m "conductor: свежие чтения, поиск эффектов, помощники планировщиков (§5.1, §5.3, §7)"
```

---
### Task B4: ответы владельца и производный вопрос

**Files:**
- Create: `conductor/actions/answers.py`
- Test: `tests/conductor/test_answers.py`

**Interfaces:**
- Produces: `Answer(comment_id: int, question_id: str, value: str)`; `parse_answers(comments: list[dict], owner_login: str, questions: dict[str, dict]) -> dict[str, list[Answer]]`; `canonical(answers: list[Answer]) -> list[str]`; `derived_question(question: dict, answers: list[Answer]) -> dict`; `Resolution(question_id, chosen: str | None, derived: dict | None, blocked: bool)`; `resolve(question: dict, by_question: dict[str, list[Answer]]) -> Resolution`; `answers_and_resolutions(comments, owner_login, questions) -> tuple[dict[str, Resolution], list[dict]]` (второе — открытые производные вопросы для проекции). Вопрос — словарь формы `owner_questions` среза 0: `question_id`, `subject`, `reason`, `evidence`, `question`, `options`, `default`.

- [ ] **Step 1: Write the failing test**

```python
"""Ответы владельца и производный вопрос (спека среза 1, §6.2; О §5.10)."""

from conductor.actions.answers import (
    answers_and_resolutions,
    canonical,
    derived_question,
    parse_answers,
    resolve,
)

Q = {
    "question_id": "ab12cd34",
    "subject": "a#1",
    "reason": "GR-SHIPPED-OPEN",
    "evidence": "e",
    "question": "a#1: закрыть?",
    "options": ["close", "keep"],
    "default": "keep",
}
OWNER = "own"


def c(cid: int, body: str, author: str = OWNER) -> dict:
    return {
        "id": cid,
        "author": author,
        "body": body,
        "created_at": "t",
        "updated_at": "t",
    }


def test_parse_name_number_foreign_and_stale() -> None:
    comments = [
        c(1, "Q-ab12cd34: close"),
        c(2, "Q-ab12cd34: 2"),
        c(3, "Q-ab12cd34: 9"),  # номер вне диапазона
        c(4, "Q-ab12cd34: close", author="someone"),
        c(5, "Q-ffffffff: close"),  # вопроса нет — устарел
    ]
    got = parse_answers(comments, OWNER, {"ab12cd34": Q})
    assert [(a.comment_id, a.value) for a in got["ab12cd34"]] == [
        (1, "close"),
        (2, "keep"),
    ]
    assert set(got) == {"ab12cd34"}


def test_single_value_is_chosen() -> None:
    by_q = parse_answers(
        [c(1, "Q-ab12cd34: close"), c(2, "Q-ab12cd34: 1")], OWNER, {"ab12cd34": Q}
    )
    assert resolve(Q, by_q).chosen == "close"


def test_conflict_makes_derived_question() -> None:
    by_q = parse_answers(
        [c(1, "Q-ab12cd34: close"), c(2, "Q-ab12cd34: keep")], OWNER, {"ab12cd34": Q}
    )
    res = resolve(Q, by_q)
    assert res.chosen is None and res.derived is not None
    assert res.derived["options"] == ["close", "keep"]  # keep не дублируется


def test_derived_id_changes_on_any_new_answer_even_same_value() -> None:
    two = parse_answers(
        [c(1, "Q-ab12cd34: close"), c(2, "Q-ab12cd34: keep")], OWNER, {"ab12cd34": Q}
    )["ab12cd34"]
    three = (
        two
        + parse_answers([c(3, "Q-ab12cd34: close")], OWNER, {"ab12cd34": Q})["ab12cd34"]
    )
    assert (
        derived_question(Q, two)["question_id"]
        != derived_question(Q, three)["question_id"]
    )
    assert canonical(three) == ["1:close", "2:keep", "3:close"]


def test_confirmation_chooses_and_conflict_on_confirmation_blocks() -> None:
    base = [c(1, "Q-ab12cd34: close"), c(2, "Q-ab12cd34: keep")]
    res, open_derived = answers_and_resolutions(base, OWNER, [Q])
    did = res["ab12cd34"].derived["question_id"]
    assert [d["question_id"] for d in open_derived] == [did]
    res, open_derived = answers_and_resolutions(
        base + [c(3, f"Q-{did}: close")], OWNER, [Q]
    )
    assert res["ab12cd34"].chosen == "close" and open_derived == []
    res, _ = answers_and_resolutions(
        base + [c(3, f"Q-{did}: close"), c(4, f"Q-{did}: keep")], OWNER, [Q]
    )
    assert res["ab12cd34"].blocked and res["ab12cd34"].chosen is None


def test_keep_confirmation_executes_nothing() -> None:
    base = [c(1, "Q-ab12cd34: close"), c(2, "Q-ab12cd34: keep")]
    did = answers_and_resolutions(base, OWNER, [Q])[0]["ab12cd34"].derived[
        "question_id"
    ]
    res, _ = answers_and_resolutions(base + [c(3, f"Q-{did}: keep")], OWNER, [Q])
    assert res["ab12cd34"].chosen is None and not res["ab12cd34"].blocked
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_answers.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Ответы владельца и производный вопрос подтверждения (§6.2; О §5.10).

Ответ — комментарий владельца `Q-<id>: <имя | номер>`; учитывается текст на
момент чтения. Идентичность ответа — (comment-id, значение); `id'`
производного вопроса — функция текущего набора ответов.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

ANSWER_RE = re.compile(r"^\s*Q-([0-9a-f]{8}):\s*([A-Za-z0-9_-]+)\s*$", re.MULTILINE)
PROTOCOL = "v1"


@dataclass(frozen=True)
class Answer:
    """Действительный ответ на вопрос."""

    comment_id: int
    question_id: str
    value: str


@dataclass(frozen=True)
class Resolution:
    """Итог по вопросу: выбранное значение, производный вопрос, блокировка."""

    question_id: str
    chosen: str | None
    derived: dict[str, Any] | None
    blocked: bool


def _value(raw: str, options: list[str]) -> str | None:
    if raw in options:
        return raw
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1]
    return None


def parse_answers(
    comments: list[dict[str, Any]],
    owner_login: str,
    questions: dict[str, dict[str, Any]],
) -> dict[str, list[Answer]]:
    """Ответы владельца на известные вопросы; чужие и устаревшие — нет."""
    out: dict[str, list[Answer]] = {}
    for c in sorted(comments, key=lambda c: c.get("id") or 0):
        if c.get("author") != owner_login:
            continue
        found: dict[str, str] = {}
        for m in ANSWER_RE.finditer(c.get("body") or ""):
            q = questions.get(m.group(1))
            if (
                q is not None
                and (value := _value(m.group(2), q["options"])) is not None
            ):
                found[m.group(1)] = value
        for qid, value in found.items():
            out.setdefault(qid, []).append(Answer(int(c["id"]), qid, value))
    return out


def canonical(answers: list[Answer]) -> list[str]:
    """Канонический набор: comment-id:значение по возрастанию comment-id."""
    return [
        f"{a.comment_id}:{a.value}" for a in sorted(answers, key=lambda a: a.comment_id)
    ]


def derived_question(question: dict[str, Any], answers: list[Answer]) -> dict[str, Any]:
    """Производный вопрос подтверждения Q-<id'> (§6.2)."""
    chosen = {a.value for a in answers}
    options = [o for o in question["options"] if o in chosen]
    if "keep" not in options:
        options.append("keep")
    raw = "\x1f".join(
        ("confirm", question["question_id"], *canonical(answers), PROTOCOL, *options)
    )
    qid = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return {
        "question_id": qid,
        "subject": question["subject"],
        "reason": "confirm",
        "evidence": question["question_id"],
        "question": (
            f"{question['subject']}: на Q-{question['question_id']} разные "
            "ответы — какой исполнить?"
        ),
        "options": options,
        "default": "keep",
    }


def resolve(
    question: dict[str, Any], by_question: dict[str, list[Answer]]
) -> Resolution:
    """Итог по вопросу с учётом подтверждения."""
    qid = question["question_id"]
    mine = by_question.get(qid, [])
    values = {a.value for a in mine}
    if not mine:
        return Resolution(qid, None, None, False)
    if len(values) == 1:
        return Resolution(qid, values.pop(), None, False)
    derived = derived_question(question, mine)
    confirms = {a.value for a in by_question.get(derived["question_id"], [])}
    if not confirms:
        return Resolution(qid, None, derived, False)
    if len(confirms) > 1:  # конфликт на подтверждении: новых вопросов нет
        return Resolution(qid, None, derived, True)
    value = confirms.pop()
    return Resolution(qid, None if value == "keep" else value, derived, False)


def answers_and_resolutions(
    comments: list[dict[str, Any]], owner_login: str, questions: list[dict[str, Any]]
) -> tuple[dict[str, Resolution], list[dict[str, Any]]]:
    """Итоги по вопросам и открытые производные вопросы (два прохода)."""
    known = {q["question_id"]: q for q in questions}
    first = parse_answers(comments, owner_login, known)
    derived = [
        r.derived for q in questions if (r := resolve(q, first)).derived is not None
    ]
    known.update({d["question_id"]: d for d in derived if d is not None})
    by_q = parse_answers(comments, owner_login, known)
    results = {q["question_id"]: resolve(q, by_q) for q in questions}
    open_derived = [
        r.derived
        for r in results.values()
        if r.derived is not None and r.derived["question_id"] not in by_q
    ]
    return results, open_derived
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/conductor/test_answers.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add conductor/actions/answers.py tests/conductor/test_answers.py
git commit -m "conductor: ответы владельца и производный вопрос подтверждения (§6.2)"
```

---
### Task B5: основание закрытия и очередь владельца

**Files:**
- Create: `conductor/actions/shipped.py`, `conductor/actions/owner_queue.py`
- Test: `tests/conductor/test_owner_queue.py`, `tests/conductor/test_revalidate_queue.py`

**Interfaces:**
- Consumes: B3 (`common`, `fresh`), B4 (`answers`), `snapshot.{owner_questions, question_id}`, `rank.rank_of`, `opstate.OpState.episodes` (A5), `writer.{PlanRecord, Step}` (A11), `markers` (A3), `gh_write.Mutation` (A10).
- Produces (`shipped.py`): `Shipped(subject, period, basis_a: dict | None, old_basis: bool)`; `shipped_state(inputs, subject) -> Shipped | None`; `reopened_with_old_basis(result, inputs) -> list[Shipped]` (только с рангом).
- Produces (`owner_queue.py`): `QUEUE_TITLE`; `make_question(subject, reason, evidence, text, options) -> dict`; `op_failure_questions(episodes) -> list[dict]`; `queue_questions(result, inputs, episodes) -> list[dict]`; `projection(questions) -> str`; `queue_issue(inputs, bot) -> tuple[dict | None, str | None]` (запись очереди или находка); `plan_owner_queue(questions, derived, inputs, umbrella, owner_login, bot, fresh, notes, levels=None) -> list[PlanRecord]` — ОДНА запись `owner_queue` (ревью P2-2): шаги тело (или создание) → закрепление (`optional`: сбой не блокирует вопросы) → комментарий на каждый новый вопрос (`Step.revision` = id вопроса, `Step.authority` — уровень позиции субъекта вопроса); сбой или неопределённость тела/создания блокирует вопросы этого плана; `revalidate` — `queue_check`: создание — очереди всё ещё нет; прочее — очередь открыта и открытые производные вопросы по свежим комментариям те же; комментарий — и маркера нет. `question_levels(result, inputs, questions) -> dict[subject, authority]`; `queue_check(fresh, base, derived_ids, owner_login, bot)`.

- [ ] **Step 1: Write the failing test**

```python
"""Очередь владельца и вопросы (спека среза 1, §5.4, §6.1, §7.5)."""

from conductor.actions.owner_queue import (
    op_failure_questions,
    plan_owner_queue,
    projection,
    queue_issue,
    queue_questions,
)
from conductor.actions.shipped import shipped_state
from conductor.markers import h1, make, with_marker
from conductor.opstate import Episode
from tests.conductor.fixtures import record
from tests.conductor.slice1_fixtures import BOT, comment, world

UMB = "own/ai-orchestrators-workspace"
Q1 = {
    "question_id": "11111111",
    "subject": "a#1",
    "reason": "cancelled",
    "evidence": "e",
    "question": "a#1: предпосылка отменена?",
    "options": ["drop-wait", "keep"],
    "default": "keep",
}


def _queue(
    state: str = "open", body: str = "", pinned: bool = True, comments=None, author=BOT
):
    return record(
        "ai-orchestrators-workspace",
        9,
        state=state,
        body=body,
        author=author,
        labels=["owner-queue"],
        comments=comments or [],
        pinned=pinned,
        repo_full=UMB,
    )


def _plan(questions, queue_records):
    _, inp = world(queue_records=queue_records)
    notes: list[dict] = []
    recs = plan_owner_queue(questions, [], inp, UMB, "own", BOT, None, notes)
    return recs, notes


def ops(recs) -> list[list[str]]:
    return [[s.mutation.op for s in r.steps] for r in recs]


def flags(recs) -> list[bool]:
    return [s.optional for r in recs for s in r.steps]


def test_no_queue_no_questions_nothing() -> None:
    assert _plan([], []) == ([], [])


def test_create_pin_and_question_comments() -> None:
    recs, _ = _plan([Q1], [])
    # одна запись: сбой создания блокирует вопросы, сбой закрепления — нет
    assert ops(recs) == [["create", "pin", "comment"]]
    assert flags(recs) == [False, True, False]
    create, pin, question = (s.mutation for s in recs[0].steps)
    assert create.labels == ("owner-queue",) and create.repo == UMB
    assert pin.number is None and question.number is None  # номер — из создания
    assert "Q-11111111" in question.text and "@own" in question.text
    assert recs[0].steps[2].revision == "11111111"


def test_open_queue_rewrites_body_pins_and_skips_known_questions() -> None:
    marker = make("q", id=h1("q", "11111111"))
    asked = comment(BOT, with_marker("старый", marker), "2026-10-01T00:00:00Z", 1)
    recs, _ = _plan([Q1], [_queue(body="старое", pinned=False, comments=[asked])])
    assert ops(recs) == [["body", "pin"]]


def test_body_equal_projection_no_write() -> None:
    text = projection([Q1])
    body = with_marker(text, make("queue", projection=h1("projection", text)))
    marker = make("q", id=h1("q", "11111111"))
    asked = comment(BOT, with_marker("x", marker), "2026-10-01T00:00:00Z", 1)
    recs, _ = _plan([Q1], [_queue(body=body, comments=[asked])])
    assert recs == []


def test_closed_queue_is_not_reopened() -> None:
    recs, notes = _plan([Q1], [_queue(state="closed")])
    assert recs == [] and notes == [{"finding": "GR-QUEUE-CLOSED"}]


def test_two_queues_are_ambiguous_and_foreign_queue_ignored() -> None:
    recs, notes = _plan([Q1], [_queue(), _queue()])
    assert recs == [] and notes == [{"finding": "GR-QUEUE-AMBIGUOUS"}]
    _, inp = world(queue_records=[_queue(author="someone")])
    assert queue_issue(inp, BOT) == (None, None)


def test_op_failure_question_is_stable_per_episode() -> None:
    ep = Episode(
        "m1",
        "r1",
        ("r1", "r2"),
        "500",
        ("t1", "t2"),
        {"op": "comment", "target": "own/a#1"},
    )
    longer = Episode(
        "m1", "r1", ("r1", "r2", "r3"), "500", ("t1", "t2", "t3"), ep.detail
    )
    [q1], [q2] = op_failure_questions([ep]), op_failure_questions([longer])
    assert q1["question_id"] == q2["question_id"] and q1["reason"] == "op-failure"
    new_episode = Episode("m1", "r7", ("r7", "r8"), "500", ("t7", "t8"), ep.detail)
    assert op_failure_questions([new_episode])[0]["question_id"] != q1["question_id"]


def test_shipped_question_carries_period_and_basis_a_drops_it() -> None:
    todos = {"a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1\n"}
    issue = record("a", 1, title="a#1")
    pr = record("a", 5, is_pr=True, state="closed", merged=True, closing_refs=["a#1"])
    reopened = {
        "a#1": {
            "created_at": "2026-09-01T00:00:00Z",
            "period": "RE_1",
            "period_start": "2026-09-20T00:00:00Z",
            "closed_by": [
                {
                    "repo": "a",
                    "number": 5,
                    "merged": True,
                    "merged_at": "2026-09-10T00:00:00Z",
                    "merge_sha": "m5",
                    "base_is_default": True,
                }
            ],
        }
    }
    _, inp = world(todos, [issue, pr], issue_extras=reopened)
    state = shipped_state(inp, "a#1")
    assert (
        state is not None
        and state.basis_a is None
        and state.old_basis
        and state.period == "RE_1"
    )
    fresh = {
        "a#1": {
            **reopened["a#1"],
            "period": "0",
            "period_start": "2026-09-01T00:00:00Z",
        }
    }
    result2, inp2 = world(todos, [issue, pr], issue_extras=fresh)
    assert shipped_state(inp2, "a#1").basis_a is not None
    assert all(
        q["reason"] != "GR-SHIPPED-OPEN" for q in queue_questions(result2, inp2, [])
    )
```

`tests/conductor/test_revalidate_queue.py` (регрессия P1-1/P2-2 через настоящий `Writer`: ответы владельца изменились — ни тела, ни вопросов; очередь появилась — создания нет; двойники пишут):

```python
"""owner_queue: свежая сверка очереди и ответов (§5.1 шаг 8, §6.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from conductor.actions.owner_queue import plan_owner_queue
from conductor.writer import PlanRecord
from tests.conductor.fixtures import record
from tests.conductor.slice1_fixtures import BOT, world
from tests.conductor.slice1_world import (
    UMB,
    World,
    outcomes,
)

QQ = {
    "question_id": "11111111",
    "subject": "a#1",
    "reason": "cancelled",
    "evidence": "e",
    "question": "a#1: предпосылка отменена?",
    "options": ["drop-wait", "keep"],
    "default": "keep",
}


def queue_world(tmp: Path, exists: bool) -> tuple[World, list[PlanRecord]]:
    w = World(tmp)
    queue = []
    if exists:
        w.client.issue(UMB, 9, labels=["owner-queue"], user=BOT, body="старое")
        queue = [
            record(
                "ai-orchestrators-workspace",
                9,
                body="старое",
                author=BOT,
                labels=["owner-queue"],
                comments=[],
                pinned=True,
                repo_full=UMB,
            )
        ]
    _, inp = world(queue_records=queue)
    notes: list[dict[str, Any]] = []
    recs = plan_owner_queue([QQ], [], inp, UMB, "own", BOT, w.fresh, notes)
    return w, recs


@pytest.mark.parametrize("exists", [True, False])
def test_queue_twin_writes(tmp_path: Path, exists: bool) -> None:
    w, recs = queue_world(tmp_path, exists)
    reports = w.run(recs, "owner_queue")
    assert {r.outcome for r in reports} == {"success"}


def test_queue_removed_when_owner_answers_change(tmp_path: Path) -> None:
    """Ответы владельца изменились (конфликт → новый производный вопрос):
    проекция плана устарела — ни тела, ни вопросов."""
    w, recs = queue_world(tmp_path, exists=True)
    w.client.add_comment(UMB, 9, "own", "Q-11111111: drop-wait")
    w.client.add_comment(UMB, 9, "own", "Q-11111111: keep")
    reports = w.run(recs, "owner_queue")
    assert outcomes(reports)[0] == ("removed", "ответы владельца изменились")
    assert w.client.sent == []


def test_queue_create_removed_when_queue_appeared(tmp_path: Path) -> None:
    w, recs = queue_world(tmp_path, exists=False)
    w.client.issue(UMB, 50, labels=["owner-queue"], user=BOT)
    reports = w.run(recs, "owner_queue")
    assert outcomes(reports)[0] == ("removed", "очередь уже есть")
    assert w.client.sent == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_owner_queue.py tests/conductor/test_revalidate_queue.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `conductor/actions/shipped.py`**

```python
"""Основание close_shipped и период открытия (спека среза 1, §7.5).

Период — от последнего reopened (любого автора, после любого закрытия) или
от создания. (а) — PR, влитый в ветку по умолчанию ПОСЛЕ начала периода.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from conductor.inputs import Inputs
from conductor.opstate import parse_ts
from conductor.rank import rank_of
from conductor.snapshot import Result


@dataclass(frozen=True)
class Shipped:
    """Состояние основания закрытия issue."""

    subject: str
    period: str
    basis_a: dict[str, Any] | None
    old_basis: bool


def shipped_state(inputs: Inputs, subject: str) -> Shipped | None:
    """Основание для открытого issue; extras нет — None (неизвестно)."""
    ext = inputs.issue_extras.get(subject)
    if ext is None:
        return None
    start = parse_ts(ext["period_start"])
    merged = [
        p
        for p in ext["closed_by"]
        if p["merged"] and p["base_is_default"] and p["merged_at"]
    ]
    current = [p for p in merged if parse_ts(p["merged_at"]) >= start]
    old = [p for p in merged if parse_ts(p["merged_at"]) < start]
    return Shipped(
        subject,
        ext["period"],
        current[0] if current else None,
        bool(old) and not current and ext["period"] != "0",
    )


def reopened_with_old_basis(result: Result, inputs: Inputs) -> list[Shipped]:
    """Переоткрытые issues с рангом, у которых (а) осталось в прежнем периоде."""
    out = []
    for subject in sorted(inputs.issue_extras):
        node = result.graph.nodes.get(subject)
        if node is None or not node.is_open:
            continue
        if rank_of(result.graph, result.roadmap, subject) is None:
            continue
        state = shipped_state(inputs, subject)
        if state is not None and state.old_basis:
            out.append(state)
    return out
```

- [ ] **Step 4: Write `conductor/actions/owner_queue.py`**

```python
"""Очередь владельца: вопросы и записи (спека среза 1, §5.4, §6.1).

Тело — проекция и ничего не доказывает; события — в треде. Закрытую
владельцем очередь не переоткрываем; больше одной — записей нет. Все шаги
очереди — ОДНА запись плана: сбой или неопределённость тела (создания)
блокирует вопросы этого плана; сбой закрепления — нет (§6.1 шаг 2).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from conductor.actions.answers import answers_and_resolutions
from conductor.actions.common import authority, thread_events
from conductor.actions.shipped import reopened_with_old_basis, shipped_state
from conductor.fresh import (
    FreshReader,
    comment_check,
    first_reason,
    open_check,
    valid,
)
from conductor.gh_write import Mutation
from conductor.inputs import Inputs
from conductor.markers import h1, make, render, with_marker
from conductor.opstate import Episode
from conductor.roadmap import Roadmap
from conductor.snapshot import Result, owner_questions, question_id
from conductor.writer import PlanRecord, Step

QUEUE_TITLE = "Очередь владельца (conductor)"
SHIPPED = "GR-SHIPPED-OPEN"


def make_question(
    subject: str, reason: str, evidence: str, text: str, options: tuple[str, ...]
) -> dict[str, Any]:
    """Вопрос в форме owner_questions среза 0."""
    return {
        "question_id": question_id(reason, subject, evidence, options),
        "subject": subject,
        "reason": reason,
        "evidence": evidence,
        "question": f"{subject}: {text}",
        "options": list(options),
        "default": "keep",
    }


def op_failure_questions(episodes: list[Episode]) -> list[dict[str, Any]]:
    """Один вопрос на эпизод сбоя; id — ключ серии и первый прогон (§5.4)."""
    out = []
    for ep in episodes:
        raw = "\x1f".join(("op-failure", ep.mutation_id, ep.first_run, "retry", "keep"))
        target = ep.detail.get("target", "?")
        out.append(
            {
                "question_id": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8],
                "subject": target,
                "reason": "op-failure",
                "evidence": ep.mutation_id,
                "question": (
                    f"{target}: операционный сбой {ep.detail.get('op', '?')} "
                    f"в прогонах {', '.join(ep.runs)} ({ep.error})"
                ),
                "options": ["retry", "keep"],
                "default": "keep",
            }
        )
    return out


def queue_questions(
    result: Result, inputs: Inputs, episodes: list[Episode]
) -> list[dict[str, Any]]:
    """Вопросы очереди: среза 0 (GR-SHIPPED-OPEN — с периодом, без (а)),
    переоткрытые с прежним основанием, эпизоды сбоев."""
    out: list[dict[str, Any]] = []
    for q in owner_questions(result):
        if q["reason"] != SHIPPED:
            out.append(q)
            continue
        state = shipped_state(inputs, q["subject"])
        if state is not None and state.basis_a is not None:
            continue  # (а): исполняется без вопроса
        period = state.period if state is not None else "0"
        text = q["question"].split(": ", 1)[-1]
        out.append(
            make_question(
                q["subject"],
                SHIPPED,
                f"{q['evidence']}|period:{period}",
                text,
                tuple(q["options"]),
            )
        )
    asked = {q["subject"] for q in out if q["reason"] == SHIPPED}
    for state in reopened_with_old_basis(result, inputs):
        if state.subject not in asked:
            out.append(
                make_question(
                    state.subject,
                    SHIPPED,
                    f"reopened|period:{state.period}",
                    "переоткрыт после закрытия влитым PR: закрыть снова?",
                    ("close", "keep"),
                )
            )
    out += op_failure_questions(episodes)
    return sorted(out, key=lambda q: (q["subject"], q["reason"], q["question_id"]))


def projection(questions: list[dict[str, Any]]) -> str:
    """Текст тела очереди (без маркера)."""
    lines = [f"# {QUEUE_TITLE}", "", "Отвечайте комментарием `Q-<id>: <вариант>`.", ""]
    if not questions:
        lines.append("Вопросов нет.")
    for q in questions:
        opts = ", ".join(q["options"])
        lines.append(
            f"- Q-{q['question_id']} — {q['question']} — варианты: {opts} "
            f"(по умолчанию {q['default']})"
        )
    return "\n".join(lines)


def queue_issue(inputs: Inputs, bot: str) -> tuple[dict[str, Any] | None, str | None]:
    """Запись очереди бота или находка (GR-QUEUE-CLOSED / GR-QUEUE-AMBIGUOUS)."""
    mine = [r for r in inputs.queue_records if r.get("author") == bot]
    if len(mine) > 1:
        return None, "GR-QUEUE-AMBIGUOUS"
    if not mine:
        return None, None
    if mine[0]["state"] != "open":
        return None, "GR-QUEUE-CLOSED"
    return mine[0], None


def _question_text(q: dict[str, Any], owner_login: str) -> str:
    opts = ", ".join(q["options"])
    return (
        f"@{owner_login} Q-{q['question_id']}: {q['question']}\n"
        f"Варианты: {opts} (по умолчанию {q['default']})."
    )


def queue_check(
    fresh: FreshReader,
    base: list[dict[str, Any]],
    derived_ids: set[str],
    owner_login: str,
    bot: str,
) -> Callable[[Mutation], str | None]:
    """Шаг 8 очереди: создание — очереди всё ещё нет; прочее — очередь
    открыта, а открытые производные вопросы по свежим комментариям те же
    (ответ владельца изменился → проекция и вопросы устарели); комментарий —
    ещё и маркера вопроса нет."""

    def no_queue(m: Mutation) -> str | None:
        found = fresh.queue_found(m.repo, bot)
        if found is None:
            return "очередь не прочитана"
        return "очередь уже есть" if found else None

    def same_answers(m: Mutation) -> str | None:
        comments = fresh.comments(m.repo, m.number or 0)
        if comments is None:
            return "ответы не прочитаны"
        _, derived = answers_and_resolutions(comments, owner_login, base)
        ids = {d["question_id"] for d in derived}
        return None if ids == derived_ids else "ответы владельца изменились"

    def check(m: Mutation) -> str | None:
        if m.op == "create":
            return no_queue(m)
        steps: list[Callable[[], str | None]] = [
            lambda: open_check(fresh, m.repo, m.number or 0)
        ]
        if m.op in ("body", "comment"):
            steps.append(lambda: same_answers(m))
        if m.op == "comment":
            steps.append(lambda: comment_check(fresh, m, bot))
        return first_reason(*steps)

    return check


def plan_owner_queue(
    questions: list[dict[str, Any]],
    derived: list[dict[str, Any]],
    inputs: Inputs,
    umbrella: str,
    owner_login: str,
    bot: str,
    fresh: FreshReader | None,
    notes: list[dict[str, Any]],
    levels: dict[str, Callable[[Roadmap, int], int]] | None = None,
) -> list[PlanRecord]:
    """Запись очереди по таблице §6.1: тело (создание) → закрепление →
    новые вопросы, одной записью с зависимостями шагов.

    questions — вопросы ядра и эпизодов; derived — открытые производные.
    levels — уровень позиции субъекта вопроса (О §2.4) для его комментария.
    """
    queue, finding = queue_issue(inputs, bot)
    if finding is not None:
        notes.append({"finding": finding})
        return []
    every = questions + derived
    if queue is None and not every:
        return []
    text = projection(every)
    body = with_marker(text, make("queue", projection=h1("projection", text)))
    number = queue["number"] if queue is not None else None
    steps: list[Step] = []
    if queue is None:
        create = Mutation(
            "create",
            umbrella,
            None,
            text=body,
            title=QUEUE_TITLE,
            labels=("owner-queue",),
        )
        steps.append(Step(create, "owner-queue", revision=h1("rev", text)))
    elif (queue.get("body") or "").strip() != body.strip():
        steps.append(
            Step(
                Mutation("body", umbrella, number, text=body),
                "body",
                revision=h1("rev", text),
            )
        )
    if queue is None or not queue.get("pinned"):
        steps.append(
            Step(Mutation("pin", umbrella, number), "pin", "pin", optional=True)
        )
    asked: set[Any] = set()
    if queue is not None:
        events, edited = thread_events_of(queue, bot)
        asked = {m for m, _ in events}
        if edited:
            notes.append(
                {"finding": "MK-EDITED", "action": "owner_queue", "thread": umbrella}
            )
    for q in every:
        marker = make("q", id=h1("q", q["question_id"]))
        if marker in asked:
            continue
        m = Mutation(
            "comment",
            umbrella,
            number,
            text=with_marker(_question_text(q, owner_login), marker),
        )
        steps.append(
            Step(
                m,
                render(marker),
                revision=q["question_id"],
                authority=(levels or {}).get(q["subject"]),
            )
        )
    if not steps:
        return []
    check = (
        queue_check(
            fresh,
            questions,
            {d["question_id"] for d in derived},
            owner_login,
            bot,
        )
        if fresh
        else valid
    )
    return [
        PlanRecord("owner_queue", umbrella, h1("rev", text), 1, tuple(steps), check)
    ]


def question_levels(
    result: Result, inputs: Inputs, questions: list[dict[str, Any]]
) -> dict[str, Callable[[Roadmap, int], int]]:
    """Уровень позиции субъекта каждого вопроса, если субъект — узел графа."""
    return {
        q["subject"]: authority(result, inputs, q["subject"])
        for q in questions
        if q["subject"] in result.graph.nodes
    }


def thread_events_of(
    rec: dict[str, Any], bot: str
) -> tuple[list[tuple[Any, dict[str, Any]]], int]:
    """thread_events для записи вне графа (очередь читается отдельно)."""
    from conductor.graph import Graph

    graph = Graph(nodes={}, edges=[], records={"queue": rec})
    return thread_events(graph, "queue", bot)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/conductor/test_owner_queue.py tests/conductor/test_revalidate_queue.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add conductor/actions/shipped.py conductor/actions/owner_queue.py tests/conductor/test_owner_queue.py tests/conductor/test_revalidate_queue.py
git commit -m "conductor: очередь владельца — вопросы, эпизоды сбоев, записи §6.1"
```

---

### Task B6: `notify_satisfied`

**Files:**
- Create: `conductor/actions/notify.py`
- Test: `tests/conductor/test_notify.py`, `tests/conductor/test_revalidate_notify.py`

**Interfaces:**
- Consumes: B3 (`common`, `fresh`), A3, A10, A11.
- Produces: `plan_notify(result, inputs, bot, fresh, notes) -> list[PlanRecord]` — по записи на каждый ОТКРЫТЫЙ адрес (решение 5: тредов нет — «канал доставки — срез 1b», все закрыты — «нет открытого адреса доставки»; `MK-EDITED` — находка); `holds_edge(fresh, graph, inputs, ref) -> str | None`; `notify_check(fresh, graph, inputs, ref, fact, bot)` — `revalidate`: адрес открыт, маркера нет, потребитель открыт и держит тег (`@blocked_by` или `@trigger`) на свежем origin, `fresh_fact` равен факту плана; уровень — `authority(consumer)`.

- [ ] **Step 1: Write the failing test**

```python
"""notify_satisfied (спека среза 1, §7.2)."""

from conductor.actions.common import wait_id, wait_refs
from conductor.actions.notify import plan_notify
from conductor.markers import h1, make, with_marker
from tests.conductor.fixtures import record
from tests.conductor.slice1_fixtures import BOT, REQUESTS, TODOS, comment, world

DONE = {**TODOS, "b": "- [x] b @owner:TBD @id:b @epic:eco.bg\n"}
SECOND = record("a", 6, labels=["inbox"], body="slug: goal\nfrom: devtools\n")


def _plan(todos=DONE, records=None, **extra):
    result, inp = world(todos, records if records is not None else REQUESTS, **extra)
    notes: list[dict] = []
    return plan_notify(result, inp, BOT, None, notes), notes, result


def test_one_record_per_address_with_stable_key() -> None:
    recs, _, _ = _plan(records=[*REQUESTS, SECOND], done_facts={"todo://b/b": "s1"})
    assert [r.subject for r in recs] == ["a#3", "a#6"]
    keys = {r.steps[0].event_key for r in recs}
    assert len(keys) == 1  # одна тройка (ожидание, доказательство) — разные адреса


def test_existing_marker_no_duplicate_and_new_fact_new_marker() -> None:
    _, _, result = _plan(done_facts={"todo://b/b": "s1"})
    [(ref, _)] = [
        (r, w)
        for r, w in wait_refs(result.graph, result.waits)
        if r.prereq == "todo://b/b"
    ]
    marker = make("sat", wait=wait_id(ref), evidence=h1("evidence", "item:s1"))
    delivered = record(
        "a",
        3,
        labels=["inbox"],
        body="slug: goal\nfrom: devtools\n",
        comments=[comment(BOT, with_marker("x", marker), "2026-10-01T00:00:00Z", 1)],
    )
    again, _, _ = _plan(
        records=[delivered, REQUESTS[1]], done_facts={"todo://b/b": "s1"}
    )
    assert again == []
    renewed, _, _ = _plan(
        records=[delivered, REQUESTS[1]], done_facts={"todo://b/b": "s2"}
    )
    assert len(renewed) == 1


def test_edited_marker_is_not_delivery() -> None:
    recs, _, _ = _plan(done_facts={"todo://b/b": "s1"})
    marker_text = recs[0].steps[0].mutation.text
    edited = record(
        "a",
        3,
        labels=["inbox"],
        body="slug: goal\nfrom: devtools\n",
        comments=[
            comment(
                BOT,
                marker_text,
                "2026-10-01T00:00:00Z",
                1,
                updated="2026-10-02T00:00:00Z",
            )
        ],
    )
    again, notes, _ = _plan(
        records=[edited, REQUESTS[1]], done_facts={"todo://b/b": "s1"}
    )
    assert (
        len(again) == 1
        and {
            "finding": "MK-EDITED",
            "action": "notify_satisfied",
            "thread": "a#3",
        }
        in notes
    )


def test_todo_only_consumer_reports_channel() -> None:
    recs, notes, _ = _plan(records=[REQUESTS[1]], done_facts={"todo://b/b": "s1"})
    assert recs == [] and any(
        n.get("reason") == "канал доставки — срез 1b" for n in notes
    )


def test_unknown_fact_no_write() -> None:
    recs, notes, _ = _plan()
    assert recs == [] and any(
        n.get("reason") == "факт выполнения неизвестен" for n in notes
    )


def test_closed_addresses_are_not_channel_1b() -> None:
    """Решение владельца 5: все заявки закрыты — «нет открытого адреса»."""
    closed = record(
        "a", 3, state="closed", labels=["inbox"], body="slug: goal\nfrom: devtools\n"
    )
    recs, notes, _ = _plan(
        records=[closed, REQUESTS[1]], done_facts={"todo://b/b": "s1"}
    )
    reasons = {n.get("reason") for n in notes}
    assert recs == [] and "нет открытого адреса доставки" in reasons
    assert "канал доставки — срез 1b" not in reasons


def test_issue_prerequisite_fact_is_closed_event_id() -> None:
    """Решение владельца 2: fact_id issue — id события closed, не closedAt."""
    todos = {
        "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 @blocked_by:b#7\n"
    }
    closed = record(
        "b",
        7,
        state="closed",
        state_reason="completed",
        closed_at="2026-10-01T00:00:00Z",
    )
    goal_req = record("a", 3, labels=["inbox"], body="slug: goal\nfrom: devtools\n")
    no_event, notes, _ = _plan(todos, [goal_req, closed])
    assert no_event == [] and any(
        n.get("reason") == "факт выполнения неизвестен" for n in notes
    )
    [rec] = _plan(todos, [goal_req, closed], closed_events={"b#7": "CE_1"})[0]
    [again] = _plan(todos, [goal_req, closed], closed_events={"b#7": "CE_2"})[0]
    assert rec.steps[0].event_key != again.steps[0].event_key  # новое закрытие
    assert "closed:CE_1" in rec.steps[0].mutation.text
```

`tests/conductor/test_revalidate_notify.py` (регрессия P1-1: снятая отметка `[x]`, повторная отметка (новый факт), снятый тег ожидания, закрытый адрес между планированием и отправкой — снято, мутаций нет; двойник пишет):

```python
"""notify_satisfied: свежая сверка перед отправкой (§5.1 шаг 8, §7.2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from conductor.actions.notify import plan_notify
from conductor.writer import PlanRecord
from tests.conductor.slice1_fixtures import BOT, REQUESTS, world
from tests.conductor.slice1_world import (
    B_DONE,
    B_OPEN,
    GOAL,
    GOAL_FREE,
    World,
    commit,
    outcomes,
)


def notify_world(tmp: Path) -> tuple[World, list[PlanRecord]]:
    w = World(tmp)
    commit(tmp / "a", GOAL)
    commit(tmp / "b", B_OPEN)
    done = commit(tmp / "b", B_DONE, "done")
    result, inp = world(
        {"a": GOAL, "b": B_DONE}, REQUESTS, done_facts={"todo://b/b": done}
    )
    notes: list[dict[str, Any]] = []
    return w, plan_notify(result, inp, BOT, w.fresh, notes)


def test_notify_twin_sends(tmp_path: Path) -> None:
    w, recs = notify_world(tmp_path)
    assert outcomes(w.run(recs, "notify_satisfied")) == [("success", "")]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda w: commit(w.tmp / "b", B_OPEN, "reopen"), "доказательство изменилось"),
        (lambda w: commit(w.tmp / "a", GOAL_FREE, "untag"), "тег ожидания снят"),
        (lambda w: w.client.issue("own/a", 3, state="closed"), "субъект закрыт"),
        # канонический разбор: `@id` предпосылки в бэктиках — пункта нет
        (
            lambda w: commit(w.tmp / "b", B_DONE.replace("@id:b", "`@id:b`"), "q"),
            "доказательство изменилось",
        ),
        # тег ожидания в бэктиках — ребра нет
        (
            lambda w: commit(
                w.tmp / "a",
                GOAL.replace("@blocked_by:todo://b/b", "`@blocked_by:todo://b/b`"),
                "q",
            ),
            "тег ожидания снят",
        ),
    ],
)
def test_notify_removed_when_basis_changes(tmp_path: Path, change, reason) -> None:
    w, recs = notify_world(tmp_path)
    change(w)
    assert outcomes(w.run(recs, "notify_satisfied")) == [("removed", reason)]
    assert w.client.sent == []


def test_notify_removed_when_fact_redone(tmp_path: Path) -> None:
    """Снятие и повторная отметка [x] — новый факт: старая запись снята."""
    w, recs = notify_world(tmp_path)
    commit(tmp_path / "b", B_OPEN, "reopen")
    commit(tmp_path / "b", B_DONE, "done again")
    assert outcomes(w.run(recs, "notify_satisfied")) == [
        ("removed", "доказательство изменилось")
    ]


def test_notify_quoted_id_redone_fact_is_new(tmp_path: Path) -> None:
    """Раунд 4 (R4-2): `@id:"b"` — тот же пункт для ядра; повторная отметка
    после переписывания id — новый факт, старая запись снята."""
    w, recs = notify_world(tmp_path)
    quoted = B_DONE.replace("@id:b", '@id:"b"')
    commit(tmp_path / "b", quoted, "quote id")
    commit(tmp_path / "b", quoted.replace("[x]", "[ ]"), "reopen")
    commit(tmp_path / "b", quoted, "done again")
    assert outcomes(w.run(recs, "notify_satisfied")) == [
        ("removed", "доказательство изменилось")
    ]
    assert w.client.sent == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_notify.py tests/conductor/test_revalidate_notify.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
"""notify_satisfied (спека среза 1, §7.2).

Идентичность — (ожидание, доказательство, адрес); маркер ищется в треде
этого адреса. TODO-only потребитель тредов не имеет — канал среза 1b; треды
есть, но все закрыты — «нет открытого адреса» (решение владельца 5).
Перед отправкой (шаг 8 §5.1) — свежая сверка: адрес открыт и маркера нет,
потребитель держит ребро, факт выполнения тот же.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from conductor.actions.common import (
    WaitRef,
    addresses,
    authority,
    edited_findings,
    fact_of,
    fresh_fact,
    fresh_item,
    no_address_reason,
    target,
    thread_events,
    threads,
    wait_id,
    wait_refs,
)
from conductor.fresh import (
    FreshReader,
    comment_check,
    first_reason,
    open_check,
    valid,
)
from conductor.gh_write import Mutation
from conductor.graph import Graph
from conductor.inputs import Inputs
from conductor.markers import h1, make, render, with_marker
from conductor.snapshot import Result
from conductor.writer import PlanRecord, Step


def holds_edge(
    fresh: FreshReader, graph: Graph, inputs: Inputs, ref: WaitRef
) -> str | None:
    """Потребитель открыт и всё ещё держит тег ожидания (свежее чтение)."""
    node = graph.nodes.get(ref.src)
    if node is not None and node.kind != "item":
        repo, number = target(inputs, ref.src)
        return open_check(fresh, repo, number)
    state, _, problem = fresh_item(fresh, ref.src)
    if problem is not None:
        return problem
    if state is None or state.done:
        return "потребитель закрыт"
    if ref.prereq is None:
        held = state.trigger == ref.raw.removeprefix("trigger:")
    else:
        held = ref.raw in state.blocked_by
    return None if held else "тег ожидания снят"


def notify_check(
    fresh: FreshReader,
    graph: Graph,
    inputs: Inputs,
    ref: WaitRef,
    fact: str,
    bot: str,
) -> Callable[[Mutation], str | None]:
    """Шаг 8: адрес открыт, маркера нет, ребро на месте, факт тот же."""

    def same_fact() -> str | None:
        now, problem = fresh_fact(fresh, graph, inputs, ref)
        if problem is not None:
            return problem
        return None if now == fact else "доказательство изменилось"

    def check(m: Mutation) -> str | None:
        return first_reason(
            lambda: open_check(fresh, m.repo, m.number or 0),
            lambda: comment_check(fresh, m, bot),
            lambda: holds_edge(fresh, graph, inputs, ref),
            same_fact,
        )

    return check


def plan_notify(
    result: Result,
    inputs: Inputs,
    bot: str,
    fresh: FreshReader | None,
    notes: list[dict[str, Any]],
) -> list[PlanRecord]:
    """Уведомления о выполненных предпосылках по каждому открытому адресу."""
    graph = result.graph
    records: list[PlanRecord] = []
    for ref, wait in wait_refs(graph, result.waits):
        consumer = graph.nodes.get(ref.consumer)
        if wait.verdict != "satisfied" or consumer is None or not consumer.is_open:
            continue
        where = f"{ref.src}|{ref.raw}"
        addrs = addresses(graph, ref.consumer)
        if not addrs:
            reason = no_address_reason(graph, ref.consumer)
            notes.append(
                {"action": "notify_satisfied", "wait": where, "reason": reason}
            )
            continue
        fact = fact_of(graph, inputs, ref)
        if fact is None:
            notes.append(
                {
                    "action": "notify_satisfied",
                    "wait": where,
                    "reason": "факт выполнения неизвестен",
                }
            )
            continue
        wid = wait_id(ref)
        marker = make("sat", wait=wid, evidence=h1("evidence", fact))
        text = (
            f"Предпосылка выполнена: {ref.prereq or ref.raw} ({fact}). "
            f"Ждал: {ref.consumer}."
        )
        notes += edited_findings(
            graph, threads(graph, ref.consumer), bot, "notify_satisfied"
        )
        level = authority(result, inputs, ref.consumer)
        for addr in addrs:
            events, _ = thread_events(graph, addr, bot)
            if any(m == marker for m, _ in events):
                continue
            repo, number = target(inputs, addr)
            m = Mutation("comment", repo, number, text=with_marker(text, marker))
            records.append(
                PlanRecord(
                    "notify_satisfied",
                    addr,
                    h1("rev", wid, fact),
                    1,
                    (Step(m, render(marker)),),
                    notify_check(fresh, graph, inputs, ref, fact, bot)
                    if fresh
                    else valid,
                    level,
                )
            )
    return records
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/conductor/test_notify.py tests/conductor/test_revalidate_notify.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add conductor/actions/notify.py tests/conductor/test_notify.py tests/conductor/test_revalidate_notify.py
git commit -m "conductor: notify_satisfied по тройке (ожидание, доказательство, адрес) (§7.2)"
```

---
### Task B7: `nudge` и `pr_nudge`

**Files:**
- Create: `conductor/actions/nudge.py`
- Test: `tests/conductor/test_nudge.py`, `tests/conductor/test_revalidate_nudge.py`

**Interfaces:**
- Consumes: B3, A3, A10, A11, `rank.rank_of`, `snapshot.PR_NEEDS`.
- Produces: `plan_nudges(result, inputs, bot, now, fresh, notes) -> list[PlanRecord]`; `plan_pr_nudges(result, inputs, bot, now, fresh, notes) -> list[PlanRecord]`; `pr_text(need: str, rec: dict) -> str`; `moved_since(fresh, graph, inputs, node_id, since, bot) -> str | None`; `nudge_check(...)` — `revalidate` пинка: адрес открыт, маркера нет, ожидание всё ещё pending (`fresh_fact` = None), тег на месте и период `P` тот же (`edge_period`), нового движения нет (комментарии не от App во всех тредах, коммиты с `@id`, `updated_at` реализующих PR); `pr_nudge_check(fresh, rec, need, bot)` — маркера нет; свежие основания PR (`pr_facts`): открыт, не драфт, head прежний, потребность `pr_need` та же, для `fix_pr` — тот же набор упавших проверок (имена и ссылки текста), иначе снято; не прочитано или усечено — не писать; `updated_at` — дополнительный консервативный запрет (повторное ревью F3). Пинок идёт в первый ОТКРЫТЫЙ адрес, но прошлые маркеры `nudge` ищутся во ВСЕХ тредах продюсера, включая закрытые (решение 4: смена адреса не сбрасывает нумерацию и интервал); все треды закрыты — «нет открытого адреса доставки» (решение 5). Уровень — `authority(prereq | PR, ranked=True)`.

- [ ] **Step 1: Write the failing test**

```python
"""nudge и pr_nudge (спека среза 1, §7.3–7.4)."""

from datetime import timedelta

from conductor.actions.common import wait_id, wait_refs
from conductor.actions.nudge import plan_nudges, plan_pr_nudges
from conductor.markers import h1, make, render, with_marker
from tests.conductor.fixtures import record
from tests.conductor.slice1_fixtures import BOT, NOW, REQUESTS, comment, world

PERIOD = {"todo://a/goal|todo://b/b": ["p1", "2026-09-30T00:00:00Z"]}
D = timedelta(days=1)


def _nudges(records=None, now=NOW, **extra):
    extra.setdefault("edge_periods", PERIOD)
    result, inp = world(records=records if records is not None else REQUESTS, **extra)
    notes: list[dict] = []
    return plan_nudges(result, inp, BOT, now, None, notes), notes, result


def _marker(result, n: int):
    [(ref, _)] = [
        (r, w)
        for r, w in wait_refs(result.graph, result.waits)
        if r.prereq == "todo://b/b"
    ]
    return make("nudge", wait=wait_id(ref), p=h1("period", "p1"), n=str(n))


def test_first_nudge_to_producer_address() -> None:
    recs, _, result = _nudges()
    [rec] = recs
    assert rec.subject == "b#4" and rec.steps[0].mutation.repo == "own/b"
    assert rec.steps[0].event_key == render(_marker(result, 1))
    assert render(_marker(result, 1)) in rec.steps[0].mutation.text


def test_no_age_no_nudge() -> None:
    recs, notes, _ = _nudges(edge_periods={})
    assert recs == [] and any(n.get("reason") == "возраст неизвестен" for n in notes)


def test_recent_movement_blocks_first_nudge() -> None:
    moving = record(
        "b",
        4,
        labels=["inbox"],
        body="slug: b\nfrom: devtools\n",
        comments=[comment("dev", "делаю", (NOW - D).strftime("%Y-%m-%dT%H:%M:%SZ"), 2)],
    )
    recs, _, _ = _nudges([REQUESTS[0], moving])
    assert recs == []


def test_bot_comments_are_not_movement_and_repeat_after_renudge() -> None:
    _, _, result = _nudges()
    first = with_marker("пинок", _marker(result, 1))
    at = (NOW - 8 * D).strftime("%Y-%m-%dT%H:%M:%SZ")
    thread = record(
        "b",
        4,
        labels=["inbox"],
        body="slug: b\nfrom: devtools\n",
        comments=[comment(BOT, first, at, 1)],
    )
    recs, _, _ = _nudges([REQUESTS[0], thread])
    [rec] = recs
    assert render(_marker(result, 2)) in rec.steps[0].mutation.text  # n=2


def test_no_rank_no_nudge() -> None:
    todos = {
        "a": "- [ ] g @owner:github:own @id:goal @epic:eco.bg @blocked_by:todo://b/b\n",
        "b": "- [ ] b @owner:TBD @id:b @epic:eco.bg\n",
    }
    recs, notes, _ = _nudges(todos=todos)
    assert recs == [] and any(n.get("reason") == "нет ранга" for n in notes)


PR_TODOS = {"a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1\n"}


def _pr(**fields):
    base = {
        "is_pr": True,
        "body": "@id:goal",
        "created_at": "2026-09-20T00:00:00Z",
        "ready_at": None,
        "last_commit_at": "2026-09-21T00:00:00Z",
        "reviews": [],
        "is_draft": False,
        "red_checks": [{"name": "test", "url": "https://ci/1"}],
        "ci": "red",
    }
    base.update(fields)
    return record("a", 7, **base)


def _pr_nudges(pr):
    result, inp = world(PR_TODOS, [pr])
    notes: list[dict] = []
    return plan_pr_nudges(result, inp, BOT, NOW, None, notes), notes


def test_red_pr_nudge_names_checks_without_log() -> None:
    [rec], _ = _pr_nudges(_pr())
    text = rec.steps[0].mutation.text
    assert "test" in text and "https://ci/1" in text and "лог не прочитан" in text
    assert rec.steps[0].mutation.number == 7


def test_draft_and_fresh_red_pr_are_not_nudged() -> None:
    recs, notes = _pr_nudges(_pr(is_draft=True))
    assert recs == [] and any(n.get("reason") == "драфт" for n in notes)
    recent = (NOW - D).strftime("%Y-%m-%dT%H:%M:%SZ")
    recs, _ = _pr_nudges(_pr(ready_at=recent, last_commit_at=recent))
    assert recs == []  # красный CI сам пинка не даёт


SECOND_B = record("b", 5, labels=["inbox"], body="slug: b\nfrom: devtools\n")


def _closed_first(comments):
    return record(
        "b",
        4,
        state="closed",
        labels=["inbox"],
        body="slug: b\nfrom: devtools\n",
        comments=comments,
    )


def test_prior_nudge_in_closed_address_keeps_interval() -> None:
    """Решение владельца 4: смена адреса не сбрасывает историю и интервал."""
    _, _, result = _nudges()
    at = (NOW - 2 * D).strftime("%Y-%m-%dT%H:%M:%SZ")
    first = comment(BOT, with_marker("пинок", _marker(result, 1)), at, 1)
    recs, _, _ = _nudges([REQUESTS[0], _closed_first([first]), SECOND_B])
    assert recs == []  # пинок был 2 дня назад в закрытом b#4: renudge 7 дней


def test_prior_nudge_numbering_continues_in_new_address() -> None:
    _, _, result = _nudges()
    at = (NOW - 8 * D).strftime("%Y-%m-%dT%H:%M:%SZ")
    first = comment(BOT, with_marker("пинок", _marker(result, 1)), at, 1)
    [rec] = _nudges([REQUESTS[0], _closed_first([first]), SECOND_B])[0]
    assert rec.subject == "b#5"
    assert render(_marker(result, 2)) in rec.steps[0].mutation.text


def test_all_addresses_closed_is_named() -> None:
    """Решение владельца 5: треды есть, но все закрыты — не «нет адреса»."""
    recs, notes, _ = _nudges([REQUESTS[0], _closed_first([])])
    assert recs == []
    assert any(n.get("reason") == "нет открытого адреса доставки" for n in notes)
```

`tests/conductor/test_revalidate_nudge.py` (регрессия P1-1: новое движение не от App, выполненная предпосылка, снятый или заново добавленный тег (новый период) — снято; комментарий App движением не считается; F3 — `pr_nudge` при CI red→green и green→red на том же head и `updated_at`, снятом одобрении, другом наборе упавших проверок, драфте, новом head, ошибке чтения — мутаций нет):

```python
"""nudge: свежая сверка ожидания, периода и движения (§5.1 шаг 8, §7.3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from conductor.actions.nudge import plan_nudges, plan_pr_nudges
from conductor.gh_app import CallResult
from conductor.writer import PlanRecord
from tests.conductor.fixtures import record
from tests.conductor.slice1_fixtures import BOT, NOW, REQUESTS, world
from tests.conductor.slice1_world import (
    B_DONE,
    B_OPEN,
    GOAL,
    GOAL_FREE,
    World,
    commit,
    outcomes,
)


def nudge_world(tmp: Path) -> tuple[World, list[PlanRecord]]:
    w = World(tmp)
    commit(tmp / "a", GOAL_FREE)
    p_sha = commit(tmp / "a", GOAL, "edge")
    commit(tmp / "b", B_OPEN)
    period = {"todo://a/goal|todo://b/b": [p_sha, "2026-09-30T00:00:00Z"]}
    result, inp = world({"a": GOAL, "b": B_OPEN}, REQUESTS, edge_periods=period)
    notes: list[dict[str, Any]] = []
    recs = plan_nudges(result, inp, BOT, NOW, w.fresh, notes)
    assert [r.subject for r in recs] == ["b#4"], notes
    return w, recs


def test_nudge_twin_sends(tmp_path: Path) -> None:
    w, recs = nudge_world(tmp_path)
    assert outcomes(w.run(recs, "nudge")) == [("success", "")]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            lambda w: w.client.add_comment(
                "own/b", 4, "dev", "делаю", created_at="2026-10-10T11:00:00Z"
            ),
            "новое движение",
        ),
        (lambda w: commit(w.tmp / "b", B_DONE, "done"), "ожидание больше не pending"),
        (lambda w: commit(w.tmp / "a", GOAL_FREE, "untag"), "тег ожидания снят"),
        (
            lambda w: commit(
                w.tmp / "a",
                GOAL.replace("@blocked_by:todo://b/b", "`@blocked_by:todo://b/b`"),
                "quote",
            ),
            "тег ожидания снят",
        ),
        (
            lambda w: (
                commit(w.tmp / "a", GOAL_FREE, "untag"),
                commit(w.tmp / "a", GOAL, "retag"),
            ),
            "период изменился",
        ),
    ],
)
def test_nudge_removed_when_wait_changes(tmp_path: Path, change, reason) -> None:
    w, recs = nudge_world(tmp_path)
    change(w)
    assert outcomes(w.run(recs, "nudge")) == [("removed", reason)]
    assert w.client.sent == []


def test_nudge_bot_comment_is_not_movement(tmp_path: Path) -> None:
    w, recs = nudge_world(tmp_path)
    w.client.add_comment("own/b", 4, BOT, "наш", created_at="2026-10-10T11:00:00Z")
    assert outcomes(w.run(recs, "nudge")) == [("success", "")]


# --- pr_nudge: свежая потребность PR (F3 повторного ревью) -------------------

PR_TODOS = {"a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1\n"}
RED = {
    "__typename": "CheckRun",
    "name": "test",
    "conclusion": "FAILURE",
    "detailsUrl": "https://ci/1",
}
GREEN = {
    "__typename": "CheckRun",
    "name": "test",
    "conclusion": "SUCCESS",
    "detailsUrl": "https://ci/1",
}
APPROVED = {"state": "APPROVED", "commit": {"oid": "h"}}


def pr_world(tmp: Path, ci: str, approved: bool) -> tuple[World, list[PlanRecord]]:
    """PR a!7 реализует цель фокуса; план — по CI ci и одобрению head."""
    w = World(tmp)
    red = [{"name": "test", "url": "https://ci/1"}] if ci == "red" else []
    pr = record(
        "a",
        7,
        is_pr=True,
        body="@id:goal",
        created_at="2026-09-20T00:00:00Z",
        last_commit_at="2026-09-21T00:00:00Z",
        reviews=[],
        is_draft=False,
        red_checks=red,
        ci=ci,
        approved_at_head=approved,
    )
    w.client.pull(
        "own/a",
        7,
        head={"sha": "h"},
        updated_at=pr["updated_at"],
        checks=[RED if ci == "red" else GREEN],
        reviews=[APPROVED] if approved else [],
    )
    result, inp = world(PR_TODOS, [pr])
    recs = plan_pr_nudges(result, inp, BOT, NOW, w.fresh, [])
    assert [r.subject for r in recs] == ["a!7"]
    return w, recs


@pytest.mark.parametrize(("ci", "approved"), [("red", False), ("green", True)])
def test_pr_nudge_twin_sends(tmp_path: Path, ci: str, approved: bool) -> None:
    w, recs = pr_world(tmp_path, ci, approved)
    assert outcomes(w.run(recs, "pr_nudge")) == [("success", "")]


@pytest.mark.parametrize(
    ("ci", "approved", "change", "reason"),
    [
        # тот же head и updated_at: CI перезапущен и позеленел — «CI красный» устарел
        ("red", False, {"checks": [GREEN]}, "потребность PR изменилась"),
        # и обратно: готовый к мержу PR покраснел
        ("green", True, {"checks": [RED]}, "потребность PR изменилась"),
        # одобрение снято — уже не «готов к мержу»
        ("green", True, {"reviews": []}, "потребность PR изменилась"),
        # красный, но упала другая проверка — текст называет не те имена
        (
            "red",
            False,
            {"checks": [{**RED, "name": "lint", "detailsUrl": "https://ci/2"}]},
            "набор упавших проверок изменился",
        ),
        ("red", False, {"draft": True}, "PR закрыт или драфт"),
        ("red", False, {"head": {"sha": "h2"}}, "новая голова PR"),
    ],
)
def test_pr_nudge_removed_when_need_changes(
    tmp_path: Path, ci: str, approved: bool, change, reason
) -> None:
    w, recs = pr_world(tmp_path, ci, approved)
    w.client.pull("own/a", 7, **change)
    assert outcomes(w.run(recs, "pr_nudge")) == [("removed", reason)]
    assert w.client.sent == []


def test_pr_nudge_unreadable_checks_do_not_write(tmp_path: Path) -> None:
    w, recs = pr_world(tmp_path, "red", False)
    w.client.override[("POST", "/graphql")] = CallResult("uncertain", None)
    assert outcomes(w.run(recs, "pr_nudge")) == [
        ("removed", "основания PR не прочитаны")
    ]
    assert w.client.sent == []


def test_nudge_quoted_id_readded_edge_is_new_period(tmp_path: Path) -> None:
    """Раунд 4 (R4-2): ребро снято и добавлено при `@id:"goal"` — новый период."""
    w, recs = nudge_world(tmp_path)
    quote = '@id:"goal"'
    commit(tmp_path / "a", GOAL.replace("@id:goal", quote), "quote id")
    commit(tmp_path / "a", GOAL_FREE.replace("@id:goal", quote), "drop edge")
    commit(tmp_path / "a", GOAL.replace("@id:goal", quote), "readd edge")
    assert outcomes(w.run(recs, "nudge")) == [("removed", "период изменился")]
    assert w.client.sent == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_nudge.py tests/conductor/test_revalidate_nudge.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
"""nudge и pr_nudge (спека среза 1, §7.3–7.4).

Возраст — от периода P (ребро) или от последнего ready_for_review (PR);
тишина — от max(начало возраста, последнее движение не от App). Пинок —
только по позициям с рангом; красный CI пинка сам не даёт. Пинок идёт в
первый открытый адрес, а нумерация и интервал — по ожиданию и периоду во
ВСЕХ тредах продюсера (решение владельца 4): смена адреса их не сбрасывает.
Перед отправкой — свежая сверка ожидания, периода и движения (шаг 8 §5.1).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from conductor import facts
from conductor.actions.common import (
    WaitRef,
    addresses,
    authority,
    due,
    edited_findings,
    entry_rank,
    fresh_fact,
    fresh_item,
    movement,
    node_repo_item,
    pr_activity,
    target,
    thread_events,
    threads,
    wait_id,
    wait_refs,
)
from conductor.fresh import (
    FreshReader,
    comment_check,
    first_reason,
    open_check,
    pr_need,
    valid,
)
from conductor.gh_write import Mutation
from conductor.graph import Graph
from conductor.inputs import Inputs
from conductor.markers import h1, make, render, with_marker
from conductor.opstate import parse_ts
from conductor.rank import rank_of
from conductor.snapshot import PR_NEEDS, Result
from conductor.sources_git import GitError, last_commit_mentioning
from conductor.writer import PlanRecord, Step


def _limits(result: Result) -> tuple[timedelta, timedelta]:
    limits = result.roadmap.limits
    return timedelta(days=limits["stale_after_days"]), timedelta(
        days=limits["renudge_after_days"]
    )


def _prior(
    graph: Any, addrs: list[str], bot: str, kind: str, **match: str
) -> list[tuple[int, datetime]]:
    """Действительные маркеры пинка во всех тредах addrs (открытых и закрытых)."""
    return [
        (int(m.get("n")), parse_ts(c["created_at"]))
        for addr in addrs
        for m, c in thread_events(graph, addr, bot)[0]
        if m.kind == kind and all(m.get(k) == v for k, v in match.items())
    ]


def _newer(stamp: str | None, since: datetime | None) -> bool:
    return bool(stamp) and (since is None or parse_ts(stamp or "") > since)


def moved_since(
    fresh: FreshReader,
    graph: Graph,
    inputs: Inputs,
    node_id: str,
    since: datetime | None,
    bot: str,
) -> str | None:
    """Новое движение по узлу работы после since (свежие чтения, §7.3).

    Комментарии не от App во всех тредах, коммиты с `@id`, открытые PR с
    `implements` (по `updated_at` PR — консервативно: изменение снимает пинок
    до следующего прогона, который посчитает движение заново).
    """
    for member in sorted(graph.members(node_id)):
        node = graph.nodes.get(member)
        if node is None:
            continue
        if node.kind == "item":
            parts = node_repo_item(member)
            clone = fresh.git(parts[0]) if parts else None
            if parts is None or clone is None:
                return "история не прочитана"
            try:
                when = last_commit_mentioning(clone[0], clone[1], f"@id:{parts[1]}")
            except GitError:
                return "история не прочитана"
            if _newer(when, since):
                return "новое движение"
            continue
        repo, number = target(inputs, member)
        comments = fresh.comments(repo, number)
        if comments is None:
            return "тред не прочитан"
        if any(c["author"] != bot and _newer(c["created_at"], since) for c in comments):
            return "новое движение"
    for member in sorted(graph.members(node_id)):
        for edge in graph.into(member, "implements"):
            pr = graph.nodes.get(edge.src)
            if pr is None or not pr.is_open:
                continue
            repo, number = target(inputs, edge.src)
            pull = fresh.pull(repo, number)
            planned = (graph.records.get(edge.src) or {}).get("updated_at")
            if pull is None or pull.get("updated_at") != planned:
                return "новое движение по PR"
    return None


def nudge_check(
    fresh: FreshReader,
    graph: Graph,
    inputs: Inputs,
    ref: WaitRef,
    p_sha: str,
    moved: datetime | None,
    bot: str,
) -> Callable[[Mutation], str | None]:
    """Шаг 8: адрес открыт, маркера нет, ожидание pending, период тот же,
    движения после планирования нет."""

    def pending() -> str | None:
        fact, problem = fresh_fact(fresh, graph, inputs, ref)
        if problem is not None:
            return problem
        return None if fact is None else "ожидание больше не pending"

    def same_period() -> str | None:
        state, clone, problem = fresh_item(fresh, ref.src)
        if problem is not None or clone is None:
            return problem or "период не прочитан"
        if state is None or ref.raw not in state.blocked_by:
            return "тег ожидания снят"
        repo_key, item = node_repo_item(ref.src) or ("", "")
        try:
            period = facts.edge_period(clone[0], clone[1], repo_key, item, ref.raw)
        except GitError:
            return "период не прочитан"
        return None if period and period[0] == p_sha else "период изменился"

    def check(m: Mutation) -> str | None:
        return first_reason(
            lambda: open_check(fresh, m.repo, m.number or 0),
            lambda: comment_check(fresh, m, bot),
            pending,
            same_period,
            lambda: moved_since(fresh, graph, inputs, ref.prereq or "", moved, bot),
        )

    return check


def plan_nudges(
    result: Result,
    inputs: Inputs,
    bot: str,
    now: datetime,
    fresh: FreshReader | None,
    notes: list[dict[str, Any]],
) -> list[PlanRecord]:
    """Пинки продюсерам по ожиданиям pending (§7.3)."""
    graph = result.graph
    stale, renudge = _limits(result)
    records: list[PlanRecord] = []
    for ref, wait in wait_refs(graph, result.waits):
        if wait.verdict != "pending" or ref.prereq is None:
            continue
        where = f"{ref.src}|{ref.raw}"
        if rank_of(graph, result.roadmap, ref.prereq) is None:
            notes.append({"action": "nudge", "wait": where, "reason": "нет ранга"})
            continue
        addrs, every = addresses(graph, ref.prereq), threads(graph, ref.prereq)
        if not addrs:
            reason = "нет открытого адреса доставки" if every else "нет адреса"
            notes.append({"action": "nudge", "wait": where, "reason": reason})
            continue
        period = inputs.edge_periods.get(where)
        if period is None:
            notes.append(
                {"action": "nudge", "wait": where, "reason": "возраст неизвестен"}
            )
            continue
        p_sha, p_date = period
        age_start = parse_ts(p_date)
        moved = movement(graph, inputs, ref.prereq, bot)
        silent_since = max(age_start, moved) if moved else age_start
        addr, wid, p = addrs[0], wait_id(ref), h1("period", p_sha)
        notes += edited_findings(graph, every, bot, "nudge")
        prior = _prior(graph, every, bot, "nudge", wait=wid, p=p)
        n = due(now, age_start, silent_since, moved, prior, stale, renudge)
        if n is None:
            continue
        marker = make("nudge", wait=wid, p=p, n=str(n))
        text = (
            f"Ждём {ref.prereq}: от него зависит {ref.consumer} с {p_date[:10]}. "
            f"Следующий шаг — довести {ref.prereq} или ответить в этом треде."
        )
        repo, number = target(inputs, addr)
        m = Mutation("comment", repo, number, text=with_marker(text, marker))
        records.append(
            PlanRecord(
                "nudge",
                addr,
                h1("rev", wid, p, str(n)),
                1,
                (Step(m, render(marker)),),
                nudge_check(fresh, graph, inputs, ref, p_sha, moved, bot)
                if fresh
                else valid,
                authority(result, inputs, ref.prereq, ranked=True),
            )
        )
    return records


def pr_text(need: str, rec: dict[str, Any]) -> str:
    """Текст пинка PR: что ждёт и следующий шаг; для красного — имена проверок."""
    if need == "fix_pr":
        checks = "; ".join(
            f"{c['name']} — {c['url']}" for c in rec.get("red_checks", [])
        )
        return (
            f"CI красный на head: {checks or 'проверки не названы'}; "
            "лог не прочитан conductor — нужен разбор автором."
        )
    if need == "review":
        return "PR ждёт ревью на текущем head."
    if need == "wait_ci":
        return "PR ждёт завершения проверок на текущем head."
    return (
        "PR готов к мержу по данным conductor; мерж — за контуром мержа или владельцем."
    )


def _red(checks: list[dict[str, str]]) -> set[tuple[str, str]]:
    return {(c["name"], c["url"]) for c in checks}


def pr_nudge_check(
    fresh: FreshReader, rec: dict[str, Any], need: str, bot: str
) -> Callable[[Mutation], str | None]:
    """Шаг 8: свежие основания потребности PR на head (проверки, одобрение)
    дают ту же потребность и, для красного CI, те же упавшие проверки, что в
    тексте плана; PR открыт, не драфт, head прежний; маркера нет.
    `updated_at` — дополнительный консервативный запрет, не замена чтения."""

    def same_need(m: Mutation) -> str | None:
        facts = fresh.pr_facts(m.repo, m.number or 0)
        if facts is None:
            return "основания PR не прочитаны"
        if not facts["open"] or facts["draft"]:
            return "PR закрыт или драфт"
        if facts["head"] != rec.get("head_sha"):
            return "новая голова PR"
        if pr_need(facts) != need:
            return "потребность PR изменилась"
        if need == "fix_pr" and _red(facts["red"]) != _red(rec.get("red_checks", [])):
            return "набор упавших проверок изменился"
        if facts["updated_at"] != rec.get("updated_at"):
            return "новое движение по PR"
        return None

    def check(m: Mutation) -> str | None:
        return first_reason(lambda: comment_check(fresh, m, bot), lambda: same_need(m))

    return check


def plan_pr_nudges(
    result: Result,
    inputs: Inputs,
    bot: str,
    now: datetime,
    fresh: FreshReader | None,
    notes: list[dict[str, Any]],
) -> list[PlanRecord]:
    """Пинки по застоявшимся PR позиций с рангом (§7.4)."""
    graph = result.graph
    stale, renudge = _limits(result)
    records: list[PlanRecord] = []
    for node in sorted(graph.nodes.values(), key=lambda n: n.node_id):
        if node.kind != "pr" or not node.is_open:
            continue
        assessment = result.assessments.get(node.node_id)
        if assessment is None or assessment.need not in PR_NEEDS:
            continue
        if entry_rank(result, node.node_id) is None:
            notes.append(
                {"action": "pr_nudge", "pr": node.node_id, "reason": "нет ранга"}
            )
            continue
        rec = graph.records.get(node.node_id) or {}
        if rec.get("is_draft"):
            notes.append({"action": "pr_nudge", "pr": node.node_id, "reason": "драфт"})
            continue
        start_raw = rec.get("ready_at") or rec.get("created_at")
        if not start_raw:
            notes.append(
                {
                    "action": "pr_nudge",
                    "pr": node.node_id,
                    "reason": "возраст неизвестен",
                }
            )
            continue
        age_start = parse_ts(start_raw)
        activity = [parse_ts(s) for s in pr_activity(rec, bot)]
        moved = max(activity) if activity else None
        silent_since = max(age_start, moved) if moved else age_start
        pr = h1("pr", node.node_id)
        notes += edited_findings(graph, [node.node_id], bot, "pr_nudge")
        prior = _prior(graph, [node.node_id], bot, "prnudge", pr=pr)
        n = due(now, age_start, silent_since, moved, prior, stale, renudge)
        if n is None:
            continue
        marker = make("prnudge", pr=pr, n=str(n))
        repo, number = target(inputs, node.node_id)
        m = Mutation(
            "comment",
            repo,
            number,
            text=with_marker(pr_text(assessment.need, rec), marker),
        )
        records.append(
            PlanRecord(
                "pr_nudge",
                node.node_id,
                h1("rev", pr, str(n)),
                1,
                (Step(m, render(marker)),),
                pr_nudge_check(fresh, rec, assessment.need, bot) if fresh else valid,
                authority(result, inputs, node.node_id, ranked=True),
            )
        )
    return records
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/conductor/test_nudge.py tests/conductor/test_revalidate_nudge.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add conductor/actions/nudge.py tests/conductor/test_nudge.py tests/conductor/test_revalidate_nudge.py
git commit -m "conductor: nudge и pr_nudge — возраст, тишина, две ветки повтора (§7.3–7.4)"
```

---

### Task B8: `close_shipped`

**Files:**
- Create: `conductor/actions/close.py`
- Test: `tests/conductor/test_close.py`, `tests/conductor/test_revalidate_close.py`

**Interfaces:**
- Consumes: B3, B5 (`shipped_state`), A3, A10, A11, `rank.rank_of`.
- Produces: `QueueRef(repo, number, owner)`; `plan_close(result, inputs, bot, close_answers: dict[str, dict], fresh, notes, queue: QueueRef | None = None) -> list[PlanRecord]` (`close_answers`: subject → вопрос, на который исполнимый ответ `close`); `close_check(fresh, graph, inputs, subject, period, basis, question, queue, rendered, bot)` — `revalidate` ПЕРЕД КАЖДЫМ шагом: issue открыт, период тот же (последний `reopened` timeline или `0`), основание в силе — (а) PR влит с тем же merge SHA и всё ещё закрывает issue; (б) ответ `close` действителен по свежим комментариям очереди И основание самого вопроса пересчитано (`question_basis`: сначала пункт на свежем origin каноническим разбором — есть и выполнен (`fresh_item`), затем склейка заявки с ним по свежей записи правилами ядра (`fresh_accepted`); снимок графа прогона существования пункта не доказывает; влитый и закрывающий PR; переоткрытие) с той же идентичностью вопроса — ответ не заменяет доказательство (повторное ревью F1); комментарий — маркера ещё нет; закрытие — действительный маркер подтверждения с полным текущим ключом `rendered` есть, в том числе при доведении из прошлого прогона (F2). Уровень — `authority(subject, ranked=вопроса нет)`.

- [ ] **Step 1: Write the failing test**

```python
"""close_shipped (спека среза 1, §7.5)."""

from conductor.actions.close import plan_close
from conductor.markers import h1, make, with_marker
from tests.conductor.fixtures import record
from tests.conductor.slice1_fixtures import BOT, comment, world

TODOS = {"a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 @blocked_by:a#1\n"}


def _extras(
    period: str = "0",
    start: str = "2026-09-01T00:00:00Z",
    merged_at: str = "2026-09-10T00:00:00Z",
):
    return {
        "a#1": {
            "created_at": "2026-09-01T00:00:00Z",
            "period": period,
            "period_start": start,
            "closed_by": [
                {
                    "repo": "a",
                    "number": 5,
                    "merged": True,
                    "merged_at": merged_at,
                    "merge_sha": "m5",
                    "base_is_default": True,
                }
            ],
        }
    }


def _plan(issue=None, answers=None, **extra):
    result, inp = world(TODOS, [issue or record("a", 1)], **extra)
    notes: list[dict] = []
    return plan_close(result, inp, BOT, answers or {}, None, notes), notes


def ops(recs) -> list[list[str]]:
    return [[s.mutation.op for s in r.steps] for r in recs]


def test_basis_a_comment_then_close() -> None:
    recs, _ = _plan(issue_extras=_extras())
    assert ops(recs) == [["comment", "close"]]
    assert "m5" in recs[0].steps[0].mutation.text


def test_marker_present_only_close() -> None:
    marker = make(
        "close",
        node=h1("node", "a#1"),
        period=h1("period", "0"),
        evidence=h1("evidence", "merged:m5"),
    )
    issue = record(
        "a",
        1,
        comments=[comment(BOT, with_marker("x", marker), "2026-09-11T00:00:00Z", 1)],
    )
    recs, _ = _plan(issue, issue_extras=_extras())
    assert ops(recs) == [["close"]]


def test_marker_of_other_evidence_does_not_count() -> None:
    other = make(
        "close",
        node=h1("node", "a#1"),
        period=h1("period", "0"),
        evidence=h1("evidence", "merged:mX"),
    )
    issue = record(
        "a",
        1,
        comments=[comment(BOT, with_marker("x", other), "2026-09-11T00:00:00Z", 1)],
    )
    recs, _ = _plan(issue, issue_extras=_extras())
    assert ops(recs) == [["comment", "close"]]


def test_reopened_after_any_close_needs_answer() -> None:
    reopened = _extras(period="RE_1", start="2026-09-20T00:00:00Z")
    recs, notes = _plan(issue_extras=reopened)
    assert recs == [] and {"finding": "GR-REOPENED", "subject": "a#1"} in notes
    q = {"question_id": "abcd1234", "evidence": "reopened|period:RE_1"}
    recs, _ = _plan(issue_extras=reopened, answers={"a#1": q})
    assert (
        ops(recs) == [["comment", "close"]]
        and "Q-abcd1234" in recs[0].steps[0].mutation.text
    )


def test_pr_merged_after_reopen_is_new_basis() -> None:
    later = _extras(
        period="RE_1", start="2026-09-20T00:00:00Z", merged_at="2026-09-25T00:00:00Z"
    )
    recs, _ = _plan(issue_extras=later)
    assert ops(recs) == [["comment", "close"]]


def test_unranked_issue_is_not_closed() -> None:
    result, inp = world(
        {"a": "- [ ] g @owner:github:own @id:goal @epic:eco.bg @blocked_by:a#1\n"},
        [record("a", 1)],
        issue_extras=_extras(),
    )
    assert plan_close(result, inp, BOT, {}, None, []) == []
```

`tests/conductor/test_revalidate_close.py` (регрессия P1-1: удалённый или конфликтующий ответ, переоткрытие (новый период), не подтверждённый или больше не закрывающий PR, закрытый субъект, появившийся маркер; F1 — снятая `[x]`, исчезновение пункта по каноническому разбору (удалён, переименован, `@id` в бэктиках — раунд 3, Codex) для заявки с меткой и для шапки без метки, смена slug и снятие распознавания заявки (метка `inbox` и `from:` сняты, `slug` остался — ядро склейку больше не видит) при ответе `close`, двойники — заявка с меткой, шапка без метки, снятие одной метки при целой шапке; F2 — удалённое, отредактированное или другое подтверждение перед закрытием, в том числе при доведении; до первого шага и между шагами — закрытия нет):

```python
"""close_shipped: свежая сверка периода и основания перед каждым шагом (§7.5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from conductor.actions.close import SHIPPED, QueueRef, plan_close
from conductor.actions.owner_queue import make_question, queue_questions
from conductor.markers import h1, make, parse_body, with_marker
from conductor.writer import PlanRecord
from tests.conductor.fixtures import record
from tests.conductor.slice1_fixtures import BOT, world
from tests.conductor.slice1_world import (
    UMB,
    World,
    commit,
    outcomes,
)

CLOSE_TODOS = {
    "a": "- [ ] g @owner:github:own @id:goal @epic:eco.focus1 @blocked_by:a#1\n"
}
# вопрос о PR, влитом не в ветку по умолчанию: основание (б) — ответ владельца
Q = make_question("a#1", SHIPPED, "a!5 влит|period:0", "закрыть?", ("close", "keep"))


def extras(closed_by: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "a#1": {
            "created_at": "2026-09-01T00:00:00Z",
            "period": "0",
            "period_start": "2026-09-01T00:00:00Z",
            "closed_by": closed_by,
        }
    }


MERGED = {
    "repo": "a",
    "number": 5,
    "merged": True,
    "merged_at": "2026-09-10T00:00:00Z",
    "merge_sha": "m5",
    "base_is_default": True,
}


def close_world(tmp: Path, by_answer: bool) -> tuple[World, list[PlanRecord]]:
    w = World(tmp)
    w.client.issue(UMB, 9, labels=["owner-queue"], user=BOT)
    w.client.add_comment(UMB, 9, "own", f"Q-{Q['question_id']}: close")
    w.client.pull("own/a", 5, merged=True, merge_commit_sha="m5", closing=["own/a#1"])
    result, inp = world(
        CLOSE_TODOS,
        [record("a", 1)],
        issue_extras=extras([] if by_answer else [MERGED]),
    )
    notes: list[dict[str, Any]] = []
    answers = {"a#1": Q} if by_answer else {}
    queue = QueueRef(UMB, 9, "own")
    recs = plan_close(result, inp, BOT, answers, w.fresh, notes, queue)
    assert [[s.mutation.op for s in r.steps] for r in recs] == [["comment", "close"]]
    return w, recs


@pytest.mark.parametrize("by_answer", [True, False])
def test_close_twin_closes(tmp_path: Path, by_answer: bool) -> None:
    w, recs = close_world(tmp_path, by_answer)
    assert outcomes(w.run(recs, "close_shipped")) == [("success", "")] * 2
    assert w.client.issue("own/a", 1)["state"] == "closed"


def _reopen(w: World) -> None:
    w.client.issue("own/a", 1, timeline=[{"event": "reopened", "node_id": "RE_9"}])


def _drop_answer(w: World) -> None:
    w.client.issue(UMB, 9)["comments"].clear()


def _conflict(w: World) -> None:
    w.client.add_comment(UMB, 9, "own", f"Q-{Q['question_id']}: keep")


def _unlink_pr(w: World) -> None:
    w.client.pull("own/a", 5, closing=[])


@pytest.mark.parametrize(
    ("by_answer", "change", "reason"),
    [
        (True, _drop_answer, "ответ изменился"),
        (True, _conflict, "ответ изменился"),
        (True, _reopen, "период изменился"),
        (False, _reopen, "период изменился"),
        (
            False,
            lambda w: w.client.pulls.clear(),
            "основание (а): PR основания не прочитан",
        ),
        (False, _unlink_pr, "основание (а): PR больше не закрывает issue"),
        (True, _unlink_pr, "PR больше не закрывает issue"),
    ],
)
def test_close_removed_before_first_step(
    tmp_path: Path, by_answer: bool, change, reason
) -> None:
    w, recs = close_world(tmp_path, by_answer)
    change(w)
    reports = w.run(recs, "close_shipped")
    assert outcomes(reports) == [("removed", reason), ("skipped_dependent", "removed")]
    assert w.client.sent == []


@pytest.mark.parametrize(
    ("by_answer", "change", "reason"),
    [
        (True, _drop_answer, "ответ изменился"),
        (True, _reopen, "период изменился"),
        (False, _reopen, "период изменился"),
    ],
)
def test_close_removed_between_steps(
    tmp_path: Path, by_answer: bool, change, reason
) -> None:
    """Комментарий отправлен, затем основание изменилось — закрытия нет."""
    w, recs = close_world(tmp_path, by_answer)
    reports = w.run(recs, "close_shipped", between=lambda: change(w))
    assert outcomes(reports) == [("success", ""), ("removed", reason)]
    assert [s[0] for s in w.client.sent] == ["POST"]
    assert w.client.issue("own/a", 1)["state"] == "open"


def test_close_removed_when_issue_closed_meanwhile(tmp_path: Path) -> None:
    w, recs = close_world(tmp_path, by_answer=False)
    w.client.issue("own/a", 1, state="closed", state_reason="completed")
    reports = w.run(recs, "close_shipped")
    assert outcomes(reports)[0] == ("removed", "субъект закрыт")
    assert w.client.sent == []


def test_close_comment_removed_when_marker_appeared(tmp_path: Path) -> None:
    """Маркер уже есть по свежему чтению — комментарий снят (и закрытие —
    до следующего прогона, который спланирует только шаг 2)."""
    w, recs = close_world(tmp_path, by_answer=False)
    w.client.add_comment("own/a", 1, BOT, recs[0].steps[0].mutation.text)
    reports = w.run(recs, "close_shipped")
    assert outcomes(reports)[0] == ("removed", "маркер уже есть")
    assert w.client.sent == []


def _delete_confirmation(w: World) -> None:
    w.client.issue("own/a", 1)["comments"].clear()


def _edit_confirmation(w: World) -> None:
    w.client.issue("own/a", 1)["comments"][-1]["updated_at"] = "2026-10-02T00:00:00Z"


@pytest.mark.parametrize("by_answer", [True, False])
@pytest.mark.parametrize("change", [_delete_confirmation, _edit_confirmation])
def test_close_needs_valid_confirmation(
    tmp_path: Path, by_answer: bool, change
) -> None:
    """F2: подтверждение удалено или отредактировано после шага 1 — закрытия нет."""
    w, recs = close_world(tmp_path, by_answer)
    reports = w.run(recs, "close_shipped", between=lambda: change(w))
    assert outcomes(reports) == [
        ("success", ""),
        ("removed", "подтверждения в треде нет"),
    ]
    assert w.client.issue("own/a", 1)["state"] == "open"


def test_close_completion_from_previous_run_needs_confirmation(tmp_path: Path) -> None:
    """F2, доведение: маркер был при планировании (план — только шаг 2), но
    исчез до отправки или в треде маркер другого доказательства."""
    w, recs = close_world(tmp_path, by_answer=False)
    text = recs[0].steps[0].mutation.text
    only_close = PlanRecord(
        recs[0].action,
        recs[0].subject,
        recs[0].revision,
        1,
        recs[0].steps[1:],
        recs[0].revalidate,
        recs[0].authority,
    )
    planned = parse_body(text)
    assert planned is not None
    other = make(
        "close",
        node=planned.get("node"),
        period=planned.get("period"),
        evidence=h1("evidence", "merged:другой"),
    )
    w.client.add_comment("own/a", 1, BOT, with_marker("x", other))
    assert outcomes(w.run([only_close], "close_shipped")) == [
        ("removed", "подтверждения в треде нет")
    ]
    w.client.add_comment("own/a", 1, BOT, text)
    assert outcomes(w.run([only_close], "close_shipped")) == [("success", "")]


DONE = "- [x] g @owner:github:own @id:goal @epic:eco.focus1\n"


HEADER = "slug: goal\nfrom: devtools\n"


def legacy_world(
    tmp: Path, labels: tuple[str, ...] = ("inbox",)
) -> tuple[World, list[PlanRecord]]:
    """Legacy-заявка a#1 склеена со slug goal (метка inbox или распознанная
    шапка протокола без метки — правила ядра); пункт выполнен — ядро задаёт
    GR-SHIPPED-OPEN, владелец ответил close (основание (б))."""
    w = World(tmp)
    commit(tmp / "a", DONE)
    body = HEADER
    w.client.issue("own/a", 1, body=body, labels=list(labels))
    result, inp = world(
        {"a": DONE},
        [record("a", 1, labels=list(labels), body=body)],
        issue_extras=extras([]),
    )
    q = next(q for q in queue_questions(result, inp, []) if q["reason"] == SHIPPED)
    w.client.issue(UMB, 9, labels=["owner-queue"], user=BOT)
    w.client.add_comment(UMB, 9, "own", f"Q-{q['question_id']}: close")
    recs = plan_close(
        result, inp, BOT, {"a#1": q}, w.fresh, [], QueueRef(UMB, 9, "own")
    )
    assert len(recs) == 1
    return w, recs


@pytest.mark.parametrize("labels", [("inbox",), ()])
def test_legacy_twin_closes(tmp_path: Path, labels: tuple[str, ...]) -> None:
    """Двойники: заявка с меткой inbox и корректная шапка без метки."""
    w, recs = legacy_world(tmp_path, labels)
    assert outcomes(w.run(recs, "close_shipped")) == [("success", "")] * 2


def test_label_removed_but_header_kept_still_closes(tmp_path: Path) -> None:
    """Ядро признаёт и шапку без метки: снятие одной метки склейку не снимает."""
    w, recs = legacy_world(tmp_path)
    w.client.issue("own/a", 1, labels=[])
    assert outcomes(w.run(recs, "close_shipped")) == [("success", "")] * 2


def _unrecognize(w: World) -> None:
    """Метка inbox и поле from сняты, slug остался — ядро склейку не видит."""
    w.client.issue("own/a", 1, body="slug: goal\n", labels=[])


def test_core_no_longer_glues_after_unrecognize() -> None:
    body = "slug: goal\n"
    result, _ = world({"a": DONE}, [record("a", 1, body=body, labels=[])])
    assert not any(f.code == SHIPPED for f in result.findings)


def _retract(w: World) -> None:
    commit(w.tmp / "a", DONE.replace("[x]", "[ ]"), "retract completion")


def _relink(w: World) -> None:
    w.client.issue("own/a", 1, body="slug: other\nfrom: devtools\n")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (_retract, "пункт больше не выполнен"),
        (_relink, "склейка заявки с пунктом снята"),
        (_unrecognize, "склейка заявки с пунктом снята"),
    ],
)
def test_legacy_basis_rechecked_before_first_step(
    tmp_path: Path, change, reason
) -> None:
    """F1: ответ close не заменяет доказательство — снятие [x] или склейки
    до отправки снимает запись (воспроизведение владельца)."""
    w, recs = legacy_world(tmp_path)
    change(w)
    assert outcomes(w.run(recs, "close_shipped"))[0] == ("removed", reason)
    assert w.client.sent == [] and w.client.issue("own/a", 1)["state"] == "open"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (_retract, "пункт больше не выполнен"),
        (_relink, "склейка заявки с пунктом снята"),
        (_unrecognize, "склейка заявки с пунктом снята"),
    ],
)
def test_legacy_basis_rechecked_between_steps(tmp_path: Path, change, reason) -> None:
    w, recs = legacy_world(tmp_path)
    reports = w.run(recs, "close_shipped", between=lambda: change(w))
    assert outcomes(reports) == [("success", ""), ("removed", reason)]
    assert w.client.issue("own/a", 1)["state"] == "open"


CHANGED = {
    "delete": "# пунктов нет\n",
    "rename": DONE.replace("@id:goal", "@id:other"),
    "backtick": DONE.replace("@id:goal", "`@id:goal`"),
}


@pytest.mark.parametrize("labels", [("inbox",), ()], ids=["inbox", "header"])
@pytest.mark.parametrize("between", [False, True], ids=["before", "between"])
@pytest.mark.parametrize("change", sorted(CHANGED))
def test_item_identity_rechecked_canonically(
    tmp_path: Path, labels: tuple[str, ...], between: bool, change: str
) -> None:
    """Раунд 3 (Codex): пункт исчез по каноническому разбору — удалён,
    переименован или `@id` в бэктиках; снимок прогона его помнит, но ядро
    на свежем TODO не видит ни пункта, ни склейки — записи нет."""
    w, recs = legacy_world(tmp_path, labels)
    text = CHANGED[change]
    rebuilt, _ = world({"a": text}, [record("a", 1, labels=list(labels), body=HEADER)])
    assert "todo://a/goal" not in rebuilt.graph.nodes
    assert not rebuilt.graph.out("a#1", "accepted_as")

    def remove() -> None:
        commit(tmp_path / "a", text, "identity gone")

    if not between:
        remove()
    reports = w.run(recs, "close_shipped", between=remove if between else None)
    assert ("removed", "пункта больше нет") in outcomes(reports)
    assert w.client.issue("own/a", 1)["state"] == "open"
    assert [s[0] for s in w.client.sent] == (["POST"] if between else [])


@pytest.mark.parametrize(
    "body",
    [
        "slug: goal\n",
        "описание\nslug: goal\nfrom: devtools\n",
        "slug: goal\nfrom: unknown-repo\n",
    ],
)
def test_header_without_label_must_be_valid(tmp_path: Path, body: str) -> None:
    """Без метки ядро требует полную шапку с известным отправителем."""
    w, recs = legacy_world(tmp_path)
    w.client.issue("own/a", 1, body=body, labels=[])
    rebuilt, _ = world({"a": DONE}, [record("a", 1, body=body, labels=[])])
    assert not rebuilt.graph.out("a#1", "accepted_as")
    assert outcomes(w.run(recs, "close_shipped"))[0] == (
        "removed",
        "склейка заявки с пунктом снята",
    )


def test_inbox_label_does_not_require_header(tmp_path: Path) -> None:
    """С меткой inbox ядро склеивает и по одному slug — двойник."""
    w, recs = legacy_world(tmp_path)
    w.client.issue("own/a", 1, body="slug: goal\n", labels=["inbox"])
    assert outcomes(w.run(recs, "close_shipped")) == [("success", "")] * 2


@pytest.mark.parametrize("labels", [("inbox",), ()], ids=["inbox", "header"])
@pytest.mark.parametrize("between", [False, True], ids=["before", "between"])
def test_duplicate_id_refuses_write(
    tmp_path: Path, labels: tuple[str, ...], between: bool
) -> None:
    """Раунд 4 (Codex, R4-1): `@id` дважды — ядро берёт последний (открытый)
    узел; неоднозначная идентичность снимает запись, а не выбирает первый."""
    w, recs = legacy_world(tmp_path, labels)
    text = DONE + DONE.replace("[x]", "[ ]")
    rebuilt, _ = world({"a": text}, [record("a", 1, labels=list(labels), body=HEADER)])
    assert rebuilt.graph.nodes["todo://a/goal"].is_open

    def change() -> None:
        commit(tmp_path / "a", text, "duplicate id")

    if not between:
        change()
    reports = w.run(recs, "close_shipped", between=change if between else None)
    assert ("removed", "идентичность пункта неоднозначна (@id дважды)") in outcomes(
        reports
    )
    assert w.client.issue("own/a", 1)["state"] == "open"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_close.py tests/conductor/test_revalidate_close.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
"""close_shipped (спека среза 1, §7.5).

Основание действует только в своём периоде открытия. Маркер — с полным
ключом (node, period, evidence); маркер другого доказательства не считается.
Перед КАЖДЫМ шагом (шаг 8 §5.1) — свежая сверка: issue открыт, период тот
же, основание в силе — PR (а) влит с тем же merge SHA и всё ещё закрывает
issue, либо (б) ответ `close` по свежим комментариям очереди действителен И
основание самого вопроса пересчитано по свежим данным (склейка с пунктом и
его выполненность, влитый PR, переоткрытие) с той же идентичностью вопроса:
ответ разрешает закрытие, но не заменяет доказательство. Перед закрытием —
действительный маркер подтверждения с полным текущим ключом в треде.
Никогда: переоткрывать, закрывать not_planned, закрывать PR.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from conductor.actions.answers import answers_and_resolutions
from conductor.actions.common import (
    authority,
    edited_findings,
    fresh_accepted,
    fresh_item,
    target,
    thread_events,
)
from conductor.actions.shipped import shipped_state
from conductor.fresh import (
    FreshReader,
    comment_check,
    first_reason,
    marker_check,
    open_check,
    valid,
)
from conductor.gh_write import Mutation
from conductor.graph import Graph
from conductor.inputs import Inputs
from conductor.markers import h1, make, render, with_marker
from conductor.rank import rank_of
from conductor.snapshot import Result, question_id
from conductor.writer import PlanRecord, Step

SHIPPED = "GR-SHIPPED-OPEN"
DONE, MERGED, REOPENED = " выполнен", " влит", "reopened"


@dataclass(frozen=True)
class QueueRef:
    """Где читать ответы владельца: тред очереди и логин владельца."""

    repo: str
    number: int
    owner: str


def _same(
    fresh: FreshReader,
    subject: tuple[str, int],
    pr: tuple[str, int],
    merge_sha: str | None,
) -> str | None:
    """PR влит (тем же merge SHA, если он задан) и всё ещё закрывает issue."""
    facts = fresh.pr_facts(*pr)
    if facts is None:
        return "PR основания не прочитан"
    if not facts["merged"] or (merge_sha and facts["merge_sha"] != merge_sha):
        return "PR основания не влит"
    issue = f"{subject[0]}#{subject[1]}"
    return None if issue in facts["closing"] else "PR больше не закрывает issue"


def question_basis(
    fresh: FreshReader,
    graph: Graph,
    inputs: Inputs,
    subject: str,
    question: dict[str, Any],
    period: str,
) -> str | None:
    """Основание вопроса GR-SHIPPED-OPEN по свежим данным; его идентичность
    (question-id из свежего доказательства) должна совпасть с планом."""
    head = question["evidence"].rsplit("|period:", 1)[0]
    repo, number = target(inputs, subject)
    if head.endswith(DONE):  # склейка заявки с выполненным пунктом
        item = head.removesuffix(DONE)
        # пункт — каноническим разбором свежего origin ДО склейки: склейка
        # ядра опирается на существование пункта, а снимок прогона его помнит
        state, _, problem = fresh_item(fresh, item)
        if problem is not None:
            return problem
        if state is None:
            return "пункта больше нет"
        if not state.done:
            return "пункт больше не выполнен"
        accepted, problem = fresh_accepted(fresh, graph, inputs, subject)
        if problem is not None:
            return problem
        if accepted != item:
            return "склейка заявки с пунктом снята"
    elif head.endswith(MERGED):
        problem = _same(
            fresh, (repo, number), target(inputs, head.removesuffix(MERGED)), None
        )
        if problem is not None:
            return problem
    elif head != REOPENED:  # прежний период: влитый PR неизменен, период — ниже
        return "основание вопроса не распознано"
    evidence = f"{head}|period:{period}"
    fresh_id = question_id(SHIPPED, subject, evidence, tuple(question["options"]))
    return (
        None if fresh_id == question["question_id"] else "основание вопроса изменилось"
    )


def close_check(
    fresh: FreshReader,
    graph: Graph,
    inputs: Inputs,
    subject: str,
    period: str,
    basis: dict[str, Any],
    question: dict[str, Any] | None,
    queue: QueueRef | None,
    rendered: str,
    bot: str,
) -> Callable[[Mutation], str | None]:
    """Шаг 8 для обоих шагов: открыт, период, основание; комментарий — маркера
    ещё нет; закрытие — подтверждение с полным ключом есть."""

    def same_period(m: Mutation) -> str | None:
        event = fresh.last_event(m.repo, m.number or 0, "reopened")
        if event is None:
            return "timeline не прочитан"
        return None if (event or "0") == period else "период изменился"

    def answer_holds() -> str | None:
        if queue is None or question is None:
            return "очередь не найдена"
        comments = fresh.comments(queue.repo, queue.number)
        if comments is None:
            return "ответы не прочитаны"
        results, _ = answers_and_resolutions(comments, queue.owner, [question])
        res = results[question["question_id"]]
        return None if res.chosen == "close" and not res.blocked else "ответ изменился"

    def basis_holds(m: Mutation) -> str | None:
        if question is None:
            problem = _same(
                fresh,
                (m.repo, m.number or 0),
                (basis["repo_full"], basis["number"]),
                basis["merge_sha"],
            )
            return None if problem is None else f"основание (а): {problem}"
        return first_reason(
            answer_holds,
            lambda: question_basis(fresh, graph, inputs, subject, question, period),
        )

    def check(m: Mutation) -> str | None:
        steps: list[Callable[[], str | None]] = [
            lambda: open_check(fresh, m.repo, m.number or 0),
            lambda: same_period(m),
            lambda: basis_holds(m),
        ]
        if m.op == "comment":
            steps.append(lambda: comment_check(fresh, m, bot))
        else:
            steps.append(lambda: marker_check(fresh, m, rendered, bot))
        return first_reason(*steps)

    return check


def plan_close(
    result: Result,
    inputs: Inputs,
    bot: str,
    close_answers: dict[str, dict[str, Any]],
    fresh: FreshReader | None,
    notes: list[dict[str, Any]],
    queue: QueueRef | None = None,
) -> list[PlanRecord]:
    """Записи закрытия по основанию (а) текущего периода или ответу close."""
    graph = result.graph
    records: list[PlanRecord] = []
    for subject in sorted(set(inputs.issue_extras) | set(close_answers)):
        node = graph.nodes.get(subject)
        if node is None or node.kind != "issue" or not node.is_open:
            continue
        if (
            subject not in close_answers
            and rank_of(graph, result.roadmap, subject) is None
        ):
            continue
        state = shipped_state(inputs, subject)
        if state is None:
            notes.append(
                {
                    "action": "close_shipped",
                    "subject": subject,
                    "reason": "период не прочитан",
                }
            )
            continue
        question: dict[str, Any] | None = None
        basis: dict[str, Any] = {}
        if state.basis_a is not None:
            pr = state.basis_a
            fact = f"merged:{pr['merge_sha']}"
            how = (
                f"влит PR {pr['repo']}!{pr['number']} ({(pr['merge_sha'] or '')[:12]})"
            )
            basis = {
                "repo_full": f"{inputs.owner}/{pr['repo']}",
                "number": pr["number"],
                "merge_sha": pr["merge_sha"],
            }
        elif subject in close_answers:
            question = close_answers[subject]
            fact = f"answer:{question['question_id']}"
            how = (
                f"ответ владельца Q-{question['question_id']}: close "
                f"(основание: {question['evidence']})"
            )
        else:
            if state.old_basis:
                notes.append({"finding": "GR-REOPENED", "subject": subject})
            continue
        marker = make(
            "close",
            node=h1("node", subject),
            period=h1("period", state.period),
            evidence=h1("evidence", fact),
        )
        events, _ = thread_events(graph, subject, bot)
        notes += edited_findings(graph, [subject], bot, "close_shipped")
        repo, number = target(inputs, subject)
        steps: list[Step] = []
        if not any(m == marker for m, _ in events):
            text = f"Выполнение подтверждено: {how}. Закрываю как completed."
            steps.append(
                Step(
                    Mutation("comment", repo, number, text=with_marker(text, marker)),
                    render(marker),
                )
            )
        steps.append(Step(Mutation("close", repo, number), "close"))
        records.append(
            PlanRecord(
                "close_shipped",
                subject,
                h1("rev", state.period, fact),
                1,
                tuple(steps),
                close_check(
                    fresh,
                    graph,
                    inputs,
                    subject,
                    state.period,
                    basis,
                    question,
                    queue,
                    render(marker),
                    bot,
                )
                if fresh
                else valid,
                # ответ владельца — основание и без ранга (§7.5 (б)); иначе ранг
                authority(result, inputs, subject, ranked=question is None),
            )
        )
    return records
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/conductor/test_close.py tests/conductor/test_revalidate_close.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add conductor/actions/close.py tests/conductor/test_close.py tests/conductor/test_revalidate_close.py
git commit -m "conductor: close_shipped по периоду открытия и полному ключу маркера (§7.5)"
```

---
### Task B9: сборка плана и CLI

**Files:**
- Modify: `conductor/actions/__init__.py`, `conductor/__main__.py`
- Test: `tests/conductor/test_plan_records.py`, `tests/conductor/test_cli_slice1.py`

**Interfaces:**
- Consumes: B1–B8, A5, A11, A12.
- Produces: `PlanContext` (не frozen) с полями `cfg, umbrella, bot_login, state, client=None, git_repo: GitRepo | None = None, owner_login="", now=<UTC now>, notes: list[dict]=[]`; `plan_records(result, inputs, ctx) -> list[PlanRecord]` (порядок: очередь → закрытие → уведомления → пинки → пинки PR; `FreshReader(ctx.client, ctx.git_repo)` для `revalidate`; очередь — одна запись с производными вопросами отдельно; закрытие по ответу получает `QueueRef` для свежей сверки ответа). CLI: `--trigger {timer,manual}` (в снимке `trigger`), `collect(..., slice1=True, umbrella_dir=umbrella_name(cfg))` при `--config` (P2-4: в `acceptance` мини-флот читает только песочницу), `_git_repo(root, inputs, umbrella_dir) -> GitRepo` (свежий `fetch` + `origin/<default>` для шага 8), `reconcile(...)` ДО планирования (P2-3; сбой записи состояния — `OPSTATE-WRITE`, записей нет), заметки планировщиков и найденные эффекты — в `snapshot["writer"]["notes"]`.

- [ ] **Step 1: Write the failing tests**

`tests/conductor/test_plan_records.py`:

```python
"""Сборка плана и сквозной прогон через исполнитель (срез 1)."""

import json
from datetime import timedelta
from pathlib import Path

from conductor.actions import PlanContext, plan_records
from conductor.host_config import HostConfig
from conductor.journal import MutationLog, RunJournal
from conductor.opstate import init_state, open_state
from conductor.roadmap import parse_roadmap
from conductor.writer import Writer
from tests.conductor.fake_app import FakeClient
from tests.conductor.fixtures import EPICS, ROADMAP
from tests.conductor.slice1_fixtures import BOT, NOW, TODOS, world

CFG = HostConfig(
    app_id=1,
    installation_id=2,
    private_key=Path("/k"),
    profile="fleet",
    shadow=False,
    state_dir=Path("/s"),
)
DONE = {**TODOS, "b": "- [x] b @owner:TBD @id:b @epic:eco.bg\n"}
UMB = "own/ai-orchestrators-workspace"


def _ctx(tmp_path: Path) -> PlanContext:
    init_state(tmp_path, NOW - timedelta(hours=3))
    state = open_state(tmp_path, NOW).state
    assert state is not None
    return PlanContext(CFG, UMB, BOT, state, owner_login="own", now=NOW)


def test_partial_graph_plans_nothing(tmp_path: Path) -> None:
    result, inp = world(gh_state="error")
    ctx = _ctx(tmp_path)
    assert plan_records(result, inp, ctx) == []
    assert ctx.notes == [{"note": "граф partial — записей нет"}]


def test_end_to_end_with_writer(tmp_path: Path) -> None:
    result, inp = world(DONE, done_facts={"todo://b/b": "s1"})
    ctx = _ctx(tmp_path / "state")
    records = plan_records(result, inp, ctx)
    actions = [r.action for r in records]
    assert "notify_satisfied" in actions
    enabled = json.dumps(
        ["owner_queue", "notify_satisfied", "nudge", "pr_nudge", "close_shipped"]
    )
    rm = parse_roadmap(
        ROADMAP.replace("autonomy = 0", f"autonomy = 1\nenabled_actions = {enabled}"),
        EPICS,
    )
    client = FakeClient()
    writer = Writer(
        cfg=CFG,
        client=client,
        state=ctx.state,
        log=MutationLog(tmp_path / "run"),
        journal=RunJournal(tmp_path / "run"),
        fence=frozenset({"own/a", "own/b", UMB}),
        load_roadmap=lambda: rm,
        hostname="vps",
        run_id="r1",
        level_cap=3,
        clock=lambda: NOW,
    )
    reports = writer.execute(records)
    assert reports and all(r.outcome == "success" for r in reports)
    assert any(p.endswith("/issues/3/comments") for _, p, _ in client.sent)
```

`tests/conductor/test_cli_slice1.py` (регрессия P2-3 на пути CLI: найденный маркер незавершённой попытки закрывает её `ok` до планирования; `trigger` и заметки в снимке):

```python
"""CLI среза 1: поиск эффектов до плана (регрессия P2-3), заметки, trigger."""

from datetime import UTC, datetime, timedelta

import conductor.__main__ as cli
from tests.conductor.test_cli_writer import _live_world, _snap, env  # noqa: F401


def test_run_settles_found_effects(env, monkeypatch) -> None:  # noqa: F811
    """Регрессия P2-3: CLI ищет эффекты незавершённых попыток до плана."""
    tmp, cfg, rep = _live_world(env, monkeypatch)
    client = cli.AppClient(None, None)
    marker = "<!-- conductor:v1 q id=h1-0123456789abcdef -->"
    client.add_comment("own/a", 1, client.bot_login, f"вопрос\n{marker}")
    opened = cli.open_state(tmp / "state", datetime.now(UTC))
    assert opened.state is not None
    opened.state.begin_attempt(
        attempt_id="old",
        mutation_id="m",
        effect_key="e",
        target="own/a#1",
        marker_key=marker,
        action="owner_queue",
        subject="own/ws",
        now=datetime.now(UTC) - timedelta(minutes=5),
        op="comment",
        expected=marker,
    )
    seen: list[str] = []
    monkeypatch.setattr(
        cli,
        "plan_records",
        lambda result, inputs, ctx: (
            seen.append(ctx.state.attempts["old"]["status"]) or []
        ),
    )
    out = tmp / "out"
    argv = ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
    assert cli.main(argv) == 0
    assert seen == ["ok"]
    assert {"settled": "own/a#1", "op": "comment"} in _snap(out)["writer"]["notes"]


def test_snapshot_carries_trigger_and_notes(env, monkeypatch) -> None:  # noqa: F811
    tmp, cfg, rep = _live_world(env, monkeypatch)
    monkeypatch.setattr(
        cli,
        "plan_records",
        lambda result, inputs, ctx: ctx.notes.append({"note": "x"}) or [],
    )
    out = tmp / "out"
    argv = ["run", "--replay", str(rep), "--out", str(out), "--config", str(cfg)]
    assert cli.main([*argv, "--trigger", "timer"]) == 0
    snap = _snap(out)
    assert snap["trigger"] == "timer" and {"note": "x"} in snap["writer"]["notes"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_plan_records.py tests/conductor/test_cli_slice1.py -q`
Expected: FAIL — `TypeError` (в `PlanContext` нет `owner_login`) или пустой план; `--trigger` не разобран.

- [ ] **Step 3: Replace `conductor/actions/__init__.py`**

```python
"""Планировщики действий среза 1 (спека среза 1, §6–7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from conductor.actions.answers import answers_and_resolutions
from conductor.actions.close import QueueRef, plan_close
from conductor.actions.notify import plan_notify
from conductor.actions.nudge import plan_nudges, plan_pr_nudges
from conductor.actions.owner_queue import (
    SHIPPED,
    plan_owner_queue,
    question_levels,
    queue_issue,
    queue_questions,
)
from conductor.fresh import FreshReader, GitRepo
from conductor.host_config import HostConfig
from conductor.inputs import Inputs
from conductor.opstate import OpState
from conductor.snapshot import Result
from conductor.writer import PlanRecord


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class PlanContext:
    """Что планировщикам нужно сверх результата ядра; notes — в снимок."""

    cfg: HostConfig
    umbrella: str
    bot_login: str
    state: OpState
    client: Any = None
    git_repo: GitRepo | None = None
    owner_login: str = ""
    now: datetime = field(default_factory=_utcnow)
    notes: list[dict[str, Any]] = field(default_factory=list)


def _close_answers(
    questions: list[dict[str, Any]],
    resolutions: dict[str, Any],
    notes: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Исполняется только close на GR-SHIPPED-OPEN; прочие ответы — заметка."""
    close: dict[str, dict[str, Any]] = {}
    for q in questions:
        res = resolutions[q["question_id"]]
        if res.blocked:
            notes.append(
                {
                    "question": q["question_id"],
                    "status": "конфликт на подтверждении: оставьте один ответ",
                }
            )
        elif res.chosen is None or res.chosen == "keep":
            continue
        elif q["reason"] == SHIPPED and res.chosen == "close":
            close[q["subject"]] = q
        else:
            notes.append(
                {
                    "question": q["question_id"],
                    "answer": res.chosen,
                    "status": "ответ принят; исполнение недоступно в текущем срезе",
                }
            )
    return close


def plan_records(result: Result, inputs: Inputs, ctx: PlanContext) -> list[PlanRecord]:
    """Записи плана прогона; граф partial — ни одной (О §2.4)."""
    if result.graph_state == "partial":
        ctx.notes.append({"note": "граф partial — записей нет"})
        return []
    fresh = FreshReader(ctx.client, ctx.git_repo) if ctx.client is not None else None
    bot = ctx.bot_login
    owner = ctx.owner_login or ctx.umbrella.split("/")[0]
    questions = queue_questions(result, inputs, ctx.state.episodes())
    queue, _ = queue_issue(inputs, bot)
    resolutions, open_derived = answers_and_resolutions(
        queue["comments"] if queue else [], owner, questions
    )
    close_answers = _close_answers(questions, resolutions, ctx.notes)
    queue_ref = QueueRef(ctx.umbrella, queue["number"], owner) if queue else None
    levels = question_levels(result, inputs, questions + open_derived)
    return [
        *plan_owner_queue(
            questions,
            open_derived,
            inputs,
            ctx.umbrella,
            owner,
            bot,
            fresh,
            ctx.notes,
            levels,
        ),
        *plan_close(result, inputs, bot, close_answers, fresh, ctx.notes, queue_ref),
        *plan_notify(result, inputs, bot, fresh, ctx.notes),
        *plan_nudges(result, inputs, bot, ctx.now, fresh, ctx.notes),
        *plan_pr_nudges(result, inputs, bot, ctx.now, fresh, ctx.notes),
    ]
```

- [ ] **Step 4: Wire CLI**

Применить к `conductor/__main__.py`:

```diff
diff --git a/conductor/__main__.py b/conductor/__main__.py
index 91df227..87218c3 100644
--- a/conductor/__main__.py
+++ b/conductor/__main__.py
@@ -19,6 +19,7 @@ from typing import Any
 from conductor.actions import PlanContext, plan_records
 from conductor.app_calls import AppCalls, init_host
 from conductor.collect import collect, read_manifest
+from conductor.fresh import FreshReader, GitRepo
 from conductor.gh_app import AppClient, Blocked, JournalLost
 from conductor.graph import canonical_id, normalizer
 from conductor.host_config import (
@@ -32,17 +33,19 @@ from conductor.host_config import (
 )
 from conductor.inputs import Inputs, RepoTodo, load_inputs, save_inputs
 from conductor.journal import MutationLog, RunJournal
+from conductor.manifest import fleet_repos
 from conductor.opstate import (
     StateError,
     init_state,
     open_state,
     recover_state,
 )
+from conductor.reconcile import reconcile
 from conductor.render import render_plan, render_status, render_why
 from conductor.roadmap import Roadmap, parse_roadmap
 from conductor.snapshot import Result, evaluate, to_snapshot
 from conductor.sources_gh import run_gh
-from conductor.sources_git import fetch, read_file_at_origin
+from conductor.sources_git import default_ref, fetch, read_file_at_origin
 from conductor.writer import StopPoint, Writer
 
 EXIT_OK, EXIT_ARGS, EXIT_NO_SOURCE, EXIT_CONFIG, EXIT_STOP = 0, 2, 3, 4, 5
@@ -62,6 +65,7 @@ def _parser() -> argparse.ArgumentParser:
     p.add_argument("--out", type=Path, default=Path("out/conductor"))
     p.add_argument("--level", type=int, choices=range(4), default=0)
     p.add_argument("--config", type=Path)
+    p.add_argument("--trigger", choices=("timer", "manual"), default="manual")
     p.add_argument("--recover", action="store_true")
     p.add_argument("command", nargs="?")
     p.add_argument("target", nargs="?")
@@ -72,7 +76,7 @@ def _now() -> str:
     return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
 
 
-def _inputs(args: argparse.Namespace) -> Inputs | None:
+def _inputs(args: argparse.Namespace, umbrella_dir: str) -> Inputs | None:
     if args.replay is not None:
         replayed = load_inputs(args.replay)
         if args.roadmap is not None:  # черновик роадмапа проверяется на записи
@@ -92,7 +96,7 @@ def _inputs(args: argparse.Namespace) -> Inputs | None:
         text: str | None = args.manifest.read_text(encoding="utf-8")
         origin: tuple[str, str | None] = (f"file:{args.manifest}", None)
     else:
-        text, origin, errors = read_manifest(args.root, not args.no_fetch)
+        text, origin, errors = read_manifest(args.root, not args.no_fetch, umbrella_dir)
     if text is None:
         print("; ".join(errors), file=sys.stderr)
         return None
@@ -106,6 +110,8 @@ def _inputs(args: argparse.Namespace) -> Inputs | None:
         socket.gethostname(),
         _now(),
         errors,
+        slice1=args.config is not None,
+        umbrella_dir=umbrella_dir,
     )
 
 
@@ -190,6 +196,22 @@ def _origin_roadmap(
     return parse_roadmap(text, epics) if state == "read" else None
 
 
+def _git_repo(root: Path, inputs: Inputs, umbrella_dir: str) -> GitRepo:
+    """Свежий клон репо флота для шага 8: fetch, затем origin/<default>."""
+    dirs = {r.key: r.git_dir for r in fleet_repos(inputs.manifest_text, umbrella_dir)}
+
+    def clone(key: str) -> tuple[Path, str] | None:
+        if key not in dirs:
+            return None
+        repo_dir = root / dirs[key]
+        if fetch(repo_dir) is not None:
+            return None
+        ref = default_ref(repo_dir)
+        return (repo_dir, ref) if ref is not None else None
+
+    return clone
+
+
 def _start_checks(client: Any, umbrella: str) -> str | None:
     """О §8.2 на старте: ключ, установка владельца, покрытие зонтика профиля."""
     try:
@@ -236,6 +258,13 @@ def _write_phase(
         reason = ", ".join([*findings, problem])
         return EXIT_CONFIG, {"is_writer": False, "reason": reason}, []
     umbrella_dir = umbrella_name(cfg)
+    git_repo = _git_repo(args.root, inputs, umbrella_dir)
+    bot = client.bot_login or ""
+    try:  # §5.3: найденный эффект закрывает попытку ДО планирования и задержки
+        settled = reconcile(opened.state, FreshReader(client, git_repo), bot, now)
+    except OSError:
+        opened.state.end_run(now)
+        return EXIT_CONFIG, {"is_writer": False, "reason": "OPSTATE-WRITE"}, []
     writer = Writer(
         cfg=cfg,
         client=client,
@@ -249,17 +278,32 @@ def _write_phase(
         level_cap=level_cap(args, inputs),
         partial=result.graph_state == "partial",
     )
-    ctx = PlanContext(cfg, umbrella, client.bot_login or "", opened.state)
+    ctx = PlanContext(
+        cfg,
+        umbrella,
+        bot,
+        opened.state,
+        client=client,
+        git_repo=git_repo,
+        owner_login=umbrella.split("/")[0],
+        now=datetime.now(UTC),
+        notes=list(settled),
+    )
     try:
         reports = writer.execute(plan_records(result, inputs, ctx))
     except StopPoint as stop:
-        block = {"is_writer": True, "reason": f"точка остановки {stop.name}"}
+        block = {
+            "is_writer": True,
+            "reason": f"точка остановки {stop.name}",
+            "notes": ctx.notes,
+        }
         return EXIT_STOP, block, [{"stop_point": stop.name}]
     block = {
         "is_writer": True,
         "reason": "тень" if cfg.shadow else "запись",
         "level_cap": level_cap(args, inputs),
         "findings": findings,
+        "notes": ctx.notes,
     }
     return (EXIT_CONFIG if findings else EXIT_OK), block, [asdict(r) for r in reports]
 
@@ -273,6 +317,7 @@ def _run(
     result = evaluate(inputs, level_cap(args, inputs))
     run_id = inputs.captured_at.replace(":", "")
     snap = to_snapshot(result, inputs, run_id, _previous(args.out))
+    snap["trigger"] = args.trigger
     run_dir = args.out / run_id
     run_dir.mkdir(parents=True, exist_ok=True)
     save_inputs(inputs, run_dir / "inputs.json")
@@ -355,7 +400,7 @@ def main(argv: list[str] | None = None) -> int:
 def _command(
     args: argparse.Namespace, cfg: HostConfig | None, cfg_error: str | None
 ) -> int:
-    inputs = _inputs(args)
+    inputs = _inputs(args, umbrella_name(cfg))
     if inputs is None:
         return EXIT_NO_SOURCE
     if all(t.state == "error" for t in inputs.todos) and inputs.gh_state != "read":
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/conductor -q`
Expected: PASS (включая `test_cli_writer.py` части A, `test_plan_records.py`, `test_cli_slice1.py`).

- [ ] **Step 6: Mutation check of the real checks (ревью п. 4)**

Удаление настоящей проверки основания — не lambda-заглушки — должно ронять хотя бы один тест. По одному удалите каждое условие `revalidate` планировщиков (`close`: открыт, период, основание, маркер, склейка и выполненность пункта, основание вопроса, PR закрывает issue, подтверждение перед закрытием; `pr_nudge`: потребность, набор упавших проверок, драфт, head; `notify`: адрес открыт, ребро, факт; `nudge`: pending, период, движение, «бот — не движение»; очередь: ответы, создание), решения 2/4/5 (`closed_events`, prior по всем тредам, причины без адреса), механизмы части A (`run_level`, `min` с потолком, `position_level`, `optional`, verify), lock в `run`/`init-state`, поиск эффектов (`reconcile` в CLI, `settle`), `lost` в `init_host`, зонтик профиля в `collect` — и прогоните `uv run pytest tests/conductor -q -x`. Автоматизировано: из корня worktree `python3 <_cowork_output>/2026-09-30-conductor-slice1/tools/mutate_check.py` — печатает `убит`/`ЖИВ` по каждому из 47 условий, код 1 при выжившем. Выжил — добавить тест и повторить (на коде плана: 0 выживших, 2026-10-01).

- [ ] **Step 7: Commit**

```bash
git add conductor/actions/__init__.py conductor/__main__.py \
    tests/conductor/test_plan_records.py tests/conductor/test_cli_slice1.py
git commit -m "conductor: сборка плана среза 1, поиск эффектов до плана, --trigger, зонтик профиля"
```

---

### Task B10: показатели ступени

**Files:**
- Create: `conductor/stage_report.py`
- Modify: `conductor/__main__.py` (команда `stage-report`)
- Test: `tests/conductor/test_stage_report.py`

**Interfaces:**
- Produces: `stage_report(out: Path, since: datetime, until: datetime) -> dict` с ключами `timer_runs`, `partial_share`, `availability`, `verdict` (`"не проверено"` | `"не пройдена"` | `"показатели в норме"`), `successes`, `to_review`, `shadow_proposals`; CLI `stage-report --out DIR --since ISO --until ISO` печатает JSON, код 0.

- [ ] **Step 1: Write the failing test**

```python
"""Показатели ступени (спека среза 1, §10.2)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from conductor.stage_report import stage_report

T0 = datetime(2026, 10, 1, tzinfo=UTC)
H = timedelta(hours=1)


def _run(
    out: Path, at: datetime, state: str, trigger: str = "timer", journal=None
) -> None:
    d = out / at.strftime("%Y-%m-%dT%H%M%SZ")
    d.mkdir(parents=True)
    snap = {
        "started_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "graph_state": state,
        "trigger": trigger,
        "actions": {"plan": [], "journal": journal or []},
    }
    (d / "snapshot.json").write_text(json.dumps(snap), encoding="utf-8")


def test_owner_example_72_slots_fails(tmp_path: Path) -> None:
    for i in range(3):
        _run(tmp_path, T0 + i * H, "partial")
    _run(
        tmp_path,
        T0 + 3 * H,
        "complete",
        journal=[{"action": "owner_queue", "outcome": "success"}],
    )
    rep = stage_report(tmp_path, T0, T0 + 72 * H)
    assert rep["partial_share"] == 0.75 and rep["availability"] == 4 / 72
    assert rep["verdict"] == "не пройдена"


def test_manual_runs_do_not_help(tmp_path: Path) -> None:
    _run(tmp_path, T0, "complete")
    for i in range(1, 72):
        _run(tmp_path, T0 + i * H + timedelta(minutes=5), "complete", trigger="manual")
    rep = stage_report(tmp_path, T0, T0 + 72 * H)
    assert rep["availability"] == 1 / 72 and rep["verdict"] == "не пройдена"


def test_healthy_stage_and_counts(tmp_path: Path) -> None:
    for i in range(72):
        journal = [{"action": "nudge", "outcome": "success"}] if i == 5 else []
        if i == 6:
            journal = [{"action": "nudge", "outcome": "uncertain"}]
        _run(tmp_path, T0 + i * H, "complete", journal=journal)
    rep = stage_report(tmp_path, T0, T0 + 72 * H)
    assert rep["verdict"] == "показатели в норме"
    assert rep["successes"] == {"nudge": 1} and rep["to_review"] == {"uncertain": 1}


def test_empty_is_not_verified(tmp_path: Path) -> None:
    assert stage_report(tmp_path, T0, T0 + 72 * H)["verdict"] == "не проверено"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_stage_report.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `conductor/stage_report.py`**

```python
"""Показатели ступени включения (спека среза 1, §10.2).

Полнота — partial / (complete + partial) по прогонам ТАЙМЕРА; доступность —
часовые слоты с прогоном таймера / слоты периода. Ручные прогоны не входят
ни туда, ни туда. Переход ступени решает владелец правкой роадмапа.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from conductor.opstate import parse_ts

MAX_PARTIAL = 0.05
MIN_AVAILABILITY = 0.90
REVIEW = frozenset(
    {"uncertain", "failed", "revoked_action", "skipped_dependent", "not_sent"}
)


def _snapshots(out: Path, since: datetime, until: datetime) -> list[dict[str, Any]]:
    snaps = []
    for path in sorted(out.glob("*/snapshot.json")) if out.is_dir() else []:
        try:
            snap = json.loads(path.read_text(encoding="utf-8"))
            started = parse_ts(snap["started_at"])
        except (OSError, ValueError, KeyError):
            continue
        if since <= started < until:
            snaps.append(snap)
    return snaps


def _journal(snaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        j
        for s in snaps
        for j in (s.get("actions") or {}).get("journal", [])
        if isinstance(j, dict) and "action" in j
    ]


def stage_report(out: Path, since: datetime, until: datetime) -> dict[str, Any]:
    """Показатели ступени за [since, until)."""
    snaps = _snapshots(out, since, until)
    graphs = [
        s
        for s in snaps
        if s.get("trigger") == "timer"
        and s.get("graph_state") in ("complete", "partial")
    ]
    slots = int((until - since).total_seconds() // 3600)
    covered = {
        parse_ts(s["started_at"]).replace(minute=0, second=0, microsecond=0)
        for s in graphs
    }
    partial = (
        sum(s["graph_state"] == "partial" for s in graphs) / len(graphs)
        if graphs
        else None
    )
    availability = len(covered) / slots if slots else None
    if partial is None or availability is None:
        verdict = "не проверено"
    elif partial > MAX_PARTIAL or availability < MIN_AVAILABILITY:
        verdict = "не пройдена"
    else:
        verdict = "показатели в норме"
    journal = _journal(snaps)
    return {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "timer_runs": len(graphs),
        "partial_share": partial,
        "availability": availability,
        "verdict": verdict,
        "successes": dict(
            Counter(j["action"] for j in journal if j.get("outcome") == "success")
        ),
        "to_review": dict(
            Counter(j["outcome"] for j in journal if j.get("outcome") in REVIEW)
        ),
        "shadow_proposals": dict(
            Counter(j["action"] for j in journal if j.get("outcome") == "shadow")
        ),
    }
```

- [ ] **Step 4: Wire the command**

Применить к `conductor/__main__.py` (`COMMANDS` + `stage-report`, `--since`/`--until`, ветка в `main()` сразу после `init-state`):

```diff
diff --git a/conductor/__main__.py b/conductor/__main__.py
index 87218c3..177387c 100644
--- a/conductor/__main__.py
+++ b/conductor/__main__.py
@@ -38,6 +38,7 @@ from conductor.opstate import (
     StateError,
     init_state,
     open_state,
+    parse_ts,
     recover_state,
 )
 from conductor.reconcile import reconcile
@@ -46,10 +47,11 @@ from conductor.roadmap import Roadmap, parse_roadmap
 from conductor.snapshot import Result, evaluate, to_snapshot
 from conductor.sources_gh import run_gh
 from conductor.sources_git import default_ref, fetch, read_file_at_origin
+from conductor.stage_report import stage_report
 from conductor.writer import StopPoint, Writer
 
 EXIT_OK, EXIT_ARGS, EXIT_NO_SOURCE, EXIT_CONFIG, EXIT_STOP = 0, 2, 3, 4, 5
-COMMANDS = ("status", "why", "plan", "run", "record", "init-state")
+COMMANDS = ("status", "why", "plan", "run", "record", "init-state", "stage-report")
 # ежечасный таймер: неделя прогонов (~3 МБ каждый) — не растить диск общего VPS
 KEEP_RUNS = 168
 
@@ -66,6 +68,8 @@ def _parser() -> argparse.ArgumentParser:
     p.add_argument("--level", type=int, choices=range(4), default=0)
     p.add_argument("--config", type=Path)
     p.add_argument("--trigger", choices=("timer", "manual"), default="manual")
+    p.add_argument("--since")
+    p.add_argument("--until")
     p.add_argument("--recover", action="store_true")
     p.add_argument("command", nargs="?")
     p.add_argument("target", nargs="?")
@@ -383,6 +387,12 @@ def main(argv: list[str] | None = None) -> int:
         return _selftest()
     if args.command == "init-state":
         return _init_state(args)
+    if args.command == "stage-report":
+        if not args.since or not args.until:
+            return EXIT_ARGS
+        report = stage_report(args.out, parse_ts(args.since), parse_ts(args.until))
+        print(json.dumps(report, ensure_ascii=False, indent=1))
+        return EXIT_OK
     if args.command not in COMMANDS or (
         args.command in ("why", "record") and not args.target
     ):
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/conductor/test_stage_report.py tests/conductor/test_cli.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add conductor/stage_report.py conductor/__main__.py tests/conductor/test_stage_report.py
git commit -m "conductor: stage-report — полнота и доступность по прогонам таймера (§10.2)"
```

---
### Task B11: деплой, песочница и квитанция приёмки

**Files:**
- Create: `deploy/conductor/conductor.toml.example`, `deploy/conductor/acceptance.toml.example`, `deploy/conductor/writer.conf`, `deploy/conductor/acceptance.sh`, `deploy/conductor/acceptance_dump.py`, `docs/conductor/acceptance/sandbox.md`, `docs/conductor/acceptance/TEMPLATE-slice1.md`
- Modify: `deploy/conductor/conductor.service` (`--trigger timer`), `deploy/conductor/README.md` (раздел «Срез 1») — диффом
- Test: `tests/conductor/test_deploy_slice1.py`

**Interfaces:**
- Consumes: `load_host_config` (A2), `markers.parse_body` (A3).
- Produces: `acceptance_dump.dump(repo: str, runner) -> dict` (issues/PR песочницы в любом состоянии: номер, состояние, причина, метки, закреплено, комментарии с автором, временем и разобранным маркером); `acceptance.sh <owner>/conductor-sandbox <autonomy> <a,b|->`.

- [ ] **Step 1: Write the failing test**

```python
"""Деплой среза 1: примеры конфигов, тень, скрипты приёмки (§8–§10)."""

import json
import subprocess
from pathlib import Path

from conductor.host_config import load_host_config
from conductor.markers import h1, make, with_marker

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "conductor"


def test_examples_are_valid_configs() -> None:
    fleet = load_host_config(DEPLOY / "conductor.toml.example")
    assert fleet.profile == "fleet" and fleet.shadow is True
    acc = load_host_config(DEPLOY / "acceptance.toml.example")
    assert acc.profile == "acceptance" and acc.state_dir != fleet.state_dir


def test_timer_marks_trigger_and_writer_dropin_adds_config() -> None:
    unit = (DEPLOY / "conductor.service").read_text(encoding="utf-8")
    assert "--trigger timer" in unit and "--config" not in unit
    dropin = (DEPLOY / "writer.conf").read_text(encoding="utf-8")
    assert "ExecStart=\n" in dropin
    assert (
        "--config /srv/conductor/conductor.toml" in dropin
        and "--trigger timer" in dropin
    )
    # lock берёт сам run --config (P1-3); внешний flock на тот же файл помешал бы
    assert "flock" not in dropin.split("[Service]")[1]
    # без явного потолка ручной и таймерный run — уровень 0 (P1-2)
    assert "--level 3" in dropin


def _acceptance(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(DEPLOY / "acceptance.sh"), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_acceptance_script_refuses_before_any_network() -> None:
    assert _acceptance("own/devtools", "1", "nudge").returncode == 2
    assert _acceptance("own/conductor-sandbox", "4", "nudge").returncode == 2
    assert _acceptance("own/conductor-sandbox", "1", "todo_hygiene_pr").returncode == 2


def test_dump_parses_markers() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "acceptance_dump", DEPLOY / "acceptance_dump.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    marker = make("q", id=h1("q", "x"))
    issues = [
        [
            {
                "number": 1,
                "state": "open",
                "state_reason": None,
                "labels": [{"name": "owner-queue"}],
                "pull_request": None,
                "title": "t",
            }
        ]
    ]
    comments = [
        [
            {
                "user": {"login": "conductor[bot]"},
                "created_at": "a",
                "updated_at": "a",
                "body": with_marker("x", marker),
            }
        ]
    ]

    def runner(args: list[str]) -> tuple[int, str, str]:
        key = " ".join(args)
        if "comments" in key:
            return 0, json.dumps(comments), ""
        return 0, json.dumps(issues), ""

    data = module.dump("own/conductor-sandbox", runner)
    [issue] = data["items"]
    assert issue["comments"][0]["marker"] == {
        "kind": "q",
        "fields": {"id": h1("q", "x")},
    }
    assert "body" not in issue["comments"][0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/conductor/test_deploy_slice1.py -q`
Expected: FAIL — нет файлов примеров.

- [ ] **Step 3: Write the files**

`deploy/conductor/conductor.toml.example`:

```toml
# /srv/conductor/conductor.toml — конфиг хоста-писателя (спека среза 1, §4.2).
# Владелец: пользователь conductor, права 0600. Ключ App — только на этом хосте.
[app]
app_id          = 123456
installation_id = 7890123
private_key     = "/srv/conductor/keys/conductor.pem"

[run]
profile   = "fleet"
shadow    = true                      # неделя тени: всё, кроме отправки
state_dir = "/srv/conductor/opstate"  # не state/: там runs/ и lock среза 0
```

`deploy/conductor/acceptance.toml.example`:

```toml
# /srv/conductor/acceptance.toml — профиль приёмки в песочнице (спека среза 1, §9).
[app]
app_id          = 123456
installation_id = 7890123
private_key     = "/srv/conductor/keys/conductor.pem"

[run]
profile   = "acceptance"
shadow    = false
state_dir = "/srv/conductor/acceptance/opstate"

[acceptance]
sandbox     = "OWNER/conductor-sandbox"
outside     = ["OWNER/conductor-sandbox-outside"]
stop_points = []                      # after_comment | before_close | after_intent | after_send
```

`deploy/conductor/writer.conf`:

```ini
# systemd drop-in: включает фазу записи (тень или ступени) — шаг владельца.
#   sudo install -D -m 0644 deploy/conductor/writer.conf \
#     /etc/systemd/system/conductor.service.d/writer.conf && sudo systemctl daemon-reload
# Откат: удалить drop-in и daemon-reload (служба вернётся к срезу 0).
# Lock хоста (/srv/conductor/state/conductor.lock) берёт сам `run --config`
# (как и init-state и ручной run) — внешний flock здесь занял бы тот же файл.
# --level 3: таймер ограничивает только роадмап (О §2.4, §7.3); без него — 0.
[Service]
ExecStart=
ExecStart=/usr/local/bin/uv run --frozen python -m conductor run --root /srv/conductor/workspace --out /srv/conductor/state/runs --trigger timer --level 3 --config /srv/conductor/conductor.toml
```


`deploy/conductor/acceptance.sh`:

```bash
#!/usr/bin/env bash
# Правка приёмочного roadmap.toml песочницы ПОЛНОМОЧИЯМИ ВЛАДЕЛЬЦА (спека среза 1, §9.1).
# Запускает владелец на своей машине своей git-учёткой — не conductor и не App.
#   deploy/conductor/acceptance.sh <owner>/conductor-sandbox <autonomy 0..3> <action,action | ->
set -euo pipefail
REPO="${1:?<owner>/conductor-sandbox}"
AUTONOMY="${2:?autonomy 0..3}"
ACTIONS="${3:?список действий через запятую или -}"
case "$REPO" in */conductor-sandbox) ;; *) echo "только <owner>/conductor-sandbox" >&2; exit 2 ;; esac
case "$AUTONOMY" in [0-3]) ;; *) echo "autonomy 0..3" >&2; exit 2 ;; esac
LIST=""
if [ "$ACTIONS" != "-" ]; then
    for a in ${ACTIONS//,/ }; do
        case "$a" in
            owner_queue | notify_satisfied | nudge | pr_nudge | close_shipped) ;;
            *) echo "неизвестное действие: $a" >&2; exit 2 ;;
        esac
        LIST="$LIST\"$a\", "
    done
    LIST="${LIST%, }"
fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git clone -q "https://github.com/$REPO.git" "$TMP/s"
python3 - "$TMP/s/roadmap.toml" "$AUTONOMY" "$LIST" <<'PY'
import re
import sys

path, autonomy, items = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path, encoding="utf-8").read()
text = re.sub(r"(?m)^autonomy\s*=.*$", f"autonomy = {autonomy}", text, count=1)
line = f"enabled_actions = [{items}]"
if re.search(r"(?m)^enabled_actions\s*=", text):
    text = re.sub(r"(?m)^enabled_actions\s*=.*$", line, text, count=1)
else:
    text = text.replace(f"autonomy = {autonomy}", f"autonomy = {autonomy}\n{line}", 1)
open(path, "w", encoding="utf-8").write(text)
PY
git -C "$TMP/s" commit -qam "acceptance: autonomy=$AUTONOMY enabled_actions=[$LIST]"
git -C "$TMP/s" push -q origin HEAD
echo "опубликовано: autonomy=$AUTONOMY enabled_actions=[$LIST]"
```

`deploy/conductor/acceptance_dump.py`:

```python
"""Снимок удалённого состояния песочницы до/после сценария (спека среза 1, §9.4).

Запускает владелец своей учёткой gh: `python3 deploy/conductor/acceptance_dump.py
<owner>/conductor-sandbox > before.json`. Тела комментариев не сохраняются —
только автор, время и разобранный маркер; токенов в выводе нет.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from conductor.markers import parse_body  # noqa: E402

Runner = Callable[[list[str]], tuple[int, str, str]]


def run_gh(args: list[str]) -> tuple[int, str, str]:
    """gh с учёткой владельца."""
    done = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    return done.returncode, done.stdout, done.stderr


def _pages(runner: Runner, path: str) -> list[dict[str, Any]]:
    code, out, err = runner(["api", "--paginate", "--slurp", path])
    if code != 0:
        raise SystemExit(f"gh api {path}: {err.strip()}")
    return [item for page in json.loads(out) for item in page]


def dump(repo: str, runner: Runner = run_gh) -> dict[str, Any]:
    """Issues и PR в любом состоянии с комментариями и маркерами."""
    items = []
    for issue in _pages(runner, f"repos/{repo}/issues?state=all&per_page=100"):
        comments = []
        for c in _pages(
            runner, f"repos/{repo}/issues/{issue['number']}/comments?per_page=100"
        ):
            marker = parse_body(c.get("body") or "")
            comments.append(
                {
                    "author": (c.get("user") or {}).get("login", ""),
                    "created_at": c.get("created_at"),
                    "updated_at": c.get("updated_at"),
                    "marker": {"kind": marker.kind, "fields": dict(marker.fields)}
                    if marker
                    else None,
                }
            )
        items.append(
            {
                "number": issue["number"],
                "is_pr": bool(issue.get("pull_request")),
                "title": issue.get("title"),
                "state": issue.get("state"),
                "state_reason": issue.get("state_reason"),
                "labels": [lab["name"] for lab in issue.get("labels", [])],
                "comments": comments,
            }
        )
    return {"repo": repo, "items": sorted(items, key=lambda i: i["number"])}


if __name__ == "__main__":
    print(json.dumps(dump(sys.argv[1]), ensure_ascii=False, indent=1))
```

`docs/conductor/acceptance/sandbox.md` — содержимое песочницы и прогон сценариев (полный текст):

````markdown
# Песочница conductor, срез 1 (спека среза 1, §9)

## Что создаёт владелец

1. Репо `<owner>/conductor-sandbox` (приватное) и `<owner>/conductor-sandbox-outside`.
2. GitHub App `conductor`: права `issues: write`, `pull_requests: write`, `contents: write`,
   `checks: read`, `statuses: read`, `metadata: read`; установка **только** на
   `conductor-sandbox`. Ключ App — на VPS в `/srv/conductor/keys/conductor.pem`
   (владелец `conductor`, `0600`).
3. Учётные данные чтения хоста (`/srv/conductor/gh/hosts.yml`) видят оба репо.

## Содержимое `conductor-sandbox` (он же зонтик и единственный репо-цель)

- `workspace-manifest.toml`:

  ```toml
  [cores.conductor-sandbox]
  repo_url = "https://github.com/<owner>/conductor-sandbox.git"
  git_dir = "conductor-sandbox"
  [cores.conductor-sandbox-outside]
  repo_url = "https://github.com/<owner>/conductor-sandbox-outside.git"
  git_dir = "conductor-sandbox-outside"
  ```

- `epics.toml`: эпики `acc.focus` (active) и `acc.bg` (active).
- `roadmap.toml`: `schema_version = 1`, `autonomy = 0`, `writer_host = "vmi3423913"`,
  `writer_since` — момент установки ключа, `[[focus]] epic = "acc.focus"`,
  `[limits] stale_after_days = 1`, `renudge_after_days = 1`.
- `TODO.md` (каждая строка — под свой сценарий):

  ```
  - [ ] Цель A4 @owner:github:<owner> @id:goal-a4 @epic:acc.focus @blocked_by:todo://conductor-sandbox/pre-a4
  - [ ] Предпосылка A4 @owner:TBD @id:pre-a4 @epic:acc.bg
  - [ ] Цель A5 @owner:github:<owner> @id:goal-a5 @epic:acc.focus @blocked_by:todo://conductor-sandbox/pre-a5
  - [ ] Предпосылка A5 @owner:TBD @id:pre-a5 @epic:acc.bg
  - [ ] Цель A10 @owner:github:<owner> @id:goal-a10 @epic:acc.focus @blocked_by:conductor-sandbox-outside#1
  ```

- Issues (метка `inbox`, тело начинается шапкой `slug:`/`from:`):
  - `#1 slug: goal-a4` и `#2 slug: goal-a4` — два адреса цели A4 (A4);
  - `#3 slug: pre-a5` — адрес продюсера A5 (A5);
  - `#4`, `#5`, `#6` — issues для A3a/A3b/A3d, к каждому заранее влит PR в `master`
    с `Fixes #N`, после чего issue **переоткрыт** владельцем (основание — только ответ);
- PR `#7` с нарочно красной проверкой (workflow `exit 1`), реализует `@id:goal-a4`,
  открыт за сутки до прогона (A6).
- `conductor-sandbox-outside#1` — открытый issue (A10: адрес вне забора).

Изоляция (ревью P2-4): в профиле `acceptance` зонтик — сама песочница (`umbrella_name(cfg)`):
манифест, роадмап, эпики, очередь и состав флота читаются только из неё, настоящий зонтик
владельца в граф не добавляется и не читается (`test_collect_slice1.py`). Внешние чтения
приёмки — только `conductor-sandbox` и объявленный `conductor-sandbox-outside`.

## Выдержка времени

За сутки до прогона: коммит рёбер `goal-a5 → pre-a5` и последнее движение по `#3`
(комментарий владельца); открытие PR `#7`. К моменту прогона — полные 24 часа.

## Клон на VPS

```bash
sudo -u conductor git clone https://github.com/<owner>/conductor-sandbox.git /srv/conductor/acceptance/workspace/conductor-sandbox
sudo -u conductor git clone https://github.com/<owner>/conductor-sandbox-outside.git /srv/conductor/acceptance/workspace/conductor-sandbox-outside
sudo -u conductor uv run --frozen python -m conductor init-state --config /srv/conductor/acceptance.toml
```

Прогон сценария (после 65 минут карантина; без `--level` потолок прогона — 0 и записей нет):

```bash
sudo -u conductor uv run --frozen python -m conductor run --root /srv/conductor/acceptance/workspace --out /srv/conductor/acceptance/runs --level 3 --config /srv/conductor/acceptance.toml
```

`init-state` и `run --config` берут lock хоста `/srv/conductor/state/conductor.lock` — тот
же, что у прогона флота: приёмка и таймер не идут одновременно (занят — прогон пропущен,
повторить позже).

## Сценарии

Таблица A1–A13 — спека среза 1, §9.3. Для каждого: `acceptance_dump.py` до,
`acceptance.sh` (если сценарий меняет роадмап), прогон, `acceptance_dump.py` после,
выдержка `runs/<id>/calls.jsonl` и `journal.jsonl`; всё — в квитанцию.
````

`docs/conductor/acceptance/TEMPLATE-slice1.md`:

````markdown
# Квитанция приёмки conductor, срез 1 — <дата>

Хост: vmi3423913. Профиль: acceptance. Коммит devtools: <sha>.

| # | Роадмап (autonomy, enabled_actions) | run_id | До (`dump`) | После (`dump`) | Учёт мутаций (`calls.jsonl`) завершён и согласован с `attempts.jsonl` | Итог |
|---|---|---|---|---|---|---|
| A1a | 0, [] | | | | | |
| A1b | 1, все | | | | | |
| A2 | | | | | | |
| A3a | | | | | | |
| A3b | | | | | | |
| A3c | | | | | | |
| A3d | | | | | | |
| A3e | | | | | | |
| A4 | | | | | | |
| A5 | | | | | | |
| A6 | | | | | | |
| A7a | | | | | | |
| A7b | | | | | | |
| A7c | | | | | | |
| A8a | | | | | | |
| A8b | | | | | | |
| A9 | | | | | | |
| A10 | | | | | | |
| A11 | | | | | | |
| A12 | | | | | | |
| A13 | | | | | | |

Отрицательный результат («вызов не отправлялся») принимается только по
завершённому учёту мутаций (`run-end`, у каждого `intent` есть `result`).
Без токенов, JWT и заголовков. Принял: <владелец>, <дата>.
````

Применить к `deploy/conductor/conductor.service` (`--trigger timer`; служба среза 0 остаётся под внешним `flock` и без `--config`) и `deploy/conductor/README.md` (раздел «Срез 1»):

```diff
diff --git a/deploy/conductor/README.md b/deploy/conductor/README.md
index dbf26d6..61d4d31 100644
--- a/deploy/conductor/README.md
+++ b/deploy/conductor/README.md
@@ -30,3 +30,27 @@
    используется только для отчёта; писать начнёт срез 1).
 
 Обновление кода: `sudo -u conductor git -C /srv/conductor/devtools pull --ff-only`.
+
+## Срез 1 — запись (шаги владельца)
+
+1. Создать GitHub App и ключ (права — `docs/conductor/acceptance/sandbox.md`, п. 2),
+   положить ключ в `/srv/conductor/keys/conductor.pem` (`conductor:conductor`, `0600`).
+2. Пройти приёмку в песочнице (`docs/conductor/acceptance/sandbox.md`), заполнить
+   квитанцию по шаблону `docs/conductor/acceptance/TEMPLATE-slice1.md`.
+3. Установить App на репо флота; скопировать `deploy/conductor/conductor.toml.example`
+   в `/srv/conductor/conductor.toml` (`0600`), вписать `app_id` и `installation_id`.
+4. `sudo -u conductor uv run --frozen python -m conductor init-state --config /srv/conductor/conductor.toml`
+   (запускать из `/srv/conductor/devtools`; карантин записей — 65 минут).
+   `init-state`, `run --config` и `--recover` сами берут lock хоста
+   `/srv/conductor/state/conductor.lock` (`[run] lock` в конфиге): занят — команда
+   не выполняется (`run` — пропуск прогона с кодом 0, `init-state` — код 4).
+5. Тень: установить drop-in `deploy/conductor/writer.conf` (команда — в его шапке).
+   Он запускает `run --level 3 --config …` без внешнего `flock` (lock берёт сам
+   процесс). Без `--level` потолок прогона — 0 (О §2.4): ручной прогон для записи —
+   тоже с `--level`. Неделя ежечасных прогонов с `shadow = true`, `autonomy = 0`.
+6. Ступени: снять тень (`shadow = false`), затем правками `roadmap.toml` зонтика:
+   `autonomy = 1`, `enabled_actions` = `["owner_queue"]` → `+ notify_satisfied` →
+   `+ nudge, pr_nudge` → `+ close_shipped`. Перед каждой — показатели:
+   `uv run --frozen python -m conductor stage-report --out /srv/conductor/state/runs --since <ISO> --until <ISO>`.
+7. Откат: убрать действие или `autonomy = 0` (правка роадмапа), либо удалить drop-in,
+   либо удалить ключ App. Сделанные записи не отменяются.
diff --git a/deploy/conductor/conductor.service b/deploy/conductor/conductor.service
index e360fd2..b0412c6 100644
--- a/deploy/conductor/conductor.service
+++ b/deploy/conductor/conductor.service
@@ -12,4 +12,4 @@ UMask=0027
 TimeoutStartSec=55min
 Environment=GH_CONFIG_DIR=/srv/conductor/gh
 WorkingDirectory=/srv/conductor/devtools
-ExecStart=/usr/bin/flock -n -E 0 /srv/conductor/state/conductor.lock /usr/local/bin/uv run --frozen python -m conductor run --root /srv/conductor/workspace --out /srv/conductor/state/runs
+ExecStart=/usr/bin/flock -n -E 0 /srv/conductor/state/conductor.lock /usr/local/bin/uv run --frozen python -m conductor run --root /srv/conductor/workspace --out /srv/conductor/state/runs --trigger timer
```

- [ ] **Step 4: Run tests**

Run: `chmod +x deploy/conductor/acceptance.sh && uv run pytest tests/conductor/test_deploy_slice1.py tests/conductor/test_deploy.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite, lint, types**

Run: `uv run pytest -q && uv run --group selfcheck ruff format conductor tests/conductor deploy/conductor && uv run --group selfcheck ruff check --fix conductor tests/conductor deploy/conductor && uv run --group selfcheck pyrefly check conductor tests/conductor`
Expected: всё зелёное; ruff `--fix` правит только сортировку импортов и форматирование; pyrefly — 0 ошибок в `conductor/` и `tests/conductor/`.

Примечание (проверено сборкой кода частей A и B `tools/build_plan.py` в чистый клон devtools, 2026-10-01: база `57ad631`, повторно — на `179f9b8`; conductor, `pyproject.toml` и `uv.lock` между ними не менялись, кроме пина steward): `tests/conductor` — 499 passed, pyrefly по `conductor tests/conductor` — 0 ошибок, ruff — чисто; мутационная проверка (Task B9, шаг 6) — 47 условий, 0 выживших.

- [ ] **Step 6: Commit**

```bash
git add deploy/conductor docs/conductor tests/conductor/test_deploy_slice1.py
git commit -m "conductor: деплой среза 1 — примеры конфигов, drop-in тени, приёмка в песочнице"
```

---

## Что часть B доказывает и чего не делает

- Доказывает (фейк GitHub, локальные git-репо, настоящий `Writer`): пять действий по таблицам §6–§7, повторную проверку основания перед каждым шагом (изменение между планированием и шагом и между шагами — мутаций нет), поиск эффекта после обрыва без повторной мутации, вопросы с периодом открытия и эпизодами сбоев, производный вопрос, отсутствие дублей по маркерам, стабильность `fact_id` (id события `closed` для issue), изоляцию мини-флота приёмки, показатели ступени.
- Остаточная гонка (О §5.0): только между последним свежим чтением всех оснований шага и самой мутацией — API условной записи не даёт. Намеренно не прочитанное основание к ней не относится: потребность PR, выполненность пункта и подтверждение закрытия перечитываются перед шагом.
- Не делает: живую приёмку (это шаг владельца по `docs/conductor/acceptance/sandbox.md`), включение тени и ступеней на VPS (drop-in и правки роадмапа — владелец).
